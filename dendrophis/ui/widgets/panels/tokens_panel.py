"""TokensPanel — displays prompt/completion token counts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.events import EventBus, StatsUpdatedEvent
from dendrophis.ui.widgets.panels.base import BasePanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class TokensPanel(BasePanel):
    """Panel showing token usage statistics."""

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        # Local cache of stats
        self._prompt_tokens: int = 0
        self._completion_tokens: int = 0

    def on_mount(self) -> None:
        self._event_bus.subscribe(StatsUpdatedEvent, self._on_stats_updated)
        # Initialize from current session state
        self._prompt_tokens = self._session.stats.prompt_tokens
        self._completion_tokens = self._session.stats.completion_tokens

    def on_unmount(self) -> None:
        """Unsubscribe to prevent memory leaks."""
        self._event_bus.unsubscribe(StatsUpdatedEvent, self._on_stats_updated)

    def _on_stats_updated(self, event: StatsUpdatedEvent) -> None:
        """Update local cache when stats change."""
        self._prompt_tokens = event.prompt_tokens
        self._completion_tokens = event.completion_tokens
        self.update_value()

    def render_value(self) -> str:
        """Return the token display string."""
        total = self._prompt_tokens + self._completion_tokens
        return f"[#89dceb]{total:,}[/] [dim](P:{self._prompt_tokens:,} C:{self._completion_tokens:,})[/]"
