"""Behavior-pinning tests for MainScreenEventHandler replay + screen-push logic.

These pin the extracted helpers from the 2026-10-20 maintainability pass:
- _replay_context / _replay_assistant_message / _replay_tool_message / _message_text
- _push_screen_later (factory runs at push time, not schedule time)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dendrophis.ui.screens.main_events import MainScreenEventHandler


class FakeDebug:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, line: str) -> None:
        self.lines.append(line)


class FakeChat:
    """Records every ChatView call made by the replay logic."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add_user_message(self, content: str) -> None:
        self.calls.append(("user", content))

    def start_assistant_message(self, loading: bool = True) -> None:
        self.calls.append(("assistant_start", loading))

    def append_reasoning_delta(self, reasoning: str) -> None:
        self.calls.append(("reasoning", reasoning))

    def append_text_delta(self, text: str) -> None:
        self.calls.append(("text", text))

    def add_tool_status(
        self,
        tool_name: str,
        description: str,
        arguments: str,
        index: int,
        tool_call_id: str | None = None,
    ) -> None:
        self.calls.append(("tool_status", tool_name, arguments, index, tool_call_id))

    def finish_assistant_message(self) -> None:
        self.calls.append(("assistant_finish",))

    def add_tool_result(
        self,
        name: str,
        content: str,
        description: str,
        arguments: str,
        consecutive_failures: int,
        tool_call_id: str | None = None,
    ) -> None:
        self.calls.append(("tool_result", name, content, arguments, consecutive_failures, tool_call_id))

    def add_system_message(self, content: str) -> None:
        self.calls.append(("system", content))


def make_host(messages: list) -> MainScreenEventHandler:
    """Build a mixin instance without running __init__ (it's a mixin)."""
    host = MainScreenEventHandler.__new__(MainScreenEventHandler)
    host._session = SimpleNamespace(context=SimpleNamespace(messages=messages))
    host._debug_widget = FakeDebug()
    return host


def test_replay_mixed_conversation_order_and_tool_matching() -> None:
    messages = [
        {"role": "system", "content": "you are dex"},
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "let me check",
            "reasoning_content": "thinking...",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "bash", "arguments": '{"cmd": "ls"}'}},
                {"id": "call_2", "function": {"name": "read", "arguments": '{"path": "a.py"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "bash", "content": "file.txt"},
        {"role": "tool", "tool_call_id": "call_2", "name": "read", "content": "print(1)"},
    ]
    host = make_host(messages)
    chat = FakeChat()
    host._replay_context(chat)

    assert chat.calls == [
        ("system", "you are dex"),
        ("user", "hello"),
        ("assistant_start", False),
        ("reasoning", "thinking..."),
        ("text", "let me check"),
        ("tool_status", "bash", '{"cmd": "ls"}', 0, "call_1"),
        ("tool_status", "read", '{"path": "a.py"}', 1, "call_2"),
        ("assistant_finish",),
        ("tool_result", "bash", "file.txt", '{"cmd": "ls"}', 0, "call_1"),
        ("tool_result", "read", "print(1)", '{"path": "a.py"}', 0, "call_2"),
    ]
    assert host._debug_widget.lines == []


def test_replay_tool_result_without_pending_call_uses_empty_arguments() -> None:
    messages = [
        {"role": "tool", "tool_call_id": "call_orphan", "name": "bash", "content": "orphan result"},
    ]
    host = make_host(messages)
    chat = FakeChat()
    host._replay_context(chat)

    assert chat.calls == [("tool_result", "bash", "orphan result", "", 0, "call_orphan")]


def test_replay_tool_result_reads_raw_content_not_list_joined() -> None:
    # Tool results keep the raw content value (original behavior); only
    # user/assistant/system messages go through _message_text list-joining.
    messages = [
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": ["raw", "list"]},
    ]
    host = make_host(messages)
    chat = FakeChat()
    host._replay_context(chat)

    assert chat.calls == [("tool_result", "bash", ["raw", "list"], "", 0, "c1")]


def test_replay_malformed_message_is_logged_and_replay_continues() -> None:
    messages = [
        "garbage-not-a-dict",
        {"role": "user", "content": "still works"},
    ]
    host = make_host(messages)
    chat = FakeChat()
    host._replay_context(chat)

    assert ("user", "still works") in chat.calls
    assert any(line.startswith("MESSAGE REPLAY ERROR: AttributeError") for line in host._debug_widget.lines)
    assert any(line.startswith("TRACEBACK:") for line in host._debug_widget.lines)


def test_message_text_variants() -> None:
    mt = MainScreenEventHandler._message_text
    assert mt({"content": "plain"}) == "plain"
    assert mt({"content": None}) == ""
    assert mt({}) == ""
    assert mt({"content": [{"text": "a"}, "junk", {"text": "b"}]}) == "a b"
    assert mt({"content": [{"text": "only"}]}) == "only"


class FakeApp:
    def __init__(self) -> None:
        self.pushed: list = []

    def push_screen(self, screen) -> None:
        self.pushed.append(screen)


class FakeScreen:
    pass


def test_push_screen_later_constructs_at_push_time_not_schedule_time() -> None:
    host = MainScreenEventHandler.__new__(MainScreenEventHandler)
    host.app = FakeApp()
    scheduled: list = []
    host.call_later = scheduled.append

    constructed: list = []

    def factory() -> FakeScreen:
        constructed.append(1)
        return FakeScreen()

    host._push_screen_later(factory)
    assert constructed == []  # NOT constructed at schedule time
    assert len(scheduled) == 1

    scheduled[0]()  # simulate the UI-thread callback
    assert constructed == [1]
    assert len(host.app.pushed) == 1
    assert isinstance(host.app.pushed[0], FakeScreen)


def test_dead_noop_handlers_removed() -> None:
    assert not hasattr(MainScreenEventHandler, "_on_tool_call_delta")
    assert not hasattr(MainScreenEventHandler, "_on_tool_execution_finished")


def test_all_screen_push_handlers_use_shared_helper() -> None:
    import inspect

    for name in (
        "_on_tool_confirmation_request",
        "_on_multiple_choice_request",
        "_on_edit_proposal",
        "_on_write_proposal",
        "_on_append_proposal",
        "_on_python_exec_proposal",
        "_on_primer_screen_request",
    ):
        source = inspect.getsource(getattr(MainScreenEventHandler, name))
        assert "_push_screen_later" in source, f"{name} does not use the shared helper"
        assert "call_later" not in source.replace("_push_screen_later", ""), f"{name} still calls call_later directly"
        assert "push_screen" not in source.replace("_push_screen_later", ""), f"{name} still pushes screens directly"


def test_compaction_events_rendered_as_inline_tool_calls() -> None:
    from dendrophis.events import (
        CompactionCompletedEvent,
        CompactionFailedEvent,
        CompactionStartedEvent,
    )
    from dendrophis.ui.widgets.chat_view import _format_tool_args

    host = make_host([])
    fake_chat = FakeChat()
    host.query_one = lambda _widget_type: fake_chat

    host._on_compaction_started(CompactionStartedEvent(original_token_count=112345, threshold=0.85))
    assert len(fake_chat.calls) == 1
    status_call = fake_chat.calls[0]
    assert status_call[0] == "tool_status"
    assert status_call[1] == "compact_context"
    compaction_call_id = status_call[4]
    assert compaction_call_id is not None
    assert compaction_call_id.startswith("compact_")
    assert "112,345 tokens" in _format_tool_args("compact_context", status_call[2])

    host._on_compaction_completed(
        CompactionCompletedEvent(
            previous_token_count=112345,
            new_token_count=42100,
            tokens_reclaimed=70245,
            summary="Compacted summary text",
        )
    )
    assert len(fake_chat.calls) == 2
    result_call = fake_chat.calls[1]
    assert result_call[0] == "tool_result"
    assert result_call[1] == "compact_context"
    assert result_call[2] == "Compacted summary text"
    assert result_call[4] == 0
    assert result_call[5] == compaction_call_id
    formatted_result = _format_tool_args("compact_context", result_call[3], result_call[2])
    assert "112,345 → 42,100" in formatted_result
    assert "-70,245 reclaimed" in formatted_result

    host._on_compaction_started(CompactionStartedEvent(original_token_count=50000, threshold=0.85))
    second_call_id = fake_chat.calls[2][4]
    host._on_compaction_failed(CompactionFailedEvent(reason="No messages to compact"))
    failed_call = fake_chat.calls[3]
    assert failed_call[0] == "tool_result"
    assert failed_call[1] == "compact_context"
    assert failed_call[2] == "No messages to compact"
    assert failed_call[4] == 1
    assert failed_call[5] == second_call_id
    assert "No messages to compact" in _format_tool_args("compact_context", failed_call[3])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
