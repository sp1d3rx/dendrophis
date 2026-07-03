"""List directory tool implementation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path
from dendrophis.tools.names import ToolName


class ListDirTool(BaseTool):
    """List the contents of a directory."""

    @property
    def name(self) -> str:
        return ToolName.LIST_DIR

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. Returns file and subdirectory names. "
            "Use this to explore project structure before reading files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the directory to list (relative to CWD). Default: '.'",
                },
            },
            "required": [],
        }

    async def execute(self, path: str = ".") -> dict[str, Any]:
        try:
            error_message = is_blocked_path(path)
            if error_message:
                return {"error": error_message}

            dir_path = Path(path)
            if not dir_path.exists():
                return {"error": f"Path does not exist: {path}"}
            if not dir_path.is_dir():
                return {"error": f"Not a directory: {path}"}

            def _list_entries():
                return [entry.name + ("/" if entry.is_dir() else "") for entry in sorted(dir_path.iterdir())]

            entries = await asyncio.to_thread(_list_entries)
            return {
                "path": str(dir_path),
                "entries": entries,
                "count": len(entries),
            }
        except Exception as exc:
            return {"error": str(exc)}
