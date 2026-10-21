"""Tests for SpeedPanel session-average TPS tracking."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from dendrophis.events import (
    EventBus,
    SessionResetRequest,
    StatsUpdatedEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
)
from dendrophis.ui.widgets.panels.speed_panel import SpeedPanel


@pytest.fixture
def event_bus() -> EventBus:
    bus = EventBus()
    yield bus
    bus.shutdown(wait=True)


def _make_panel(event_bus: EventBus) -> SpeedPanel:
    """Create a SpeedPanel wired to the given bus without mounting it."""
    session = MagicMock()
    session.stats.tokens_per_sec = 0.0
    session.stats.time_to_first_token = 0.0
    panel = SpeedPanel(session, event_bus)
    panel._events = event_bus.bind(panel)
    panel._tokens_per_sec = session.stats.tokens_per_sec
    panel._time_to_first_token = session.stats.time_to_first_token
    return panel


def _invoke(panel: SpeedPanel, event: Any) -> None:
    """Synchronously deliver an event to the panel's bound listeners.

    EventBus.publish dispatches through a thread pool / event loop, which is
    non-deterministic for assertions; this drives the registered handlers
    directly in order.
    """
    for subscription in panel._events._subscriptions:
        if subscription.event_type is type(event) and not subscription.is_async:
            subscription.handler(event)


def _stats_event(tokens_per_sec: float) -> StatsUpdatedEvent:
    return StatsUpdatedEvent(
        prompt_tokens=100,
        completion_tokens=50,
        total_cost_usd=0.01,
        tokens_per_sec=tokens_per_sec,
        time_to_first_token=0.5,
    )


def _run_turn(panel: SpeedPanel, samples: float) -> None:
    """Simulate a full streaming turn: start, stats emits, finish."""
    _invoke(panel, StreamingStartedEvent(user_message="hi"))
    for sample in samples:
        _invoke(panel, _stats_event(sample))
    _invoke(panel, StreamingFinishedEvent())


def test_session_average_tracks_turn_means(event_bus: EventBus) -> None:
    """The session average is the turn-weighted mean of per-turn TPS."""
    panel = _make_panel(event_bus)

    # Turn 1: samples averaging 20.0
    _run_turn(panel, (20.0, 20.0, 20.0))
    # Turn 2: samples averaging 10.0
    _run_turn(panel, (10.0, 10.0))
    # Turn 3: samples averaging 30.0
    _run_turn(panel, (30.0, 30.0, 30.0))

    assert panel._session_turn_count == 3
    assert panel._avg_tokens_per_sec == 20.0  # (20 + 10 + 30) / 3


def test_zero_tps_stats_not_counted(event_bus: EventBus) -> None:
    """Idle stats emits (tokens_per_sec=0) must not pollute the turn samples."""
    panel = _make_panel(event_bus)

    _invoke(panel, StreamingStartedEvent(user_message="hi"))
    _invoke(panel, _stats_event(0.0))
    for sample in (20.0, 20.0):
        _invoke(panel, _stats_event(sample))
    _invoke(panel, StreamingFinishedEvent())

    assert panel._session_turn_count == 1
    assert panel._avg_tokens_per_sec == 20.0


def test_finalize_without_samples_is_noop(event_bus: EventBus) -> None:
    """Finishing a turn with no TPS samples must not count as a turn."""
    panel = _make_panel(event_bus)

    _invoke(panel, StreamingStartedEvent(user_message="hi"))
    _invoke(panel, StreamingFinishedEvent())

    assert panel._session_turn_count == 0
    assert panel._avg_tokens_per_sec == 0.0


def test_streaming_finished_folds_turn_into_average(event_bus: EventBus) -> None:
    """StreamingFinishedEvent is what folds a turn into the session average."""
    panel = _make_panel(event_bus)

    _invoke(panel, StreamingStartedEvent(user_message="hi"))
    _invoke(panel, _stats_event(20.0))
    assert panel._session_turn_count == 0
    assert panel._avg_tokens_per_sec == 0.0

    _invoke(panel, StreamingFinishedEvent())

    assert panel._session_turn_count == 1
    assert panel._avg_tokens_per_sec == 20.0


def test_session_reset_clears_average(event_bus: EventBus) -> None:
    """SessionResetRequest zeroes the session average and turn state."""
    panel = _make_panel(event_bus)

    _run_turn(panel, (20.0, 20.0))
    assert panel._session_turn_count == 1

    _invoke(panel, SessionResetRequest())

    assert panel._session_turn_count == 0
    assert panel._avg_tokens_per_sec == 0.0
    assert panel._turn_tps_sum == 0.0
    assert panel._turn_tps_samples == 0


def test_streaming_started_resets_turn_accumulators(event_bus: EventBus) -> None:
    """A new streaming segment starts a fresh turn sample window."""
    panel = _make_panel(event_bus)

    _invoke(panel, StreamingStartedEvent(user_message="hi"))
    _invoke(panel, _stats_event(20.0))
    assert panel._turn_tps_samples == 1

    _invoke(panel, StreamingStartedEvent(user_message="hi"))
    assert panel._turn_tps_samples == 0
    assert panel._turn_tps_sum == 0.0
