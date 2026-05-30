"""Tools subsystem — registry, executor, and built-ins."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.tools.builtins.filesystem import get_filesystem_tools
from dendrophis.tools.builtins.interaction import AskMultipleChoiceTool
from dendrophis.tools.executor import ToolExecutor, ToolResult
from dendrophis.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus
    from dendrophis.memory import MemoryStore


__all__ = [
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
    "create_builtin_registry",
]


def create_builtin_registry(
    event_bus: IEventBus,
    interactive: bool = False,
    memory_store: MemoryStore | None = None,
) -> ToolRegistry:
    """Create a tool registry populated with all built-in tools.

    Args:
        event_bus: The event bus for tools that need it
        interactive: If True, use interactive versions of tools
        memory_store: If provided, add memory tools (save_memory, search_memory, recall_memory, delete_memory)
    """
    registry = ToolRegistry()

    # Add filesystem tools
    fs_tools = get_filesystem_tools()
    if interactive:
        from dendrophis.tools.interactive.edit import InteractiveEditTool
        from dendrophis.tools.interactive.write import InteractiveWriteTool

        fs_tools = [t for t in fs_tools if t.name not in ("edit", "write")]
        edit_tool = InteractiveEditTool(event_bus)
        edit_tool.silent = True  # Auto-approve edits without confirmation
        fs_tools.append(edit_tool)
        write_tool = InteractiveWriteTool(event_bus)
        write_tool.silent = True  # Auto-approve writes without confirmation
        fs_tools.append(write_tool)

    for tool in fs_tools:
        registry.add(tool)

    # Add interaction tools (requires event bus)
    registry.add(AskMultipleChoiceTool(event_bus))

    # Add subagent tool
    from dendrophis.tools.builtins.subagents import InvokeSubagentTool

    registry.add(InvokeSubagentTool())

    # Add memory tools if memory_store is provided
    if memory_store is not None:
        from dendrophis.tools.builtins.memory import (
            DeleteMemoryTool,
            RecallMemoryTool,
            SaveMemoryTool,
            SearchMemoryTool,
        )

        registry.add(SaveMemoryTool(memory_store))
        registry.add(SearchMemoryTool(memory_store))
        registry.add(RecallMemoryTool(memory_store))
        registry.add(DeleteMemoryTool(memory_store))

    # Add function analysis tools for surgical editing
    from dendrophis.tools.builtins.function_analyzer import FunctionAnalyzerTool
    from dendrophis.tools.builtins.function_tools import GetFunctionTool, ReplaceFunctionTool

    registry.add(FunctionAnalyzerTool())
    registry.add(GetFunctionTool())
    registry.add(ReplaceFunctionTool())

    return registry
