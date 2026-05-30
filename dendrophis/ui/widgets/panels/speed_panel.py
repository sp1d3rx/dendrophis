"""SpeedPanel — displays tokens per second and TTFT."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.widgets import Sparkline, Static

from dendrophis.events import (
    EventBus,
    StatsUpdatedEvent,
    StreamingStartedEvent,
)
from dendrophis.ui.widgets.panels.event_panel import EventPanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class SpeedPanel(EventPanel):
    """Panel showing generation speed metrics."""

    DEFAULT_CSS = """
    SpeedPanel Sparkline {
        height: 1;
        margin-top: 1;
        color: #f9e2af; /* Catppuccin Yellow */
    }
    """

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        self._tps_history: list[float] = []
        self._label = Static()
        self._sparkline = Sparkline(data=[0.0], summary_function=max)
        # Local cache of stats
        self._tokens_per_sec: float = 0.0
        self._time_to_first_token: float = 0.0

    def compose(self) -> ComposeResult:
        yield self._label
        yield self._sparkline

    def on_mount(self) -> None:
        super().on_mount()
        self._handlers = [
            (StatsUpdatedEvent, self._on_stats_updated),
            (StreamingStartedEvent, self._on_streaming_started),
        ]
        for event_type, handler in self._handlers:
            self._event_bus.subscribe(event_type, handler)
        # Initialize from current session state
        self._tokens_per_sec = self._session.stats.tokens_per_sec
        self._time_to_first_token = self._session.stats.time_to_first_token
        self.update_display()

    def on_unmount(self) -> None:
        """Unsubscribe to prevent memory leaks."""
        for event_type, handler in self._handlers:
            self._event_bus.unsubscribe(event_type, handler)
        self._handlers.clear()

    def _on_streaming_started(self, event: StreamingStartedEvent) -> None:
        """Reset history when a new generation starts."""
        self._tps_history = []
        self._sparkline.data = [0.0]

    def _on_stats_updated(self, event: StatsUpdatedEvent) -> None:
        """Update history and display when stats change."""
        if event.tokens_per_sec > 0:
            self._tps_history.append(event.tokens_per_sec)
            self._tps_history = self._tps_history[-30:]
            self._sparkline.data = self._tps_history
        # Update local cache
        self._tokens_per_sec = event.tokens_per_sec
        self._time_to_first_token = event.time_to_first_token
        self.update_display()

    def update_display(self) -> None:
        """Refresh the speed display."""
        self._label.update(
            f"TPS: [#f9e2af]{self._tokens_per_sec:.1f}[/]\nTTFT: [#f9e2af]{self._time_to_first_token:.2f}s[/]"
        )
