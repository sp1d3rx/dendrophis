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
