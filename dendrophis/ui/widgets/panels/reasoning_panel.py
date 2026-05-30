"""ReasoningPanel — displays and cycles reasoning_effort for reasoning models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.events import EventBus, ReasoningEffortChangedEvent, ReasoningEffortChangeRequest
from dendrophis.ui.widgets.panels.base import BasePanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


# Cycle order: None = model default, then explicit levels, then "none" to disable.
EFFORTS = [None, "low", "medium", "high", "xhigh", "none"]

LABELS = {
    None: "default",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "none": "off",
}


class ReasoningPanel(BasePanel):
    """Panel showing reasoning effort. Click to cycle through levels."""

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        self._reasoning_effort: str | None = session.config.llm.reasoning_effort

        # Subscribe to reasoning effort change events
        self._event_bus.subscribe(ReasoningEffortChangedEvent, self._on_reasoning_effort_changed)

    def _on_reasoning_effort_changed(self, event: ReasoningEffortChangedEvent) -> None:
        """Update local state when reasoning effort changes via event bus."""
        self._reasoning_effort = event.reasoning_effort
        self.update_value()

    def render_value(self) -> str:
        """Return the reasoning effort display string."""
        label = LABELS.get(self._reasoning_effort, str(self._reasoning_effort))
        return f"[#89b4fa]{label}[/]"

    def on_click(self) -> None:
        """Cycle to next reasoning effort level on click."""
        current = self._reasoning_effort
        try:
            idx = EFFORTS.index(current)
        except ValueError:
            idx = 0
        next_effort = EFFORTS[(idx + 1) % len(EFFORTS)]
        # Publish request event instead of directly mutating session
        self._event_bus.publish(ReasoningEffortChangeRequest(reasoning_effort=next_effort))
