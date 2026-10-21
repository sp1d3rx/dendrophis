"""Abstract base class for all tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Abstract base class for all Dendrophis tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """The unique name of the tool."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """A detailed description of what the tool does."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema defining the tool's parameters."""
        ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Execute the tool with the given arguments."""
        ...

    @property
    def self_confirming(self) -> bool:
        """True if this tool handles its own human confirmation internally."""
        return False

    def execution_timeout(self, arguments: dict[str, Any]) -> float | None:
        """Per-call execution timeout in seconds, or None for the executor default.

        The tool's own timeout is the only one that can actually kill a stuck
        subprocess, so the executor adds a margin on top of this value.
        """
        return None

    @property
    def permission_controlled(self) -> bool:
        """False for internal tools that should not appear in permission settings."""
        return True

    @property
    def schema(self) -> dict[str, Any]:
        """Return the tool's schema in OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
