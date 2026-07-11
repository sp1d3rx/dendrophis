"""Built-in filesystem tools package."""

from __future__ import annotations

from dendrophis.tools.base import BaseTool

try:
    from dendrophis.tools.builtins.filesystem.bash import BashTool
except ImportError:
    BashTool = None

try:
    from dendrophis.tools.builtins.filesystem.edit import EditTool
except ImportError:
    EditTool = None

try:
    from dendrophis.tools.builtins.filesystem.edit_function import EditFunctionTool
except ImportError:
    EditFunctionTool = None

try:
    from dendrophis.tools.builtins.filesystem.glob import GlobTool
except ImportError:
    GlobTool = None

try:
    from dendrophis.tools.builtins.filesystem.list_dir import ListDirTool
except ImportError:
    ListDirTool = None

try:
    from dendrophis.tools.builtins.filesystem.read import ReadTool
except ImportError:
    ReadTool = None

try:
    from dendrophis.tools.builtins.filesystem.read_file import ReadFileTool
except ImportError:
    ReadFileTool = None

try:
    from dendrophis.tools.builtins.filesystem.ripgrep import RipgrepTool
except ImportError:
    RipgrepTool = None

try:
    from dendrophis.tools.builtins.filesystem.write import WriteTool
except ImportError:
    WriteTool = None

try:
    from dendrophis.tools.builtins.filesystem.write_file import WriteFileTool
except ImportError:
    WriteFileTool = None

try:
    from dendrophis.tools.builtins.filesystem.patch import PatchTool
except ImportError:
    PatchTool = None

__all__ = [
    "BashTool",
    "EditFunctionTool",
    "EditTool",
    "GlobTool",
    "ListDirTool",
    "PatchTool",
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
    filesystem_tools: list[BaseTool] = []
    if GlobTool is not None:
        filesystem_tools.append(GlobTool())
    if ReadTool is not None:
        filesystem_tools.append(ReadTool())
    if RipgrepTool is not None:
        filesystem_tools.append(RipgrepTool())
    if EditTool is not None:
        filesystem_tools.append(EditTool())
    if BashTool is not None:
        filesystem_tools.append(BashTool())
    if WriteTool is not None:
        filesystem_tools.append(WriteTool())
    if PatchTool is not None:
        filesystem_tools.append(PatchTool())
    return filesystem_tools


def get_agent_tools() -> list[BaseTool]:
    """Factory to create the agent-friendly, high-level toolset."""
    agent_tools: list[BaseTool] = []
    if ReadFileTool is not None:
        agent_tools.append(ReadFileTool())
    if WriteFileTool is not None:
        agent_tools.append(WriteFileTool())
    if EditFunctionTool is not None:
        agent_tools.append(EditFunctionTool())
    if ListDirTool is not None:
        agent_tools.append(ListDirTool())
    if EditTool is not None:
        agent_tools.append(EditTool())
    if PatchTool is not None:
        agent_tools.append(PatchTool())
    return agent_tools
