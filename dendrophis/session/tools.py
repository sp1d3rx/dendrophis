"""Tool execution logic for Session."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from dendrophis.events import (
    ToolConfirmationRequestEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
)
from dendrophis.permissions import Decision, PermissionPolicy
from dendrophis.tools.bash_sandbox import BashSandbox, is_heredoc_write_pattern
from dendrophis.tools.names import ToolName

# Constants for tool execution timeouts
CONFIRMATION_TIMEOUT = 300.0  # 5 minutes
POLL_INTERVAL = 0.1
TOOL_EXECUTION_TIMEOUT = 120.0  # 2 minutes


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


@dataclass
class FallbackToolResult:
    """A fallback ToolResultLike structure for executing error and timeout results."""

    tool_call_id: str
    name: str
    content: str


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
        return "error" in json.loads(content)
    except Exception:
        return "error" in content.lower() or "execution failed" in content.lower()


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
        import os

        if os.environ.get("DENDROPHIS_TOOL_LOG") == "1":
            from dendrophis.session.chat import _tool_log

            _tool_log("=== SESSION TOOL EXECUTOR ===")
            _tool_log(f"Executing {len(tool_calls)} tool calls")
            for index, tool_call in enumerate(tool_calls):
                _tool_log(f"  Tool {index + 1}: {tool_call.name}(id={tool_call.id})")
                _tool_log(f"    Arguments: {tool_call.arguments!r}")

        policy = PermissionPolicy.from_config(self._config)

        pending_approvals: list[tuple[Any, str]] = []
        invalid_tools: list[tuple[Any, str]] = []
        approved_tools: list[tuple[Any, bool]] = []

        for tool_call in tool_calls:
            if self._cancel_flag.is_set():
                break

            # Validate arguments first before doing any permission or confirmation checks
            error_message = self._validate_tool_arguments(tool_call)
            if error_message is not None:
                invalid_tools.append((tool_call, error_message))
                continue

            if tool_call.name == ToolName.BASH:
                self._process_bash_tool(
                    tool_call,
                    policy,
                    invalid_tools,
                    approved_tools,
                    pending_approvals,
                )
            else:
                self._process_regular_tool(
                    tool_call,
                    policy,
                    invalid_tools,
                    approved_tools,
                    pending_approvals,
                )

        results: list[Any] = []

        # Add invalid tool results first
        for tool_call, error_message in invalid_tools:
            result = self._make_error_result(tool_call, error_message)
            results.append(result)

        # Poll for confirmation responses
        for tool_call, request_id in pending_approvals:
            if self._cancel_flag.is_set():
                break

            approved = await self._wait_for_confirmation(request_id)

            if approved is None:
                # Timeout
                self._pending_confirmations.pop(request_id, None)
                error_message = '{"error": "Tool execution timed out waiting for approval"}'
                result = self._make_error_result(tool_call, error_message)
                results.append(result)
            elif not approved:
                error_message = '{"error": "Tool execution rejected by user"}'
                result = self._make_error_result(tool_call, error_message)
                results.append(result)
            else:
                approved_tools.append((tool_call, False))

        # Execute all approved tools
        for tool_call, silent in approved_tools:
            if self._cancel_flag.is_set():
                break

            await self._execute_single_tool(tool_call, silent, results)

        return results

    def _process_bash_tool(
        self,
        tool_call: Any,
        policy: PermissionPolicy,
        invalid_tools: list[tuple[Any, str]],
        approved_tools: list[tuple[Any, bool]],
        pending_approvals: list[tuple[Any, str]],
    ) -> None:
        """Apply permission policy to bash tool calls."""
        try:
            args = json.loads(tool_call.arguments) if tool_call.arguments else {}
            command = args.get("command", "")
            if is_heredoc_write_pattern(command):
                error_message = (
                    f"Bash heredoc file writes should use the 'write' tool instead. Command: {command[:50]}..."
                )
                invalid_tools.append((tool_call, error_message))
                return
            simulation = BashSandbox().simulate(command)
            decision, reason = policy.check_bash(simulation)
            if decision == Decision.DENY:
                invalid_tools.append((tool_call, f"Blocked by permission policy: {reason}"))
                return
            if decision == Decision.ALLOW:
                approved_tools.append((tool_call, True))
                return
            # CONFIRM falls through
        except Exception:
            pass  # Let invalid JSON reach normal error handling

        self._request_confirmation(tool_call, pending_approvals)

    def _process_regular_tool(
        self,
        tool_call: Any,
        policy: PermissionPolicy,
        invalid_tools: list[tuple[Any, str]],
        approved_tools: list[tuple[Any, bool]],
        pending_approvals: list[tuple[Any, str]],
    ) -> None:
        """Apply permission policy to non-bash tool calls."""
        decision = policy.check_tool(tool_call.name)
        if decision == Decision.DENY:
            invalid_tools.append((tool_call, f"Tool '{tool_call.name}' is not permitted"))
            return
        if decision == Decision.ALLOW:
            approved_tools.append((tool_call, True))
            return

        # CONFIRM — skip generic dialog if tool manages its own confirmation
        tool_obj = self._tool_registry.get(tool_call.name) if self._tool_registry else None
        if tool_obj is not None and tool_obj.self_confirming:
            approved_tools.append((tool_call, False))
            return

        self._request_confirmation(tool_call, pending_approvals)

    def _request_confirmation(self, tool_call: Any, pending_approvals: list[tuple[Any, str]]) -> None:
        """Request user confirmation for a tool call."""
        request_id = str(uuid.uuid4())
        self._pending_confirmations[request_id] = True
        self._emit(
            ToolConfirmationRequestEvent(
                request_id=request_id,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )
        pending_approvals.append((tool_call, request_id))

    async def _wait_for_confirmation(self, request_id: str) -> bool | None:
        """Wait for user confirmation response. Returns True if approved, False if rejected, None if timeout."""
        waited = 0.0
        while waited < CONFIRMATION_TIMEOUT:
            if request_id in self._confirmation_results:
                approved = self._confirmation_results.pop(request_id)
                self._pending_confirmations.pop(request_id, None)
                return approved
            await asyncio.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL
            if self._cancel_flag.is_set():
                return None
        return None

    def _validate_tool_arguments(self, tool_call: Any) -> str | None:
        """Validate that all required parameters are present in tool call arguments.

        Returns an error message string if invalid, or None if valid.
        """
        tool_obj = self._tool_registry.get(tool_call.name) if self._tool_registry else None
        if tool_obj is None:
            if self._tool_registry and getattr(self._tool_registry, "is_disabled", None) and self._tool_registry.is_disabled(tool_call.name):
                return f"Tool '{tool_call.name}' is currently disabled and is not available."
            return f"Unknown tool: '{tool_call.name}'"

        try:
            import json

            args = json.loads(tool_call.arguments) if tool_call.arguments else {}
        except Exception as parse_error:
            return f"Invalid arguments format: {parse_error}"

        if hasattr(tool_obj, "parameters") and isinstance(tool_obj.parameters, dict):
            required_params = tool_obj.parameters.get("required", [])
            missing_params = [param for param in required_params if param not in args]
            if missing_params:
                missing_str = ", ".join(missing_params)
                return f"Missing required parameter(s): {missing_str}"

        return None

    @staticmethod
    def _make_error_result(tool_call: Any, error_message: str) -> FallbackToolResult:
        """Create an error result object for a tool call."""
        return FallbackToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=json.dumps({"error": error_message}),
        )

    async def _execute_single_tool(self, tool_call: Any, silent: bool, results: list[Any]) -> None:
        """Execute a single approved tool call."""
        # Emit tool execution started
        description = ""
        try:
            args = json.loads(tool_call.arguments) if tool_call.arguments else {}
            description = args.get("description", "")
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
            tool_obj = self._tool_registry.get(tool_call.name)
            if tool_obj is not None and tool_obj.self_confirming:
                tool_obj.silent = silent

        # Execute the tool
        try:
            if self._tool_executor is None:
                raise ValueError("No tool executor provided")
            result = await asyncio.wait_for(
                self._tool_executor.execute(tool_call),
                timeout=TOOL_EXECUTION_TIMEOUT,
            )
        except TimeoutError:
            result = FallbackToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content='{"error": "Tool execution timed out after 120 seconds"}',
            )
        except Exception as exception_error:
            result = FallbackToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps({"error": f"Execution failed: {exception_error}"}),
            )

        # Emit tool execution finished
        success = "error" not in result.content.lower()
        self._emit(ToolExecutionFinishedEvent(tool_name=tool_call.name, success=success))
        results.append(result)
