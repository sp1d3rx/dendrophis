from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from dendrophis.config.loader import ConfigLoader
from dendrophis.events import EventBus
from dendrophis.events.types import DoneEvent, StreamingFinishedEvent, StreamingStartedEvent, TextDeltaEvent
from dendrophis.session.factory import SessionFactory


async def test_session_cancellation(tmp_path: Path) -> None:
    config_file = tmp_path / "dendrophis.yaml"
    memory_db_path = tmp_path / "memory.db"
    config_file.write_text(
        f'llm:\n  base_url: "http://127.0.0.1:9/v1"\n  api_key: "test-key"\n  model: "test-model"\n'
        f'caching:\n  enabled: false\n  pr_enabled: false\nmemory_db: "{memory_db_path}"\n',
        encoding="utf-8",
    )
    config_loader = ConfigLoader.load(config_path=str(config_file))

    event_bus = EventBus()
    event_bus.set_event_loop(asyncio.get_running_loop())
    session = SessionFactory.create_session(config_loader, event_bus)

    async def fake_stream_chat(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        for chunk_index in range(50):
            yield TextDeltaEvent(delta=f"word{chunk_index} ")
            await asyncio.sleep(0.01)
        yield DoneEvent(finish_reason="stop")

    session.llm.stream_chat = fake_stream_chat  # type: ignore[method-assign]

    all_events: list[Any] = []
    text_received = asyncio.Event()

    def collect_events(event: Any) -> None:
        all_events.append(event)
        if isinstance(event, TextDeltaEvent) and len(all_events) >= 5:
            text_received.set()

    event_bus.subscribe(TextDeltaEvent, collect_events)
    event_bus.subscribe(StreamingStartedEvent, collect_events)
    event_bus.subscribe(StreamingFinishedEvent, collect_events)

    try:
        chat_task = asyncio.create_task(session.send_message("Tell me a long story about Python"))
        await asyncio.wait_for(text_received.wait(), timeout=2.0)
        session.cancel_streaming()
        await asyncio.wait_for(chat_task, timeout=2.0)

        text_events = [event for event in all_events if isinstance(event, TextDeltaEvent)]
        assert len(text_events) >= 4
        assert len(text_events) < 50
    finally:
        await session.aclose()
        event_bus.shutdown(wait=False)
