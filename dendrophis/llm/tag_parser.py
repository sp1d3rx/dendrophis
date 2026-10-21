"""Tag state machine and embedded tool-call parsing for LLM text output.

This module owns the *text-side* of LLM output interpretation. It is split out
from :mod:`dendrophis.llm.stream` (which owns the *SSE-chunk* side) so each
module has a single job:

* :mod:`~dendrophis.llm.stream` turns raw SSE chunks into stream events and
  drives this module's state machine as text deltas arrive.
* This module turns the accumulated text into structured tool calls, handling
  the provider-specific ```` `` tags, thinking blocks, and the XML/JSON payload
  formats that local/MLC servers embed in the text stream.

Two entry points are used by the rest of the codebase:

* :func:`_run_tag_state_machine` — incremental, called per text delta. It walks
  the text through the ``text`` / ``thinking`` / ``tool_calling`` modes and
  emits events as tags are recognised.
* :func:`parse_text_tool_calls` — batch, called on the finished text of a
  completion that reported ``finish_reason='tool_calls'`` but populated the text
  stream instead of the structured ``delta.tool_calls`` field.

Both paths decode tool-call payloads through the same helpers
(:func:`_decode_tool_call_payload` / :func:`_tool_arguments_from_payload`), so
the XML and JSON formats are handled in exactly one place.
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
    r"|<\|channel>thought\n.*?(?:<channel\|>|$)",
    re.DOTALL,
)

# Opening fence of a ```json block (fallback tool-call format).
_JSON_FENCE = re.compile(r"```(?:json)?\s*")


# ---------------------------------------------------------------------------
# Tag tables
# ---------------------------------------------------------------------------
#
# The state machine scans for these literal tags. A tag that appears in both a
# START and a CLOSE table is a *boundary marker*: some models emit the same
# token both before and after the payload (e.g. ``<tool_call>...json...<tool_call|>``),
# so the first occurrence opens the call and the next one closes it.
#
# Resolution order matters: in ``tool_calling`` mode the close tags are searched
# before the start tags, so a boundary marker that is both (``<tool_call|>``)
# is treated as a *close* while a call is open. That is what lets the same token
# both open and close.

# Thinking / reasoning blocks.
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

# Tool-call openers. ``<tool_call>`` is a dual start/close boundary marker (see note
# above): it opens a call in text mode and closes one in tool_calling mode.
TOOL_START_TAGS = (
    "<tool_call>",
    "<tool_call|>",
    "<|tool_call>",
    "<|tool_call_start|>",
    "[TOOL_CALLS]",
)

# Tool-call closers. ``<tool_call|>`` also appears in TOOL_START_TAGS on
# purpose — it is the boundary marker that both opens and closes a call.
TOOL_CLOSE_TAGS = (
    "</tool_call>",
    "</tool_call|>",
    "<tool_call|>",
    "<|tool_call_end|>",
    "[/TOOL_CALLS]",
)


# ---------------------------------------------------------------------------
# Payload decoding (shared by the streaming and batch paths)
# ---------------------------------------------------------------------------


def _parse_function_xml(rawText: str) -> tuple[str, dict] | None:
    """Parse <function=name><parameter=p>v</parameter>...</function> into (name, args).

    Returns None if the text doesn't match this format.
    """
    functionMatch = re.match(r"<function=([^>]+)>(.*)</function>", rawText.strip(), re.DOTALL)
    if not functionMatch:
        return None
    functionName = functionMatch.group(1).strip()
    toolArguments: dict = {}
    for parameterMatch in re.finditer(r"<parameter=([^>]+)>(.*?)</parameter>", functionMatch.group(2), re.DOTALL):
        argumentKey = parameterMatch.group(1).strip()
        argumentValue = parameterMatch.group(2).strip()
        try:
            toolArguments[argumentKey] = json.loads(argumentValue)
        except json.JSONDecodeError:
            toolArguments[argumentKey] = argumentValue
    return functionName, toolArguments


def _tool_arguments_from_payload(decodedPayload: dict) -> str:
    """Serialize the arguments from a decoded JSON tool-call payload.

    Uses the explicit ``"arguments"`` key when present; otherwise treats every
    other top-level key as an argument. Returns a JSON string for dict
    arguments and ``str()`` for anything else, so the result is always a str.
    """
    arguments = decodedPayload.get("arguments")
    if arguments is None:
        arguments = {key: value for key, value in decodedPayload.items() if key != "name"}
    return json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)


def _decode_tool_call_payload(payload: str) -> tuple[str | None, str] | None:
    """Decode a raw tool-call payload into ``(name, serialized_arguments)``.

    Tries the Sushi-Coder / Hermes XML format first, then a JSON object.
    Returns ``None`` when the payload is neither an XML function block nor a
    JSON object.

    For a JSON object the name is the value of its ``"name"`` key, or ``None``
    when that key is absent or null. Callers apply their own policy for a
    missing name: the streaming path (``_emit_tool_call_events``) defaults it to
    ``"unknown"`` so a malformed call still surfaces, while the batch path
    (``parse_text_tool_calls``) skips nameless payloads rather than executing a
    call that has no tool to dispatch to.
    """
    # Format 1: Sushi-Coder / Hermes XML.
    parsedFunction = _parse_function_xml(payload)
    if parsedFunction:
        toolName, toolArguments = parsedFunction
        return toolName, json.dumps(toolArguments)

    # Format 2: JSON object {"name": ..., "arguments": ...}.
    try:
        decodedPayload = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(decodedPayload, dict):
        return None
    return decodedPayload.get("name"), _tool_arguments_from_payload(decodedPayload)


def _emit_tool_call_events(callContent: str) -> list[StreamEvent]:
    """Parse tool call content (XML or JSON) and return the sequence of events."""
    events: list[StreamEvent] = []
    toolCallIndex = 999
    toolCallId = f"call_{uuid.uuid4().hex[:8]}"

    decoded = _decode_tool_call_payload(callContent)
    if decoded is None:
        # Neither XML nor JSON — surface the raw content as text.
        events.append(TextDeltaEvent(delta=f"<tool_call>{callContent}"))
        return events

    toolName, serializedArguments = decoded
    if toolName is None:
        toolName = "unknown"
    events.append(ToolCallStartEvent(index=toolCallIndex, id=toolCallId, name=toolName))
    events.append(ToolCallDeltaEvent(index=toolCallIndex, arguments_delta=serializedArguments))
    events.append(
        ToolCallDoneEvent(
            tool_call=ToolCall(index=toolCallIndex, id=toolCallId, name=toolName, arguments=serializedArguments)
        )
    )
    return events


# ---------------------------------------------------------------------------
# Tag state machine (incremental, driven per text delta by stream.py)
# ---------------------------------------------------------------------------


def _scan_text_mode(
    remainingContent: str,
    parsingState: dict[str, Any],
    events: list[StreamEvent],
) -> str:
    """Scan text mode for tag starts. Returns unconsumed content."""
    modeIndices: list[tuple[int, str, int]] = []
    for thinkTag in THINK_START_TAGS:
        thinkIndex = remainingContent.find(thinkTag)
        if thinkIndex != -1:
            modeIndices.append((thinkIndex, "thinking", len(thinkTag)))

    for toolTag in TOOL_START_TAGS:
        toolIndex = remainingContent.find(toolTag)
        if toolIndex != -1:
            modeIndices.append((toolIndex, "tool_calling", len(toolTag)))

    # Check for partial tags at the very end of the string
    potentialTagIndex = remainingContent.rfind("<")
    potentialBracketIndex = remainingContent.rfind("[")
    candidateIndex = max(potentialTagIndex, potentialBracketIndex)
    if candidateIndex != -1:
        foundTagIndex = min(modeIndices, key=lambda modePair: modePair[0])[0] if modeIndices else -1
        if foundTagIndex == -1 or candidateIndex > foundTagIndex:
            fragment = remainingContent[candidateIndex:]
            allStartTags = THINK_START_TAGS + TOOL_START_TAGS
            if any(startTag.startswith(fragment) for startTag in allStartTags):
                if candidateIndex > 0:
                    events.append(TextDeltaEvent(delta=remainingContent[:candidateIndex]))
                parsingState["pending"] = fragment
                return ""

    if not modeIndices:
        events.append(TextDeltaEvent(delta=remainingContent))
        return ""

    firstIndex, nextMode, tagLength = min(modeIndices, key=lambda modePair: modePair[0])
    if firstIndex > 0:
        events.append(TextDeltaEvent(delta=remainingContent[:firstIndex]))

    if nextMode == "tool_calling":
        # Fire as soon as the opening tag is confirmed, so UI can surface a
        # "tool in progress" state while the (possibly long) arguments build,
        # rather than only when the closing tag completes the call.
        events.append(ToolCallStreamingEvent())

    parsingState["mode"] = nextMode
    return remainingContent[firstIndex + tagLength :]


def _scan_thinking_mode(
    remainingContent: str,
    parsingState: dict[str, Any],
    events: list[StreamEvent],
) -> str:
    """Scan thinking mode for tag ends. Returns unconsumed content.

    Tool calls and special tags are disabled in thoughts. We only detect
    the end of thoughts (e.g. </think>).
    """
    foundEndIndices: list[tuple[int, int]] = []
    for closeTag in THOUGHT_CLOSE_TAGS:
        tagIndex = remainingContent.find(closeTag)
        if tagIndex != -1:
            foundEndIndices.append((tagIndex, len(closeTag)))

    if foundEndIndices:
        firstIndex, tagLength = min(foundEndIndices, key=lambda tagPair: tagPair[0])
        if firstIndex > 0:
            events.append(ReasoningDeltaEvent(delta=remainingContent[:firstIndex]))
        parsingState["mode"] = "text"
        return remainingContent[firstIndex + tagLength :]

    # Check for partial close tag at the very end of remainingContent
    for candidateLength in range(min(len(remainingContent), 15), 0, -1):
        candidateSuffix = remainingContent[-candidateLength:]
        if any(closeTag.startswith(candidateSuffix) for closeTag in THOUGHT_CLOSE_TAGS):
            prefixIndex = len(remainingContent) - candidateLength
            if prefixIndex > 0:
                events.append(ReasoningDeltaEvent(delta=remainingContent[:prefixIndex]))
            parsingState["pending"] = candidateSuffix
            return ""

    events.append(ReasoningDeltaEvent(delta=remainingContent))
    return ""


def _scan_tool_calling_mode(
    remainingContent: str,
    parsingState: dict[str, Any],
    events: list[StreamEvent],
) -> str:
    """Scan tool_calling mode for tag ends. Returns unconsumed content.

    Close tags are searched before start tags, so a dual boundary marker
    (``<tool_call|>``) that is both a start and a close resolves as a *close*
    while a call is open — the next call is only opened once we are back in
    text mode.
    """
    interestingIndices: list[tuple[int, str, int]] = []
    for closeTag in TOOL_CLOSE_TAGS:
        closeIndex = remainingContent.find(closeTag)
        if closeIndex != -1:
            interestingIndices.append((closeIndex, "end", len(closeTag)))

    for nextToolTag in TOOL_START_TAGS:
        nextToolIndex = remainingContent.find(nextToolTag)
        if nextToolIndex != -1:
            interestingIndices.append((nextToolIndex, "transition", 0))

    for nextThinkTag in THINK_START_TAGS:
        nextThinkIndex = remainingContent.find(nextThinkTag)
        if nextThinkIndex != -1:
            interestingIndices.append((nextThinkIndex, "transition", 0))

    if not interestingIndices:
        allPotentialTags = TOOL_CLOSE_TAGS + TOOL_START_TAGS + THINK_START_TAGS
        for candidateLength in range(min(len(remainingContent), 20), 0, -1):
            candidateSuffix = remainingContent[-candidateLength:]
            if any(targetTag.startswith(candidateSuffix) for targetTag in allPotentialTags):
                prefixIndex = len(remainingContent) - candidateLength
                parsingState["buffer"] += remainingContent[:prefixIndex]
                parsingState["pending"] = candidateSuffix
                return ""

        parsingState["buffer"] += remainingContent
        return ""

    firstIndex, typeName, tagLength = min(interestingIndices, key=lambda itemPair: itemPair[0])
    callPayload = parsingState["buffer"] + remainingContent[:firstIndex]
    events.extend(_emit_tool_call_events(callPayload))
    parsingState["buffer"] = ""
    parsingState["mode"] = "text"

    if typeName == "end":
        return remainingContent[firstIndex + tagLength :]
    return remainingContent[firstIndex:]


def _run_tag_state_machine(
    remainingContent: str,
    parsingState: dict[str, Any],
    events: list[StreamEvent],
) -> str:
    """Run the tag state machine until all content is consumed.

    Dispatches to the appropriate scanner based on parsingState["mode"].
    Each scanner returns unconsumed content for the next iteration.
    """
    while remainingContent:
        if parsingState["mode"] == "text":
            remainingContent = _scan_text_mode(remainingContent, parsingState, events)
        elif parsingState["mode"] == "thinking":
            remainingContent = _scan_thinking_mode(remainingContent, parsingState, events)
        elif parsingState["mode"] == "tool_calling":
            remainingContent = _scan_tool_calling_mode(remainingContent, parsingState, events)
    return remainingContent


# ---------------------------------------------------------------------------
# Batch parsing (finished text -> ToolCall list)
# ---------------------------------------------------------------------------


def _extract_balanced_json(text: str, start: int) -> str | None:
    """Return the balanced JSON object starting at text[start] (which must be '{').

    Tracks string state so braces inside string values do not affect depth.
    Returns None if the object is not closed within text.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
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
                return text[start : i + 1]
    return None


def parse_text_tool_calls(rawText: str) -> list[ToolCall]:
    """Parse tool calls embedded in LLM text output (local/MLC servers).

    Handles three formats:
    1. <tool_call><function=name><parameter=p>v</parameter></function></tool_call> (Sushi-Coder/Hermes XML)
    2. <tool_call>{"name": ..., "arguments": ...}</tool_call> tags (Qwen3.5 native)
    3. ```json {"name": ..., "arguments": ...} ``` fenced blocks (fallback)

    MLC returns finish_reason='tool_calls' but populates the text stream rather
    than the structured delta.tool_calls field.
    """
    # Strip thoughts so tool calls mentioned inside thoughts are never executed
    cleanedText = _THINKING_BLOCK.sub("", rawText)

    results: list[ToolCall] = []

    if not results:
        for regexMatch in re.finditer(
            r"(?:<\|?tool_call\|?>|<\|tool_call_start\|>)(.*?)(?:<\|?/?tool_call\|?>|<\|tool_call_end\|>|$)",
            cleanedText,
            re.DOTALL,
        ):
            matchedContent = regexMatch.group(1).strip()
            rawToolCallId = f"call_{uuid.uuid4().hex[:8]}"

            # Formats 1 & 2 (XML, then JSON) share the streaming path's decoder.
            decoded = _decode_tool_call_payload(matchedContent)
            if decoded is None:
                continue
            toolName, serializedArguments = decoded
            # A nameless payload has no tool to dispatch to — skip it rather
            # than executing a call with a null/missing name.
            if toolName is None:
                continue
            results.append(
                ToolCall(
                    index=len(results),
                    id=rawToolCallId,
                    name=toolName,
                    arguments=serializedArguments,
                )
            )

    if not results:
        # Fenced JSON blocks: locate a fence, then extract the first balanced
        # JSON object after it (string-aware, so braces inside string values
        # cannot truncate the match). A missing closing fence is tolerated.
        # After a successful parse the scan resumes past the consumed object so
        # fences inside it (or the block's closing fence) are not re-matched.
        searchFrom = 0
        while True:
            fenceMatch = _JSON_FENCE.search(cleanedText, searchFrom)
            if fenceMatch is None:
                break
            braceStart = cleanedText.find("{", fenceMatch.end())
            if braceStart == -1:
                break
            candidate = _extract_balanced_json(cleanedText, braceStart)
            if candidate is not None:
                try:
                    decodedPayload = json.loads(candidate)
                except json.JSONDecodeError:
                    decodedPayload = None
                if isinstance(decodedPayload, dict):
                    toolName = decodedPayload.get("name")
                    arguments = decodedPayload.get("arguments")
                    if toolName and arguments is not None:
                        # Don't sanitize tool names for LLM context - they should remain unchanged
                        # toolName = _sanitize_tool_name(toolName)
                        # Sanitize tool call ID to meet provider requirements (alphanumeric, 9 chars)
                        rawToolCallId = f"call_{uuid.uuid4().hex[:8]}"
                        results.append(
                            ToolCall(
                                index=len(results),
                                id=rawToolCallId,  # No hashing - use original ID
                                name=toolName,
                                arguments=json.dumps(arguments) if isinstance(arguments, dict) else str(arguments),
                            )
                        )
                        searchFrom = braceStart + len(candidate)
                        continue
            searchFrom = fenceMatch.end()

    return results
