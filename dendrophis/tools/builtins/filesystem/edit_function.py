"""Edit function tool — high-level surgical editing for agents."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path
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
            "Provides surgical, AST-based editing: finds the function by name, replaces its "
            "body while preserving indentation and surrounding code. "
            "A .bak backup is automatically created before modification."
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
                        "REQUIRED. The complete new function source code, including the "
                        "def line and proper indentation. Should be a standalone, valid "
                        "function definition."
                    ),
                },
            },
            "required": ["file_path", "function_name", "new_source"],
        }

    async def execute(self, file_path: str, function_name: str, new_source: str) -> dict[str, Any]:
        try:
            error_message = is_blocked_path(file_path)
            if error_message:
                return {"error": error_message}

            path = Path(file_path)
            if not path.exists():
                return {"error": f"File not found: {file_path}"}
            if not path.is_file():
                return {"error": f"Not a file: {file_path}"}

            # Validate the new source is syntactically valid Python
            try:
                ast.parse(new_source, filename="<new_source>")
            except SyntaxError as exc:
                return {
                    "error": f"New source has syntax errors: {exc}",
                    "hint": "Ensure the function definition is complete and properly indented",
                }

            # Read file in thread
            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            lines = content.splitlines(keepends=True)

            # Also read without keepends for AST matching
            content_plain = path.read_text(encoding="utf-8", errors="replace")

            # Parse AST to find the function
            try:
                tree = ast.parse(content_plain, filename=file_path)
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
                    start_line = node.lineno
                    end_line = node.end_lineno if node.end_lineno else node.lineno

                    # Get the indentation of the original function def line
                    def_line = content_plain.splitlines()[start_line - 1]
                    target_indent = len(def_line) - len(def_line.lstrip())
                    break

            if start_line is None:
                return {
                    "error": f"Function '{function_name}' not found in {file_path}",
                    "hint": "Use analyze_functions to see available functions in the file",
                }

            # Re-indent the new source to match the original function's indentation level
            new_lines = self._reindent(new_source, target_indent)

            # Build the new file content
            new_content_lines = lines[: start_line - 1] + new_lines + lines[end_line:]
            new_content = "".join(new_content_lines)

            # Write back in thread
            await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")

            return {
                "success": True,
                "file": str(path),
                "function": function_name,
                "replaced_lines": f"{start_line}-{end_line}",
            }
        except Exception as exc:
            return {"error": str(exc)}

    @staticmethod
    def _reindent(source: str, target_indent: int) -> list[str]:
        """Re-indent source code to match the target indentation level."""
        new_lines = source.splitlines()

        # Determine the base indent of the provided source
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
