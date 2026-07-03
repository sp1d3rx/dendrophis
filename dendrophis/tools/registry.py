"""Tool registry — holds and manages available tool instances."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dendrophis.tools.names import ToolName

if TYPE_CHECKING:
    from dendrophis.tools.base import BaseTool


class ToolRegistry:
    """Holds all registered tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._disabled_tools: dict[str, BaseTool] = {}

    def add(self, tool: BaseTool) -> None:
        """Add a tool instance to the registry."""
        self._tools[tool.name] = tool
        self._disabled_tools.pop(tool.name, None)

    def remove(self, name: str) -> None:
        """Remove a tool from the registry by name."""
        tool = self._tools.pop(name, None)
        if tool:
            self._disabled_tools[name] = tool

    def is_disabled(self, name: str) -> bool:
        """Return True if the tool is currently disabled."""
        return name in self._disabled_tools

    def get(self, name: str) -> BaseTool | None:
        """Return the named tool, or None if not registered."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def all(self) -> list[BaseTool]:
        """Return all registered tool instances, ordered by preference."""
        # Preferred order: investigation tools, then editing, then execution
        preferred_order = [
            ToolName.GLOB,
            ToolName.RIPGREP,
            ToolName.READ,
            ToolName.READ_FILE,
            ToolName.LIST_DIR,
            ToolName.EDIT,
            ToolName.EDIT_FUNCTION,
            ToolName.WRITE,
            ToolName.WRITE_FILE,
            ToolName.BASH,
        ]

        ordered_tools: list[BaseTool] = []
        remaining = list(self._tools.values())

        for name in preferred_order:
            if name in self._tools:
                ordered_tools.append(self._tools[name])
                for index, tool in enumerate(remaining):
                    if tool.name == name:
                        remaining.pop(index)
                        break

        # Add any remaining tools not in preferred order
        ordered_tools.extend(remaining)
        return ordered_tools

    def all_schema(self) -> list[dict[str, Any]]:
        """Return list of all tool schemas for OpenAI, ordered by preference."""
        # Preferred order: investigation tools, then editing, then execution
        preferred_order = [
            ToolName.GLOB,
            ToolName.RIPGREP,
            ToolName.READ,
            ToolName.READ_FILE,
            ToolName.LIST_DIR,
            ToolName.EDIT,
            ToolName.EDIT_FUNCTION,
            ToolName.WRITE,
            ToolName.WRITE_FILE,
            ToolName.BASH,
        ]

        ordered_tools: list[dict[str, Any]] = []
        remaining = list(self._tools.values())

        for name in preferred_order:
            if name in self._tools:
                ordered_tools.append(self._tools[name].schema)
                for index, tool in enumerate(remaining):
                    if tool.name == name:
                        remaining.pop(index)
                        break

        # Add any remaining tools not in preferred order
        ordered_tools.extend(tool.schema for tool in remaining)

        return ordered_tools
