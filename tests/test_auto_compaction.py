"""Auto-compaction must fire MID-LOOP, not only at turn start.

Regressions covered:
1. A long agentic turn (stream -> tool calls -> tool results -> repeat) that
   pushes context past compaction_threshold between iterations must trigger
   compaction at the top of the next loop iteration.
2. If compaction keeps failing, a breaker caps attempts at 3 per turn instead
   of retrying every iteration.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.events import (
    ContextUpdatedEvent,
    ErrorEvent,
    ToolCall,
    TurnResult,
)
from dendrophis.session.chat import ChatOrchestrator

THRESHOLD_TOKENS = 850


class _Recorder:
    """Event-bus stub that records published events in order."""

    def __init__(self) -> None:
        self.events: list = []

    def publish(self, event) -> None:
        self.events.append(event)


class _FakeContext:
    """Minimal ContextManager stand-in whose context 'fills up' after a tool result.

    Token math: start at 100; +10 per user/assistant message; +900 per tool
    result. So after the first tool round, token_count = 100 + 10 + 10 + 900
    = 1020 >= THRESHOLD_TOKENS and needs_compaction() flips to True.
    """

    def __init__(self) -> None:
        self.messages: list[dict] = [{"role": "system", "content": "sys"}]
        self.token_count = 100
        self._turn_count = 0

    def append_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self.token_count += 10
        self._turn_count += 1

    def append_assistant(self, content: str, tool_calls=None, reasoning_content: str | None = None) -> None:
        self.messages.append({"role": "assistant", "content": content})
        self.token_count += 10

    def append_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content})
        self.token_count += 900

    def get_messages_for_api(self) -> list[dict]:
        return list(self.messages)

    def get_turn_count(self) -> int:
        return self._turn_count

    def needs_compaction(self) -> bool:
        return self.token_count >= THRESHOLD_TOKENS

    @property
    def token_pct(self) -> float:
        return self.token_count / 1_000


class _FakeExecutor:
    async def execute(self, tool_calls):
        return [SimpleNamespace(tool_call_id=call.id, name=call.name, content='{"ok": true}') for call in tool_calls]


class _FillingLLM:
    """Yields `tool_rounds` tool-call turns, then a final stop turn."""

    def __init__(self, tool_rounds: int) -> None:
        self.tool_rounds = tool_rounds
        self.calls = 0

    async def stream_chat(self, messages, tools=None, enable_cache_control=False):
        self.calls += 1
        if self.calls <= self.tool_rounds:
            yield TurnResult(
                text="",
                reasoning="",
                tool_calls=[ToolCall(index=0, id="call_1", name="read", arguments='{"path": "x"}')],
                finish_reason="tool_calls",
            )
        else:
            yield TurnResult(text="done", reasoning="", tool_calls=[], finish_reason="stop")


def _config() -> DendrophisConfig:
    cfg = DendrophisConfig(llm=LLMConfig(model="test-model", base_url="http://127.0.0.1:9/v1", api_key="k"))
    cfg.caching.enabled = False  # skip understanding / file-cache side paths
    return cfg


def _orchestrator(recorder: _Recorder, context: _FakeContext, llm, compactor) -> ChatOrchestrator:
    return ChatOrchestrator(
        context=context,
        llm=llm,
        stats=MagicMock(),
        config=_config(),
        event_bus=recorder,
        understanding_detector=None,
        tool_registry=None,
        tool_executor_session=_FakeExecutor(),
        skill_manager=None,
        compactor=compactor,
    )


@pytest.mark.anyio
async def test_auto_compaction_fires_mid_loop() -> None:
    """Context that fills past the threshold during a tool round must be compacted."""
    recorder = _Recorder()
    context = _FakeContext()
    llm = _FillingLLM(tool_rounds=1)

    compactor_calls = 0

    async def compactor(ctx, llm_client, enable_caching=False) -> dict:
        nonlocal compactor_calls
        compactor_calls += 1
        ctx.token_count = 100  # simulate successful compaction freeing tokens
        return {"compacted": True, "messages_compacted": 5, "kept_recent": 2}

    orchestrator = _orchestrator(recorder, context, llm, compactor)
    await orchestrator.send_message("go")

    assert compactor_calls == 1, "compaction must fire mid-loop when the threshold is crossed"
    assert context.token_count < THRESHOLD_TOKENS
    # A ContextUpdatedEvent must be emitted after the mid-loop compaction so the UI can refresh.
    assert any(isinstance(e, ContextUpdatedEvent) for e in recorder.events)
    assert not any(isinstance(e, ErrorEvent) for e in recorder.events)


@pytest.mark.anyio
async def test_auto_compaction_breaker_caps_failures() -> None:
    """A persistently failing compactor must not be retried on every loop iteration."""
    recorder = _Recorder()
    context = _FakeContext()
    llm = _FillingLLM(tool_rounds=5)  # long agentic turn: 6 loop iterations

    compactor_calls = 0

    async def failing_compactor(ctx, llm_client, enable_caching=False) -> dict:
        nonlocal compactor_calls
        compactor_calls += 1
        raise RuntimeError("summary LLM exploded")

    orchestrator = _orchestrator(recorder, context, llm, failing_compactor)
    await orchestrator.send_message("go")

    assert compactor_calls == 3, f"breaker must cap attempts at 3, got {compactor_calls}"
    failures = [e for e in recorder.events if isinstance(e, ErrorEvent) and "compaction" in e.message]
    assert len(failures) == 3
