"""ContextPanel — displays context usage percentage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.events import ContextUpdatedEvent, EventBus, ModelSwitchedEvent, listen
from dendrophis.ui.widgets.panels.base import TextPanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class ContextPanel(TextPanel):
    """Panel showing context window usage."""

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        # Local cache
        self._token_count: int = 0
        self._context_limit: int = session.config.llm.context_limit
        self._token_pct: float = 0.0

    def on_mount(self) -> None:
        self._events = self._event_bus.bind(self)
        # Initialize from current session state
        context_mgr = self._session.context
        self._token_count = context_mgr.token_count
        self._token_pct = context_mgr.token_pct

    def on_unmount(self) -> None:
        self._events.unsubscribe_all()

    @listen
    def _on_context_updated(self, event: ContextUpdatedEvent) -> None:
        """Update local cache when context changes."""
        self._token_count = event.token_count
        self._token_pct = event.token_pct
        self.update_value()

    @listen
    def _on_model_switched(self, event: ModelSwitchedEvent) -> None:
        """Update context limit when model switches."""
        self._context_limit = event.context_window
        self.update_value()

    def render_value(self) -> str:
        """Return the context display string."""
        token_pct = self._token_pct * 100

        # Choose color based on usage
        if token_pct >= 90:
            color = "#f38ba8"  # red
        elif token_pct >= 75:
            color = "#fab387"  # peach
        else:
            color = "#74c7ec"  # sapphire

        return f"[{color}]{token_pct:.1f}%[/{color}] [dim]({self._token_count:,} / {self._context_limit:,})[/]"
