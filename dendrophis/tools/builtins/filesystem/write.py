"""Write tool implementation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path
from dendrophis.tools.names import ToolName


class WriteTool(BaseTool):
    """Create a completely new file."""

    @property
    def name(self) -> str:
        return ToolName.WRITE

    @property
    def description(self) -> str:
        return (
            "Write content to a file, creating it if it doesn't exist, or overwriting it if it does. "
            "Provide the FULL file content."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": ("REQUIRED. Relative path to the file to write (relative to CWD/project root)"),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "REQUIRED. The content to write. DO NOT escape double quotes or use escaped characters;"
                        " provide the RAW text exactly as it should appear in the file."
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
            try:
                resolved = path.resolve()
                cwd = Path.cwd().resolve()
                if not str(resolved).startswith(str(cwd)):
                    return {"error": f"File path must be within working directory: {file_path}"}
            except Exception:
                pass

            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, content, encoding="utf-8")

            return {
                "success": True,
                "file": str(path),
                "written_bytes": len(content.encode("utf-8")),
            }
        except Exception as error:
            return {"error": str(error)}
