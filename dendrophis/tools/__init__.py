"""Tools subsystem — registry, executor, and built-ins."""

from __future__ import annotations

from dendrophis.tools.executor import ToolExecutor, ToolResult
from dendrophis.tools.factory import create_builtin_registry
from dendrophis.tools.mcp import MCPManager, MCPTool
from dendrophis.tools.registry import ToolRegistry

__all__ = [
    "MCPManager",
    "MCPTool",
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
    "create_builtin_registry",
]
