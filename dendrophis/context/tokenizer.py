"""Token counting — tiktoken with char-based fallback."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

logger = logging.getLogger(__name__)

_enc = None
_enc_failed = False

# Built-in 're' is JIT-friendly on PyPy. Matches words or punctuation clusters.
_heuristic_pattern = re.compile(r"\w+|[^\w\s]+")


def _get_enc():
    """Return the cached tiktoken encoding, initialising it on first call."""
    global _enc, _enc_failed
    if _enc is None and not _enc_failed:
        try:
            import tiktoken

            _enc = tiktoken.get_encoding("cl100k_base")
        except Exception as error:
            _enc_failed = True
            logger.debug("tiktoken unavailable, using heuristic token estimate: %s", error)
    return _enc


def _heuristic_estimate(text: str) -> int:
    """Pure-python estimate without C extensions, JIT-friendly on PyPy."""
    if not text:
        return 0
    match_count = len(_heuristic_pattern.findall(text))
    return max(1, int(match_count * 1.25))


def count_tokens(text: str) -> int:
    """Estimate token count for a string."""
    if not text:
        return 0
    if sys.implementation.name == "pypy":
        return _heuristic_estimate(text)

    encoding = _get_enc()
    if encoding is not None:
        try:
            return len(encoding.encode(text, disallowed_special=()))
        except Exception as error:
            logger.debug("tiktoken encode failed, falling back to heuristic: %s", error)
    return _heuristic_estimate(text)


def count_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens across a list of OpenAI-format messages."""
    total_tokens = 0
    for message in messages:
        content = message.get("content") or ""
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "image_url":
                        total_tokens += 310
                    else:
                        total_tokens += count_tokens(block.get("text", ""))
        elif content:
            total_tokens += count_tokens(str(content))

        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            total_tokens += count_tokens(reasoning)

        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for call_entry in tool_calls:
                if isinstance(call_entry, dict):
                    function_spec = call_entry.get("function")
                    if isinstance(function_spec, dict):
                        total_tokens += count_tokens(str(function_spec.get("name", "")))
                        total_tokens += count_tokens(str(function_spec.get("arguments", "")))
        total_tokens += 4
    return total_tokens
