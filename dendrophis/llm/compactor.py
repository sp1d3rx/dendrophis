"""Context compaction — summarizes old messages when context fills up."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dendrophis.context.manager import ContextManager
    from dendrophis.llm.client import LLMClient


SUMMARY_PROMPT = (
    "You are summarizing conversation history for context compaction. "
    "Your goal: preserve everything needed to continue the work without re-reading the full history.\n"
    "Preserve: session intent/goals, file paths and modifications, architectural decisions, "
    "key constraints, unresolved issues, next steps, important code patterns.\n"
    "Omit: verbose tool outputs, exploratory dead-ends, redundant confirmations.\n"
    "Be concise but thorough. Output only the summary, no preamble or apologies.\n\n"
    "--- CONVERSATION HISTORY ---\n{history}\n--- END HISTORY ---"
)

TAIL_TURNS = 6


def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    """Serialise a list of OpenAI-format messages to a plain-text transcript."""
    transcript_lines: list[str] = []
    for msg in messages:
        # Edge case: Handle None or non-dict messages
        if not isinstance(msg, dict):
            continue

        # Edge case: Handle missing role field
        role = msg.get("role", "unknown")
        if not isinstance(role, str):
            role = "unknown"

        # Edge case: Handle various content formats
        content = msg.get("content")
        if content is None:
            content = ""
        elif isinstance(content, list):
            # Handle list content with robust error handling
            content_parts = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if isinstance(text, str):
                        content_parts.append(text)
                elif isinstance(block, str):
                    content_parts.append(block)
            content = " ".join(content_parts)
        elif not isinstance(content, str):
            content = str(content)

        # Edge case: Truncate very long content to prevent memory issues
        if len(content) > 10000:  # 10k characters
            content = content[:10000] + "... [truncated]"

        transcript_lines.append(f"[{role.upper()}]\n{content}")
    return "\n\n".join(transcript_lines)


async def compact(context: ContextManager, llm: LLMClient, enable_caching: bool = False) -> dict[str, Any]:
    """Summarize the compactable window in-place.

    Keeps the system prompt and the last TAIL_TURNS * 2 messages verbatim.
    Replaces everything in between with a single summary message.

    Returns a dict with details about what was compacted.
    """
    messages = context.messages
    # Find first non-system message with edge case handling
    try:
        start = next(
            (
                index
                for index, message in enumerate(messages)
                if isinstance(message, dict) and message.get("role") != "system"
            ),
            0,
        )
    except StopIteration:
        start = 0

    tail_count = TAIL_TURNS * 2
    end = max(start, len(messages) - tail_count)

    # Snap end back to the nearest user-message boundary so we never leave
    # orphaned tool-result messages at the head of the kept tail.  Cutting
    # between an assistant+tool_calls message and its tool results produces an
    # invalid "tool after user" sequence that most providers reject.
    # Edge case: Handle malformed messages during boundary detection
    while end > start:
        if end >= len(messages):
            break
        message = messages[end]
        if not isinstance(message, dict):
            end -= 1
            continue
        if message.get("role") == "user":
            break
        end -= 1

    compactable = messages[start:end]
    if not compactable:
        return {"compacted": False, "reason": "No messages to compact"}

    # Edge case: Check for excessively large compaction operations
    if len(compactable) > 1000:  # Safety limit
        return {"compacted": False, "reason": "Too many messages to compact at once"}

    messages_compacted = len(compactable)
    history_text = _messages_to_text(compactable)
    summary_prompt = SUMMARY_PROMPT.format(history=history_text)
    summary_messages = [{"role": "user", "content": summary_prompt}]

    from dendrophis.events import ErrorEvent, ReasoningDeltaEvent, TextDeltaEvent

    summary_parts: list[str] = []
    async for event in llm.stream_chat(summary_messages, tools=None):
        if isinstance(event, TextDeltaEvent):
            summary_parts.append(event.delta)
        elif isinstance(event, ReasoningDeltaEvent):
            # Some models return reasoning content instead of text
            summary_parts.append(event.delta)
        elif isinstance(event, ErrorEvent):
            raise RuntimeError(f"LLM error during compaction: {event.message}")

    summary = "".join(summary_parts).strip()
    if not summary:
        raise RuntimeError(
            f"Compaction summary was empty — model returned no content "
            f"({messages_compacted} messages, {len(history_text)} chars of history)"
        )

    summary_message: dict[str, Any] = {
        "role": "user",
        "content": f"[Context summary — earlier conversation compacted]\n\n{summary}",
    }

    # Add cache control if Tier 3 caching is enabled
    if enable_caching and context._config.caching.tier3_on_compaction:
        summary_message["cache_control"] = {"type": "ephemeral"}

    context.messages = [*messages[:start], summary_message, *messages[end:]]
    context.recalculate_tokens()

    return {
        "compacted": True,
        "messages_compacted": messages_compacted,
        "summary": summary,
        "kept_recent": len(messages) - end,
    }
