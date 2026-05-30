"""Subagent system for dendrophis.

Orchestrator + specialized workers architecture.
"""

from .executor import ExecutionResult, SubagentExecutor
from .messages import SubagentRequest, SubagentResponse
from .registry import SubagentRegistry, get_registry

__all__ = [
    "ExecutionResult",
    "SubagentExecutor",
    "SubagentRegistry",
    "SubagentRequest",
    "SubagentResponse",
    "get_registry",
]

# Module-level reference to the active session's subagent executor.
# Set by Session._initialize_subagents() for tools to access.
_session_executor: SubagentExecutor | None = None


def set_session_executor(executor: SubagentExecutor) -> None:
    """Set the global session executor reference."""
    global _session_executor
    _session_executor = executor


def get_session_executor() -> SubagentExecutor | None:
    """Get the global session executor reference."""
    return _session_executor
