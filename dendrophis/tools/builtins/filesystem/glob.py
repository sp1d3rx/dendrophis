"""Glob tool implementation."""

from __future__ import annotations

import asyncio
import fnmatch
import os
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path
from dendrophis.tools.names import ToolName

_EXCLUDED_DIRS = frozenset(
    {
        ".venv",
        "__pycache__",
        ".git",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)
_MAX_GLOB_RESULTS = 500


def _is_excluded_dir(dirname: str) -> bool:
    return dirname in _EXCLUDED_DIRS or dirname.startswith(".venv") or dirname.endswith(".egg-info")


def _matches_glob(relative_posix: str, filename: str, pattern: str) -> bool:
    """Match a POSIX relative path against standard glob/rglob patterns."""
    normalized = pattern.lstrip("./")
    if not normalized:
        return True
    if normalized.startswith("**/"):
        sub_pattern = normalized[3:]
        if "/" not in sub_pattern:
            return fnmatch.fnmatchcase(filename, sub_pattern)
        return fnmatch.fnmatchcase(relative_posix, sub_pattern) or fnmatch.fnmatchcase(
            relative_posix, f"*/{sub_pattern}"
        )
    if "**/" in normalized:
        prefix, suffix = normalized.split("**/", 1)
        if fnmatch.fnmatchcase(relative_posix, f"{prefix}{suffix}"):
            return True
        return fnmatch.fnmatchcase(relative_posix, f"{prefix}*/{suffix}")
    if "/" not in normalized:
        # Path.rglob("*.py") matches at any depth; preserve that behavior.
        return fnmatch.fnmatchcase(filename, normalized) or fnmatch.fnmatchcase(relative_posix, normalized)
    return fnmatch.fnmatchcase(relative_posix, normalized)


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
            if path is not None:
                error_message = is_blocked_path(path)
                if error_message:
                    return {"error": error_message}

            search_path = Path(path) if path else Path.cwd()
            if not search_path.exists():
                return {"error": f"Path does not exist: {path}"}

            def _find_matches() -> list[str]:
                matched_with_mtime: list[tuple[float, Path]] = []
                search_root = search_path.resolve()
                current_working_directory = Path.cwd().resolve()

                for root_dir, dirnames, filenames in os.walk(search_root):
                    # Prune excluded directories in-place so .venv / .git / node_modules are never traversed.
                    dirnames[:] = [dirname for dirname in dirnames if not _is_excluded_dir(dirname)]
                    root_path = Path(root_dir)
                    for filename in filenames:
                        candidate_path = root_path / filename
                        try:
                            relative_posix = candidate_path.relative_to(search_root).as_posix()
                        except ValueError:
                            continue
                        if not _matches_glob(relative_posix, filename, pattern):
                            continue
                        try:
                            mtime = candidate_path.stat().st_mtime
                        except OSError:
                            continue
                        matched_with_mtime.append((mtime, candidate_path))

                matched_with_mtime.sort(key=lambda pair: pair[0], reverse=True)
                relative_paths: list[str] = []
                for _mtime, path_entry in matched_with_mtime[:_MAX_GLOB_RESULTS]:
                    if path_entry.is_relative_to(current_working_directory):
                        relative_paths.append(str(path_entry.relative_to(current_working_directory)))
                    else:
                        relative_paths.append(str(path_entry))
                return relative_paths

            filtered = await asyncio.to_thread(_find_matches)

            return {
                "files": filtered,
                "count": len(filtered),
            }
        except Exception as exception_error:
            return {"error": str(exception_error)}
