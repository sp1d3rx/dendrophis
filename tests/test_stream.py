"""Tests for SSE parser and LLM output format helpers."""

from httpx_sse import ServerSentEvent

from dendrophis.llm.stream import (
    _extract_delta_reasoning,
    _extract_delta_text,
    _extract_tool_call_chunk,
    _parse_function_xml,
    parse_sse_event,
    parse_text_tool_calls,
)


def test_extract_delta_text() -> None:
    """Test text extraction from delta dictionaries."""
    assert _extract_delta_text({}) is None
    assert _extract_delta_text({"content": "hello"}) == "hello"
    assert _extract_delta_text({"content": 42}) == "42"
    assert _extract_delta_text({"content": ["hello", "world"]}) == "hello world"
    assert _extract_delta_text({"content": [{"text": "hello"}, "world"]}) == "hello world"


def test_extract_delta_reasoning() -> None:
    """Test reasoning extraction from delta dictionaries."""
    assert _extract_delta_reasoning({}) is None
    assert _extract_delta_reasoning({"reasoning_content": "thinking"}) == "thinking"
    assert _extract_delta_reasoning({"thought": "thinking"}) == "thinking"
    assert _extract_delta_reasoning({"reasoning": "None"}) is None


def test_extract_tool_call_chunk() -> None:
    """Test extracting tool call chunks."""
    result = _extract_tool_call_chunk(
        {
            "index": 1,
            "id": "tc-123",
            "function": {"name": "test_tool", "arguments": '{"param": "val"}'},
        }
    )
    assert result == (1, "tc-123", "test_tool", '{"param": "val"}')


def test_parse_function_xml() -> None:
    """Test parsing function XML format."""
    xmlText = "<function=test_tool><parameter=param>val</parameter></function>"
    result = _parse_function_xml(xmlText)
    assert result == ("test_tool", {"param": "val"})


def test_parse_text_tool_calls() -> None:
    """Test parsing tool calls from text output."""
    xmlText = "<tool_call><function=test_tool><parameter=param>val</parameter></function></tool_call>"
    results = parse_text_tool_calls(xmlText)
    assert len(results) == 1
    assert results[0].name == "test_tool"
    assert results[0].arguments == '{"param": "val"}'


def test_parse_sse_event() -> None:
    """Test parsing SSE events."""
    event = ServerSentEvent(
        event="message",
        data='{"choices": [{"delta": {"content": "hello"}}]}',
        id="1",
    )
    events, inProgressCalls, parsingState = parse_sse_event(event, {}, None)
    assert len(events) == 1
    assert events[0].delta == "hello"
    assert inProgressCalls == {}
    assert parsingState == {"mode": "text", "buffer": "", "pending": ""}


def test_parse_lfm_tool_calls() -> None:
    """Test parsing LFM format python-like tool calls."""
    lfm_text = '<|tool_call_start|>[read(why="Check files", file_path=".", why="To list files")]<|tool_call_end|>'
    results = parse_text_tool_calls(lfm_text)
    assert len(results) == 1
    assert results[0].name == "read"
    assert results[0].arguments == '{"why": "To list files", "file_path": "."}'


def test_lfm_streaming_tool_call() -> None:
    """Test LFM tool call parsing under streaming mode."""
    first_event = ServerSentEvent(
        event="message",
        data='{"choices": [{"delta": {"content": "<|tool_call_start|>[read(why=\\"Check\\", file_path=\\".\\")]"}}]}',
        id="1",
    )
    second_event = ServerSentEvent(
        event="message",
        data='{"choices": [{"delta": {"content": "<|tool_call_end|>"}}]}',
        id="2",
    )

    in_progress: dict = {}
    state = None

    events_1, in_progress, state = parse_sse_event(first_event, in_progress, state)
    assert len(events_1) == 0  # Still buffering tool call

    events_2, in_progress, state = parse_sse_event(second_event, in_progress, state)
    assert len(events_2) == 3  # ToolCallStart, ToolCallDelta, ToolCallDone
    assert events_2[0].name == "read"


def test_parse_text_tool_calls_with_pipe() -> None:
    """Test parsing tool calls from text output containing pipe character in tags."""
    pipe_text = "<tool_call|><function=test_tool><parameter=param>val</parameter></function></tool_call|>"
    results = parse_text_tool_calls(pipe_text)
    assert len(results) == 1
    assert results[0].name == "test_tool"
    assert results[0].arguments == '{"param": "val"}'


def test_tool_call_pipe_streaming() -> None:
    """Test tool call parsing with pipe character in tags under streaming mode."""
    first_event = ServerSentEvent(
        event="message",
        data='{"choices": [{"delta": {"content": "<tool_call|><function=test_tool><parameter=param>val</parameter></function>"}}]}', # noqa: E501
        id="1",
    )
    second_event = ServerSentEvent(
        event="message",
        data='{"choices": [{"delta": {"content": "</tool_call|>"}}]}',
        id="2",
    )

    in_progress: dict = {}
    state = None

    events_1, in_progress, state = parse_sse_event(first_event, in_progress, state)
    assert len(events_1) == 0  # Still buffering tool call

    events_2, in_progress, state = parse_sse_event(second_event, in_progress, state)
    assert len(events_2) == 3  # ToolCallStart, ToolCallDelta, ToolCallDone
    assert events_2[0].name == "test_tool"


def test_lfm_short_tags_parsing() -> None:
    """Test parsing tool calls from text output using short LFM tags."""
    short_lfm_text = '<|tool_call>[read(why="Check files", file_path=".")]<tool_call|>'
    results = parse_text_tool_calls(short_lfm_text)
    assert len(results) == 1
    assert results[0].name == "read"
    assert results[0].arguments == '{"why": "Check files", "file_path": "."}'


def test_lfm_short_tags_streaming() -> None:
    """Test short LFM tool call parsing under streaming mode."""
    first_event = ServerSentEvent(
        event="message",
        data='{"choices": [{"delta": {"content": "<|tool_call>[read(why=\\"Check\\", file_path=\\".\\")]"}}]}',
        id="1",
    )
    second_event = ServerSentEvent(
        event="message",
        data='{"choices": [{"delta": {"content": "<tool_call|>"}}]}',
        id="2",
    )

    in_progress: dict = {}
    state = None

    events_1, in_progress, state = parse_sse_event(first_event, in_progress, state)
    assert len(events_1) == 0  # Still buffering tool call

    events_2, in_progress, state = parse_sse_event(second_event, in_progress, state)
    assert len(events_2) == 3  # ToolCallStart, ToolCallDelta, ToolCallDone
    assert events_2[0].name == "read"
