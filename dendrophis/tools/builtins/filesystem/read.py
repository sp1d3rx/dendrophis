"""Read tool implementation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path
from dendrophis.tools.names import ToolName


class ReadTool(BaseTool):
    """Read a file or directory."""

    @property
    def name(self) -> str:
        return ToolName.READ

    @property
    def description(self) -> str:
        return "Read a file or directory. For files, returns content. For directories, returns entries."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "REQUIRED. Relative path to the file/directory to read (relative to CWD/project root)"
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed). Default: 1",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read. Default: 2000",
                },
            },
            "required": ["file_path"],
        }

    async def execute(self, file_path: str, offset: int = 1, limit: int = 2000) -> dict[str, Any]:
        try:
            error_message = is_blocked_path(file_path)
            if error_message:
                return {"error": error_message}

            path = Path(file_path)
            if not path.exists():
                return {"error": f"Path does not exist: {file_path}"}

            if path.is_dir():

                def _list_dir():
                    return [f"{entry.name}/" if entry.is_dir() else entry.name for entry in sorted(path.iterdir())]

                entries = await asyncio.to_thread(_list_dir)
                return {
                    "type": "directory",
                    "path": str(path),
                    "entries": entries,
                }

            # Read file in thread
            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            lines = content.splitlines()

            total_lines = len(lines)
            start_index = max(0, offset - 1)
            end_index = min(start_index + limit, total_lines)

            selected_lines = lines[start_index:end_index]

            return {
                "type": "file",
                "path": str(path),
                "content": "\n".join(selected_lines),
                "total_lines": total_lines,
                "showing_lines": f"{start_index + 1}-{end_index}",
            }
        except Exception as exception_error:
            return {"error": str(exception_error)}
