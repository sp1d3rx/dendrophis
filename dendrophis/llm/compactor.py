"""Context compaction — summarizes old messages when context fills up."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from dendrophis.events import (
    CompactionCompletedEvent,
    CompactionFailedEvent,
    CompactionStartedEvent,
    ErrorEvent,
    TextDeltaEvent,
    get_event_bus,
)

if TYPE_CHECKING:
    from dendrophis.context.manager import ContextManager
    from dendrophis.llm.client import LLMClient


SUMMARY_PROMPT = (
    "Summarize the following conversation history into a compact working state for long-horizon "
    "iterative problem solving (propose -> measure -> reflect -> revise).\n"
    "Preserve all high-signal state needed to continue productive iteration without regression:\n"
    "1. Objective & Constraints: task goals, acceptance/verification criteria, and active constraints.\n"
    "2. Working State & Edits: inspected/modified file paths, architectural decisions, and key code invariants.\n"
    "3. Verifiable Feedback & Metrics: exact test pass/fail counts, validation/benchmark scores, timings, "
    "return codes, and error diagnostics—explicitly recording the baseline and best result achieved so far "
    "(and which state produced it).\n"
    "4. Attempted Hypotheses & Dead-Ends: approaches that failed, timed out, or regressed metrics, along with "
    "the concrete reason each failed so it is not repeated.\n"
    "5. Open Issues & Next Plan: unresolved constraints, unverified claims, and concrete next refinement steps.\n"
    "Omit: raw unchanged file dumps, repetitive boilerplate logs, redundant confirmations, "
    "and any meta-commentary about producing the summary.\n"
    "Do not start with phrases like 'Here is a summary', 'Let me extract', or 'The conversation involved'.\n"
    "Output only the compact working-state summary. No preamble or apologies.\n\n"
    "--- CONVERSATION HISTORY ---\n{history}\n--- END HISTORY ---"
)

TAIL_TURNS = 6
TOOL_HEAD_CHARS = 400
TOOL_TAIL_CHARS = 800


def _truncate_head_tail(raw_text: str, head_chars: int = TOOL_HEAD_CHARS, tail_chars: int = TOOL_TAIL_CHARS) -> str:
    """Keep both the start and end of tool output so trailing metrics and tracebacks survive."""
    max_chars = head_chars + tail_chars
    if len(raw_text) <= max_chars:
        return raw_text
    return f"{raw_text[:head_chars]}\n... [middle truncated] ...\n{raw_text[-tail_chars:]}"


def _format_assistant_tool_calls(tool_calls: Any) -> str:
    """Render a compact summary of tool calls attached to an assistant message."""
    if not isinstance(tool_calls, list) or not tool_calls:
        return ""
    call_summaries: list[str] = []
    for call_item in tool_calls:
        if not isinstance(call_item, dict):
            continue
        function_info = call_item.get("function")
        if not isinstance(function_info, dict):
            continue
        function_name = function_info.get("name") or "unknown"
        raw_arguments = str(function_info.get("arguments") or "")
        if len(raw_arguments) > 200:
            raw_arguments = raw_arguments[:200] + "..."
        call_summaries.append(f"{function_name}({raw_arguments})")
    if not call_summaries:
        return ""
    return "Tool calls: " + "; ".join(call_summaries)


def _format_tool_message(message_dict: dict[str, Any], raw_content: str) -> str | None:
    """Format a tool result compactly, keeping verifiable outcomes while omitting raw file dumps."""
    tool_name = message_dict.get("name")
    if not isinstance(tool_name, str) or not tool_name:
        tool_name = "tool"

    stripped_content = raw_content.strip()
    if not stripped_content:
        return None

    try:
        parsed_payload = json.loads(stripped_content)
    except (json.JSONDecodeError, TypeError):
        return f"[TOOL:{tool_name}]\n{_truncate_head_tail(stripped_content)}"

    if not isinstance(parsed_payload, dict):
        return f"[TOOL:{tool_name}]\n{_truncate_head_tail(stripped_content)}"

    status_value = parsed_payload.get("status")
    if status_value in ("unchanged_since_last_read", "missing_tool_result"):
        return None

    if "error" in parsed_payload:
        error_text = _truncate_head_tail(str(parsed_payload["error"]))
        return f"[TOOL:{tool_name} ERROR]\n{error_text}"

    if tool_name in {"read", "read_file"} and (parsed_payload.get("type") == "file" or "content" in parsed_payload):
        read_path = parsed_payload.get("path") or parsed_payload.get("file")
        return f"[TOOL:{tool_name}] path={read_path}" if read_path else None

    if tool_name == "glob" and isinstance(parsed_payload.get("files"), list):
        file_count = parsed_payload.get("count", len(parsed_payload["files"]))
        return f"[TOOL:glob] count={file_count}"

    if tool_name == "list_dir" and "entries" in parsed_payload:
        dir_path = parsed_payload.get("path", "")
        entry_count = len(parsed_payload["entries"]) if isinstance(parsed_payload["entries"], list) else 0
        return f"[TOOL:list_dir] path={dir_path} count={entry_count}"

    if "returncode" in parsed_payload or "stdout" in parsed_payload or "stderr" in parsed_payload:
        outcome_lines: list[str] = []
        if "returncode" in parsed_payload:
            outcome_lines.append(f"returncode={parsed_payload['returncode']}")
        elif "success" in parsed_payload:
            outcome_lines.append(f"success={parsed_payload['success']}")
        stdout_text = str(parsed_payload.get("stdout") or "").strip()
        stderr_text = str(parsed_payload.get("stderr") or "").strip()
        if stdout_text:
            outcome_lines.append(f"stdout:\n{_truncate_head_tail(stdout_text)}")
        if stderr_text:
            outcome_lines.append(f"stderr:\n{_truncate_head_tail(stderr_text)}")
        return f"[TOOL:{tool_name}]\n" + "\n".join(outcome_lines)

    return f"[TOOL:{tool_name}]\n{_truncate_head_tail(stripped_content)}"


def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    """Serialise a list of OpenAI-format messages to a plain-text transcript."""
    transcript_lines: list[str] = []
    for message_item in messages:
        if not isinstance(message_item, dict):
            continue

        role_name = message_item.get("role", "unknown")
        if not isinstance(role_name, str):
            role_name = "unknown"

        if role_name == "system":
            continue

        raw_content = message_item.get("content")
        if raw_content is None:
            content_text = ""
        elif isinstance(raw_content, list):
            content_parts = []
            for block in raw_content:
                if isinstance(block, dict):
                    block_text = block.get("text", "")
                    if isinstance(block_text, str):
                        content_parts.append(block_text)
                elif isinstance(block, str):
                    content_parts.append(block)
            content_text = " ".join(content_parts)
        elif not isinstance(raw_content, str):
            content_text = str(raw_content)
        else:
            content_text = raw_content

        if role_name == "tool":
            formatted_tool = _format_tool_message(message_item, content_text)
            if formatted_tool:
                transcript_lines.append(formatted_tool)
            continue

        if len(content_text) > 10000:
            content_text = content_text[:10000] + "... [truncated]"

        if role_name == "assistant":
            calls_summary = _format_assistant_tool_calls(message_item.get("tool_calls"))
            if calls_summary:
                content_text = f"{content_text}\n{calls_summary}".strip() if content_text else calls_summary

        transcript_lines.append(f"[{role_name.upper()}]\n{content_text}")
    return "\n\n".join(transcript_lines)


def _find_safe_compaction_end(messages: list[dict[str, Any]], start: int, target_end: int) -> int:
    """Snap `target_end` back to a clean turn boundary (`user` preferred, `assistant` fallback).

    Never cuts between an `assistant` tool-call message and its `tool` result messages.
    """
    end = min(target_end, len(messages))
    # 1. Prefer snapping to a `user` message boundary
    user_boundary = end
    while user_boundary > start:
        if user_boundary < len(messages):
            msg = messages[user_boundary]
            if isinstance(msg, dict) and msg.get("role") == "user":
                return user_boundary
        user_boundary -= 1

    # 2. Single-turn agentic loop fallback: snap to an `assistant` message boundary (> start + 1)
    # so the initial `user` prompt stays in `compactable` (summarized) or at `start`, and the
    # kept tail begins cleanly on an `assistant` step rather than mid-tool-results.
    assistant_boundary = end
    while assistant_boundary > start + 1:
        if assistant_boundary < len(messages):
            msg = messages[assistant_boundary]
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return assistant_boundary
        assistant_boundary -= 1

    return start


async def compact(context: ContextManager, llm: LLMClient, enable_caching: bool = False) -> dict[str, Any]:
    """Summarize the compactable window in-place."""
    messages = context.messages
    start = next(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, dict) and message.get("role") != "system"
        ),
        0,
    )

    tail_count = TAIL_TURNS * 2
    if len(messages) - start <= tail_count:
        tail_count = max(2, len(messages) - start - 2)
    raw_end = max(start, len(messages) - tail_count)
    end = _find_safe_compaction_end(messages, start, raw_end)

    compactable = messages[start:end]
    event_bus = get_event_bus()
    original_token_count = context.token_count
    threshold = int(context._config.llm.compaction_threshold * context.context_limit)

    if not compactable:
        reason = "No messages to compact"
        if event_bus:
            event_bus.publish(CompactionFailedEvent(reason=reason))
        return {"compacted": False, "reason": reason}

    if len(compactable) > 1000:
        reason = "Too many messages to compact at once"
        if event_bus:
            event_bus.publish(CompactionFailedEvent(reason=reason))
        return {"compacted": False, "reason": reason}

    if event_bus:
        event_bus.publish(
            CompactionStartedEvent(
                original_token_count=original_token_count,
                threshold=threshold,
            )
        )

    messages_compacted = len(compactable)
    history_text = _messages_to_text(compactable)
    summary_prompt = SUMMARY_PROMPT.format(history=history_text)
    summary_messages = [{"role": "user", "content": summary_prompt}]

    summary_parts: list[str] = []
    async for event in llm.stream_chat(summary_messages, tools=None):
        if isinstance(event, TextDeltaEvent):
            summary_parts.append(event.delta)
        elif isinstance(event, ErrorEvent):
            if event_bus:
                event_bus.publish(CompactionFailedEvent(reason=f"LLM error: {event.message}"))
            raise RuntimeError(f"LLM error during compaction: {event.message}")

    summary = "".join(summary_parts).strip()
    if not summary:
        if event_bus:
            event_bus.publish(CompactionFailedEvent(reason="Compaction summary was empty"))
        raise RuntimeError(
            f"Compaction summary was empty — model returned no content "
            f"({messages_compacted} messages, {len(history_text)} chars of history)"
        )

    summary_message: dict[str, Any] = {
        "role": "assistant",
        "content": f"[Context summary — earlier conversation compacted]\n\n{summary}",
    }

    if enable_caching and context._config.caching.tier3_on_compaction:
        summary_message["cache_control"] = {"type": "ephemeral"}

    context.messages = [*messages[:start], summary_message, *messages[end:]]
    context.recalculate_tokens()
    context.clear_read_hashes()

    tokens_reclaimed = max(0, original_token_count - context.token_count)
    if event_bus:
        event_bus.publish(
            CompactionCompletedEvent(
                previous_token_count=original_token_count,
                new_token_count=context.token_count,
                tokens_reclaimed=tokens_reclaimed,
                summary=summary,
            )
        )

    return {
        "compacted": True,
        "messages_compacted": messages_compacted,
        "summary": summary,
        "kept_recent": len(messages) - end,
    }
