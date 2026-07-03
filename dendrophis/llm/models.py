"""LLM message helpers and model-capability heuristics."""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Model capability constants — single source of truth
# ---------------------------------------------------------------------------

_TOOL_FAMILIES: tuple[str, ...] = (
    "claude",
    "command-r",
    "deepseek-chat",
    "deepseek-coder",
    "deepseek-r1",
    "deepseek-v3",
    "dolphin",
    "fireworks/firefunction",
    "gemini-1.5",
    "gemini-2.0",
    "gemma",
    "gpt-3.5",
    "gpt-4",
    "kimi",
    "llama-3.1",
    "llama-3.2",
    "llama-3.3",
    "ministral",
    "mistral",
    "codestral",
    "nova",
    "olmo",
    "qwen",
)

_CACHING_SUPPORTED: tuple[str, ...] = (
    "claude-3",
    "deepseek",
    "gpt-4",
    "kimi",
    "moonshot",
    "mistral",
    "codestral",
    "gemma",
)

_REASONING_EFFORT_FAMILIES: tuple[str, ...] = (
    "deepseek-r1",
    "deepseek-r2",
    "gemini",
    "gemma",
    "kimi",
    "moonshot",
    "mistral",
    "codestral",
    "o1",
    "o3",
)


def supports_tools_by_id(model_id: str) -> bool:
    """ID-only heuristic for tool-call support (no provider metadata)."""
    low = model_id.lower()
    if ":free" in low:
        return False
    if any(family in low for family in _TOOL_FAMILIES):
        return True
    if "instruct" in low or "chat" in low or "claude" in low:
        return "llama-2" not in low and "mistral-7b" not in low
    return False


def supports_caching_by_id(model_id: str) -> bool:
    """ID-only heuristic for prompt-cache support."""
    low = model_id.lower()
    return any(p in low for p in _CACHING_SUPPORTED)


def supports_reasoning_effort_by_id(model_id: str) -> bool:
    """Return True if the model accepts the reasoning_effort parameter."""
    low = model_id.lower()
    return any(p in low for p in _REASONING_EFFORT_FAMILIES)


def supports_prompt_cache_key_by_id(model_id: str) -> bool:
    """Return True if the model accepts the prompt_cache_key parameter (Mistral/Kimi).

    Requests with the same key and model share a KV cache, even if prompts differ slightly
    in formatting or ordering. Recommended format: session-scoped like "user123-chat456".
    """
    low = model_id.lower()
    # Mistral and Kimi models support prompt_cache_key
    prompt_cache_families = ("mistral", "mixtral", "kimi", "moonshot")
    return any(p in low for p in prompt_cache_families)


def make_user_message(content: str) -> dict[str, Any]:
    """Build an OpenAI-format user message dict with timestamp."""
    from datetime import datetime

    ts = datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip()
    return {"role": "user", "content": f"[{ts}] {content}"}


def make_assistant_message(
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
) -> dict[str, Any]:
    """Build an OpenAI-format assistant message dict, optionally with tool calls or reasoning."""
    # Some providers (like MLC and Amazon Nova) reject empty or whitespace content strings
    # when tool calls are present. Using None (null in JSON) is mandatory for tool-only messages.
    final_content = content
    if tool_calls and (not content or not content.strip()):
        final_content = None
    elif not final_content:
        final_content = ""

    msg: dict[str, Any] = {"role": "assistant", "content": final_content}
    if tool_calls:
        # Log tool calls being added to assistant message
        import os

        if os.environ.get("DENDROPHIS_TOOL_LOG") == "1":
            from dendrophis.session.chat import _tool_log

            _tool_log("=== ASSISTANT MESSAGE WITH TOOL CALLS ===")
            content_preview = final_content[:50] if final_content else ""
            _tool_log(
                f"Assistant content: {content_preview}{'...' if final_content and len(final_content) > 50 else ''}"
            )
            _tool_log(f"Tool calls: {len(tool_calls)}")
            for i, tc in enumerate(tool_calls):
                func = tc.get("function", {})
                _tool_log(f"  Tool {i + 1}: {func.get('name')}")
                args = func.get("arguments", "")
                _tool_log(f"    Arguments: {args[:100]}{'...' if len(args) > 100 else ''}")

        # REJECT hashed IDs as tool names - they should never appear
        for tc in tool_calls:
            tc_name = tc.get("function", {}).get("name", "")
            # Check if name looks like hashed ID (9 hex chars)
            if len(tc_name) == 9 and all(c in "0123456789abcdef" for c in tc_name):
                raise ValueError(
                    f"Tool name cannot be hashed ID: {tc_name}. This indicates a bug in tool call processing."
                )
        msg["tool_calls"] = tool_calls
    if reasoning_content:
        msg["reasoning_content"] = reasoning_content
    return msg


def make_tool_result_message(tool_call_id: str, name: str, content: Any) -> dict[str, Any]:
    """Build an OpenAI-format tool result message dict."""
    # from dendrophis.utils import _sanitize_tool_id  # REMOVED - no tool ID hashing

    # Ensure content is a JSON string as required by most APIs
    if not isinstance(content, str):
        try:
            content = json.dumps(content)
        except (TypeError, ValueError):
            content = str(content)

    # Tool call IDs are no longer hashed - use original
    tool_call_id = tool_call_id

    # REJECT hashed IDs as tool names - they should never appear
    if len(name) == 9 and all(c in "0123456789abcdef" for c in name):
        raise ValueError(f"Tool name cannot be hashed ID: {name}. This indicates a bug in tool call processing.")

    return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}
