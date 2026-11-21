"""Edit function tool — high-level surgical editing for agents."""

from __future__ import annotations

import ast
import asyncio
import difflib
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import (
    backup_existing_file,
    is_blocked_path,
    remove_backup_file,
    restore_backup_file,
    run_auto_lint,
)
from dendrophis.tools.names import ToolName


class EditFunctionTool(BaseTool):
    """Replace a function's implementation directly with provided source code.

    This is the high-level tool agents should use for surgical Python edits.
    It finds the function by name using AST, preserves surrounding context and
    indentation, and swaps in the new implementation in a single call.
    """

    @property
    def name(self) -> str:
        return ToolName.EDIT_FUNCTION

    @property
    def description(self) -> str:
        return (
            "Replace a function's implementation in a Python file with new source code. "
            "Provides surgical, AST-based editing: finds the function by name (including decorators), "
            "replaces its body while preserving indentation and surrounding code."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "REQUIRED. Relative path to the Python file to edit (relative to CWD/project root)",
                },
                "function_name": {
                    "type": "string",
                    "description": "REQUIRED. Name of the function to replace",
                },
                "new_source": {
                    "type": "string",
                    "description": (
                        "REQUIRED. The complete new function source code, including any decorators, "
                        "the def line, and proper indentation. Should be a standalone, valid "
                        "function definition."
                    ),
                },
            },
            "required": ["file_path", "function_name", "new_source"],
        }

    async def execute(self, file_path: str, function_name: str, new_source: str) -> dict[str, Any]:
        backup_path: Path | None = None
        path = Path(file_path)
        try:
            error_message = is_blocked_path(file_path)
            if error_message:
                return {"error": error_message}

            if not path.exists():
                return {"error": f"File not found: {file_path}"}
            if not path.is_file():
                return {"error": f"Not a file: {file_path}"}

            try:
                ast.parse(new_source, filename="<new_source>")
            except SyntaxError as exc:
                return {
                    "error": f"New source has syntax errors: {exc}",
                    "hint": "Ensure the function definition is complete and properly indented",
                }

            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            lines = content.splitlines(keepends=True)
            plain_lines = content.splitlines()

            try:
                tree = ast.parse(content, filename=file_path)
            except SyntaxError as exc:
                return {
                    "error": f"Cannot parse target file: {exc}",
                    "hint": "The file may have syntax errors. Fix them first using edit or write.",
                }

            target_indent = None
            start_line = None
            end_line = None

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                    start_line = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
                    end_line = node.end_lineno if node.end_lineno else node.lineno
                    def_line = plain_lines[node.lineno - 1]
                    target_indent = len(def_line) - len(def_line.lstrip())
                    break

            if start_line is None or end_line is None or target_indent is None:
                return {
                    "error": f"Function '{function_name}' not found in {file_path}",
                    "hint": "Use analyze_functions to see available functions in the file",
                }

            new_lines = self._reindent(new_source, target_indent)
            new_content_lines = lines[: start_line - 1] + new_lines + lines[end_line:]
            new_content = "".join(new_content_lines)

            diff_lines = list(
                difflib.unified_diff(
                    lines,
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{file_path}",
                    tofile=f"b/{file_path}",
                )
            )
            added = sum(1 for diff_line in diff_lines if diff_line.startswith("+") and not diff_line.startswith("+++"))
            removed = sum(
                1 for diff_line in diff_lines if diff_line.startswith("-") and not diff_line.startswith("---")
            )

            backup_path = await asyncio.to_thread(backup_existing_file, path)
            try:
                await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")
            except Exception:
                await asyncio.to_thread(restore_backup_file, backup_path, path)
                raise

            lint_errors = await asyncio.to_thread(run_auto_lint, file_path)

            result: dict[str, Any] = {
                "success": True,
                "file": str(path),
                "function": function_name,
                "replaced_lines": f"{start_line}-{end_line}",
                "changes": f"+{added}/-{removed}",
                "diff": "".join(diff_lines),
            }
            if lint_errors:
                result["lint_errors"] = lint_errors
                result["hint"] = "Code formatted/auto-fixed. Please fix remaining lint/syntax errors."
            return result
        except Exception as exception_error:
            return {"error": str(exception_error)}
        finally:
            await asyncio.to_thread(remove_backup_file, backup_path)

    @staticmethod
    def _reindent(source: str, target_indent: int) -> list[str]:
        """Re-indent source code to match the target indentation level."""
        new_lines = source.splitlines()

        base_indent = None
        for line in new_lines:
            stripped = line.lstrip()
            if stripped:
                base_indent = len(line) - len(stripped)
                break

        if base_indent is None:
            base_indent = 0

        offset = target_indent - base_indent
        result = []

        for line in new_lines:
            stripped = line.lstrip()
            if stripped:
                new_indent = max(0, len(line) - len(stripped) + offset)
                result.append(" " * new_indent + stripped + "\n")
            else:
                result.append(line + "\n" if not line.endswith("\n") else line)

        return result
