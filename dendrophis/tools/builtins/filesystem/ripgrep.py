"""Ripgrep tool implementation."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path
from dendrophis.tools.names import ToolName


class RipgrepTool(BaseTool):
    """Search file contents using ripgrep."""

    @property
    def name(self) -> str:
        return ToolName.RIPGREP

    @property
    def description(self) -> str:
        return (
            "Search file contents using ripgrep (rg). "
            "PREFERRED over 'bash' for searching code as it is faster and handles common ignore patterns. "
            "Returns matching file paths with line numbers and context."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "REQUIRED. Regex pattern to search for (e.g., 'class .*:' or 'TODO')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Default: current working directory (.)",
                },
                "include": {
                    "type": "string",
                    "description": (
                        "File glob pattern to filter by (e.g., '*.py', '*.{ts,tsx}'). "
                        "Highly recommended for large projects."
                    ),
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, path: str | None = None, include: str | None = None) -> dict[str, Any]:
        try:
            search_path = Path(path) if path else Path.cwd()
            if not search_path.exists():
                return {"error": f"Path does not exist: {path}"}

            if path is not None:
                error_message = is_blocked_path(path)
                if error_message:
                    return {"error": error_message}

            cmd = ["rg", "--json", "--line-number", "--max-count", "5", "-C", "2"]
            if include:
                cmd.extend(["-g", include])
            cmd.extend([pattern, str(search_path)])

            result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=30.0)

            if result.returncode not in (0, 1):
                return {
                    "error": f"ripgrep failed (exit code {result.returncode})",
                    "stderr": result.stderr[:500],
                }

            matches_by_file: dict[str, list[dict]] = {}
            for line in result.stdout.splitlines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "match":
                        file_path = data["data"]["path"]["text"]
                        try:
                            file_path_absolute = Path(file_path).resolve()
                            current_working_directory = Path.cwd().resolve()
                            if file_path_absolute.is_relative_to(current_working_directory):
                                file_path = str(file_path_absolute.relative_to(current_working_directory))
                        except Exception:
                            pass
                        line_num = data["data"]["line_number"]
                        lines_data = data["data"].get("lines", {})
                        text = lines_data.get("text", "")

                        if file_path not in matches_by_file:
                            matches_by_file[file_path] = []
                        matches_by_file[file_path].append(
                            {
                                "line": line_num,
                                "content": text[:200],
                            }
                        )
                except (json.JSONDecodeError, KeyError):
                    continue

            matches = [{"file": fpath, "matches": matches_by_file[fpath]} for fpath in matches_by_file]
            return {
                "pattern": pattern,
                "matches": matches[:20],
                "total_files_matched": len(matches),
            }
        except subprocess.TimeoutExpired:
            return {"error": "ripgrep timed out after 30 seconds"}
        except FileNotFoundError:
            return {"error": "ripgrep (rg) not found. Install: https://github.com/BurntSushi/ripgrep#installation"}
        except Exception as exception_error:
            return {"error": str(exception_error)}
