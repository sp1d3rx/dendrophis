"""Append tool implementation."""

from __future__ import annotations

import asyncio
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
        backup_path: Path | None = None
        path = Path(file_path)
        try:
            error_message = is_blocked_path(file_path)
            if error_message:
                return {"error": error_message}

            path.parent.mkdir(parents=True, exist_ok=True)
            backup_path = await asyncio.to_thread(backup_existing_file, path)

            def _append() -> int:
                with path.open("a", encoding="utf-8") as file_handle:
                    file_handle.write(content)
                return len(content.encode("utf-8"))

            try:
                written_bytes = await asyncio.to_thread(_append)
            except Exception:
                await asyncio.to_thread(restore_backup_file, backup_path, path)
                raise

            lint_errors = await asyncio.to_thread(run_auto_lint, file_path)

            result: dict[str, Any] = {
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
        finally:
            await asyncio.to_thread(remove_backup_file, backup_path)
