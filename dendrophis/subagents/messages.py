"""Message types for subagent communication."""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class SubagentRequest:
    """Request sent to a subagent."""

    agent: Literal[
        "researcher",
        "planner",
        "code-writer",
        "code-reviewer",
        "test-runner",
        "debugger",
    ]
    task_id: str
    payload: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentResponse:
    """Response from a subagent."""

    agent: str
    task_id: str
    status: Literal["success", "failure", "needs_clarification"]
    result: dict[str, Any]
    logs: list[str] = field(default_factory=list)
