"""SpeedPanel — displays tokens per second and TTFT."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.widgets import Sparkline, Static

from dendrophis.events import (
    EventBus,
    SessionResetRequest,
    StatsUpdatedEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
    listen,
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
        # Session-average TPS: turn-weighted mean of per-turn TPS values
        self._avg_tokens_per_sec: float = 0.0
        self._session_turn_count: int = 0
        # Current-turn TPS samples (accumulated from StatsUpdatedEvents)
        self._turn_tps_sum: float = 0.0
        self._turn_tps_samples: int = 0

    def compose(self) -> ComposeResult:
        yield self._label
        yield self._sparkline

    def on_mount(self) -> None:
        super().on_mount()
        self._events = self._event_bus.bind(self)
        # Initialize from current session state
        self._tokens_per_sec = self._session.stats.tokens_per_sec
        self._time_to_first_token = self._session.stats.time_to_first_token
        self.update_display()

    def on_unmount(self) -> None:
        """Unsubscribe to prevent memory leaks."""
        self._events.unsubscribe_all()

    @listen
    def _on_streaming_started(self, event: StreamingStartedEvent) -> None:
        """Reset history when a new generation starts."""
        self._tps_history = []
        self._sparkline.data = [0.0]
        self._turn_tps_sum = 0.0
        self._turn_tps_samples = 0

    @listen
    def _on_stats_updated(self, event: StatsUpdatedEvent) -> None:
        """Update history and display when stats change."""
        if event.tokens_per_sec > 0:
            self._tps_history.append(event.tokens_per_sec)
            self._tps_history = self._tps_history[-30:]
            self._sparkline.data = self._tps_history
            # Accumulate per-turn TPS samples for the session average
            self._turn_tps_sum += event.tokens_per_sec
            self._turn_tps_samples += 1
        # Update local cache
        self._tokens_per_sec = event.tokens_per_sec
        self._time_to_first_token = event.time_to_first_token
        self.update_display()

    @listen
    def _on_streaming_finished(self, event: StreamingFinishedEvent) -> None:
        """Fold the completed turn's TPS into the session average."""
        self._finalize_turn_average()
        self.update_display()

    @listen
    def _on_session_reset(self, event: SessionResetRequest) -> None:
        """Reset the session-average TPS on a fresh session."""
        self._avg_tokens_per_sec = 0.0
        self._session_turn_count = 0
        self._turn_tps_sum = 0.0
        self._turn_tps_samples = 0
        self.update_display()

    def _finalize_turn_average(self) -> None:
        """Fold the current turn's mean TPS into the session average."""
        if self._turn_tps_samples == 0:
            return
        turn_mean = self._turn_tps_sum / self._turn_tps_samples
        if self._session_turn_count == 0:
            self._avg_tokens_per_sec = turn_mean
        else:
            self._avg_tokens_per_sec = (self._session_turn_count * self._avg_tokens_per_sec + turn_mean) / (
                self._session_turn_count + 1
            )
        self._session_turn_count += 1
        self._turn_tps_sum = 0.0
        self._turn_tps_samples = 0

    def update_display(self) -> None:
        """Refresh the speed display."""
        self._label.update(
            f"TPS: [#f9e2af]{self._tokens_per_sec:.1f}[/]\n"
            f"TPS (Avg): [#f9e2af]{self._avg_tokens_per_sec:.1f}[/]\n"
            f"TTFT: [#f9e2af]{self._time_to_first_token:.2f}s[/]"
        )

    def update_value(self, *args, **kwargs) -> None:
        """Force a refresh of the panel's display."""
        self.update_display()
