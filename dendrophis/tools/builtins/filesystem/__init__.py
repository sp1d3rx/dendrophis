"""Built-in filesystem tools package."""

from __future__ import annotations

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.bash import BashTool
from dendrophis.tools.builtins.filesystem.edit import EditTool
from dendrophis.tools.builtins.filesystem.edit_function import EditFunctionTool
from dendrophis.tools.builtins.filesystem.glob import GlobTool
from dendrophis.tools.builtins.filesystem.list_dir import ListDirTool
from dendrophis.tools.builtins.filesystem.read import ReadTool
from dendrophis.tools.builtins.filesystem.read_file import ReadFileTool
from dendrophis.tools.builtins.filesystem.ripgrep import RipgrepTool
from dendrophis.tools.builtins.filesystem.write import WriteTool
from dendrophis.tools.builtins.filesystem.write_file import WriteFileTool

__all__ = [
    "BashTool",
    "EditFunctionTool",
    "EditTool",
    "GlobTool",
    "ListDirTool",
    "ReadFileTool",
    "ReadTool",
    "RipgrepTool",
    "WriteFileTool",
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
    ]


def get_agent_tools() -> list[BaseTool]:
    """Factory to create the agent-friendly, high-level toolset.

    These tools use descriptive names (read_file, write_file, edit_function, list_dir)
    that map cleanly to the agent system prompt and work with the backup middleware.
    """
    return [
        ReadFileTool(),
        WriteFileTool(),
        EditFunctionTool(),
        ListDirTool(),
        EditTool(),
    ]
