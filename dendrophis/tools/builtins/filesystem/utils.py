"""Shared filesystem helper utilities."""

from __future__ import annotations

from pathlib import Path


def is_blocked_path(file_path: str) -> str | None:
    """Return error message if path is forbidden, else None."""
    if file_path.startswith("/"):
        return f"Absolute paths not allowed: {file_path}"
    resolved = Path(file_path).resolve()
    parts = resolved.parts
    # Block /dev/* regardless of how it is reached (e.g. ../dev/tty)
    for index, part in enumerate(parts):
        if part == "dev" and index > 0 and parts[index - 1] == "/":
            return f"Access to /dev blocked: {file_path}"
    return None


def run_auto_lint(file_path: str) -> str | None:
    """Run ruff format and ruff check on a python file to format and fix errors."""
    import shutil
    import subprocess

    if not file_path.endswith(".py"):
        return None

    # Check if ruff is available
    if not shutil.which("ruff"):
        return None

    try:
        # 1. Run ruff format
        subprocess.run(["ruff", "format", file_path], capture_output=True, text=True, check=False)

        # 2. Run ruff check with --fix
        subprocess.run(["ruff", "check", "--fix", file_path], capture_output=True, text=True, check=False)

        # 3. Check for any remaining errors
        result = subprocess.run(["ruff", "check", file_path], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return result.stdout.strip()
    except Exception as exception_error:
        return f"Auto-linting failed: {exception_error}"

    return None
