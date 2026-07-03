"""PrimerPanel — displays project primer and understanding phase status."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.events import (
    ContextUpdatedEvent,
    EventBus,
    PrimerLoadedEvent,
    PrimerScreenRequest,
    UnderstandingStatsUpdatedEvent,
)
from dendrophis.ui.widgets.panels.base import TextPanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class PrimerPanel(TextPanel):
    """Panel showing project primer status and understanding phase detection."""

    REFRESH_INTERVAL = 5.0  # Re-check every 5s (faster so understanding status feels responsive)

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        # Local cache for primer info
        self._primer_loaded: bool = False
        self._primer_file_count: int = 0
        self._primer_project_name: str = "?"
        # Local cache for understanding stats
        self._understanding_established: bool = False
        self._understanding_checkpoint: int = -1
        self._understanding_min_turns: int = 5
        self._understanding_current_turn: int = 0

    def on_mount(self) -> None:
        super().on_mount()
        self._event_bus.subscribe(ContextUpdatedEvent, self._on_context_updated)
        self._event_bus.subscribe(UnderstandingStatsUpdatedEvent, self._on_understanding_updated)
        self._event_bus.subscribe(PrimerLoadedEvent, self._on_primer_loaded)
        # Initialize from current session state
        self._update_from_session()

    def on_unmount(self) -> None:
        self._event_bus.unsubscribe(ContextUpdatedEvent, self._on_context_updated)
        self._event_bus.unsubscribe(UnderstandingStatsUpdatedEvent, self._on_understanding_updated)
        self._event_bus.unsubscribe(PrimerLoadedEvent, self._on_primer_loaded)

    def on_click(self) -> None:
        """Open the primer screen when clicked."""
        self._event_bus.publish(PrimerScreenRequest())

    def _update_from_session(self) -> None:
        """Initialize local cache from session state."""
        # Understanding stats
        stats = self._session.get_understanding_stats()
        self._understanding_established = stats.get("established", False)
        self._understanding_checkpoint = stats.get("checkpoint_turn", -1)
        self._understanding_min_turns = stats.get("min_turns_required", 5)
        self._understanding_current_turn = self._session.context.get_turn_count()

        # Primer info
        info = self._session.load_project_primer()
        if info:
            self._primer_loaded = True
            self._primer_file_count = info.get("file_count", 0)
            self._primer_project_name = info.get("project_name", "?")
        else:
            self._primer_loaded = False
            self._primer_file_count = 0
            self._primer_project_name = "?"

    def _on_context_updated(self, event: ContextUpdatedEvent) -> None:
        """Update turn count when context changes."""
        self._understanding_current_turn = event.turn_count
        self.update_value()

    def _on_understanding_updated(self, event: UnderstandingStatsUpdatedEvent) -> None:
        """Update understanding stats when they change."""
        self._understanding_established = event.established
        self._understanding_checkpoint = event.checkpoint_turn
        self._understanding_min_turns = event.min_turns_required
        self._understanding_current_turn = event.current_turn
        self.update_value()

    def _on_primer_loaded(self, event: PrimerLoadedEvent) -> None:
        """Update primer info when primer is loaded."""
        self._primer_loaded = event.project_id is not None
        self._primer_file_count = event.file_count
        self._primer_project_name = event.project_name or "?"
        self.update_value()

    def render_value(self) -> str:
        """Return the primer + understanding status display string."""
        # ── Understanding phase ──────────────────────────────────────────
        if self._understanding_established:
            understanding_line = f"[#a6e3a1]✔ Established[/]  [dim](turn {self._understanding_checkpoint})[/]"
        elif self._understanding_current_turn >= self._understanding_min_turns:
            understanding_line = (
                f"[#f9e2af]⟳ Learning[/]  "
                f"[dim](turn {self._understanding_current_turn}/{self._understanding_min_turns}+)[/]"
            )
        else:
            understanding_line = (
                f"[dim]◌ Gathering[/]  "
                f"[dim](turn {self._understanding_current_turn}/{self._understanding_min_turns}+)[/]"
            )

        # ── Project primer ──────────────────────────────────────────────
        if not self._primer_loaded:
            return f"{understanding_line}\n[dim]No primer[/dim]"

        return f"{understanding_line}\n[#94e2d5]{self._primer_file_count} files[/]\n[dim]{self._primer_project_name}[/]"
