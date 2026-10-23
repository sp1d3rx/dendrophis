"""Patch tool implementation for applying multiple non-contiguous search-and-replace blocks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import (
    backup_existing_file,
    is_blocked_path,
    run_auto_lint,
    try_unescape,
)
from dendrophis.tools.names import ToolName


def extract_patch_strings(edit_entry: dict[str, str]) -> tuple[str | None, str | None]:
    """Extract search and replace strings from an edit dict, supporting common alias keys."""
    search_string = edit_entry.get("search")
    if search_string is None:
        search_string = edit_entry.get("old_string")
    if search_string is None:
        search_string = edit_entry.get("old")

    replace_string = edit_entry.get("replace")
    if replace_string is None:
        replace_string = edit_entry.get("new_string")
    if replace_string is None:
        replace_string = edit_entry.get("new")

    return search_string, replace_string


class PatchTool(BaseTool):
    """Edit a file by applying multiple non-contiguous search-and-replace blocks."""

    @property
    def name(self) -> str:
        return ToolName.PATCH

    @property
    def description(self) -> str:
        return (
            "Apply multiple non-contiguous search-and-replace edits to a file in a single tool call. "
            "To avoid 'multiple occurrences' errors, you MUST include several lines of surrounding context "
            "in 'search' to make the match 100% unique. IMPORTANT: All edits are applied sequentially to "
            "the file. If any search block is not found or is ambiguous, the entire patch will be aborted."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "REQUIRED. Relative path to the file to edit (relative to project root)",
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "search": {
                                "type": "string",
                                "description": "REQUIRED. The exact block of text to search for (must match exactly)",
                            },
                            "replace": {
                                "type": "string",
                                "description": "REQUIRED. The replacement block of text.",
                            },
                        },
                        "required": ["search", "replace"],
                    },
                    "description": "REQUIRED. List of search-and-replace blocks to apply to the file.",
                },
            },
            "required": ["file_path", "edits"],
        }

    async def execute(self, file_path: str, edits: list[dict[str, str]]) -> dict[str, Any]:
        try:
            error_message = is_blocked_path(file_path)
            if error_message:
                return {"error": error_message}

            path = Path(file_path)
            if not (path.exists() and path.is_file()):
                return {"error": f"Path is not a valid file: {file_path}"}

            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")

            new_content = content
            applied_edits = []

            for edit_index, edit_entry in enumerate(edits):
                if not isinstance(edit_entry, dict):
                    return {
                        "error": (
                            f"Invalid edit at edit_index {edit_index}: expected dict with 'search' and 'replace' keys"
                        )
                    }
                search_string, replace_string = extract_patch_strings(edit_entry)
                if not search_string:
                    return {
                        "error": f"Missing or empty 'search' string at edit_index {edit_index}",
                        "hint": "Each item in 'edits' must be a dict with non-empty 'search' and 'replace' strings",
                    }
                if replace_string is None:
                    return {
                        "error": f"Missing 'replace' string at edit_index {edit_index}",
                        "hint": "Each item in 'edits' must include both 'search' and 'replace' keys",
                    }

                if search_string not in new_content:
                    unescaped_search = try_unescape(search_string)
                    if unescaped_search != search_string and unescaped_search in new_content:
                        search_string = unescaped_search
                        replace_string = try_unescape(replace_string)
                    else:
                        return {
                            "error": f"Search block at edit_index {edit_index} not found in file",
                            "hint": "Text must match exactly, using raw characters not escape sequences",
                        }

                count = new_content.count(search_string)
                if count > 1:
                    return {
                        "error": (
                            f"Ambiguous edit at edit_index {edit_index}: found {count} occurrences of the search block"
                        ),
                        "hint": "Provide more context for this search block to make it unique",
                    }

                new_content = new_content.replace(search_string, replace_string, 1)
                applied_edits.append(
                    {
                        "search": search_string[:50] + "..." if len(search_string) > 50 else search_string,
                        "replace": replace_string[:50] + "..." if len(replace_string) > 50 else replace_string,
                    }
                )

            await asyncio.to_thread(backup_existing_file, path)
            await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")

            # Run auto-linting
            lint_errors = await asyncio.to_thread(run_auto_lint, file_path)

            result = {
                "success": True,
                "file": str(path),
                "applied_edits_count": len(applied_edits),
            }
            if lint_errors:
                result["lint_errors"] = lint_errors
                result["hint"] = "Code formatted/auto-fixed. Please fix remaining lint/syntax errors."
            return result
        except Exception as exception_error:
            return {"error": str(exception_error)}
