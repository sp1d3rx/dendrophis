"""Tool execution logic for Session."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from dendrophis.events import (
    ToolConfirmationCancelledEvent,
    ToolConfirmationRequestEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
)
from dendrophis.permissions import Decision, PermissionPolicy
from dendrophis.tools.bash_sandbox import BashSandbox, is_heredoc_write_pattern
from dendrophis.tools.executor import ToolResult, is_tool_error
from dendrophis.tools.names import ToolName

# Constants for tool execution timeouts
CONFIRMATION_TIMEOUT = 300.0  # 5 minutes
POLL_INTERVAL = 0.1
# Safety-net timeout for tools that don't declare their own (see BaseTool.execution_timeout).
TOOL_EXECUTION_TIMEOUT = 120.0  # 2 minutes
# Margin added on top of a tool's declared timeout so the tool's own timeout
# (the only one that can kill a stuck subprocess) fires first.
TOOL_TIMEOUT_MARGIN = 15.0

# Alias for backwards compatibility
FallbackToolResult = ToolResult


class ToolLike(Protocol):
    """Protocol for tool objects expected by SessionToolExecutor."""

    @property
    def self_confirming(self) -> bool: ...

    def execution_timeout(self, arguments: dict[str, Any]) -> float | None: ...

    @property
    def silent(self) -> bool: ...

    @silent.setter
    def silent(self, value: bool) -> None: ...


class ToolRegistryLike(Protocol):
    """Protocol for tool registry expected by SessionToolExecutor."""

    def get(self, name: str) -> ToolLike | None: ...


class ToolResultLike(Protocol):
    """Protocol for tool results expected by SessionToolExecutor."""

    @property
    def tool_call_id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def content(self) -> str: ...

    @property
    def success(self) -> bool: ...


class ToolExecutorLike(Protocol):
    """Protocol for tool executors expected by SessionToolExecutor."""

    async def execute(self, tool_call: Any) -> ToolResultLike: ...


def tool_call_to_payload(tool_call: Any) -> dict[str, Any]:
    """Convert a tool call to a payload dict for context storage."""
    return {
        "id": tool_call.id,  # No hashing - use original ID
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": tool_call.arguments or "{}",
        },
    }


class SessionToolExecutor:
    """Handles tool execution with confirmation flow for Session."""

    def __init__(
        self,
        tool_registry: ToolRegistryLike | None,
        tool_executor: ToolExecutorLike | None,
        event_bus: Any | None,
        config: Any,
        pending_confirmations: dict[str, bool],
        confirmation_results: dict[str, bool],
        cancel_flag: threading.Event,
        emit: Callable[[Any], None],
        debug_logger: Callable[[str], None] | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._event_bus = event_bus
        self._config = config
        self._pending_confirmations = pending_confirmations
        self._confirmation_results = confirmation_results
        self._cancel_flag = cancel_flag
        self._emit = emit
        self._debug_logger = debug_logger
        self._confirmation_futures: dict[str, asyncio.Future] = {}
        self._cancel_watcher_task: asyncio.Task | None = None

    def respond_to_confirmation(self, request_identifier: str, approved: bool) -> None:
        """Record a confirmation response and wake its waiting coroutine immediately."""
        self._confirmation_results[request_identifier] = approved
        future = self._confirmation_futures.get(request_identifier)
        if future is not None and not future.done():
            future.set_result(approved)

    def update_tools(
        self,
        tool_registry: ToolRegistryLike | None,
        tool_executor: ToolExecutorLike | None,
    ) -> None:
        """Update the tool registry and executor dependencies."""
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor

    async def execute(self, tool_calls: list[Any]) -> list[Any]:
        """Execute tool calls with hierarchical confirmation flow."""
        # Log tool execution start
        if os.environ.get("DENDROPHIS_TOOL_LOG") == "1":
            from dendrophis.utils import _tool_log

            _tool_log("=== SESSION TOOL EXECUTOR ===")
            _tool_log(f"Executing {len(tool_calls)} tool calls")
            for call_index, tool_call in enumerate(tool_calls):
                _tool_log(f"  Tool {call_index + 1}: {tool_call.name}(id={tool_call.id})")
                _tool_log(f"    Arguments: {tool_call.arguments!r}")

        policy = PermissionPolicy.from_config(self._config)

        pending_approvals: list[tuple[int, Any, str]] = []
        invalid_tools: list[tuple[int, Any, str]] = []
        approved_tools: list[tuple[int, Any, bool]] = []

        for call_index, tool_call in enumerate(tool_calls):
            if self._cancel_flag.is_set():
                break

            # Validate arguments first before doing any permission or confirmation checks
            error_message = self._validate_tool_arguments(tool_call)
            if error_message is not None:
                invalid_tools.append((call_index, tool_call, error_message))
                continue

            if tool_call.name == ToolName.BASH:
                self._process_bash_tool(
                    call_index,
                    tool_call,
                    policy,
                    invalid_tools,
                    approved_tools,
                    pending_approvals,
                )
            else:
                self._process_regular_tool(
                    call_index,
                    tool_call,
                    policy,
                    invalid_tools,
                    approved_tools,
                    pending_approvals,
                )

        results_by_index: dict[int, Any] = {}

        # Add invalid tool results first
        for call_index, tool_call, error_message in invalid_tools:
            single_result = self._make_error_result(tool_call, error_message)
            results_by_index[call_index] = single_result

        # Wait for confirmation responses (woken by respond_to_confirmation, no polling)
        if pending_approvals:
            self._ensure_cancel_watcher()
        for call_index, tool_call, request_identifier in pending_approvals:
            if self._cancel_flag.is_set():
                break

            approved = await self._wait_for_confirmation(request_identifier)

            if approved is None:
                # Timeout
                self._pending_confirmations.pop(request_identifier, None)
                # Plain message: _make_error_result performs the JSON encoding.
                # (Passing a pre-encoded '{"error": ...}' string here used to
                # double-encode it into {"error": "{\\"error\\": ...}"}.)
                single_result = self._make_error_result(tool_call, "Tool execution timed out waiting for approval")
                results_by_index[call_index] = single_result
            elif not approved:
                single_result = self._make_error_result(tool_call, "Tool execution rejected by user")
                results_by_index[call_index] = single_result
            else:
                approved_tools.append((call_index, tool_call, False))

        # No more confirmation waits this turn — stop the cancel watcher.
        await self._stop_cancel_watcher()

        # Sort approved tools by original call index to preserve tool call order
        approved_tools.sort(key=lambda approved_item: approved_item[0])

        # Execute all approved tools
        if self._parallel_tools_enabled() and not self._batch_has_self_confirming(approved_tools):
            # Run the batch concurrently; gather preserves input order so each result
            # maps back to its original call index.
            outcomes = await asyncio.gather(
                *(self._run_approved_tool(item) for item in approved_tools),
                return_exceptions=True,
            )
            for (call_index, tool_call, _silent), outcome in zip(approved_tools, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    single_result = self._make_error_result(tool_call, f"Tool execution crashed: {outcome}")
                else:
                    single_result = outcome
                results_by_index[call_index] = single_result
        else:
            for call_index, tool_call, silent in approved_tools:
                if self._cancel_flag.is_set():
                    break

                single_result = await self._execute_single_tool(tool_call, silent)
                results_by_index[call_index] = single_result

        return [results_by_index[call_index] for call_index in range(len(tool_calls)) if call_index in results_by_index]

    def _process_bash_tool(
        self,
        call_index: int,
        tool_call: Any,
        policy: PermissionPolicy,
        invalid_tools: list[tuple[int, Any, str]],
        approved_tools: list[tuple[int, Any, bool]],
        pending_approvals: list[tuple[int, Any, str]],
    ) -> None:
        """Apply permission policy to bash tool calls.

        Only argument parsing is tolerated here. A malformed ``arguments``
        payload is a malformed call, so it is rejected as invalid rather than
        asked for confirmation. Likewise, if the sandbox cannot simulate the
        command we fail closed (reject with a visible error) instead of
        silently falling through to a confirmation prompt for a command that
        was never safety-analyzed.
        """
        try:
            arguments = json.loads(tool_call.arguments) if tool_call.arguments else {}
        except Exception as parse_error:
            invalid_tools.append((call_index, tool_call, f"Invalid arguments format: {parse_error}"))
            return
        if not isinstance(arguments, dict):
            invalid_tools.append((call_index, tool_call, "Invalid arguments format: expected a JSON object"))
            return

        command = arguments.get("command", "")
        if not isinstance(command, str):
            invalid_tools.append((call_index, tool_call, "Invalid arguments format: 'command' must be a string"))
            return

        if is_heredoc_write_pattern(command):
            error_message = f"Bash heredoc file writes should use the 'write' tool instead. Command: {command[:50]}..."
            invalid_tools.append((call_index, tool_call, error_message))
            return

        try:
            simulation = BashSandbox().simulate(command)
        except Exception as simulation_error:
            if self._debug_logger is not None:
                self._debug_logger(f"Bash simulation failed for command {command!r}: {simulation_error!r}")
            invalid_tools.append((call_index, tool_call, f"Bash simulation failed: {simulation_error}"))
            return

        if self._tool_registry is not None:
            bash_tool = self._tool_registry.get(tool_call.name)
            cache_simulation = getattr(bash_tool, "set_precomputed_simulation", None)
            if callable(cache_simulation):
                cache_simulation(command, simulation)
        decision, reason = policy.check_bash(simulation)
        if decision == Decision.DENY:
            invalid_tools.append((call_index, tool_call, f"Blocked by permission policy: {reason}"))
            return
        if decision == Decision.ALLOW:
            approved_tools.append((call_index, tool_call, True))
            return
        # CONFIRM falls through
        self._request_confirmation(call_index, tool_call, pending_approvals)

    def _process_regular_tool(
        self,
        call_index: int,
        tool_call: Any,
        policy: PermissionPolicy,
        invalid_tools: list[tuple[int, Any, str]],
        approved_tools: list[tuple[int, Any, bool]],
        pending_approvals: list[tuple[int, Any, str]],
    ) -> None:
        """Apply permission policy to non-bash tool calls."""
        decision = policy.check_tool(tool_call.name)
        if decision == Decision.DENY:
            invalid_tools.append((call_index, tool_call, f"Tool '{tool_call.name}' is not permitted"))
            return
        if decision == Decision.ALLOW:
            approved_tools.append((call_index, tool_call, True))
            return

        # CONFIRM — skip generic dialog if tool manages its own confirmation
        tool_object = self._tool_registry.get(tool_call.name) if self._tool_registry else None
        if tool_object is not None and tool_object.self_confirming:
            approved_tools.append((call_index, tool_call, False))
            return

        self._request_confirmation(call_index, tool_call, pending_approvals)

    def _request_confirmation(
        self, call_index: int, tool_call: Any, pending_approvals: list[tuple[int, Any, str]]
    ) -> None:
        """Request user confirmation for a tool call."""
        request_identifier = str(uuid.uuid4())
        self._pending_confirmations[request_identifier] = True
        self._confirmation_futures[request_identifier] = asyncio.get_running_loop().create_future()
        self._emit(
            ToolConfirmationRequestEvent(
                request_id=request_identifier,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )
        pending_approvals.append((call_index, tool_call, request_identifier))

    async def _wait_for_confirmation(self, request_identifier: str) -> bool | None:
        """Wait for a user confirmation response.

        Returns True if approved, False if rejected, None on timeout or cancellation.
        Woken immediately by respond_to_confirmation (or the cancel watcher) — no polling.
        """
        future = self._confirmation_futures.get(request_identifier)
        if future is None:
            # Response already recorded before we started waiting.
            return self._confirmation_results.pop(request_identifier, None)
        try:
            result = await asyncio.wait_for(future, timeout=CONFIRMATION_TIMEOUT)
        except TimeoutError:
            result = None
        finally:
            self._confirmation_futures.pop(request_identifier, None)
            self._pending_confirmations.pop(request_identifier, None)
        if result is None:
            reason = "cancelled" if self._cancel_flag.is_set() else "timeout"
            self._emit(ToolConfirmationCancelledEvent(request_id=request_identifier, reason=reason))
        return result

    def _ensure_cancel_watcher(self) -> None:
        """Start the single task that resolves pending confirmations when streaming is cancelled."""
        if self._cancel_watcher_task is None or self._cancel_watcher_task.done():
            self._cancel_watcher_task = asyncio.create_task(self._watch_cancel_flag())

    async def _stop_cancel_watcher(self) -> None:
        """Stop the cancel watcher if it is still running (turn ended without cancel)."""
        watcher = self._cancel_watcher_task
        self._cancel_watcher_task = None
        if watcher is not None and not watcher.done():
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher

    async def _watch_cancel_flag(self) -> None:
        """Mirror the threading cancel flag into pending confirmation futures.

        ``_cancel_flag`` is a ``threading.Event`` set from the UI/cancel thread,
        which has no native asyncio awaitable. Instead of blocking a thread-pool
        worker on ``event.wait()`` per confirmation, this single shared task
        polls the flag on the event loop every ``POLL_INTERVAL`` (100 ms —
        imperceptible for a cancel) and, once set, resolves every pending
        confirmation future with ``None`` so the waiting coroutines treat it as
        a cancellation.
        """
        while not self._cancel_flag.is_set():
            await asyncio.sleep(POLL_INTERVAL)
        for future in list(self._confirmation_futures.values()):
            if not future.done():
                future.set_result(None)

    def _parallel_tools_enabled(self) -> bool:
        """Return the configured parallel-execution switch (default: enabled)."""
        tools_config = getattr(self._config, "tools", None)
        return bool(getattr(tools_config, "parallel_tools", True))

    def _batch_has_self_confirming(self, approved_tools: list[tuple[int, Any, bool]]) -> bool:
        """True if the batch contains a tool that manages its own interactive confirmation.

        Interactive confirmations must not overlap, so such batches run sequentially.
        """
        if not self._tool_registry:
            return False
        for _call_index, tool_call, _silent in approved_tools:
            tool_instance = self._tool_registry.get(tool_call.name)
            if tool_instance is not None and getattr(tool_instance, "self_confirming", False):
                return True
        return False

    async def _run_approved_tool(self, item: tuple[int, Any, bool]) -> Any:
        """Execute one approved tool call (concurrent batch path)."""
        _call_index, tool_call, silent = item
        return await self._execute_single_tool(tool_call, silent)

    def _validate_tool_arguments(self, tool_call: Any) -> str | None:
        """Validate that all required parameters are present in tool call arguments.

        Returns an error message string if invalid, or None if valid.
        """
        tool_instance = self._tool_registry.get(tool_call.name) if self._tool_registry else None
        if tool_instance is None:
            if (
                self._tool_registry
                and getattr(self._tool_registry, "is_disabled", None)
                and self._tool_registry.is_disabled(tool_call.name)
            ):
                return f"Tool '{tool_call.name}' is currently disabled and is not available."
            return f"Unknown tool: '{tool_call.name}'"

        try:
            argument_dictionary = json.loads(tool_call.arguments) if tool_call.arguments else {}
        except Exception as argument_parse_error:
            return f"Invalid arguments format: {argument_parse_error}"

        if hasattr(tool_instance, "parameters") and isinstance(tool_instance.parameters, dict):
            required_parameters = tool_instance.parameters.get("required", [])
            missing_parameters = [
                parameter_name for parameter_name in required_parameters if parameter_name not in argument_dictionary
            ]
            if missing_parameters:
                missing_parameters_summary = ", ".join(missing_parameters)
                return f"Missing required parameter(s): {missing_parameters_summary}"

        return None

    @staticmethod
    def _make_error_result(tool_call: Any, error_message: str) -> ToolResult:
        """Create an error result object for a tool call."""
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=json.dumps({"error": error_message}),
            success=False,
        )

    def _resolve_base_timeout(self, tool_call: Any) -> float:
        """Resolve the tool's declared per-call timeout, falling back to the executor default."""
        tool_instance = self._tool_registry.get(tool_call.name) if self._tool_registry else None
        if tool_instance is not None:
            timeout_fn = getattr(tool_instance, "execution_timeout", None)
            if timeout_fn is not None:
                try:
                    call_arguments = json.loads(tool_call.arguments) if tool_call.arguments else {}
                except Exception:
                    call_arguments = {}
                declared = timeout_fn(call_arguments)
                if declared is not None:
                    return declared
        return TOOL_EXECUTION_TIMEOUT

    async def _execute_single_tool(self, tool_call: Any, silent: bool) -> Any:
        """Execute a single approved tool call."""
        # Emit tool execution started
        description = ""
        try:
            call_arguments = json.loads(tool_call.arguments) if tool_call.arguments else {}
            description = call_arguments.get("description", "")
        except Exception:
            pass

        self._emit(
            ToolExecutionStartedEvent(
                tool_name=tool_call.name,
                description=description,
                arguments=tool_call.arguments,
                tool_call_index=tool_call.index,
            )
        )

        # For self-confirming tools, communicate whether to skip interactive UI
        if self._tool_registry:
            tool_instance = self._tool_registry.get(tool_call.name)
            if tool_instance is not None and getattr(tool_instance, "self_confirming", False):
                tool_instance.silent = silent

        # Execute the tool
        start_time = asyncio.get_running_loop().time()
        error_details: str | None = None
        # The tool's declared timeout fires first (it can kill the subprocess);
        # the executor limit is a safety net for tools without one.
        base_timeout = self._resolve_base_timeout(tool_call)
        timeout_limit = base_timeout + TOOL_TIMEOUT_MARGIN
        try:
            if self._tool_executor is None:
                raise ValueError("No tool executor provided")
            single_result = await asyncio.wait_for(
                self._tool_executor.execute(tool_call),
                timeout=timeout_limit,
            )
        except TimeoutError:
            timeout_seconds_integer = int(base_timeout)
            error_details = f"Tool execution timed out after {timeout_seconds_integer} seconds"
            single_result = self._make_error_result(tool_call, error_details)
        except Exception as execution_error:
            error_details = str(execution_error)
            single_result = self._make_error_result(tool_call, f"Execution failed: {execution_error}")

        duration_seconds = max(0.0, asyncio.get_running_loop().time() - start_time)

        # Emit tool execution finished
        execution_succeeded = (
            single_result.success if hasattr(single_result, "success") else not is_tool_error(single_result.content)
        )
        self._emit(
            ToolExecutionFinishedEvent(
                tool_name=tool_call.name,
                success=execution_succeeded,
                tool_call_id=tool_call.id,
                duration_seconds=duration_seconds,
                error_message=error_details,
            )
        )
        return single_result
