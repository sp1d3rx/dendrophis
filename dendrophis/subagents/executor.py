"""Subagent execution engine."""

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from .messages import SubagentRequest, SubagentResponse
from .registry import get_registry


@dataclass
class ExecutionResult:
    """Result of executing a subagent task."""

    success: bool
    response: SubagentResponse | None = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)


class SubagentExecutor:
    """Executes subagent requests and manages their lifecycle."""

    def __init__(self):
        self.registry = get_registry()
        self._active_tasks: dict[str, SubagentRequest] = {}
        self._task_status: dict[str, Literal["pending", "running", "complete", "failed"]] = {}

    async def execute(
        self,
        agent: str,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute a subagent request.

        Args:
            agent: Name of the subagent to invoke
            payload: Task-specific data
            context: Additional context (files, memories, etc.)

        Returns:
            ExecutionResult with response or error
        """
        # Validate agent exists
        definition = self.registry.get(agent)
        if not definition:
            return ExecutionResult(
                success=False,
                error=f"Unknown agent: {agent}",
            )

        # Create request
        task_id = str(uuid.uuid4())[:8]
        request = SubagentRequest(
            agent=agent,
            task_id=task_id,
            payload=payload,
            context=context or {},
        )
        self._active_tasks[task_id] = request
        self._task_status[task_id] = "pending"

        try:
            self._task_status[task_id] = "running"
            # Invoke handler if registered
            if definition.handler:
                response = await definition.handler(request)
            else:
                response = SubagentResponse(
                    agent=agent,
                    task_id=task_id,
                    status="failure",
                    result={"error": f"No handler registered for {agent}"},
                )
            self._task_status[task_id] = "complete" if response.status == "success" else "failed"
            return ExecutionResult(success=response.status == "success", response=response)

        except Exception as e:
            self._task_status[task_id] = "failed"
            return ExecutionResult(
                success=False,
                error=str(e),
            )
        finally:
            del self._active_tasks[task_id]

    def get_status(self, task_id: str) -> Literal["pending", "running", "complete", "failed", "unknown"]:
        """Get status of a task."""
        return self._task_status.get(task_id, "unknown")
