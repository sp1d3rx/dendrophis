"""Glob tool implementation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path
from dendrophis.tools.names import ToolName


class GlobTool(BaseTool):
    """Find files by glob pattern."""

    @property
    def name(self) -> str:
        return ToolName.GLOB

    @property
    def description(self) -> str:
        return "Find files by glob pattern. Returns a list of matching file paths sorted by modification time."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "REQUIRED. Glob pattern to match (e.g., '**/*.py', 'src/**/*.ts')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. If not provided, uses current working directory.",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, path: str | None = None) -> dict[str, Any]:
        try:
            search_path = Path(path) if path else Path.cwd()
            if not search_path.exists():
                return {"error": f"Path does not exist: {path}"}

            # Apply shared guard to search_path if explicitly provided
            if path is not None:
                error_message = is_blocked_path(path)
                if error_message:
                    return {"error": error_message}

            EXCLUDED_DIRS = {
                ".venv",
                "__pycache__",
                ".git",
                "node_modules",
                ".pytest_cache",
                ".ruff_cache",
                "dist",
                "build",
                ".egg-info",
            }

            def should_include(path_obj: Path) -> bool:
                try:
                    if not path_obj.is_file():
                        return False
                    # Check if any parent directory should be excluded
                    for part in path_obj.parts:
                        if part in EXCLUDED_DIRS:
                            return False
                        if part.startswith(".venv"):
                            return False
                    # Check if the file itself exists and is readable
                    path_obj.stat()
                    return True
                except (PermissionError, FileNotFoundError, OSError):
                    return False

            # Run rglob in a thread as it can be slow on large trees
            def _find_matches():
                results = [path_entry for path_entry in search_path.rglob(pattern) if should_include(path_entry)]

                # Sort by mtime, ignoring files that might disappear during sorting
                def _safe_mtime(path_entry_to_check):
                    try:
                        return path_entry_to_check.stat().st_mtime
                    except (OSError, FileNotFoundError):
                        return 0

                results.sort(key=_safe_mtime, reverse=True)

                relative_paths = []
                current_working_directory = Path.cwd().resolve()
                for path_entry in results:
                    try:
                        path_entry_absolute = path_entry.resolve()
                        if path_entry_absolute.is_relative_to(current_working_directory):
                            relative_paths.append(str(path_entry_absolute.relative_to(current_working_directory)))
                        else:
                            relative_paths.append(str(path_entry))
                    except Exception:
                        relative_paths.append(str(path_entry))
                return relative_paths

            filtered = await asyncio.to_thread(_find_matches)

            return {
                "files": filtered,
                "count": len(filtered),
            }
        except Exception as exception_error:
            return {"error": str(exception_error)}
