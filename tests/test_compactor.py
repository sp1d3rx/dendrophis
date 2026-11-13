"""Tests for context compaction."""

import json

import pytest

from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.events import ReasoningDeltaEvent, TextDeltaEvent
from dendrophis.llm.compactor import SUMMARY_PROMPT, _messages_to_text, compact


def test_messages_to_text_excludes_system_and_raw_read_bodies() -> None:
    file_read_payload = json.dumps({"type": "file", "path": "src/model.py", "content": "RAW_FILE_BODY_SECRET"})
    unchanged_stub_payload = json.dumps({"status": "unchanged_since_last_read", "path": "src/model.py"})
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "system", "content": "system instructions that should be omitted"},
        {"role": "tool", "name": "read", "content": file_read_payload},
        {"role": "tool", "name": "read", "content": unchanged_stub_payload},
        {"role": "assistant", "content": "Hi there"},
        {"role": "user", "content": "Thanks"},
    ]
    text = _messages_to_text(messages)
    assert "system instructions that should be omitted" not in text
    assert "RAW_FILE_BODY_SECRET" not in text
    assert "unchanged_since_last_read" not in text
    assert "[TOOL:read] path=src/model.py" in text
    assert "Hello" in text
    assert "Hi there" in text
    assert "Thanks" in text


def test_messages_to_text_retains_verifiable_tool_outcomes_and_tail_metrics() -> None:
    long_training_stdout = "epoch=1 loss=0.95\n" + ("progress_line\n" * 200) + "FINAL val_score=0.842 passed=18/18"
    bash_payload = json.dumps({"success": True, "returncode": 0, "stdout": long_training_stdout, "stderr": ""})
    error_payload = json.dumps({"error": "old_string not found in solver.py"})
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "bash", "arguments": '{"command": "pytest -q"}'}},
            ],
        },
        {"role": "tool", "name": "bash", "tool_call_id": "call_1", "content": bash_payload},
        {"role": "tool", "name": "edit", "tool_call_id": "call_2", "content": error_payload},
    ]
    text = _messages_to_text(messages)
    assert "Tool calls: bash(" in text
    assert "returncode=0" in text
    assert "epoch=1 loss=0.95" in text
    assert "FINAL val_score=0.842 passed=18/18" in text
    assert "[TOOL:edit ERROR]" in text
    assert "old_string not found in solver.py" in text


def test_summary_prompt_is_arex_friendly() -> None:
    assert "propose -> measure -> reflect -> revise" in SUMMARY_PROMPT
    assert "Verifiable Feedback & Metrics" in SUMMARY_PROMPT
    assert "Attempted Hypotheses & Dead-Ends" in SUMMARY_PROMPT
    assert "best result achieved so far" in SUMMARY_PROMPT


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
