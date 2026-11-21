"""Built-in filesystem tools package."""

from __future__ import annotations

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.append import AppendTool
from dendrophis.tools.builtins.filesystem.bash import BashTool
from dendrophis.tools.builtins.filesystem.edit import EditTool
from dendrophis.tools.builtins.filesystem.edit_function import EditFunctionTool
from dendrophis.tools.builtins.filesystem.glob import GlobTool
from dendrophis.tools.builtins.filesystem.list_dir import ListDirTool
from dendrophis.tools.builtins.filesystem.patch import PatchTool
from dendrophis.tools.builtins.filesystem.read import ReadTool
from dendrophis.tools.builtins.filesystem.ripgrep import RipgrepTool
from dendrophis.tools.builtins.filesystem.write import WriteTool

__all__ = [
    "AppendTool",
    "BashTool",
    "EditFunctionTool",
    "EditTool",
    "GlobTool",
    "ListDirTool",
    "PatchTool",
    "ReadTool",
    "RipgrepTool",
    "WriteTool",
    "get_agent_tools",
    "get_filesystem_tools",
]


def get_filesystem_tools() -> list[BaseTool]:
    """Factory to create all filesystem tool instances."""
    return [
        GlobTool(),
        ReadTool(),
        RipgrepTool(),
        EditTool(),
        BashTool(),
        WriteTool(),
        AppendTool(),
        PatchTool(),
    ]


def get_agent_tools() -> list[BaseTool]:
    """Factory to create the subagent filesystem toolset."""
    return [
        ReadTool(),
        WriteTool(),
        EditTool(),
        PatchTool(),
        AppendTool(),
        EditFunctionTool(),
        ListDirTool(),
    ]
