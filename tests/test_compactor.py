"""Tests for context compaction."""

import pytest

from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.events import ReasoningDeltaEvent, TextDeltaEvent
from dendrophis.llm.compactor import _messages_to_text, compact


@pytest.mark.parametrize("dropped_role", ["system", "tool"])
def test_messages_to_text_excludes_roles(dropped_role: str) -> None:
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": dropped_role, "content": "content that should be omitted"},
        {"role": "assistant", "content": "Hi there"},
        {"role": "user", "content": "Thanks"},
    ]
    text = _messages_to_text(messages)
    assert "content that should be omitted" not in text
    assert "Hello" in text
    assert "Hi there" in text
    assert "Thanks" in text


def test_messages_to_text_handles_malformed_messages() -> None:
    messages = [
        None,
        {"role": "user"},
        {"content": "no role"},
        {"role": "assistant", "content": ["text", {"text": "list content"}]},
        {"role": "user", "content": 123},
        {"role": "user", "content": "x" * 20000},
    ]
    text = _messages_to_text(messages)
    assert "no role" in text
    assert "list content" in text
    assert "123" in text
    assert text.count("x") == 10001  # 10000 kept + 1 in "[truncated]"
    assert "[truncated]" in text


@pytest.mark.anyio
async def test_compact_ignores_reasoning_deltas() -> None:
    """Reasoning/thinking tokens must not leak into the compacted summary."""

    class FakeLLM:
        async def stream_chat(self, messages, tools=None):
            yield ReasoningDeltaEvent(delta="The user wants me to summarize...")
            yield TextDeltaEvent(delta="Final summary text.")

    config = DendrophisConfig()
    context = ContextManager(config)
    context.messages = [
        {"role": "system", "content": "sys"},
        *[
            {"role": role, "content": content}
            for i in range(8)
            for role, content in [("user", f"u{i}"), ("assistant", f"a{i}")]
        ],
    ]

    result = await compact(context, FakeLLM())

    assert result["compacted"] is True
    summary_msg = context.messages[1]
    assert summary_msg["role"] == "assistant"
    assert "The user wants me to summarize" not in summary_msg["content"]
    assert "Final summary text" in summary_msg["content"]


@pytest.mark.anyio
async def test_compact_short_history() -> None:
    """Test compaction with a short history to verify dynamic tail_count scaling."""

    class FakeLLM:
        async def stream_chat(self, messages, tools=None):
            yield TextDeltaEvent(delta="Compacted summary.")

    config = DendrophisConfig()
    context = ContextManager(config)
    # Total of 6 messages (3 turns). TAIL_TURNS * 2 = 12, so this history is short.
    context.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "how are you?"},
        {"role": "assistant", "content": "good"},
        {"role": "user", "content": "last user message"},
    ]

    result = await compact(context, FakeLLM())

    assert result["compacted"] is True
    # Verify that the history was compacted
    assert result["messages_compacted"] > 0
    # Summary message is at index 1
    assert "Compacted summary." in context.messages[1]["content"]