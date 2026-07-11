"""Built-in tools package."""

from __future__ import annotations

# Import get_filesystem_tools and get_agent_tools from filesystem safely
try:
    from dendrophis.tools.builtins.filesystem import (
        BashTool,
        EditTool,
        GlobTool,
        ReadTool,
        RipgrepTool,
        WriteTool,
        PatchTool,
        get_agent_tools,
        get_filesystem_tools,
    )
except ImportError:
    BashTool = None
    EditTool = None
    GlobTool = None
    ReadTool = None
    RipgrepTool = None
    WriteTool = None
    PatchTool = None
    get_agent_tools = None
    get_filesystem_tools = None

try:
    from dendrophis.tools.builtins.function_analyzer import analyze_functions
except ImportError:
    analyze_functions = None

try:
    from dendrophis.tools.builtins.function_tools import get_function, replace_function
except ImportError:
    get_function = None
    replace_function = None

try:
    from dendrophis.tools.builtins.interaction import AskMultipleChoiceTool
except ImportError:
    AskMultipleChoiceTool = None

try:
    from dendrophis.tools.builtins.memory import (
        DeleteMemoryTool,
        RecallMemoryTool,
        SaveMemoryTool,
        SearchMemoryTool,
    )
except ImportError:
    DeleteMemoryTool = None
    RecallMemoryTool = None
    SaveMemoryTool = None
    SearchMemoryTool = None

try:
    from dendrophis.tools.builtins.python_exec import execute_code
except ImportError:
    execute_code = None

try:
    from dendrophis.tools.builtins.subagents import InvokeSubagentTool
except ImportError:
    InvokeSubagentTool = None

__all__ = [
    "AskMultipleChoiceTool",
    "BashTool",
    "DeleteMemoryTool",
    "EditTool",
    "GlobTool",
    "InvokeSubagentTool",
    "ReadTool",
    "RecallMemoryTool",
    "RipgrepTool",
    "SaveMemoryTool",
    "SearchMemoryTool",
    "WriteTool",
    "PatchTool",
    "analyze_functions",
    "execute_code",
    "get_agent_tools",
    "get_filesystem_tools",
    "get_function",
    "replace_function",
]
