"""Edit tool implementation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path, run_auto_lint
from dendrophis.tools.names import ToolName


class EditTool(BaseTool):
    """Edit a file by replacing exact text."""

    @property
    def name(self) -> str:
        return ToolName.EDIT

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing exact text. To avoid 'multiple occurrences' errors, "
            "you MUST include several lines of surrounding context in 'old_string' to "
            "make the match 100% unique. IMPORTANT: Use ACTUAL raw characters "
            "(including literal newlines). Do NOT use escaped representations like "
            "'\\\\n' or '\\\\\\\\n' in your tool calls; the match is performed against "
            "the raw file bytes."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": ("REQUIRED. Relative path to the file to edit (relative to CWD/project root)"),
                },
                "old_string": {
                    "type": "string",
                    "description": "REQUIRED. The exact text to replace (must match exactly)",
                },
                "new_string": {
                    "type": "string",
                    "description": (
                        "REQUIRED. The replacement text. DO NOT escape double quotes or use escaped characters;"
                        " provide the RAW text exactly as it should appear in the file."
                    ),
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(self, file_path: str, old_string: str, new_string: str) -> dict[str, Any]:
        try:
            error_message = is_blocked_path(file_path)
            if error_message:
                return {"error": error_message}

            path = Path(file_path)
            if not (path.exists() and path.is_file()):
                return {"error": f"Path is not a valid file: {file_path}"}

            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")

            # Attempt to unescape doubly-escaped sequences (common LLM mistake: \\n instead of \n)
            def _try_unescape(string_value: str) -> str:
                try:
                    return string_value.encode("raw_unicode_escape").decode("unicode_escape")
                except Exception:
                    return string_value.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")

            if old_string not in content:
                unescaped = _try_unescape(old_string)
                if unescaped != old_string and unescaped in content:
                    old_string = unescaped
                    new_string = _try_unescape(new_string)
                else:
                    return {
                        "error": "old_string not found in file",
                        "hint": "Text must match exactly, using raw characters not escape sequences",
                    }

            count = content.count(old_string)
            if count > 1:
                return {
                    "error": f"Found {count} occurrences",
                    "hint": "Provide more context",
                }

            new_content = content.replace(old_string, new_string, 1)
            await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")

            # Run auto-linting
            lint_errors = await asyncio.to_thread(run_auto_lint, file_path)

            result = {
                "success": True,
                "file": str(path),
                "replaced": (old_string[:100] + "..." if len(old_string) > 100 else old_string),
            }
            if lint_errors:
                result["lint_errors"] = lint_errors
                result["hint"] = "Code formatted/auto-fixed. Please fix remaining lint/syntax errors."
            return result
        except Exception as exception_error:
            return {"error": str(exception_error)}
