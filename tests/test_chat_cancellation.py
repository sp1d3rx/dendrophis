"""F10: ChatOrchestrator.send_message must propagate task-level cancellation.

A task-level cancel (task.cancel(), loop teardown, web/server.py stop()) delivers
asyncio.CancelledError into the running turn. send_message must emit its user-facing
events (ErrorEvent("Streaming cancelled") + WaitingForInputEvent) and then RE-RAISE,
so the task reports as cancelled (task.cancelled() is True) instead of silently
reporting success. The cooperative Stop-button path (cancel_flag) is unaffected.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.events import ErrorEvent, WaitingForInputEvent
from dendrophis.session.chat import ChatOrchestrator


class _Recorder:
    """Event-bus stub that records published events in order."""

    def __init__(self) -> None:
        self.events: list = []

    def publish(self, event) -> None:
        self.events.append(event)


class _StubContext:
    """Minimal ContextManager stand-in for the send_message pre-stream path."""

    def __init__(self) -> None:
        self.user_messages: list[str] = []

    def append_user(self, text) -> None:
        self.user_messages.append(text)

    def needs_compaction(self) -> bool:
        return False


def _config() -> DendrophisConfig:
    cfg = DendrophisConfig(llm=LLMConfig(model="test-model", base_url="http://127.0.0.1:9/v1", api_key="k"))
    cfg.caching.enabled = False  # skip understanding / file-cache side paths
    return cfg


def _orchestrator(recorder: _Recorder, completion_loop) -> ChatOrchestrator:
    orchestrator = ChatOrchestrator(
        context=_StubContext(),
        llm=MagicMock(),
        stats=MagicMock(),
        config=_config(),
        event_bus=recorder,
        understanding_detector=MagicMock(),
        tool_registry=MagicMock(),
        tool_executor_session=MagicMock(),
        skill_manager=None,
        compactor=MagicMock(),
    )
    # Replace the real completion loop with a test-controlled coroutine so the
    # turn can be parked at a known await point (the streaming section).
    orchestrator._run_completion_loop = completion_loop
    return orchestrator


async def test_task_level_cancel_propagates_and_emits() -> None:
    recorder = _Recorder()
    started = asyncio.Event()

    async def blocking_loop() -> None:
        started.set()
        await asyncio.Event().wait()  # block until the task is cancelled

    orchestrator = _orchestrator(recorder, blocking_loop)

    task = asyncio.create_task(orchestrator.send_message("hello"))
    await asyncio.wait_for(started.wait(), timeout=2)  # ensure we're at the streaming await
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled() is True, "cancel must be reported, not swallowed as success"
    assert orchestrator.is_streaming() is False, "streaming flag must be reset on cancel"

    messages = [event.message for event in recorder.events if isinstance(event, ErrorEvent)]
    assert "Streaming cancelled" in messages
    assert any(isinstance(event, WaitingForInputEvent) for event in recorder.events)


async def test_normal_completion_is_not_cancelled() -> None:
    """Guard: the re-raise must not leak into the clean (non-cancelled) path."""
    recorder = _Recorder()

    async def instant_loop() -> None:
        return None

    orchestrator = _orchestrator(recorder, instant_loop)

    task = asyncio.create_task(orchestrator.send_message("hello"))
    await task  # must complete cleanly, no exception

    assert task.cancelled() is False
    assert orchestrator.is_streaming() is False
    assert not any(isinstance(event, ErrorEvent) for event in recorder.events)
    assert any(isinstance(event, WaitingForInputEvent) for event in recorder.events)
