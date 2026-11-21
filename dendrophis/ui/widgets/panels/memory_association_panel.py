"""MemoryAssociationPanel - displays wandering thoughts and associations.

Not a search interface. A place where memories surface organically,
framed with uncertainty: "this makes me think of... but maybe not"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape

from dendrophis.events import EventBus, MemoryAssociationEvent, listen
from dendrophis.memory.association import MemoryAssociationGenerator
from dendrophis.ui.widgets.panels.base import TextPanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class MemoryAssociationPanel(TextPanel):
    """Panel for displaying memory associations - "this reminds me of..."

    Shows up occasionally, not on every turn. Framed as thoughts,
    not facts. Can be dismissed or clicked to explore.
    """

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._store = session._memory_store
        self._event_bus = event_bus
        self._generator = MemoryAssociationGenerator(self._store)
        self._current_association: MemoryAssociationEvent | None = None
        self._dismissed = False

    def on_mount(self) -> None:
        self._events = self._event_bus.bind(self)
        super().on_mount()

    def on_unmount(self) -> None:
        self._events.unsubscribe_all()

    @listen
    def _on_association(self, event: MemoryAssociationEvent) -> None:
        """Handle a new memory association event."""
        self._current_association = event
        self._dismissed = False
        self.update_value()

    def render_value(self) -> str:
        """Render the current association with appropriate styling."""
        if self._dismissed or not self._current_association:
            return "[dim]...[/dim]"

        event = self._current_association
        text = MemoryAssociationGenerator.format_association(event)

        # Style based on confidence
        if event.confidence == "strong":
            prefix = "[#89b4fa]💭[/] "  # Blue thought bubble
        elif event.confidence == "weak":
            prefix = "[#f9e2af]~[/] "  # Yellow tilde for uncertainty
        else:  # random
            prefix = "[#6c7086]?[/] "  # Gray question for wandering

        return f"{prefix}[italic]{escape(text)}[/italic]"

    def dismiss(self) -> None:
        """User dismissed this association."""
        self._dismissed = True
        self.update_value()

    def clear(self) -> None:
        """Clear the current association."""
        self._current_association = None
        self._dismissed = False
        self.update_value()
