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
from dendrophis.tools.executor import ToolResult
from dendrophis.tools.names import ToolName

# Constants for tool execution timeouts
CONFIRMATION_TIMEOUT = 300.0  # 5 minutes
POLL_INTERVAL = 0.1
TOOL_EXECUTION_TIMEOUT = 120.0  # 2 minutes

# Alias for backwards compatibility
FallbackToolResult = ToolResult


class ToolLike(Protocol):
    """Protocol for tool objects expected by SessionToolExecutor."""

    @property
    def self_confirming(self) -> bool: ...

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


def is_tool_error(content: str) -> bool:
    """Return True if a tool result content indicates a failure."""
    try:
        parsed_payload = json.loads(content)
        if isinstance(parsed_payload, dict):
            return "error" in parsed_payload
    except (json.JSONDecodeError, TypeError):
        pass
    content_lower = content.lower()
    return "error" in content_lower or "execution failed" in content_lower


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
        self._confirmation_event = asyncio.Event()

    def notify_confirmation(self) -> None:
        """Signal waiting coroutines that confirmation state has updated."""
        self._confirmation_event.set()

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
            from dendrophis.session.chat import _tool_log

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

        # Poll for confirmation responses
        for call_index, tool_call, request_identifier in pending_approvals:
            if self._cancel_flag.is_set():
                break

            approved = await self._wait_for_confirmation(request_identifier)

            if approved is None:
                # Timeout
                self._pending_confirmations.pop(request_identifier, None)
                error_message = '{"error": "Tool execution timed out waiting for approval"}'
                single_result = self._make_error_result(tool_call, error_message)
                results_by_index[call_index] = single_result
            elif not approved:
                error_message = '{"error": "Tool execution rejected by user"}'
                single_result = self._make_error_result(tool_call, error_message)
                results_by_index[call_index] = single_result
            else:
                approved_tools.append((call_index, tool_call, False))

        # Sort approved tools by original call index to preserve tool call order
        approved_tools.sort(key=lambda approved_item: approved_item[0])

        # Execute all approved tools
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
        """Apply permission policy to bash tool calls."""
        try:
            arguments = json.loads(tool_call.arguments) if tool_call.arguments else {}
            command = arguments.get("command", "")
            if is_heredoc_write_pattern(command):
                error_message = (
                    f"Bash heredoc file writes should use the 'write' tool instead. Command: {command[:50]}..."
                )
                invalid_tools.append((call_index, tool_call, error_message))
                return
            simulation = BashSandbox().simulate(command)
            decision, reason = policy.check_bash(simulation)
            if decision == Decision.DENY:
                invalid_tools.append((call_index, tool_call, f"Blocked by permission policy: {reason}"))
                return
            if decision == Decision.ALLOW:
                approved_tools.append((call_index, tool_call, True))
                return
            # CONFIRM falls through
        except Exception:
            pass  # Let invalid JSON reach normal error handling

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
        self._emit(
            ToolConfirmationRequestEvent(
                request_id=request_identifier,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )
        pending_approvals.append((call_index, tool_call, request_identifier))

    async def _wait_for_confirmation(self, request_identifier: str) -> bool | None:
        """Wait for user confirmation response. Returns True if approved, False if rejected, None if timeout."""
        running_loop = asyncio.get_running_loop()
        start_timestamp = running_loop.time()

        while (running_loop.time() - start_timestamp) < CONFIRMATION_TIMEOUT:
            if request_identifier in self._confirmation_results:
                approved_status = self._confirmation_results.pop(request_identifier)
                self._pending_confirmations.pop(request_identifier, None)
                return approved_status

            if self._cancel_flag.is_set():
                self._emit(ToolConfirmationCancelledEvent(request_id=request_identifier, reason="cancelled"))
                return None

            self._confirmation_event.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._confirmation_event.wait(), timeout=POLL_INTERVAL)

        self._emit(ToolConfirmationCancelledEvent(request_id=request_identifier, reason="timeout"))
        return None

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
            if tool_instance is not None and tool_instance.self_confirming:
                tool_instance.silent = silent

        # Execute the tool
        start_time = asyncio.get_running_loop().time()
        error_details: str | None = None
        try:
            if self._tool_executor is None:
                raise ValueError("No tool executor provided")
            single_result = await asyncio.wait_for(
                self._tool_executor.execute(tool_call),
                timeout=TOOL_EXECUTION_TIMEOUT,
            )
        except TimeoutError:
            error_details = "Tool execution timed out after 120 seconds"
            single_result = ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content='{"error": "Tool execution timed out after 120 seconds"}',
                success=False,
            )
        except Exception as execution_error:
            error_details = str(execution_error)
            single_result = ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps({"error": f"Execution failed: {execution_error}"}),
                success=False,
            )

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
