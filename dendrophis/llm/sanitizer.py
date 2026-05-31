"""Message sanitization strategies for different LLM providers.

Each provider has quirks about what fields they accept in the messages array.
This module uses a strategy pattern so each provider's sanitization logic
lives in one place and is easy to test in isolation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseMessageSanitizer(ABC):
    """Strategy for sanitizing messages before sending to a provider."""

    @abstractmethod
    def sanitize(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a sanitized copy of the messages list."""


class PassThroughSanitizer(BaseMessageSanitizer):
    """No-op: return messages unchanged."""

    def sanitize(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages


def _strip_keys_from_message(msg: dict[str, Any], strip_keys: set[str]) -> dict[str, Any]:
    """Return a new dict with the specified keys removed."""
    return {key: value for key, value in msg.items() if key not in strip_keys}


class StripKeysSanitizer(BaseMessageSanitizer):
    """Strip specified keys from every message."""

    def __init__(self, strip_keys: set[str]) -> None:
        self._strip_keys = strip_keys

    def sanitize(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [_strip_keys_from_message(msg, self._strip_keys) for msg in messages]


class DeepInfraSanitizer(BaseMessageSanitizer):
    """DeepInfra rejects conversations where tool result count != tool call count.

    Validates each tool-call sequence: keep only complete pairs where the number
    of following tool results exactly matches the number of tool_calls in the
    assistant message. Drop orphaned tool results and incomplete sequences.

    Also strips provider-incompatible keys (cache_control, tool_calls) as needed.
    """

    def __init__(self, strip_keys: set[str]) -> None:
        self._strip_keys = strip_keys

    def sanitize(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        validated: list[dict[str, Any]] = []
        msg_idx = 0

        while msg_idx < len(messages):
            msg = messages[msg_idx]
            role = msg.get("role")

            if role == "tool":
                # Orphaned tool result (no preceding assistant with tool_calls) -- drop.
                msg_idx += 1
                continue

            if role == "assistant" and (msg.get("tool_calls") or "<tool_call>" in (msg.get("content") or "")):
                expected = len(msg.get("tool_calls", []))

                result_end = msg_idx + 1
                while result_end < len(messages) and messages[result_end].get("role") == "tool":
                    result_end += 1
                actual = result_end - (msg_idx + 1)
                if expected > 0 and actual == expected:
                    validated.append(_strip_keys_from_message(msg, self._strip_keys))
                    validated.extend(
                        _strip_keys_from_message(messages[tool_idx], self._strip_keys)
                        for tool_idx in range(msg_idx + 1, result_end)
                    )
                    msg_idx = result_end
                else:
                    # Incomplete sequence -- drop the whole thing.
                    msg_idx = result_end
                    continue
            else:
                validated.append(_strip_keys_from_message(msg, self._strip_keys))
                msg_idx += 1

        return validated


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _compute_strip_keys(
    is_direct_anthropic: bool,
    use_xml_tools: bool,
) -> set[str]:
    """Compute which keys must be stripped based on provider flags."""
    strip_keys: set[str] = set()
    if use_xml_tools:
        # MLC returns 422/400 if tool_calls appear in history; tool intent
        # is encoded in  tool tags in the content string instead.
        strip_keys.add("tool_calls")
    if not is_direct_anthropic:
        # cache_control is Anthropic-specific; other providers reject it.
        strip_keys.add("cache_control")
    return strip_keys


def make_sanitizer(
    *,
    is_direct_anthropic: bool = False,
    is_deepinfra: bool = False,
    use_xml_tools: bool = False,
) -> BaseMessageSanitizer:
    """Build the appropriate sanitizer strategy for the given provider context.

    Args:
        is_direct_anthropic: Direct Anthropic API (keeps cache_control).
        is_deepinfra: DeepInfra (needs tool-call pair validation).
        use_xml_tools: XML tool mode (strips tool_calls from history).

    Returns:
        A sanitizer instance ready to use.
    """
    strip_keys = _compute_strip_keys(is_direct_anthropic, use_xml_tools)

    if is_deepinfra:
        return DeepInfraSanitizer(strip_keys)

    if strip_keys:
        return StripKeysSanitizer(strip_keys)

    return PassThroughSanitizer()
