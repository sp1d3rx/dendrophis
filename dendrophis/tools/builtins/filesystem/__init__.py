"""Built-in filesystem tools package."""

from __future__ import annotations

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.bash import BashTool
from dendrophis.tools.builtins.filesystem.edit import EditTool
from dendrophis.tools.builtins.filesystem.glob import GlobTool
from dendrophis.tools.builtins.filesystem.read import ReadTool
from dendrophis.tools.builtins.filesystem.ripgrep import RipgrepTool
from dendrophis.tools.builtins.filesystem.write import WriteTool

__all__ = [
    "BashTool",
    "EditTool",
    "GlobTool",
    "ReadTool",
    "RipgrepTool",
    "WriteTool",
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
