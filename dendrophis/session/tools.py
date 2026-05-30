"""Tool execution logic for Session."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from dendrophis.events import (
    ToolConfirmationRequestEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
)
from dendrophis.permissions import Decision, PermissionPolicy
from dendrophis.tools.bash_sandbox import BashSandbox, is_heredoc_write_pattern

# from dendrophis.utils import _sanitize_tool_id  # REMOVED - no tool ID hashing

if TYPE_CHECKING:
    from dendrophis.events import EventBus
    from dendrophis.tools import ToolExecutor, ToolRegistry


# Constants for tool execution timeouts
CONFIRMATION_TIMEOUT = 300.0  # 5 minutes
POLL_INTERVAL = 0.1
TOOL_EXECUTION_TIMEOUT = 120.0  # 2 minutes


def tool_call_to_payload(tc: Any) -> dict[str, Any]:
    """Convert a tool call to a payload dict for context storage."""
    return {
        "id": tc.id,  # No hashing - use original ID
        "type": "function",
        "function": {"name": tc.name, "arguments": tc.arguments or "{}"},
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
        tool_registry: ToolRegistry | None,
        tool_executor: ToolExecutor | None,
        event_bus: EventBus | None,
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

    async def execute(self, tool_calls: list[Any]) -> list[Any]:
        """Execute tool calls with hierarchical confirmation flow."""
        # Log tool execution start
        import os
        if os.environ.get('DENDROPHIS_TOOL_LOG') == '1':
            from dendrophis.session.session import _tool_log
            _tool_log("=== SESSION TOOL EXECUTOR ===")
            _tool_log(f"Executing {len(tool_calls)} tool calls")
            for i, tc in enumerate(tool_calls):
                _tool_log(f"  Tool {i+1}: {tc.name}(id={tc.id})")
                _tool_log(f"    Arguments: {tc.arguments!r}")
        
        policy = PermissionPolicy.from_config(self._config)

        pending_approvals: list[tuple[Any, str]] = []
        invalid_tools: list[tuple[Any, str]] = []
        approved_tools: list[tuple[Any, bool]] = []

        for tc in tool_calls:
            if self._cancel_flag.is_set():
                break

            if tc.name == "bash":
                self._process_bash_tool(tc, policy, invalid_tools, approved_tools, pending_approvals)
            else:
                self._process_regular_tool(tc, policy, invalid_tools, approved_tools, pending_approvals)

        results: list[Any] = []

        # Add invalid tool results first
        for tc, error_msg in invalid_tools:
            result = self._make_error_result(tc, error_msg)
            results.append(result)

        # Poll for confirmation responses
        for tc, request_id in pending_approvals:
            if self._cancel_flag.is_set():
                break

            approved = await self._wait_for_confirmation(request_id)

            if approved is None:
                # Timeout
                self._pending_confirmations.pop(request_id, None)
                err_msg = '{"error": "Tool execution timed out waiting for approval"}'
                result = self._make_error_result(tc, err_msg)
                results.append(result)
            elif not approved:
                err_msg = '{"error": "Tool execution rejected by user"}'
                result = self._make_error_result(tc, err_msg)
                results.append(result)
            else:
                approved_tools.append((tc, False))

        # Execute all approved tools
        for tc, silent in approved_tools:
            if self._cancel_flag.is_set():
                break

            await self._execute_single_tool(tc, silent, results)

        return results

    def _process_bash_tool(
        self,
        tc: Any,
        policy: PermissionPolicy,
        invalid_tools: list[tuple[Any, str]],
        approved_tools: list[tuple[Any, bool]],
        pending_approvals: list[tuple[Any, str]],
    ) -> None:
        """Apply permission policy to bash tool calls."""
        try:
            args = json.loads(tc.arguments) if tc.arguments else {}
            command = args.get("command", "")
            if is_heredoc_write_pattern(command):
                msg = f"Bash heredoc file writes should use the 'write' tool instead. Command: {command[:50]}..."
                invalid_tools.append((tc, msg))
                return
            sim = BashSandbox().simulate(command)
            decision, reason = policy.check_bash(sim)
            if decision == Decision.DENY:
                invalid_tools.append((tc, f"Blocked by permission policy: {reason}"))
                return
            if decision == Decision.ALLOW:
                approved_tools.append((tc, True))
                return
            # CONFIRM falls through
        except Exception:
            pass  # Let invalid JSON reach normal error handling

        self._request_confirmation(tc, pending_approvals)

    def _process_regular_tool(
        self,
        tc: Any,
        policy: PermissionPolicy,
        invalid_tools: list[tuple[Any, str]],
        approved_tools: list[tuple[Any, bool]],
        pending_approvals: list[tuple[Any, str]],
    ) -> None:
        """Apply permission policy to non-bash tool calls."""
        decision = policy.check_tool(tc.name)
        if decision == Decision.DENY:
            invalid_tools.append((tc, f"Tool '{tc.name}' is not permitted"))
            return
        if decision == Decision.ALLOW:
            approved_tools.append((tc, True))
            return

        # CONFIRM — skip generic dialog if tool manages its own confirmation
        tool_obj = self._tool_registry.get(tc.name) if self._tool_registry else None
        if tool_obj is not None and tool_obj.self_confirming:
            approved_tools.append((tc, False))
            return

        self._request_confirmation(tc, pending_approvals)

    def _request_confirmation(self, tc: Any, pending_approvals: list[tuple[Any, str]]) -> None:
        """Request user confirmation for a tool call."""
        request_id = str(uuid.uuid4())
        self._pending_confirmations[request_id] = True
        self._emit(
            ToolConfirmationRequestEvent(
                request_id=request_id,
                tool_name=tc.name,
                arguments=tc.arguments,
            )
        )
        pending_approvals.append((tc, request_id))

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

    @staticmethod
    def _make_error_result(tc: Any, error_msg: str) -> Any:
        """Create an error result object for a tool call."""
        return type(
            "ToolResult",
            (),
            {"tool_call_id": tc.id, "name": tc.name, "content": json.dumps({"error": error_msg})},
        )

    async def _execute_single_tool(self, tc: Any, silent: bool, results: list[Any]) -> None:
        """Execute a single approved tool call."""
        # Emit tool execution started
        description = ""
        try:
            args = json.loads(tc.arguments) if tc.arguments else {}
            description = args.get("description", "")
        except Exception:
            pass

        self._emit(
            ToolExecutionStartedEvent(
                tool_name=tc.name,
                description=description,
                arguments=tc.arguments,
                tool_call_index=tc.index,
            )
        )

        # For self-confirming tools, communicate whether to skip interactive UI
        if self._tool_registry:
            tool_obj = self._tool_registry.get(tc.name)
            if tool_obj is not None and tool_obj.self_confirming:
                tool_obj.silent = silent  # type: ignore[attr-defined]

        # Execute the tool
        try:
            result = await asyncio.wait_for(
                self._tool_executor.execute(tc),
                timeout=TOOL_EXECUTION_TIMEOUT,  # type: ignore[arg-type]
            )
        except TimeoutError:
            result = type(
                "ToolResult",
                (),
                {
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": '{"error": "Tool execution timed out after 120 seconds"}',
                },
            )

        # Emit tool execution finished
        success = "error" not in result.content.lower()
        self._emit(ToolExecutionFinishedEvent(tool_name=tc.name, success=success))
        results.append(result)
