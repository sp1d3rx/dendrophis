"""Shared utilities for Dendrophis."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from pathlib import Path


def sanitize_tool_name(name: str | None) -> str:
    """Sanitize a tool name to contain only valid characters.

    Valid characters for tool names are: a-z, A-Z, 0-9, underscore (_), dash (-)
    Any other characters are replaced with underscore.
    Names are also truncated to 64 characters (max length for most APIs).

    This prevents issues with LLMs that use special tokens like [TOOL_CALLS]
    which can leak into tool names.
    """

    if not name:
        return "unknown"
    # Replace any character that's not alphanumeric, underscore, or dash with underscore
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    # Truncate to 64 characters (API limit for most providers)
    if len(sanitized) > 64:
        sanitized = sanitized[:64]
    # Ensure we don't end with underscore
    return sanitized.rstrip("_") or "unknown"


def hash_content(content: str) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(content.encode()).hexdigest()


def ensure_secure_dir(directory_path: Path) -> None:
    """Create directory if not present and restrict permissions to owner only (0700)."""
    directory_path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(directory_path, 0o700)


def ensure_secure_file(file_path: Path) -> None:
    """Restrict file permissions to owner read/write only (0600)."""
    with contextlib.suppress(OSError):
        os.chmod(file_path, 0o600)


# Backwards compatibility aliases (private names for internal use)
_sanitize_tool_name = sanitize_tool_name
_hash_content = hash_content
