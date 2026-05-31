from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    """Immutable record of a tool execution."""

    content: str
    toolCallId: str
    metadata: dict[str, Any] = field(default_factory=dict)
