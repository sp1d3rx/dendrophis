"""Token counting — tiktoken with char-based fallback."""

from __future__ import annotations

import re
import sys
from typing import Any

_enc = None

# Built-in 're' is JIT-friendly on PyPy. Matches words or punctuation clusters.
_heuristic_re = re.compile(r"\w+|[^\w\s]+")


def _get_enc():
    """Return the cached tiktoken encoding, initialising it on first call."""
    global _enc
    if _enc is None:
        try:
            import tiktoken

            _enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            pass
    return _enc


def _char_estimate(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    return max(1, len(text) // 4)


def _heuristic_estimate(text: str) -> int:
    """Better pure-python estimate without C extensions, JIT-friendly on PyPy."""
    # Count chunks and apply a 1.25x multiplier to approximate BPE tokens
    matches = len(_heuristic_re.findall(text))
    return max(1, int(matches * 1.25))


def count_tokens(text: str) -> int:
    """Estimate token count for a string."""
    # Bypass tiktoken entirely on PyPy to avoid C-extension overhead
    if sys.implementation.name == "pypy":
        return _heuristic_estimate(text)

    enc = _get_enc()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return _heuristic_estimate(text)


def count_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens across a list of OpenAI-format messages."""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += count_tokens(block.get("text", ""))
        else:
            total += count_tokens(str(content))
        total += 4
    return total
