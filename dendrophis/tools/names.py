"""Central source of truth for tool names."""

from __future__ import annotations

from enum import StrEnum


class ToolName(StrEnum):
    """Names of all built-in and interactive tools."""

    BASH = "bash"
    EDIT = "edit"
    WRITE = "write"
    GLOB = "glob"
    RIPGREP = "ripgrep"
    READ = "read"

    # Interaction
    ASK_MULTIPLE_CHOICE = "ask_multiple_choice"

    # Memory
    SAVE_MEMORY = "save_memory"
    SEARCH_MEMORY = "search_memory"
    RECALL_MEMORY = "recall_memory"
    DELETE_MEMORY = "delete_memory"

    # Subagents
    INVOKE_SUBAGENT = "invoke_subagent"

    # Function analysis
    ANALYZE_FUNCTIONS = "analyze_functions"
    GET_FUNCTION = "get_function"
    REPLACE_FUNCTION = "replace_function"

    # Code execution
    EXECUTE_CODE = "execute_code"
