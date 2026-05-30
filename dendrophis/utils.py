"""Shared utilities for Dendrophis."""

from __future__ import annotations

import hashlib
import re

# def sanitize_tool_id(tool_id: str | None) -> str:
#     """REMOVED - Tool call IDs are no longer hashed.
#     
#     This function previously used SHA256 hashing to create 9-character IDs
#     for provider compatibility, but this caused tool name corruption bugs.
#     
#     Tool call IDs now use original provider IDs without modification.
#     """
#     raise NotImplementedError("Tool ID hashing has been removed")


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


# Backwards compatibility aliases (private names for internal use)
# _sanitize_tool_id = sanitize_tool_id  # REMOVED - no tool ID hashing
_sanitize_tool_name = sanitize_tool_name
_hash_content = hash_content
