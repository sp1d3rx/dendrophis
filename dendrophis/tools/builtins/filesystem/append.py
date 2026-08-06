"""Append tool implementation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path, run_auto_lint
from dendrophis.tools.names import ToolName


class AppendTool(BaseTool):
    """Append content to the end of a file, creating it if it doesn't exist."""

    @property
    def name(self) -> str:
        return ToolName.APPEND

    @property
    def description(self) -> str:
        return (
            "Append content to the end of a file. Creates the file if it doesn't exist. "
            "Useful for adding lines to logs, .gitignore, requirements files, etc."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "REQUIRED. Relative path to the file (relative to CWD/project root)",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "REQUIRED. The content to append. Provide the RAW text exactly as it should appear in the file."
                    ),
                },
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, file_path: str, content: str) -> dict[str, Any]:
        try:
            error_message = is_blocked_path(file_path)
            if error_message:
                return {"error": error_message}

            path = Path(file_path)
            resolved = path.resolve()
            cwd = Path.cwd().resolve()
            if not resolved.is_relative_to(cwd):
                return {"error": f"File path must be within working directory: {file_path}"}

            path.parent.mkdir(parents=True, exist_ok=True)

            # Append in thread
            def _append() -> int:
                with path.open("a", encoding="utf-8") as f:
                    f.write(content)
                return len(content.encode("utf-8"))

            written_bytes = await asyncio.to_thread(_append)

            # Run auto-linting
            lint_errors = await asyncio.to_thread(run_auto_lint, file_path)

            result = {
                "success": True,
                "file": str(path),
                "appended_bytes": written_bytes,
                "appended_lines": len(content.splitlines()),
            }
            if lint_errors:
                result["lint_errors"] = lint_errors
                result["hint"] = "Code formatted/auto-fixed. Please fix remaining lint/syntax errors."
            return result
        except Exception as error:
            return {"error": str(error)}
