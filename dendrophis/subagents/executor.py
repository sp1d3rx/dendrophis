"""Subagent execution engine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from dendrophis.events import (
    SubagentTaskFinishedEvent,
    SubagentTaskStartedEvent,
    get_event_bus,
)

from .messages import SubagentRequest, SubagentResponse
from .registry import get_registry

_MAX_TRACKED_TASK_STATUSES = 200


@dataclass
class ExecutionResult:
    """Result of executing a subagent task."""

    success: bool
    response: SubagentResponse | None = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)


class SubagentExecutor:
    """Executes subagent requests and manages their lifecycle."""

    def __init__(self, event_bus: Any | None = None) -> None:
        self.registry = get_registry()
        self._event_bus = event_bus or get_event_bus()
        self._active_tasks: dict[str, SubagentRequest] = {}
        self._task_status: dict[str, Literal["pending", "running", "complete", "failed"]] = {}

    def _emit(self, event: Any) -> None:
        """Safely emit an event to the event bus if available."""
        if self._event_bus:
            self._event_bus.publish(event)

    def _record_status(self, task_id: str, status: Literal["pending", "running", "complete", "failed"]) -> None:
        self._task_status[task_id] = status
        if len(self._task_status) > _MAX_TRACKED_TASK_STATUSES:
            oldest_key = next(iter(self._task_status))
            self._task_status.pop(oldest_key, None)

    async def execute(
        self,
        agent: str,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute a subagent request."""
        definition = self.registry.get(agent)
        if not definition:
            error_message = f"Unknown agent: {agent}"
            return ExecutionResult(
                success=False,
                error=error_message,
            )

        task_id = str(uuid.uuid4())[:8]
        request = SubagentRequest(
            agent=agent,
            task_id=task_id,
            payload=payload,
            context=context or {},
        )
        self._active_tasks[task_id] = request
        self._record_status(task_id, "pending")

        self._emit(
            SubagentTaskStartedEvent(
                task_id=task_id,
                agent_name=agent,
                payload=payload,
            )
        )

        try:
            self._record_status(task_id, "running")
            if definition.handler:
                response = await definition.handler(request)
            else:
                response = SubagentResponse(
                    agent=agent,
                    task_id=task_id,
                    status="failure",
                    result={"error": f"No handler registered for {agent}"},
                )

            is_successful = response.status in ("success", "needs_clarification")
            self._record_status(task_id, "complete" if is_successful else "failed")

            self._emit(
                SubagentTaskFinishedEvent(
                    task_id=task_id,
                    agent_name=agent,
                    success=is_successful,
                    result=response.result if isinstance(response.result, dict) else {"result": response.result},
                    error_message=None if is_successful else str(response.result),
                )
            )
            return ExecutionResult(success=is_successful, response=response)

        except Exception as subagent_execution_error:
            error_message = str(subagent_execution_error)
            self._record_status(task_id, "failed")
            self._emit(
                SubagentTaskFinishedEvent(
                    task_id=task_id,
                    agent_name=agent,
                    success=False,
                    result=None,
                    error_message=error_message,
                )
            )
            return ExecutionResult(
                success=False,
                error=error_message,
            )
        finally:
            self._active_tasks.pop(task_id, None)

    def get_status(self, task_id: str) -> Literal["pending", "running", "complete", "failed", "unknown"]:
        """Get status of a task."""
        return self._task_status.get(task_id, "unknown")
