"""Factory for creating built-in tool registries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.tools.builtins.filesystem import get_filesystem_tools
from dendrophis.tools.builtins.function_analyzer import FunctionAnalyzerTool
from dendrophis.tools.builtins.function_tools import GetFunctionTool, ReplaceFunctionTool
from dendrophis.tools.builtins.interaction import AskMultipleChoiceTool
from dendrophis.tools.builtins.memory import (
    DeleteMemoryTool,
    RecallMemoryTool,
    SaveMemoryTool,
    SearchMemoryTool,
)
from dendrophis.tools.builtins.python_exec import execute_code
from dendrophis.tools.builtins.subagents import InvokeSubagentTool
from dendrophis.tools.interactive.edit import InteractiveEditTool
from dendrophis.tools.interactive.python_exec import InteractivePythonExecTool
from dendrophis.tools.interactive.write import InteractiveWriteTool
from dendrophis.tools.names import ToolName
from dendrophis.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus
    from dendrophis.memory import MemoryStore


def create_builtin_registry(
    event_bus: IEventBus,
    interactive: bool = False,
    memory_store: MemoryStore | None = None,
    no_interactive: bool = False,
) -> ToolRegistry:
    """Create a tool registry populated with all built-in tools.

    Args:
        event_bus: The event bus for tools that need it
        interactive: If True, use interactive versions of tools
        memory_store: If provided, add memory tools (save_memory, search_memory, recall_memory, delete_memory)
        no_interactive: If True, skip interactive approval flow for python exec (useful for tests)
    """
    registry = ToolRegistry()

    # Add filesystem tools
    filesystem_tools = get_filesystem_tools()
    if interactive:
        filesystem_tools = [tool for tool in filesystem_tools if tool.name not in (ToolName.EDIT, ToolName.WRITE)]
        edit_tool = InteractiveEditTool(event_bus)
        edit_tool.silent = True  # Auto-approve edits without confirmation
        filesystem_tools.append(edit_tool)

        write_tool = InteractiveWriteTool(event_bus)
        write_tool.silent = True  # Auto-approve writes without confirmation
        filesystem_tools.append(write_tool)

    for tool in filesystem_tools:
        registry.add(tool)

    # Add interaction tools (requires event bus)
    registry.add(AskMultipleChoiceTool(event_bus))

    # Add subagent tool
    registry.add(InvokeSubagentTool())

    # Add memory tools if memory_store is provided
    if memory_store is not None:
        registry.add(SaveMemoryTool(memory_store))
        registry.add(SearchMemoryTool(memory_store))
        registry.add(RecallMemoryTool(memory_store))
        registry.add(DeleteMemoryTool(memory_store))

    # Add function analysis tools for surgical editing
    registry.add(FunctionAnalyzerTool())
    registry.add(GetFunctionTool())
    registry.add(ReplaceFunctionTool())

    # Add python execution tool
    if interactive:
        python_exec = InteractivePythonExecTool(event_bus, no_interactive=no_interactive)
        python_exec.silent = False  # Always require confirmation for python exec (unless no_interactive)
        registry.add(python_exec)
    else:
        registry.add(execute_code)

    return registry
