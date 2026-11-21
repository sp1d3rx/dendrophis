"""Tag state machine and embedded tool-call parsing for LLM text output.

This module owns the *text-side* of LLM output interpretation. It is split out
from :mod:`dendrophis.llm.stream` (which owns the *SSE-chunk* side) so each
module has a single job:

* :mod:`~dendrophis.llm.stream` turns raw SSE chunks into stream events and
  drives this module's state machine as text deltas arrive.
* This module turns the accumulated text into structured tool calls, handling
  the provider-specific ``<think>`` / ``<tool_call>`` tags, thinking blocks, and
  the XML/JSON payload formats that local/MLC servers embed in the text stream.

Two entry points are used by the rest of the codebase:

* :func:`_run_tag_state_machine` — incremental, called per text delta. It walks
  the text through the ``text`` / ``thinking`` / ``tool_calling`` modes and
  emits events as tags are recognised.
* :func:`parse_text_tool_calls` — batch, called on the finished text of a
  completion that reported ``finish_reason='tool_calls'`` but populated the text
  stream instead of the structured ``delta.tool_calls`` field.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from dendrophis.events import (
    ReasoningDeltaEvent,
    StreamEvent,
    TextDeltaEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    ToolCallStreamingEvent,
)

# Model "thinking" blocks that must never be executed as tool calls.
# An unclosed opener swallows the rest of the text on purpose: anything after a
# dangling opener is treated as thought, not as a tool call.
_THINKING_BLOCK = re.compile(
    r"<think>.*?(?:</think>|$)"
    r"|<thought>.*?(?:</thought>|$)"
    r"|<reflection>.*?(?:</reflection>|$)"
    r"|<reasoning>.*?(?:</reasoning>|$)"
    r"|<\|channel>thought\n?.*?(?:<channel\|>|$)",
    re.DOTALL,
)

# Opening fence of a ```json block (fallback tool-call format).
_JSON_FENCE = re.compile(r"```(?:json)?\s*")


# ---------------------------------------------------------------------------
# Tag tables
# ---------------------------------------------------------------------------

THINK_START_TAGS = (
    "<think>",
    "<thought>",
    "<|channel>thought\n",
    "<|channel>thought",
    "<reflection>",
    "<reasoning>",
)

THOUGHT_CLOSE_TAGS = (
    "</think>",
    "</thought>",
    "<channel|>",
    "</reflection>",
    "</reasoning>",
)

TOOL_START_TAGS = (
    "<tool_call>",
    "<tool_call|>",
    "<|tool_call>",
    "<|tool_call_start|>",
    "[TOOL_CALLS]",
)

TOOL_CLOSE_TAGS = (
    "</tool_call>",
    "</tool_call|>",
    "<tool_call|>",
    "<|tool_call_end|>",
    "[/TOOL_CALLS]",
)

_ALL_START_TAGS = THINK_START_TAGS + TOOL_START_TAGS
_MAX_START_TAG_LEN = max(len(tag_item) for tag_item in _ALL_START_TAGS)
_MAX_THOUGHT_CLOSE_LEN = max(len(tag_item) for tag_item in THOUGHT_CLOSE_TAGS)
_ALL_TOOL_TRANSITION_TAGS = TOOL_CLOSE_TAGS + TOOL_START_TAGS + THINK_START_TAGS
_MAX_TOOL_TRANSITION_LEN = max(len(tag_item) for tag_item in _ALL_TOOL_TRANSITION_TAGS)


# ---------------------------------------------------------------------------
# Payload decoding (shared by the streaming and batch paths)
# ---------------------------------------------------------------------------


def _parse_function_xml(raw_text: str) -> tuple[str, dict[str, Any]] | None:
    """Parse <function=name><parameter=p>v</parameter>...</function> into (name, args)."""
    function_match = re.match(r"<function=([^>]+)>(.*)</function>", raw_text.strip(), re.DOTALL)
    if not function_match:
        return None
    function_name = function_match.group(1).strip()
    tool_arguments: dict[str, Any] = {}
    for parameter_match in re.finditer(r"<parameter=([^>]+)>(.*?)</parameter>", function_match.group(2), re.DOTALL):
        argument_key = parameter_match.group(1).strip()
        argument_value = parameter_match.group(2).strip()
        try:
            tool_arguments[argument_key] = json.loads(argument_value)
        except json.JSONDecodeError:
            tool_arguments[argument_key] = argument_value
    return function_name, tool_arguments


def _tool_arguments_from_payload(decoded_payload: dict[str, Any]) -> str:
    """Serialize the arguments from a decoded JSON tool-call payload."""
    arguments = decoded_payload.get("arguments")
    if arguments is None:
        arguments = {key: value for key, value in decoded_payload.items() if key != "name"}
    return json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)


def _decode_tool_call_payload(payload: str) -> tuple[str | None, str] | None:
    """Decode a raw tool-call payload into ``(name, serialized_arguments)``."""
    parsed_function = _parse_function_xml(payload)
    if parsed_function:
        tool_name, tool_arguments = parsed_function
        return tool_name, json.dumps(tool_arguments)

    try:
        decoded_payload = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded_payload, dict):
        return None
    return decoded_payload.get("name"), _tool_arguments_from_payload(decoded_payload)


def _emit_tool_call_events(call_content: str) -> list[StreamEvent]:
    """Parse tool call content (XML or JSON) and return the sequence of events."""
    events: list[StreamEvent] = []
    tool_call_index = 999
    tool_call_id = f"call_{uuid.uuid4().hex[:8]}"

    decoded = _decode_tool_call_payload(call_content)
    if decoded is None:
        events.append(TextDeltaEvent(delta=f"<tool_call>{call_content}"))
        return events

    tool_name, serialized_arguments = decoded
    if tool_name is None:
        tool_name = "unknown"
    events.append(ToolCallStartEvent(index=tool_call_index, id=tool_call_id, name=tool_name))
    events.append(ToolCallDeltaEvent(index=tool_call_index, arguments_delta=serialized_arguments))
    events.append(
        ToolCallDoneEvent(
            tool_call=ToolCall(index=tool_call_index, id=tool_call_id, name=tool_name, arguments=serialized_arguments)
        )
    )
    return events


# ---------------------------------------------------------------------------
# Tag state machine (incremental, driven per text delta by stream.py)
# ---------------------------------------------------------------------------


def _scan_text_mode(
    remaining_content: str,
    parsing_state: dict[str, Any],
    events: list[StreamEvent],
) -> str:
    """Scan text mode for tag starts. Returns unconsumed content."""
    mode_indices: list[tuple[int, str, int]] = []
    for think_tag in THINK_START_TAGS:
        think_index = remaining_content.find(think_tag)
        if think_index != -1:
            mode_indices.append((think_index, "thinking", len(think_tag)))

    for tool_tag in TOOL_START_TAGS:
        tool_index = remaining_content.find(tool_tag)
        if tool_index != -1:
            mode_indices.append((tool_index, "tool_calling", len(tool_tag)))

    if mode_indices:
        # Select earliest opening tag; break ties in favor of longer match (e.g. "<|channel>thought\n")
        first_index, next_mode, tag_length = min(
            mode_indices,
            key=lambda mode_pair: (mode_pair[0], -mode_pair[2]),
        )
        if first_index > 0:
            events.append(TextDeltaEvent(delta=remaining_content[:first_index]))

        if next_mode == "tool_calling":
            events.append(ToolCallStreamingEvent())

        parsing_state["mode"] = next_mode
        return remaining_content[first_index + tag_length :]

    # No complete opening tag found; check for a partial opening tag suffix at the end of remaining_content.
    for candidate_length in range(min(len(remaining_content), _MAX_START_TAG_LEN - 1), 0, -1):
        candidate_suffix = remaining_content[-candidate_length:]
        if any(start_tag.startswith(candidate_suffix) for start_tag in _ALL_START_TAGS):
            prefix_index = len(remaining_content) - candidate_length
            if prefix_index > 0:
                events.append(TextDeltaEvent(delta=remaining_content[:prefix_index]))
            parsing_state["pending"] = candidate_suffix
            return ""

    events.append(TextDeltaEvent(delta=remaining_content))
    return ""


def _scan_thinking_mode(
    remaining_content: str,
    parsing_state: dict[str, Any],
    events: list[StreamEvent],
) -> str:
    """Scan thinking mode for tag ends. Returns unconsumed content."""
    found_end_indices: list[tuple[int, int]] = []
    for close_tag in THOUGHT_CLOSE_TAGS:
        tag_index = remaining_content.find(close_tag)
        if tag_index != -1:
            found_end_indices.append((tag_index, len(close_tag)))

    if found_end_indices:
        first_index, tag_length = min(found_end_indices, key=lambda tag_pair: tag_pair[0])
        if first_index > 0:
            events.append(ReasoningDeltaEvent(delta=remaining_content[:first_index]))
        parsing_state["mode"] = "text"
        return remaining_content[first_index + tag_length :]

    for candidate_length in range(min(len(remaining_content), _MAX_THOUGHT_CLOSE_LEN - 1), 0, -1):
        candidate_suffix = remaining_content[-candidate_length:]
        if any(close_tag.startswith(candidate_suffix) for close_tag in THOUGHT_CLOSE_TAGS):
            prefix_index = len(remaining_content) - candidate_length
            if prefix_index > 0:
                events.append(ReasoningDeltaEvent(delta=remaining_content[:prefix_index]))
            parsing_state["pending"] = candidate_suffix
            return ""

    events.append(ReasoningDeltaEvent(delta=remaining_content))
    return ""


def _scan_tool_calling_mode(
    remaining_content: str,
    parsing_state: dict[str, Any],
    events: list[StreamEvent],
) -> str:
    """Scan tool_calling mode for tag ends. Returns unconsumed content."""
    interesting_indices: list[tuple[int, str, int]] = []
    for close_tag in TOOL_CLOSE_TAGS:
        close_index = remaining_content.find(close_tag)
        if close_index != -1:
            interesting_indices.append((close_index, "end", len(close_tag)))

    for next_tool_tag in TOOL_START_TAGS:
        next_tool_index = remaining_content.find(next_tool_tag)
        if next_tool_index != -1:
            interesting_indices.append((next_tool_index, "transition", 0))

    for next_think_tag in THINK_START_TAGS:
        next_think_index = remaining_content.find(next_think_tag)
        if next_think_index != -1:
            interesting_indices.append((next_think_index, "transition", 0))

    if not interesting_indices:
        for candidate_length in range(min(len(remaining_content), _MAX_TOOL_TRANSITION_LEN - 1), 0, -1):
            candidate_suffix = remaining_content[-candidate_length:]
            if any(target_tag.startswith(candidate_suffix) for target_tag in _ALL_TOOL_TRANSITION_TAGS):
                prefix_index = len(remaining_content) - candidate_length
                parsing_state["buffer"] += remaining_content[:prefix_index]
                parsing_state["pending"] = candidate_suffix
                return ""

        parsing_state["buffer"] += remaining_content
        return ""

    first_index, transition_kind, tag_length = min(interesting_indices, key=lambda item_pair: item_pair[0])
    call_payload = parsing_state["buffer"] + remaining_content[:first_index]
    events.extend(_emit_tool_call_events(call_payload))
    parsing_state["buffer"] = ""
    parsing_state["mode"] = "text"

    if transition_kind == "end":
        return remaining_content[first_index + tag_length :]
    return remaining_content[first_index:]


def _run_tag_state_machine(
    remaining_content: str,
    parsing_state: dict[str, Any],
    events: list[StreamEvent],
) -> str:
    """Run the tag state machine until all content is consumed."""
    while remaining_content:
        if parsing_state["mode"] == "text":
            remaining_content = _scan_text_mode(remaining_content, parsing_state, events)
        elif parsing_state["mode"] == "thinking":
            remaining_content = _scan_thinking_mode(remaining_content, parsing_state, events)
        elif parsing_state["mode"] == "tool_calling":
            remaining_content = _scan_tool_calling_mode(remaining_content, parsing_state, events)
        else:
            parsing_state["mode"] = "text"
    return remaining_content


# ---------------------------------------------------------------------------
# Batch parsing (finished text -> ToolCall list)
# ---------------------------------------------------------------------------


def _extract_balanced_json(text: str, start: int) -> str | None:
    """Return the balanced JSON object starting at text[start] (which must be '{')."""
    depth = 0
    in_string = False
    escaped = False
    for char_index in range(start, len(text)):
        char = text[char_index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : char_index + 1]
    return None


def parse_text_tool_calls(raw_text: str) -> list[ToolCall]:
    """Parse tool calls embedded in LLM text output (local/MLC servers)."""
    cleaned_text = _THINKING_BLOCK.sub("", raw_text)
    results: list[ToolCall] = []

    tag_pattern = (
        r"(?:<\|?tool_call\|?>|<\|tool_call_start\|>|\[TOOL_CALLS\])"
        r"(.*?)"
        r"(?:<\|?/?tool_call\|?>|<\|tool_call_end\|>|\[/TOOL_CALLS\]|$)"
    )
    for regex_match in re.finditer(tag_pattern, cleaned_text, re.DOTALL):
        matched_content = regex_match.group(1).strip()
        raw_tool_call_id = f"call_{uuid.uuid4().hex[:8]}"

        decoded = _decode_tool_call_payload(matched_content)
        if decoded is None:
            continue
        tool_name, serialized_arguments = decoded
        if tool_name is None:
            continue
        results.append(
            ToolCall(
                index=len(results),
                id=raw_tool_call_id,
                name=tool_name,
                arguments=serialized_arguments,
            )
        )

    if not results:
        search_from = 0
        while True:
            fence_match = _JSON_FENCE.search(cleaned_text, search_from)
            if fence_match is None:
                break
            brace_start = cleaned_text.find("{", fence_match.end())
            if brace_start == -1:
                break
            candidate = _extract_balanced_json(cleaned_text, brace_start)
            if candidate is not None:
                try:
                    decoded_payload = json.loads(candidate)
                except json.JSONDecodeError:
                    decoded_payload = None
                if isinstance(decoded_payload, dict):
                    tool_name = decoded_payload.get("name")
                    arguments = decoded_payload.get("arguments")
                    if tool_name and arguments is not None:
                        raw_tool_call_id = f"call_{uuid.uuid4().hex[:8]}"
                        results.append(
                            ToolCall(
                                index=len(results),
                                id=raw_tool_call_id,
                                name=tool_name,
                                arguments=json.dumps(arguments) if isinstance(arguments, dict) else str(arguments),
                            )
                        )
                        search_from = brace_start + len(candidate)
                        continue
            search_from = fence_match.end()

    return results
