"""Shared filesystem helper utilities."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path


def backup_existing_file(path: Path) -> Path | None:
    """Create a `.bak` copy of an existing file before it is modified.

    Returns the backup path, or None if there was nothing to back up.
    """
    if not path.exists() or path.suffix == ".bak":
        return None
    backup_path = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup_path)
    return backup_path


def restore_backup_file(backup_path: Path | None, target_path: Path) -> None:
    """Restore a `.bak` copy back over `target_path` if an edit fails mid-flight."""
    if backup_path is None or not backup_path.exists():
        return
    with contextlib.suppress(OSError):
        shutil.copy2(backup_path, target_path)


def remove_backup_file(backup_path: Path | None) -> None:
    """Delete a temporary `.bak` file once the modification decision is complete."""
    if backup_path is None:
        return
    with contextlib.suppress(OSError):
        backup_path.unlink(missing_ok=True)


def is_blocked_path(file_path: str) -> str | None:
    """Return error message if path is forbidden or escapes CWD, else None."""
    if file_path.startswith("/"):
        return f"Absolute paths not allowed: {file_path}"
    resolved = Path(file_path).resolve()
    parts = resolved.parts
    # Block /dev/* regardless of how it is reached (e.g. ../dev/tty)
    for index, part in enumerate(parts):
        if part == "dev" and index > 0 and parts[index - 1] == "/":
            return f"Access to /dev blocked: {file_path}"
    cwd = Path.cwd().resolve()
    if not resolved.is_relative_to(cwd):
        return f"File path must be within working directory: {file_path}"
    return None


def run_auto_lint(file_path: str) -> str | None:
    """Run ruff format and ruff check on a python file to format and fix errors."""
    if not file_path.endswith(".py"):
        return None

    if not shutil.which("ruff"):
        return None

    try:
        subprocess.run(
            ["ruff", "format", file_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=15.0,
        )

        # --fix returns non-zero and prints remaining unfixed diagnostics on stdout.
        result = subprocess.run(
            ["ruff", "check", "--fix", "--unfixable", "F401", file_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=15.0,
        )
        if result.returncode != 0:
            return result.stdout.strip()
    except Exception as exception_error:
        return f"Auto-linting failed: {exception_error}"

    return None


def try_unescape(string_value: str) -> str:
    """Attempt to unescape doubly-escaped sequences (e.g. \\n to literal newline)."""
    try:
        return string_value.encode("raw_unicode_escape").decode("unicode_escape")
    except Exception:
        return string_value.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")
