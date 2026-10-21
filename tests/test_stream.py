"""Tests for SSE parser and LLM output format helpers."""

import json

from httpx_sse import ServerSentEvent

from dendrophis.events import (
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    ToolCallStreamingEvent,
)
from dendrophis.llm.stream import (
    _extract_delta_reasoning,
    _extract_delta_text,
    _extract_tool_call_chunk,
    parse_sse_event,
    # parse_text_tool_calls is re-exported from tag_parser; importing it here
    # (rather than from tag_parser) pins that re-export for client.py.
    parse_text_tool_calls,
)
from dendrophis.llm.tag_parser import (
    _decode_tool_call_payload,
    _parse_function_xml,
    _tool_arguments_from_payload,
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
        data='{"choices": [{"delta": {"content": "<tool_call|><function=test_tool><parameter=param>val</parameter></function>"}}]}',  # noqa: E501
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
    # Opening tag detected → streaming signal fires immediately; args still buffering
    assert len(events_1) == 1
    assert isinstance(events_1[0], ToolCallStreamingEvent)

    events_2, in_progress, state = parse_sse_event(second_event, in_progress, state)
    assert len(events_2) == 3  # ToolCallStart, ToolCallDelta, ToolCallDone
    assert events_2[0].name == "test_tool"


def test_tool_call_streaming_event_fires_before_start() -> None:
    """Regression: tool light should come on while the call is being built, not after.

    Mirrors a text-based (MLC/local) provider: the model emits intro prose, then a
    `` tag whose JSON arguments stream in over subsequent chunks. The
    streaming signal must fire at opening-tag detection, BEFORE the resolved
    ToolCallStartEvent (which only fires once the closing tag completes the call).
    """

    def make_sse(content: str, sid: str) -> ServerSentEvent:
        return ServerSentEvent(
            event="message",
            data=json.dumps({"choices": [{"delta": {"content": content}}]}),
            id=sid,
        )

    # Chunk 1: intro prose + opening tag. No args yet.
    first_event = make_sse("Let me write the file.\n<tool_call><function=write_file>", "1")
    # Chunk 2: the rest of the (long) argument build + closing tag.
    second_event = make_sse(
        '<parameter=arguments>{"file_path": "a.txt", "content": "hi"}</parameter></function></tool_call>',
        "2",
    )

    in_progress: dict = {}
    state = None

    events_1, in_progress, state = parse_sse_event(first_event, in_progress, state)
    # Intro text is flushed AND the streaming signal fires immediately.
    streamings_1 = [event_item for event_item in events_1 if isinstance(event_item, ToolCallStreamingEvent)]
    starts_1 = [event_item for event_item in events_1 if isinstance(event_item, ToolCallStartEvent)]
    assert len(streamings_1) == 1, "streaming signal must fire on opening-tag detection"
    assert starts_1 == [], "resolved start event must NOT fire while args are still building"

    events_2, in_progress, state = parse_sse_event(second_event, in_progress, state)
    starts_2 = [event_item for event_item in events_2 if isinstance(event_item, ToolCallStartEvent)]
    assert len(starts_2) == 1, "resolved start event fires once the closing tag completes the call"
    assert starts_2[0].name == "write_file"

    # Global ordering: the streaming signal precedes the resolved start event.
    combined = events_1 + events_2
    first_streaming = next(
        index for index, event_item in enumerate(combined) if isinstance(event_item, ToolCallStreamingEvent)
    )
    first_start = next(index for index, event_item in enumerate(combined) if isinstance(event_item, ToolCallStartEvent))
    assert first_streaming < first_start


def test_plain_text_emits_no_tool_call_streaming() -> None:
    """Plain prose (no tool tags) must not emit a streaming signal."""
    event = ServerSentEvent(
        event="message",
        data='{"choices": [{"delta": {"content": "Just some normal text, nothing to do."}}]}',
        id="1",
    )
    events, _, _ = parse_sse_event(event, {}, None)
    assert not any(isinstance(event_item, ToolCallStreamingEvent) for event_item in events)


def test_tool_calls_disabled_inside_thoughts_streaming() -> None:
    """Tool calls and special tags mentioned inside thoughts must not trigger tool execution."""
    content_delta = (
        "<think>I should check dendrophis code. Maybe call "
        "<tool_call><function=bash><parameter=command>ls</parameter></function></tool_call>? "
        "No, let's wait.</think>Now I will speak."
    )
    first_event = ServerSentEvent(
        event="message",
        data=json.dumps({"choices": [{"delta": {"content": content_delta}}]}),
        id="1",
    )

    stream_events, in_progress_calls, parsing_state = parse_sse_event(first_event, {}, None)
    assert in_progress_calls == {}

    reasoning_events = [event_item for event_item in stream_events if isinstance(event_item, ReasoningDeltaEvent)]
    tool_streaming_events = [
        event_item for event_item in stream_events if isinstance(event_item, ToolCallStreamingEvent)
    ]
    tool_start_events = [event_item for event_item in stream_events if isinstance(event_item, ToolCallStartEvent)]
    tool_done_events = [event_item for event_item in stream_events if isinstance(event_item, ToolCallDoneEvent)]
    text_events = [event_item for event_item in stream_events if isinstance(event_item, TextDeltaEvent)]

    assert len(tool_streaming_events) == 0
    assert len(tool_start_events) == 0
    assert len(tool_done_events) == 0

    accumulated_reasoning = "".join(event_item.delta for event_item in reasoning_events)
    assert "<tool_call>" in accumulated_reasoning
    assert "</tool_call>" in accumulated_reasoning
    assert "No, let's wait." in accumulated_reasoning

    accumulated_text = "".join(event_item.delta for event_item in text_events)
    assert accumulated_text == "Now I will speak."
    assert parsing_state["mode"] == "text"


def test_special_tags_inside_thoughts_do_not_exit_early() -> None:
    """Special tags like <think> or <tool_call> inside thoughts must remain inside thoughts."""
    chunk_one = ServerSentEvent(
        event="message",
        data=json.dumps(
            {"choices": [{"delta": {"content": "<think>Thinking about <think> nested tag and <tool_call> tag </"}}]}
        ),
        id="1",
    )
    chunk_two = ServerSentEvent(
        event="message",
        data=json.dumps({"choices": [{"delta": {"content": "think>End of thoughts."}}]}),
        id="2",
    )

    in_progress_calls: dict = {}
    parsing_state = None

    events_one, in_progress_calls, parsing_state = parse_sse_event(chunk_one, in_progress_calls, parsing_state)
    events_two, in_progress_calls, parsing_state = parse_sse_event(chunk_two, in_progress_calls, parsing_state)

    combined_events = events_one + events_two
    tool_start_events = [event_item for event_item in combined_events if isinstance(event_item, ToolCallStartEvent)]
    assert len(tool_start_events) == 0

    reasoning_events = [event_item for event_item in combined_events if isinstance(event_item, ReasoningDeltaEvent)]
    accumulated_reasoning = "".join(event_item.delta for event_item in reasoning_events)
    assert "<think>" in accumulated_reasoning
    assert "<tool_call>" in accumulated_reasoning

    text_events = [event_item for event_item in combined_events if isinstance(event_item, TextDeltaEvent)]
    accumulated_text = "".join(event_item.delta for event_item in text_events)
    assert accumulated_text == "End of thoughts."


def test_parse_text_tool_calls_ignores_calls_in_thoughts() -> None:
    """parse_text_tool_calls should ignore tool calls inside thought blocks."""
    raw_thought_text = (
        "<think>Let me think: "
        "<tool_call><function=ignored_tool><parameter=param>val</parameter></function></tool_call>"
        "</think>"
        "<tool_call><function=actual_tool><parameter=param>val</parameter></function></tool_call>"
    )
    results = parse_text_tool_calls(raw_thought_text)
    assert len(results) == 1
    assert results[0].name == "actual_tool"


def test_tool_call_streaming_fires_on_gemma_tag() -> None:
    """Opening Gemma 4 tool call tag <|tool_call> must emit ToolCallStreamingEvent immediately."""
    first_event = ServerSentEvent(
        event="message",
        data=json.dumps({"choices": [{"delta": {"content": "Calling tool:\n<|tool_call>call:bash"}}]}),
        id="1",
    )
    stream_events, _in_progress_calls, _parsing_state = parse_sse_event(first_event, {}, None)
    tool_streaming_events = [
        event_item for event_item in stream_events if isinstance(event_item, ToolCallStreamingEvent)
    ]
    assert len(tool_streaming_events) == 1


async def test_status_panel_tool_light_on_streaming_event() -> None:
    """Status panel tool indicator lights up when ToolCallStreamingEvent is received."""
    from unittest.mock import MagicMock

    from dendrophis.events import EventBus, ToolExecutionFinishedEvent
    from dendrophis.ui.widgets.panels.status_panel import StatusPanel

    event_bus = EventBus()
    mock_session = MagicMock()
    status_panel = StatusPanel(session=mock_session, event_bus=event_bus)
    status_panel._events = event_bus.bind(status_panel)

    # Initial state: tool calling is off
    assert not status_panel._tool_calling
    assert "○[/] Tool" in status_panel.render_value()

    # Tool call tag seen: streaming event arrives
    status_panel._on_tool_call_streaming(ToolCallStreamingEvent())
    assert status_panel._tool_calling
    rendered_output = status_panel.render_value()
    assert "●[/] Tool" in rendered_output

    # Tool execution completes: tool calling turns off
    status_panel._on_tool_execution_finished(ToolExecutionFinishedEvent(tool_name="test_tool", success=True))
    assert not status_panel._tool_calling
    assert "○[/] Tool" in status_panel.render_value()


# --- Fenced JSON fallback (parse_text_tool_calls) ---


def test_parse_text_tool_calls_fenced_json_nested_braces() -> None:
    """Fenced JSON with nested argument objects parses fully."""
    payload = {
        "name": "invoke_subagent",
        "arguments": {"agent": "researcher", "context": {"files": ["a.py"], "depth": "quick"}},
    }
    text = f"Sure:\n```json\n{json.dumps(payload)}\n```\nDone."
    results = parse_text_tool_calls(text)
    assert len(results) == 1
    assert results[0].name == "invoke_subagent"
    assert json.loads(results[0].arguments) == payload["arguments"]


def test_parse_text_tool_calls_fenced_json_braces_in_content() -> None:
    """Braces and code fences inside a string value must not truncate the JSON."""
    payload = {
        "name": "write",
        "arguments": {
            "file_path": "a.md",
            "content": "snippet } ```python\ncode\n``` end",
        },
    }
    results = parse_text_tool_calls(f"```json\n{json.dumps(payload)}\n```")
    assert len(results) == 1
    assert results[0].name == "write"
    assert json.loads(results[0].arguments) == payload["arguments"]


def test_parse_text_tool_calls_fenced_json_missing_closing_fence() -> None:
    """A missing closing fence is tolerated."""
    payload = {"name": "read", "arguments": {"file_path": "a"}}
    results = parse_text_tool_calls(f"```json\n{json.dumps(payload)}")
    assert len(results) == 1
    assert results[0].name == "read"


def test_parse_text_tool_calls_fenced_json_multiple_blocks() -> None:
    """Multiple fenced blocks each parse exactly once (no duplicates)."""
    p1 = {"name": "read", "arguments": {"file_path": "a"}}
    p2 = {"name": "read", "arguments": {"file_path": "b"}}
    text = f"```json\n{json.dumps(p1)}\n```\nand then\n```json\n{json.dumps(p2)}\n```"
    results = parse_text_tool_calls(text)
    assert len(results) == 2
    assert [json.loads(r.arguments) for r in results] == [p1["arguments"], p2["arguments"]]


def test_parse_text_tool_calls_fenced_json_non_tool_ignored() -> None:
    """Fenced JSON that is not a tool call is ignored."""
    assert parse_text_tool_calls('```json\n{"foo": "bar"}\n```') == []


def test_parse_text_tool_calls_no_fence_ignored() -> None:
    """JSON in plain prose (no fence) is not treated as a tool call."""
    assert parse_text_tool_calls('plain text with {"name": "x", "arguments": {}} inside') == []


# --- Thinking-strip behavior (parse_text_tool_calls) ---


def test_parse_text_tool_calls_strips_thinking_block() -> None:
    """A closed thinking block is stripped; the tool call after it parses."""
    tc_xml = "<function=read><parameter=file_path>a</parameter></function>"
    text = (
        "<"
        + "think"
        + ">"
        + "I will read a file"
        + "</"
        + "think"
        + ">"
        + "<"
        + "tool_call"
        + ">"
        + tc_xml
        + "</"
        + "tool_call"
        + ">"
    )
    results = parse_text_tool_calls(text)
    assert len(results) == 1
    assert results[0].name == "read"


def test_parse_text_tool_calls_unclosed_thinking_swallows_rest() -> None:
    """An unclosed thinking opener swallows the rest; nothing is executed."""
    tc_xml = "<function=read><parameter=file_path>a</parameter></function>"
    text = "<" + "think" + ">" + "\nI should call " + "<" + "tool_call" + ">" + tc_xml
    assert parse_text_tool_calls(text) == []


def test_parse_text_tool_calls_strips_thought_tags() -> None:
    """The <thought> variant is stripped."""
    tc_xml = "<function=read><parameter=file_path>a</parameter></function>"
    text = "<thought>reasoning</thought>" + "<" + "tool_call" + ">" + tc_xml + "</" + "tool_call" + ">"
    results = parse_text_tool_calls(text)
    assert len(results) == 1
    assert results[0].name == "read"


def test_parse_text_tool_calls_strips_channel_thought() -> None:
    """The <|channel>thought variant is stripped."""
    tc_xml = "<function=read><parameter=file_path>a</parameter></function>"
    text = "<|channel>thought\nlet me think\n<channel|>" + "<" + "tool_call" + ">" + tc_xml + "</" + "tool_call" + ">"
    results = parse_text_tool_calls(text)
    assert len(results) == 1
    assert results[0].name == "read"


# --- Dedup helpers (shared by streaming and batch decode paths) ---


def test_tool_arguments_from_payload_explicit_arguments() -> None:
    """An explicit "arguments" key is serialized verbatim."""
    assert _tool_arguments_from_payload({"name": "x", "arguments": {"a": 1}}) == '{"a": 1}'


def test_tool_arguments_from_payload_fallback_to_other_keys() -> None:
    """Without an "arguments" key, every other top-level key becomes an argument."""
    assert _tool_arguments_from_payload({"name": "x", "a": 1, "b": "c"}) == '{"a": 1, "b": "c"}'


def test_tool_arguments_from_payload_non_dict_arguments() -> None:
    """Non-dict arguments are stringified, never JSON-dumped."""
    assert _tool_arguments_from_payload({"name": "x", "arguments": "raw"}) == "raw"
    assert _tool_arguments_from_payload({"name": "x", "arguments": [1, 2]}) == "[1, 2]"


def test_decode_tool_call_payload_xml() -> None:
    """The XML function format decodes to (name, json-serialized args)."""
    decoded = _decode_tool_call_payload("<function=bash><parameter=command>ls</parameter></function>")
    assert decoded == ("bash", '{"command": "ls"}')


def test_decode_tool_call_payload_json_with_arguments() -> None:
    decoded = _decode_tool_call_payload('{"name": "bash", "arguments": {"command": "ls"}}')
    assert decoded == ("bash", '{"command": "ls"}')


def test_decode_tool_call_payload_json_without_arguments_key() -> None:
    """A JSON object with no "arguments" key falls back to the other keys."""
    decoded = _decode_tool_call_payload('{"name": "bash", "command": "ls"}')
    assert decoded == ("bash", '{"command": "ls"}')


def test_decode_tool_call_payload_json_nameless_returns_none_name() -> None:
    """A JSON object without a "name" key yields a None name (caller decides)."""
    decoded = _decode_tool_call_payload('{"arguments": {"a": 1}}')
    assert decoded is not None
    assert decoded[0] is None


def test_decode_tool_call_payload_invalid_json_is_none() -> None:
    assert _decode_tool_call_payload("not a tool call at all") is None


def test_decode_tool_call_payload_json_list_is_none() -> None:
    """A JSON array is not a tool-call object — returns None (no crash)."""
    assert _decode_tool_call_payload("[1, 2, 3]") is None


def test_decode_tool_call_payload_json_scalar_is_none() -> None:
    """A JSON scalar is not a tool-call object — returns None (no crash)."""
    assert _decode_tool_call_payload('"just a string"') is None
    assert _decode_tool_call_payload("42") is None


def test_streaming_and_batch_decode_agree() -> None:
    """Both decode paths must agree on a well-formed payload (dedup invariant)."""
    payload = '{"name": "read_file", "arguments": {"file_path": "a.txt"}}'
    assert _decode_tool_call_payload(payload) == ("read_file", '{"file_path": "a.txt"}')
    batch = parse_text_tool_calls("<tool_call>" + payload + "</tool_call>")
    assert len(batch) == 1
    assert batch[0].name == "read_file"
    assert json.loads(batch[0].arguments) == {"file_path": "a.txt"}


def test_streaming_nameless_payload_defaults_to_unknown() -> None:
    """Streaming path: a nameless JSON payload surfaces as an "unknown" tool."""
    from dendrophis.llm.tag_parser import _emit_tool_call_events

    events = _emit_tool_call_events('{"arguments": {"a": 1}}')
    starts = [event for event in events if isinstance(event, ToolCallStartEvent)]
    assert len(starts) == 1
    assert starts[0].name == "unknown"


def test_batch_nameless_payload_is_skipped() -> None:
    """Batch path: a nameless JSON payload is skipped (no tool to dispatch to)."""
    assert parse_text_tool_calls("<tool_call>" + '{"arguments": {"a": 1}}' + "</tool_call>") == []


# --- Dual start/close boundary tag (<tool_call>) ---


def test_dual_boundary_tag_opens_and_closes_streaming() -> None:
    """A model that uses the same token as both open and close delimiter.

    ``<tool_call|>`` is in both TOOL_START_TAGS and TOOL_CLOSE_TAGS. The first
    occurrence opens the call (text -> tool_calling); the next one, found while
    a call is open, resolves as a *close* because close tags are matched first.
    """
    first_event = ServerSentEvent(
        event="message",
        data=json.dumps(
            {"choices": [{"delta": {"content": '<tool_call|>{"name": "bash", "arguments": {"command": "ls"}}'}}]}
        ),
        id="1",
    )
    second_event = ServerSentEvent(
        event="message",
        data=json.dumps({"choices": [{"delta": {"content": "<tool_call|>"}}]}),
        id="2",
    )

    in_progress: dict = {}
    state = None

    events_1, in_progress, state = parse_sse_event(first_event, in_progress, state)
    # Opening tag detected -> streaming signal fires; args still buffering.
    assert [type(event) for event in events_1] == [ToolCallStreamingEvent]

    events_2, in_progress, state = parse_sse_event(second_event, in_progress, state)
    starts = [event for event in events_2 if isinstance(event, ToolCallStartEvent)]
    dones = [event for event in events_2 if isinstance(event, ToolCallDoneEvent)]
    assert len(starts) == 1
    assert starts[0].name == "bash"
    assert len(dones) == 1
    assert json.loads(dones[0].tool_call.arguments) == {"command": "ls"}
    # Back in text mode after the closing boundary tag.
    assert state["mode"] == "text"
