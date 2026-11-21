"""Factory for creating built-in tool registries using dynamic discovery and Dependency Injection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.tools.builtins.todo_manager import TodoManager
from dendrophis.tools.discovery import discover_tool_classes, resolve_dependencies_and_instantiate
from dendrophis.tools.interactive.base import InteractiveBaseTool
from dendrophis.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus
    from dendrophis.memory import MemoryStore

# ClarifyTool is scoped exclusively to the code-writer subagent, not the main session.
_MAIN_SESSION_EXCLUDED_CLASSES = frozenset({"InteractiveBaseTool", "BaseTool", "ClarifyTool"})


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
    todo_manager = TodoManager(event_bus)

    dependency_dictionary = {
        "event_bus": event_bus,
        "memory_store": memory_store,
        "store": memory_store,
        "todo_manager": todo_manager,
        "no_interactive": no_interactive,
    }

    # pkgutil.walk_packages recurses into dendrophis.tools.builtins.filesystem automatically.
    discovered_classes = discover_tool_classes(
        [
            "dendrophis.tools.builtins",
            "dendrophis.tools.interactive",
        ]
    )

    instantiated_tools = []
    for tool_class in discovered_classes:
        if tool_class.__name__ in _MAIN_SESSION_EXCLUDED_CLASSES:
            continue
        tool_instance = resolve_dependencies_and_instantiate(tool_class, dependency_dictionary)
        if tool_instance is not None:
            instantiated_tools.append(tool_instance)

    interactive_tools = [tool for tool in instantiated_tools if isinstance(tool, InteractiveBaseTool)]
    base_tools = [tool for tool in instantiated_tools if not isinstance(tool, InteractiveBaseTool)]

    if interactive:
        for tool in interactive_tools:
            tool.silent = tool.name in ("edit", "write", "patch")

        interactive_names = {tool.name for tool in interactive_tools}

        for tool in interactive_tools:
            registry.add(tool)

        for tool in base_tools:
            if tool.name not in interactive_names:
                registry.add(tool)
    else:
        for tool in base_tools:
            registry.add(tool)

    return registry
