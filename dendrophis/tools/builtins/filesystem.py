"""Built-in filesystem tools: glob, read, ripgrep, edit, bash, write."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool

# ---------------------------------------------------------------------------
# Shared guard
# ---------------------------------------------------------------------------


def _is_blocked_path(file_path: str) -> str | None:
    """Return error message if path is forbidden, else None."""
    if file_path.startswith("/"):
        return f"Absolute paths not allowed: {file_path}"
    resolved = Path(file_path).resolve()
    parts = resolved.parts
    # Block /dev/* regardless of how it is reached (e.g. ../dev/tty)
    for i, part in enumerate(parts):
        if part == "dev" and i > 0 and parts[i - 1] == "/":
            return f"Access to /dev blocked: {file_path}"
    return None


class GlobTool(BaseTool):
    """Find files by glob pattern."""

    @property
    def name(self) -> str:
        return "glob"

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
                err = _is_blocked_path(path)
                if err:
                    return {"error": err}

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
                results = [p for p in search_path.rglob(pattern) if should_include(p)]

                # Sort by mtime, ignoring files that might disappear during sorting
                def _safe_mtime(p):
                    try:
                        return p.stat().st_mtime
                    except (OSError, FileNotFoundError):
                        return 0

                results.sort(key=_safe_mtime, reverse=True)
                return [str(p) for p in results]

            filtered = await asyncio.to_thread(_find_matches)

            return {
                "files": filtered,
                "count": len(filtered),
            }
        except Exception as e:
            return {"error": str(e)}


class ReadTool(BaseTool):
    """Read a file or directory."""

    @property
    def name(self) -> str:
        return "read"

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
                    "description": "REQUIRED. Absolute path to the file or directory to read",
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
            err = _is_blocked_path(file_path)
            if err:
                return {"error": err}

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
            start_idx = max(0, offset - 1)
            end_idx = min(start_idx + limit, total_lines)

            selected_lines = lines[start_idx:end_idx]

            return {
                "type": "file",
                "path": str(path),
                "content": "\n".join(selected_lines),
                "total_lines": total_lines,
                "showing_lines": f"{start_idx + 1}-{end_idx}",
            }
        except Exception as e:
            return {"error": str(e)}


class RipgrepTool(BaseTool):
    """Search file contents using ripgrep."""

    @property
    def name(self) -> str:
        return "ripgrep"

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
                err = _is_blocked_path(path)
                if err:
                    return {"error": err}

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
        except Exception as e:
            return {"error": str(e)}


class EditTool(BaseTool):
    """Edit a file by replacing exact text."""

    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing exact text. To avoid 'multiple occurrences' errors, "
            "you MUST include several lines of surrounding context in 'old_string' to "
            "make the match 100% unique. IMPORTANT: Use ACTUAL raw characters "
            "(including literal newlines). Do NOT use escaped representations like "
            "'\\\\n' or '\\\\\\\\n' in your tool calls; the match is performed against "
            "the raw file bytes."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "REQUIRED. Absolute path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "REQUIRED. The exact text to replace (must match exactly)",
                },
                "new_string": {
                    "type": "string",
                    "description": (
                        "REQUIRED. The replacement text. DO NOT escape double quotes or use escaped characters;"
                        " provide the RAW text exactly as it should appear in the file."
                    ),
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(self, file_path: str, old_string: str, new_string: str) -> dict[str, Any]:
        try:
            err = _is_blocked_path(file_path)
            if err:
                return {"error": err}

            path = Path(file_path)
            if not (path.exists() and path.is_file()):
                return {"error": f"Path is not a valid file: {file_path}"}

            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")

            # Attempt to unescape doubly-escaped sequences (common LLM mistake: \\n instead of \n)
            def _try_unescape(s: str) -> str:
                try:
                    return s.encode("raw_unicode_escape").decode("unicode_escape")
                except Exception:
                    return s.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")

            if old_string not in content:
                unescaped = _try_unescape(old_string)
                if unescaped != old_string and unescaped in content:
                    old_string = unescaped
                    new_string = _try_unescape(new_string)
                else:
                    return {
                        "error": "old_string not found in file",
                        "hint": "Text must match exactly, using raw characters not escape sequences",
                    }

            count = content.count(old_string)
            if count > 1:
                return {"error": f"Found {count} occurrences", "hint": "Provide more context"}

            new_content = content.replace(old_string, new_string, 1)
            await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")

            return {
                "success": True,
                "file": str(path),
                "replaced": old_string[:100] + "..." if len(old_string) > 100 else old_string,
            }
        except Exception as e:
            return {"error": str(e)}


class BashTool(BaseTool):
    """Execute a bash command."""

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return (
            "Execute a non-interactive bash command. DO NOT run commands that "
            "require user input (like 'vim' or 'top') as they will hang. "
            "Use with caution."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "REQUIRED. The bash command to execute",
                },
                "description": {
                    "type": "string",
                    "description": "REQUIRED. Description of what the command does",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in milliseconds (default 120000)",
                },
                "full_output": {
                    "type": "boolean",
                    "description": (
                        "Optional. True to retrieve full stdout/stderr without truncation (defaults to false)."
                    ),
                },
            },
            "required": ["command", "description"],
        }

    async def execute(
        self, command: str, description: str, timeout: int = 120000, full_output: bool = False
    ) -> dict[str, Any]:
        try:
            from dendrophis.tools.bash_sandbox import BashSandbox

            sim = BashSandbox().simulate(command)
            if sim.dangerous:
                return {"error": f"Dangerous command blocked: {sim.reason}"}

            # Reject commands that touch /dev/*
            if "/dev/" in command or command.startswith("/dev"):
                return {"error": f"Access to /dev blocked in command: {command}"}

            timeout_sec = timeout / 1000
            result = await asyncio.to_thread(
                subprocess.run, command, shell=True, capture_output=True, text=True, timeout=timeout_sec
            )

            stdout = result.stdout if result.stdout else ""
            stderr = result.stderr if result.stderr else ""

            if not full_output:
                if len(stdout) > 2000:
                    stdout = stdout[:2000] + "\n... [Output truncated. Set 'full_output': true to get complete output]"
                if len(stderr) > 2000:
                    stderr = stderr[:2000] + "\n... [Output truncated. Set 'full_output': true to get complete output]"

            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "categories": [e.category.value for e in sim.effects if hasattr(e, "category")],
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Command timed out after {timeout}ms"}
        except Exception as e:
            return {"error": str(e)}


class WriteTool(BaseTool):
    """Create a completely new file."""

    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "Create a completely new file. Fails if file already exists. Provide the FULL file content."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "REQUIRED. Absolute path to the file to write",
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
            err = _is_blocked_path(file_path)
            if err:
                return {"error": err}

            path = Path(file_path)
            try:
                resolved = path.resolve()
                cwd = Path.cwd().resolve()
                if not str(resolved).startswith(str(cwd)):
                    return {"error": f"File path must be within working directory: {file_path}"}
            except Exception:
                pass

            if path.exists():
                return {"error": f"File already exists: {file_path}", "hint": "Use 'edit' instead"}

            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, content, encoding="utf-8")

            return {
                "success": True,
                "file": str(path),
                "written_bytes": len(content.encode("utf-8")),
            }
        except Exception as e:
            return {"error": str(e)}


def get_filesystem_tools() -> list[BaseTool]:
    """Factory to create all filesystem tool instances."""
    return [
        GlobTool(),
        ReadTool(),
        RipgrepTool(),
        EditTool(),
        BashTool(),
        WriteTool(),
    ]
