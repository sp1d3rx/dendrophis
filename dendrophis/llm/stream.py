"""SSE chunk parsing for LLM streaming responses.

This module owns the *SSE-chunk* side of LLM output interpretation. It turns
raw :class:`httpx_sse.ServerSentEvent` chunks into stream events (text,
reasoning, tool-call, usage, done, error). The *text-side* work — recognising
provider ``<think>`` / ``<tool_call>`` tags, thinking blocks, and decoding the
XML/JSON tool-call payloads embedded in the text stream — lives in
:mod:`dendrophis.llm.tag_parser`, which this module drives as text deltas
arrive and re-exports for callers that parse finished text directly.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from httpx_sse import EventSource, ServerSentEvent

from dendrophis.events import (
    DoneEvent,
    ErrorEvent,
    ReasoningDeltaEvent,
    StreamEvent,
    TextDeltaEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    ToolCallStreamingEvent,
    UsageEvent,
)
from dendrophis.llm.tag_parser import (
    _emit_tool_call_events,
    _run_tag_state_machine,
    parse_text_tool_calls,
)

__all__ = [
    "iter_sse_events",
    "parse_sse_event",
    "parse_text_tool_calls",
]


def _extract_delta_text(delta: dict[str, Any]) -> str | None:
    """Return text content from a delta dict, or None if absent."""
    if not isinstance(delta, dict):
        return None

    text_content = delta.get("content")
    if isinstance(text_content, str):
        return text_content if text_content else None

    if isinstance(text_content, (int, float)):
        return str(text_content)

    if isinstance(text_content, list):
        text_parts = []
        for content_element in text_content:
            if isinstance(content_element, dict):
                text = content_element.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            elif isinstance(content_element, str):
                text_parts.append(content_element)
        return " ".join(text_parts) if text_parts else None

    return None


def _extract_delta_reasoning(delta: dict[str, Any]) -> str | None:
    """Return reasoning content from a delta dict, checking provider-specific keys."""
    if not isinstance(delta, dict):
        return None

    reasoning_keys = ("reasoning_content", "reasoning", "thought", "thought_content")
    for reasoning_key in reasoning_keys:
        reasoning_content = delta.get(reasoning_key)
        if isinstance(reasoning_content, str):
            return reasoning_content if (reasoning_content and reasoning_content != "None") else None

        if isinstance(reasoning_content, (int, float)):
            return str(reasoning_content)

        if isinstance(reasoning_content, list):
            text_parts = []
            for content_element in reasoning_content:
                if isinstance(content_element, str):
                    text_parts.append(content_element)
                elif isinstance(content_element, dict):
                    text = content_element.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
            return " ".join(text_parts) if text_parts else None
    return None


def _extract_cached_tokens(usage: dict[str, Any], tokens_details: dict[str, Any]) -> int:
    """Return discounted cache-read token count from a usage dict across provider formats."""
    cached = tokens_details.get("cached_tokens")
    if isinstance(cached, int):
        return cached
    cache_read = usage.get("cache_read_input_tokens")
    if isinstance(cache_read, int):
        return cache_read
    return 0


def _extract_tool_call_chunk(
    tool_call_chunk: dict[str, Any],
) -> tuple[int, str | None, str | None, str]:
    """Extract (index, id, name, args_delta) from a raw tool-call chunk dict."""
    if not isinstance(tool_call_chunk, dict):
        return 0, None, None, ""

    try:
        tool_call_index = int(tool_call_chunk.get("index", 0))
    except (ValueError, TypeError):
        tool_call_index = 0

    tool_call_id = tool_call_chunk.get("id")

    function_details = tool_call_chunk.get("function", {})
    if not isinstance(function_details, dict):
        function_details = {}

    tool_name = function_details.get("name")
    arguments_delta = function_details.get("arguments", "")
    if not isinstance(arguments_delta, str):
        arguments_delta = (
            json.dumps(arguments_delta) if isinstance(arguments_delta, (dict, list)) else str(arguments_delta)
        )

    # LMStudio/Qwen3 bug: sometimes puts the full JSON payload into function.name
    if tool_name and not arguments_delta and "{" in tool_name:
        json_start = tool_name.find("{")
        candidate_name = tool_name[:json_start].strip()
        candidate_args = tool_name[json_start:]
        try:
            parsed_arguments = json.loads(candidate_args)
            if isinstance(parsed_arguments, dict):
                arguments = parsed_arguments.get("arguments") or {
                    key: value for key, value in parsed_arguments.items() if key != "name"
                }
                arguments_delta = json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)
                tool_name = parsed_arguments.get("name") or candidate_name or tool_name
        except (json.JSONDecodeError, ValueError):
            pass

    return tool_call_index, tool_call_id, tool_name, arguments_delta


def _extract_stream_error(chunk: dict[str, Any]) -> list[StreamEvent] | None:
    """Return [ErrorEvent] if chunk is a provider error payload, else None."""
    if "choices" not in chunk and ("error" in chunk or "error_type" in chunk):
        raw_error = chunk.get("error")
        if isinstance(raw_error, str) and raw_error:
            error_message = raw_error
        elif isinstance(raw_error, dict):
            error_message = raw_error.get("message") or chunk.get("error_message") or str(chunk)
        else:
            error_message = chunk.get("error_message") or str(chunk)
        return [ErrorEvent(message=str(error_message))]
    return None


def _parse_responses_api_chunk(
    chunk: dict[str, Any],
    in_progress_calls: dict[int, ToolCall],
    events: list[StreamEvent],
    parsing_state: dict[str, Any],
) -> tuple[list[StreamEvent], dict[int, ToolCall], dict[str, Any]]:
    """Handle OpenRouter Responses API SSE events (type starts with 'response.')."""
    event_type = chunk.get("type", "")
    if not isinstance(event_type, str) or not event_type.startswith("response."):
        return events, in_progress_calls, parsing_state

    updated_calls = dict(in_progress_calls)

    if event_type in ("response.output_text.delta", "response.refusal.delta"):
        text_delta = chunk.get("delta", "")
        if text_delta:
            events.append(TextDeltaEvent(delta=text_delta))
    elif event_type in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
        reasoning_delta = chunk.get("delta", "")
        if reasoning_delta:
            events.append(ReasoningDeltaEvent(delta=reasoning_delta))
    elif event_type == "response.output_item.added":
        output_item = chunk.get("item", {})
        if output_item.get("type") == "function_call":
            output_index = chunk.get("output_index", 0)
            call_id = output_item.get("call_id") or output_item.get("id", f"call_{output_index}")
            tool_name = output_item.get("name", "")
            updated_calls[output_index] = ToolCall(index=output_index, id=call_id, name=tool_name, arguments="")
            events.append(ToolCallStreamingEvent())
            events.append(ToolCallStartEvent(index=output_index, id=call_id, name=tool_name))
    elif event_type == "response.function_call_arguments.delta":
        output_index = chunk.get("output_index", 0)
        text_delta = chunk.get("delta", "")
        if output_index in updated_calls:
            updated_calls[output_index].arguments += text_delta
        if text_delta:
            events.append(ToolCallDeltaEvent(index=output_index, arguments_delta=text_delta))
    elif event_type == "response.output_item.done":
        output_item = chunk.get("item", {})
        if output_item.get("type") == "function_call":
            output_index = chunk.get("output_index", 0)
            if output_index in updated_calls:
                tool_call = updated_calls.pop(output_index)
                tool_call.arguments = output_item.get("arguments", tool_call.arguments)
                events.append(ToolCallDoneEvent(tool_call=tool_call))
    elif event_type == "response.completed":
        response_details = chunk.get("response", {})
        events.extend(ToolCallDoneEvent(tool_call=tool_call) for tool_call in updated_calls.values())
        updated_calls = {}
        usage_details = response_details.get("usage", {})
        if usage_details:
            events.append(
                UsageEvent(
                    prompt_tokens=usage_details.get("input_tokens", 0),
                    completion_tokens=usage_details.get("output_tokens", 0),
                    cached_tokens=_extract_cached_tokens(
                        usage_details, usage_details.get("input_tokens_details") or {}
                    ),
                )
            )
        outputs = response_details.get("output", [])
        has_tool_calls = any(output_item.get("type") == "function_call" for output_item in outputs)
        events.append(DoneEvent(finish_reason="tool_calls" if has_tool_calls else "stop"))
    return events, updated_calls, parsing_state


_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "eos": "stop",
    "end_turn": "stop",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "length": "length",
    "max_tokens": "length",
}


def _flush_parsing_buffers(
    parsing_state: dict[str, Any],
    updated_calls: dict[int, ToolCall],
    events: list[StreamEvent],
) -> dict[int, ToolCall]:
    """Flush any pending tag fragments or in-progress tool calls at stream end."""
    if parsing_state["mode"] == "tool_calling" and parsing_state["buffer"]:
        events.extend(_emit_tool_call_events(parsing_state["buffer"]))
        parsing_state["buffer"] = ""
        parsing_state["mode"] = "text"

    if parsing_state["pending"]:
        if parsing_state["mode"] == "thinking":
            events.append(ReasoningDeltaEvent(delta=parsing_state["pending"]))
        else:
            events.append(TextDeltaEvent(delta=parsing_state["pending"]))
        parsing_state["pending"] = ""

    if updated_calls:
        events.extend(ToolCallDoneEvent(tool_call=tool_call) for tool_call in updated_calls.values())
        updated_calls = {}

    return updated_calls


def _handle_finish_reason(
    finish_reason: str | None,
    parsing_state: dict[str, Any],
    in_progress_calls: dict[int, ToolCall],
    events: list[StreamEvent],
) -> tuple[list[StreamEvent], dict[int, ToolCall], dict[str, Any]]:
    """Handle stream completion: flush buffers, emit DoneEvent."""
    if finish_reason not in _FINISH_REASON_MAP:
        return events, in_progress_calls, parsing_state

    normalized_reason = _FINISH_REASON_MAP[finish_reason]
    updated_calls = _flush_parsing_buffers(parsing_state, dict(in_progress_calls), events)
    events.append(DoneEvent(finish_reason=normalized_reason))
    return events, updated_calls, parsing_state


def _extract_usage(chunk: dict[str, Any]) -> list[StreamEvent]:
    """Return [UsageEvent] if chunk contains usage data, else []."""
    usage = chunk.get("usage")
    if usage:
        prompt_tokens_details = usage.get("prompt_tokens_details") or {}
        return [
            UsageEvent(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                cached_tokens=_extract_cached_tokens(usage, prompt_tokens_details),
            )
        ]
    return []


def parse_sse_event(
    sse_event: ServerSentEvent,
    in_progress_calls: dict[int, ToolCall],
    parsing_state: dict[str, Any] | None = None,
) -> tuple[list[StreamEvent], dict[int, ToolCall], dict[str, Any]]:
    """Parse an httpx-sse ServerSentEvent, return events and updated in-progress map."""
    if parsing_state is None:
        parsing_state = {"mode": "text", "buffer": "", "pending": ""}

    if sse_event.event == "ping":
        return [], in_progress_calls, parsing_state

    raw_payload = sse_event.data
    if raw_payload == "[DONE]":
        if in_progress_calls or parsing_state.get("buffer") or parsing_state.get("pending"):
            done_events: list[StreamEvent] = []
            had_calls = bool(in_progress_calls or parsing_state.get("mode") == "tool_calling")
            remaining_calls = _flush_parsing_buffers(parsing_state, dict(in_progress_calls), done_events)
            done_events.append(DoneEvent(finish_reason="tool_calls" if had_calls else "stop"))
            return done_events, remaining_calls, parsing_state
        return [], in_progress_calls, parsing_state

    try:
        chunk = json.loads(raw_payload)
    except json.JSONDecodeError:
        return [], in_progress_calls, parsing_state

    if not isinstance(chunk, dict):
        return [], in_progress_calls, parsing_state

    error_events = _extract_stream_error(chunk)
    if error_events is not None:
        return error_events, in_progress_calls, parsing_state

    events: list[StreamEvent] = []
    updated_calls = dict(in_progress_calls)

    events, updated_calls, parsing_state = _parse_responses_api_chunk(chunk, in_progress_calls, events, parsing_state)
    if events:
        return events, updated_calls, parsing_state

    choices = chunk.get("choices", [])
    for choice in choices:
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        reasoning_delta = _extract_delta_reasoning(delta)
        if reasoning_delta:
            events.append(ReasoningDeltaEvent(delta=reasoning_delta))

        text_delta = _extract_delta_text(delta)
        if text_delta:
            remaining_content = parsing_state.get("pending", "") + text_delta
            parsing_state["pending"] = ""
            _run_tag_state_machine(remaining_content, parsing_state, events)

        for tool_call_item in delta.get("tool_calls") or []:
            tool_call_index, tool_call_id, tool_name, arguments_delta = _extract_tool_call_chunk(tool_call_item)
            if tool_call_index not in updated_calls:
                final_tool_call_id = tool_call_id if tool_call_id else f"call_{tool_call_index}"
                updated_calls[tool_call_index] = ToolCall(
                    index=tool_call_index, id=final_tool_call_id, name=tool_name or ""
                )
                events.append(ToolCallStreamingEvent())
                events.append(ToolCallStartEvent(index=tool_call_index, id=final_tool_call_id, name=tool_name or ""))
            else:
                existing_call = updated_calls[tool_call_index]
                if tool_name and not existing_call.name:
                    existing_call.name = tool_name
                if tool_call_id and existing_call.id == f"call_{tool_call_index}":
                    existing_call.id = tool_call_id

            updated_calls[tool_call_index].arguments += arguments_delta
            if arguments_delta:
                events.append(ToolCallDeltaEvent(index=tool_call_index, arguments_delta=arguments_delta))

        events, updated_calls, parsing_state = _handle_finish_reason(
            finish_reason, parsing_state, updated_calls, events
        )

    events.extend(_extract_usage(chunk))

    return events, updated_calls, parsing_state


def iter_sse_events(event_source: Any) -> Iterator[StreamEvent]:
    """Generator wrapping an httpx response via httpx_sse EventSource for synchronous streaming."""
    in_progress_calls: dict[int, ToolCall] = {}
    parsing_state = {"mode": "text", "buffer": "", "pending": ""}
    for sse_event in EventSource(event_source).iter_sse():
        events, in_progress_calls, parsing_state = parse_sse_event(sse_event, in_progress_calls, parsing_state)
        yield from events
