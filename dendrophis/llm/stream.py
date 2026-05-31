"""SSE parser and LLM output format helpers."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from httpx_sse import ServerSentEvent

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
    UsageEvent,
)


def _extract_delta_text(delta: dict[str, Any]) -> str | None:
    """Return text content from a delta dict, or None if absent."""
    # Edge case: Handle None or non-dict delta
    if not isinstance(delta, dict):
        return None

    textContent = delta.get("content")
    if isinstance(textContent, str):
        # Edge case: Handle empty strings
        return textContent if textContent else None

    if isinstance(textContent, (int, float)):
        # Edge case: Convert numbers to strings
        return str(textContent)

    if isinstance(textContent, list):
        # Edge case: Handle list content by joining text elements
        textParts = []
        for contentElement in textContent:
            if isinstance(contentElement, dict):
                text = contentElement.get("text")
                if isinstance(text, str):
                    textParts.append(text)
            elif isinstance(contentElement, str):
                textParts.append(contentElement)
        return " ".join(textParts) if textParts else None

    return None


def _extract_delta_reasoning(delta: dict[str, Any]) -> str | None:
    """Return reasoning content from a delta dict, checking provider-specific keys."""
    # Edge case: Handle None or non-dict delta
    if not isinstance(delta, dict):
        return None

    # DeepSeek / OpenRouter / O1 / Groq reasoning keys
    reasoningKeys = ("reasoning_content", "reasoning", "thought", "thought_content")
    for reasoningKey in reasoningKeys:
        reasoningContent = delta.get(reasoningKey)
        if isinstance(reasoningContent, str):
            # Kimi K2.5 (via DeepInfra) serializes Python None as the string "None" — discard it
            return reasoningContent if (reasoningContent and reasoningContent != "None") else None

        if isinstance(reasoningContent, (int, float)):
            # Edge case: Convert numbers to strings
            return str(reasoningContent)

        if isinstance(reasoningContent, list):
            # Edge case: Handle list content by joining elements
            textParts = []
            for contentElement in reasoningContent:
                if isinstance(contentElement, str):
                    textParts.append(contentElement)
                elif isinstance(contentElement, dict):
                    text = contentElement.get("text")
                    if isinstance(text, str):
                        textParts.append(text)
            return " ".join(textParts) if textParts else None
    return None


def _extract_tool_call_chunk(
    toolCallChunk: dict[str, Any],
) -> tuple[int, str | None, str | None, str]:
    """Extract (index, id, name, args_delta) from a raw tool-call chunk dict."""
    # Edge case: Handle None or non-dict tool call
    if not isinstance(toolCallChunk, dict):
        return 0, None, None, ""

    # Edge case: Handle missing or invalid index
    try:
        toolCallIndex = int(toolCallChunk.get("index", 0))
    except (ValueError, TypeError):
        toolCallIndex = 0

    toolCallId = toolCallChunk.get("id")
    # Sanitize tool call ID to meet provider requirements (alphanumeric, 9 chars)
    # Always sanitize, even if None - this ensures we never pass invalid IDs
    toolCallId = toolCallId  # No hashing - use original ID

    # Edge case: Handle missing or invalid function
    functionDetails = toolCallChunk.get("function", {})
    if not isinstance(functionDetails, dict):
        functionDetails = {}

    toolName = functionDetails.get("name")
    # Don't sanitize tool names for LLM context - they should remain unchanged
    # toolName = _sanitize_tool_name(toolName) if toolName is not None else None

    argumentsDelta = functionDetails.get("arguments", "")
    # Edge case: Handle non-string arguments
    if not isinstance(argumentsDelta, str):
        argumentsDelta = json.dumps(argumentsDelta) if isinstance(argumentsDelta, (dict, list)) else str(argumentsDelta)

    # LMStudio/Qwen3 bug: sometimes puts the full JSON payload into function.name
    # and leaves arguments empty. Detect by looking for embedded JSON in the raw name.
    if toolName and not argumentsDelta and "{" in toolName:
        jsonStart = toolName.find("{")
        candidateName = toolName[:jsonStart].strip()
        candidateArgs = toolName[jsonStart:]
        try:
            parsedArguments = json.loads(candidateArgs)
            # Recover arguments from the embedded JSON
            arguments = parsedArguments.get("arguments") or {
                key: value for key, value in parsedArguments.items() if key != "name"
            }
            argumentsDelta = json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)
            toolName = parsedArguments.get("name") or candidateName or toolName
        except (json.JSONDecodeError, ValueError):
            pass

    # Don't sanitize tool names for LLM context - they should remain unchanged
    # toolName = _sanitize_tool_name(toolName)
    return toolCallIndex, toolCallId, toolName, argumentsDelta


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


def _parse_python_literal(literal_string: str) -> Any:
    literal_string = literal_string.strip()
    if not literal_string:
        return None
    # Booleans and None
    if literal_string == "True" or literal_string == "true":
        return True
    if literal_string == "False" or literal_string == "false":
        return False
    if literal_string == "None" or literal_string == "null":
        return None

    # Strings
    if (literal_string.startswith('"') and literal_string.endswith('"')) or (
        literal_string.startswith("'") and literal_string.endswith("'")
    ):
        # Strip quotes and handle escape characters
        content_string = literal_string[1:-1]
        # Unescape common sequences
        return content_string.encode("utf-8").decode("unicode_escape")

    # Numeric
    try:
        if "." in literal_string:
            return float(literal_string)
        return int(literal_string)
    except ValueError:
        pass

    # Fallback to json parsing for lists/dicts if formatted as json
    try:
        return json.loads(literal_string)
    except json.JSONDecodeError:
        pass

    return literal_string


def _parse_lfm_args(arguments_string: str) -> dict[str, Any]:
    arguments = {}
    total_length = len(arguments_string)
    current_index = 0

    while current_index < total_length:
        # Skip whitespace and commas
        while current_index < total_length and (
            arguments_string[current_index].isspace() or arguments_string[current_index] == ","
        ):
            current_index += 1
        if current_index >= total_length:
            break

        # Parse key
        key_start = current_index
        while current_index < total_length and (
            arguments_string[current_index].isalnum() or arguments_string[current_index] == "_"
        ):
            current_index += 1
        key_name = arguments_string[key_start:current_index]

        # Skip whitespace
        while current_index < total_length and arguments_string[current_index].isspace():
            current_index += 1

        if current_index >= total_length or arguments_string[current_index] != "=":
            # Malformed key-value or end of string
            break
        current_index += 1  # consume '='

        # Skip whitespace
        while current_index < total_length and arguments_string[current_index].isspace():
            current_index += 1

        # Parse value
        value_start = current_index
        in_double_quote = False
        in_single_quote = False
        bracket_stack = []
        is_escaped = False

        while current_index < total_length:
            character = arguments_string[current_index]
            if is_escaped:
                is_escaped = False
                current_index += 1
                continue

            if character == "\\":
                is_escaped = True
                current_index += 1
                continue

            if in_double_quote:
                if character == '"':
                    in_double_quote = False
            elif in_single_quote:
                if character == "'":
                    in_single_quote = False
            else:
                if character == '"':
                    in_double_quote = True
                elif character == "'":
                    in_single_quote = True
                elif character in ("[", "{", "("):
                    bracket_stack.append(character)
                elif character in ("]", "}", ")"):
                    if bracket_stack:
                        bracket_stack.pop()
                elif character == "," and not bracket_stack:
                    break

            current_index += 1

        value_raw = arguments_string[value_start:current_index].strip()
        value_parsed = _parse_python_literal(value_raw)
        if key_name:
            arguments[key_name] = value_parsed

    return arguments


def _emit_tool_call_events(callContent: str) -> list[StreamEvent]:
    """Parse tool call content (XML or JSON) and return the sequence of events."""
    events: list[StreamEvent] = []
    toolCallIndex = 999
    toolCallId = f"tc-{hash(callContent) % 10000}"

    # Try LFM format: [name(args)]
    stripped_content = callContent.strip()
    if stripped_content.startswith("[") and stripped_content.endswith("]"):
        inner_content = stripped_content[1:-1].strip()
        lfm_match = re.match(r"^([a-zA-Z0-9_]+)\((.*)\)$", inner_content, re.DOTALL)
        if lfm_match:
            tool_name = lfm_match.group(1).strip()
            tool_arguments = _parse_lfm_args(lfm_match.group(2))
            serialized_arguments = json.dumps(tool_arguments)
            events.append(ToolCallStartEvent(index=toolCallIndex, id=toolCallId, name=tool_name))
            events.append(ToolCallDeltaEvent(index=toolCallIndex, arguments_delta=serialized_arguments))
            events.append(
                ToolCallDoneEvent(
                    tool_call=ToolCall(
                        index=toolCallIndex, id=toolCallId, name=tool_name, arguments=serialized_arguments
                    )
                )
            )
            return events

    # Try Sushi-Coder / Hermes XML format: <function=name><parameter=p>v</parameter></function>
    parsedFunction = _parse_function_xml(callContent)
    if parsedFunction:
        toolName, toolArguments = parsedFunction
        serializedArguments = json.dumps(toolArguments)
        events.append(ToolCallStartEvent(index=toolCallIndex, id=toolCallId, name=toolName))
        events.append(ToolCallDeltaEvent(index=toolCallIndex, arguments_delta=serializedArguments))
        events.append(
            ToolCallDoneEvent(
                tool_call=ToolCall(index=toolCallIndex, id=toolCallId, name=toolName, arguments=serializedArguments)
            )
        )
        return events

    # Try JSON format: {"name": ..., "arguments": ...}
    try:
        decodedPayload = json.loads(callContent)
        toolName = decodedPayload.get("name", "unknown")
        arguments = decodedPayload.get("arguments")
        if arguments is None:
            arguments = {key: value for key, value in decodedPayload.items() if key != "name"}
        serializedArguments = json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)
        events.append(ToolCallStartEvent(index=toolCallIndex, id=toolCallId, name=toolName))
        events.append(ToolCallDeltaEvent(index=toolCallIndex, arguments_delta=serializedArguments))
        events.append(
            ToolCallDoneEvent(
                tool_call=ToolCall(index=toolCallIndex, id=toolCallId, name=toolName, arguments=serializedArguments)
            )
        )
    except json.JSONDecodeError:
        events.append(TextDeltaEvent(delta=f"<tool_call>{callContent}"))
    return events


def parse_sse_event(
    sseEvent: ServerSentEvent,
    inProgressCalls: dict[int, ToolCall],
    parsingState: dict[str, Any] | None = None,
) -> tuple[list[StreamEvent], dict[int, ToolCall], dict[str, Any]]:
    """Parse an httpx-sse ServerSentEvent, return events and updated in-progress map.

    parsingState: keeps track of <think> and <tool_call> blocks across chunks.
    """
    if parsingState is None:
        parsingState = {"mode": "text", "buffer": "", "pending": ""}

    if sseEvent.event == "ping":
        return [], inProgressCalls, parsingState

    rawPayload = sseEvent.data
    if rawPayload == "[DONE]":
        return [], inProgressCalls, parsingState

    try:
        chunk = json.loads(rawPayload)
    except json.JSONDecodeError:
        return [], inProgressCalls, parsingState

    # Surface provider error events (e.g. DeepInfra validation errors returned mid-stream)
    if "choices" not in chunk and ("error" in chunk or "error_type" in chunk):
        errorDetails = chunk.get("error") or {}
        errorMessage = errorDetails.get("message") or chunk.get("error_message") or str(chunk)
        return [ErrorEvent(message=errorMessage)], inProgressCalls, parsingState

    events: list[StreamEvent] = []
    updatedCalls = dict(inProgressCalls)

    # OpenRouter Responses API SSE events — type is in the JSON payload, not the SSE event field
    if chunk.get("type", "").startswith("response."):
        eventType = chunk.get("type", "")
        if eventType == "response.output_text.delta":
            textDelta = chunk.get("delta", "")
            if textDelta:
                events.append(TextDeltaEvent(delta=textDelta))
        elif eventType in (
            "response.reasoning_text.delta",
            "response.reasoning_summary_text.delta",
            "response.refusal.delta",
        ):
            reasoningDelta = chunk.get("delta", "")
            if reasoningDelta:
                events.append(ReasoningDeltaEvent(delta=reasoningDelta))
        elif eventType == "response.output_item.added":
            outputItem = chunk.get("item", {})
            if outputItem.get("type") == "function_call":
                outputIndex = chunk.get("output_index", 0)
                callId = outputItem.get("call_id") or outputItem.get("id", f"call_{outputIndex}")
                toolName = outputItem.get("name", "")
                updatedCalls[outputIndex] = ToolCall(index=outputIndex, id=callId, name=toolName, arguments="")
                events.append(ToolCallStartEvent(index=outputIndex, id=callId, name=toolName))
        elif eventType == "response.function_call_arguments.delta":
            outputIndex = chunk.get("output_index", 0)
            textDelta = chunk.get("delta", "")
            if outputIndex in updatedCalls:
                updatedCalls[outputIndex].arguments += textDelta
            if textDelta:
                events.append(ToolCallDeltaEvent(index=outputIndex, arguments_delta=textDelta))
        elif eventType == "response.output_item.done":
            outputItem = chunk.get("item", {})
            if outputItem.get("type") == "function_call":
                outputIndex = chunk.get("output_index", 0)
                if outputIndex in updatedCalls:
                    toolCall = updatedCalls.pop(outputIndex)
                    toolCall.arguments = outputItem.get("arguments", toolCall.arguments)
                    events.append(ToolCallDoneEvent(tool_call=toolCall))
        elif eventType == "response.completed":
            responseDetails = chunk.get("response", {})
            events.extend(ToolCallDoneEvent(tool_call=toolCall) for toolCall in updatedCalls.values())
            updatedCalls = {}
            usageDetails = responseDetails.get("usage", {})
            if usageDetails:
                events.append(
                    UsageEvent(
                        prompt_tokens=usageDetails.get("input_tokens", 0),
                        completion_tokens=usageDetails.get("output_tokens", 0),
                        cached_tokens=(usageDetails.get("input_tokens_details") or {}).get("cached_tokens", 0),
                    )
                )
            outputs = responseDetails.get("output", [])
            hasToolCalls = any(outputItem.get("type") == "function_call" for outputItem in outputs)
            events.append(DoneEvent(finish_reason="tool_calls" if hasToolCalls else "stop"))
        return events, updatedCalls, parsingState

    # Choices handling
    choices = chunk.get("choices", [])
    for choice in choices:
        delta = choice.get("delta", {})
        finishReason = choice.get("finish_reason")

        # 1. Direct Reasoning (from providers like DeepSeek/Groq)
        reasoningDelta = _extract_delta_reasoning(delta)
        if reasoningDelta:
            events.append(ReasoningDeltaEvent(delta=reasoningDelta))

        # 2. Text Content (may contain <think> or <tool_call> tags)
        textDelta = _extract_delta_text(delta)
        if textDelta:
            # Add new content to any pending fragment from previous chunk
            remainingContent = parsingState.get("pending", "") + textDelta
            parsingState["pending"] = ""

            while remainingContent:
                if parsingState["mode"] == "text":
                    # Look for start tags
                    thinkStart = remainingContent.find("<think>")
                    toolStart = remainingContent.find("<tool_call>")
                    toolStartPipe = remainingContent.find("<tool_call|>")
                    lfm_tool_start = remainingContent.find("<|tool_call_start|>")
                    lfm_tool_start_short = remainingContent.find("<|tool_call>")

                    # Check for partial tags at the very end of the string
                    potentialTagIndex = remainingContent.rfind("<")
                    if potentialTagIndex != -1:
                        # Only buffer if it's AFTER any complete tag found in this chunk
                        foundTagIndex = max(thinkStart, toolStart, toolStartPipe, lfm_tool_start, lfm_tool_start_short)
                        if potentialTagIndex > foundTagIndex:
                            fragment = remainingContent[potentialTagIndex:]
                            if (
                                "<think>".startswith(fragment)
                                or "<tool_call>".startswith(fragment)
                                or "<tool_call|>".startswith(fragment)
                                or "<|tool_call_start|>".startswith(fragment)
                                or "<|tool_call>".startswith(fragment)
                            ):
                                if potentialTagIndex > 0:
                                    events.append(TextDeltaEvent(delta=remainingContent[:potentialTagIndex]))
                                parsingState["pending"] = fragment
                                break

                    # Find whichever comes first
                    modeIndices = []
                    if thinkStart != -1:
                        modeIndices.append((thinkStart, "thinking", 7))
                    if toolStart != -1:
                        modeIndices.append((toolStart, "tool_calling", 11))
                    if toolStartPipe != -1:
                        modeIndices.append((toolStartPipe, "tool_calling", 12))
                    if lfm_tool_start != -1:
                        modeIndices.append((lfm_tool_start, "tool_calling", 19))
                    if lfm_tool_start_short != -1:
                        modeIndices.append((lfm_tool_start_short, "tool_calling", 12))

                    if not modeIndices:
                        # No complete tags, just emit text
                        events.append(TextDeltaEvent(delta=remainingContent))
                        break

                    firstIndex, nextMode, tagLength = min(modeIndices)
                    # Emit text before the tag
                    if firstIndex > 0:
                        events.append(TextDeltaEvent(delta=remainingContent[:firstIndex]))

                    # Enter new mode
                    parsingState["mode"] = nextMode
                    remainingContent = remainingContent[firstIndex + tagLength :]

                elif parsingState["mode"] == "thinking":
                    thinkEnd = remainingContent.find("</think>")

                    # Robustness: look for new tags that might indicate an implicit end
                    nextToolStart = remainingContent.find("<tool_call>")
                    next_lfm_tool_start = remainingContent.find("<|tool_call_start|>")
                    nextThinkStart = remainingContent.find("<think>")

                    interestingIndices = []
                    if thinkEnd != -1:
                        interestingIndices.append((thinkEnd, "end", 8))
                    if nextToolStart != -1:
                        interestingIndices.append((nextToolStart, "transition", 0))
                    if next_lfm_tool_start != -1:
                        interestingIndices.append((next_lfm_tool_start, "transition", 0))
                    if nextThinkStart != -1:
                        interestingIndices.append((nextThinkStart, "transition", 0))

                    if not interestingIndices:
                        # Handle partial closing or start tags
                        potentialEndIndex = remainingContent.rfind("</")
                        if potentialEndIndex != -1:
                            fragment = remainingContent[potentialEndIndex:]
                            if "</think>".startswith(fragment):
                                if potentialEndIndex > 0:
                                    events.append(ReasoningDeltaEvent(delta=remainingContent[:potentialEndIndex]))
                                parsingState["pending"] = fragment
                                break

                        potentialStartIndex = remainingContent.rfind("<")
                        if potentialStartIndex != -1:
                            fragment = remainingContent[potentialStartIndex:]
                            if (
                                "<think>".startswith(fragment)
                                or "<tool_call>".startswith(fragment)
                                or "<|tool_call_start|>".startswith(fragment)
                            ):
                                if potentialStartIndex > 0:
                                    events.append(ReasoningDeltaEvent(delta=remainingContent[:potentialStartIndex]))
                                parsingState["pending"] = fragment
                                break

                        # Still thinking
                        events.append(ReasoningDeltaEvent(delta=remainingContent))
                        break
                    firstIndex, typeName, tagLength = min(interestingIndices)
                    # Thinking ends (explicitly or implicitly)
                    if firstIndex > 0:
                        events.append(ReasoningDeltaEvent(delta=remainingContent[:firstIndex]))

                    parsingState["mode"] = "text"
                    remainingContent = (
                        remainingContent[firstIndex + tagLength :]
                        if typeName == "end"
                        else remainingContent[firstIndex:]
                    )

                elif parsingState["mode"] == "tool_calling":
                    toolEnd = remainingContent.find("</tool_call>")
                    toolEndPipe = remainingContent.find("</tool_call|>")
                    lfm_tool_end = remainingContent.find("<|tool_call_end|>")
                    tool_end_pipe_only = remainingContent.find("<tool_call|>")

                    # Robustness: look for new tags that might indicate an implicit end
                    nextToolStart = remainingContent.find("<tool_call>")
                    nextToolStartPipe = remainingContent.find("<tool_call|>")
                    next_lfm_tool_start = remainingContent.find("<|tool_call_start|>")
                    next_lfm_tool_start_short = remainingContent.find("<|tool_call>")
                    nextThinkStart = remainingContent.find("<think>")

                    interestingIndices = []
                    if toolEnd != -1:
                        interestingIndices.append((toolEnd, "end", 12))
                    if toolEndPipe != -1:
                        interestingIndices.append((toolEndPipe, "end", 13))
                    if lfm_tool_end != -1:
                        interestingIndices.append((lfm_tool_end, "end", 17))
                    if tool_end_pipe_only != -1:
                        interestingIndices.append((tool_end_pipe_only, "end", 12))
                    if nextToolStart != -1:
                        interestingIndices.append((nextToolStart, "transition", 0))
                    if nextToolStartPipe != -1:
                        interestingIndices.append((nextToolStartPipe, "transition", 0))
                    if next_lfm_tool_start != -1:
                        interestingIndices.append((next_lfm_tool_start, "transition", 0))
                    if next_lfm_tool_start_short != -1:
                        interestingIndices.append((next_lfm_tool_start_short, "transition", 0))
                    if nextThinkStart != -1:
                        interestingIndices.append((nextThinkStart, "transition", 0))

                    if not interestingIndices:
                        # Handle partial closing or start tags
                        potentialEndIndex = remainingContent.rfind("</")
                        if potentialEndIndex != -1:
                            fragment = remainingContent[potentialEndIndex:]
                            if "</tool_call>".startswith(fragment) or "</tool_call|>".startswith(fragment):
                                parsingState["buffer"] += remainingContent[:potentialEndIndex]
                                parsingState["pending"] = fragment
                                break

                        potentialStartIndex = remainingContent.rfind("<")
                        if potentialStartIndex != -1:
                            fragment = remainingContent[potentialStartIndex:]
                            if (
                                "<tool_call>".startswith(fragment)
                                or "<tool_call|>".startswith(fragment)
                                or "<think>".startswith(fragment)
                                or "<|tool_call_start|>".startswith(fragment)
                                or "<|tool_call_end|>".startswith(fragment)
                                or "<|tool_call>".startswith(fragment)
                            ):
                                parsingState["buffer"] += remainingContent[:potentialStartIndex]
                                parsingState["pending"] = fragment
                                break

                        # Still buffering tool call
                        parsingState["buffer"] += remainingContent
                        break
                    firstIndex, typeName, tagLength = min(interestingIndices)
                    # Tool call ends (explicitly or implicitly)
                    callPayload = parsingState["buffer"] + remainingContent[:firstIndex]
                    events.extend(_emit_tool_call_events(callPayload))
                    parsingState["buffer"] = ""
                    parsingState["mode"] = "text"

                    remainingContent = (
                        remainingContent[firstIndex + tagLength :]
                        if typeName == "end"
                        else remainingContent[firstIndex:]
                    )

        # 3. Standard Tool Calls (if provider supports them natively)
        for toolCall in delta.get("tool_calls") or []:
            toolCallIndex, toolCallId, toolName, argumentsDelta = _extract_tool_call_chunk(toolCall)
            if toolCallIndex not in updatedCalls:
                # If toolCallId is None, generate a valid sanitized ID for this index
                finalToolCallId = toolCallId if toolCallId else f"call_{toolCallIndex}"  # No hashing
                updatedCalls[toolCallIndex] = ToolCall(index=toolCallIndex, id=finalToolCallId, name=toolName or "")
                events.append(ToolCallStartEvent(index=toolCallIndex, id=finalToolCallId, name=toolName or ""))
            # If we already have this index, use the existing ID
            updatedCalls[toolCallIndex].arguments += argumentsDelta
            if argumentsDelta:
                events.append(ToolCallDeltaEvent(index=toolCallIndex, arguments_delta=argumentsDelta))

        # Finish handling
        if finishReason in ("tool_calls", "stop", "length", "eos"):
            # Flush pending tool call if the model stopped without a closing tag
            if parsingState["mode"] == "tool_calling" and parsingState["buffer"]:
                events.extend(_emit_tool_call_events(parsingState["buffer"]))
                parsingState["buffer"] = ""
                parsingState["mode"] = "text"

            # If we had a pending fragment, emit it as text/reasoning now
            if parsingState["pending"]:
                if parsingState["mode"] == "thinking":
                    events.append(ReasoningDeltaEvent(delta=parsingState["pending"]))
                else:
                    events.append(TextDeltaEvent(delta=parsingState["pending"]))
                parsingState["pending"] = ""

            normalizedReason = "stop" if finishReason == "eos" else finishReason
            events.extend(ToolCallDoneEvent(tool_call=tc) for tc in updatedCalls.values())
            updatedCalls = {}
            events.append(DoneEvent(finish_reason=normalizedReason))

    # Usage
    usage = chunk.get("usage")
    if usage:
        promptTokensDetails = usage.get("prompt_tokens_details") or {}
        events.append(
            UsageEvent(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                cached_tokens=promptTokensDetails.get("cached_tokens", 0),
            )
        )

    return events, updatedCalls, parsingState


def iter_sse_events(
    eventSource: Any,
) -> Any:
    """Generator wrapping httpx_sse EventSource for streaming use."""
    from httpx_sse import EventSource

    inProgressCalls: dict[int, ToolCall] = {}
    parsingState = {"mode": "text", "buffer": "", "pending": ""}
    for sseEvent in EventSource(eventSource):
        events, inProgressCalls, parsingState = parse_sse_event(sseEvent, inProgressCalls, parsingState)
        yield from events


def parse_text_tool_calls(rawText: str) -> list[ToolCall]:
    """Parse tool calls embedded in LLM text output (local/MLC servers).

    Handles three formats:
    1. <tool_call><function=name><parameter=p>v</parameter></function></tool_call> (Sushi-Coder/Hermes XML)
    2. <tool_call>{"name": ..., "arguments": ...}</tool_call> tags (Qwen3.5 native)
    3. ```json {"name": ..., "arguments": ...} ``` fenced blocks (fallback)

    MLC returns finish_reason='tool_calls' but populates the text stream rather
    than the structured delta.tool_calls field.
    """
    results: list[ToolCall] = []

    # First try LFM format: <|tool_call_start|>[name(args)]<|tool_call_end|>
    for regexMatch in re.finditer(
        r"<\|tool_call(?:_start)?\|?>(.*?)(?:<\|tool_call_end\|>|<tool_call\|?>|$)", rawText, re.DOTALL
    ):
        matchedContent = regexMatch.group(1).strip()
        if matchedContent.startswith("[") and matchedContent.endswith("]"):
            inner_content = matchedContent[1:-1].strip()
            lfm_match = re.match(r"^([a-zA-Z0-9_]+)\((.*)\)$", inner_content, re.DOTALL)
            if lfm_match:
                tool_name = lfm_match.group(1).strip()
                tool_arguments = _parse_lfm_args(lfm_match.group(2))
                rawToolCallId = f"call_{uuid.uuid4().hex[:8]}"
                results.append(
                    ToolCall(
                        index=len(results),
                        id=rawToolCallId,
                        name=tool_name,
                        arguments=json.dumps(tool_arguments),
                    )
                )

    if not results:
        for regexMatch in re.finditer(r"<tool_call\|?>(.*?)(?:</tool_call\|?>|$)", rawText, re.DOTALL):
            matchedContent = regexMatch.group(1).strip()
            rawToolCallId = f"call_{uuid.uuid4().hex[:8]}"

            # Format 1: <function=name><parameter=p>v</parameter>...</function>
            parsedFunction = _parse_function_xml(matchedContent)
            if parsedFunction:
                toolName, toolArguments = parsedFunction
                results.append(
                    ToolCall(
                        index=len(results),
                        id=rawToolCallId,
                        name=toolName,
                        arguments=json.dumps(toolArguments),
                    )
                )
                continue

            # Format 2: JSON {"name": ..., "arguments": ...}
            try:
                decodedPayload = json.loads(matchedContent)
                if isinstance(decodedPayload, dict) and "name" in decodedPayload and "arguments" in decodedPayload:
                    arguments = decodedPayload["arguments"]
                    toolName = decodedPayload.get("name", "unknown")
                    results.append(
                        ToolCall(
                            index=len(results),
                            id=rawToolCallId,
                            name=toolName,
                            arguments=json.dumps(arguments) if isinstance(arguments, dict) else str(arguments),
                        )
                    )
            except (json.JSONDecodeError, KeyError):
                pass

    if not results:
        for regexMatch in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", rawText, re.DOTALL):
            try:
                decodedPayload = json.loads(regexMatch.group(1).strip())
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
            except (json.JSONDecodeError, KeyError):
                pass

    return results
