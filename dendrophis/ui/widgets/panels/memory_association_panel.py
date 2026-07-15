"""MemoryAssociationPanel - displays wandering thoughts and associations.

Not a search interface. A place where memories surface organically,
framed with uncertainty: "this makes me think of... but maybe not"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

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

    REFRESH_INTERVAL = 1.0  # Check for new associations frequently

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._store = session._memory_store
        self._event_bus = event_bus
        self._generator = MemoryAssociationGenerator(self._store)
        self._current_association: MemoryAssociationEvent | None = None
        self._dismissed = False

    def compose(self):
        """Compose the panel layout."""
        yield Static("[dim]Memories will surface here...[/dim]", id="association-content")

    def on_mount(self) -> None:
        super().on_mount()
        self._events = self._event_bus.bind(self)
        self.set_interval(self.REFRESH_INTERVAL, self._check_for_association)

    def on_unmount(self) -> None:
        self._events.unsubscribe_all()

    @listen
    def _on_association(self, event: MemoryAssociationEvent) -> None:
        """Handle a new memory association event."""
        self._current_association = event
        self._dismissed = False
        self.update_value()

    def _check_for_association(self) -> None:
        """Periodic check - panel doesn't generate, just displays."""
        # The session generates associations and emits events
        # This panel just renders them
        pass

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

        return f"{prefix}[italic]{text}[/italic]"

    def dismiss(self) -> None:
        """User dismissed this association."""
        self._dismissed = True
        self.update_value()

    def clear(self) -> None:
        """Clear the current association."""
        self._current_association = None
        self._dismissed = False
        self.update_value()
