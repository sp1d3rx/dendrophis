"""Read file tool implementation — agent-friendly alias for read."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path
from dendrophis.tools.names import ToolName


class ReadFileTool(BaseTool):
    """Read a file's contents. Agent-friendly version of read."""

    @property
    def name(self) -> str:
        return ToolName.READ_FILE

    @property
    def description(self) -> str:
        return (
            "Read the full contents of a file. Returns the text content, total line count, "
            "and the range of lines returned. Use this to inspect code or configuration files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "REQUIRED. Relative path to the file to read (relative to CWD/project root)",
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
                return {"error": f"File not found: {file_path}"}
            if not path.is_file():
                return {"error": f"Not a file: {file_path}"}

            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            lines = content.splitlines()

            total_lines = len(lines)
            start_index = max(0, offset - 1)
            end_index = min(start_index + limit, total_lines)
            selected_lines = lines[start_index:end_index]

            return {
                "file": str(path),
                "content": "\n".join(selected_lines),
                "total_lines": total_lines,
                "showing_lines": f"{start_index + 1}-{end_index}",
            }
        except Exception as exc:
            return {"error": str(exc)}
