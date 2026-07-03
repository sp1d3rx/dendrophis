"""ModelPanel — displays current model with switch capability."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.message import Message

from dendrophis.events import EventBus, ModelSwitchedEvent
from dendrophis.ui.widgets.panels.base import TextPanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class ModelPanel(TextPanel):
    """Panel showing current model with switch capability."""

    class Switched(Message):
        """Emitted when the model should be switched."""

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        self._model: str = session.config.llm.model

    def on_mount(self) -> None:
        self._event_bus.subscribe(ModelSwitchedEvent, self._on_model_switched)

    def on_unmount(self) -> None:
        """Unsubscribe to prevent memory leaks."""
        self._event_bus.unsubscribe(ModelSwitchedEvent, self._on_model_switched)

    def _on_model_switched(self, event: ModelSwitchedEvent) -> None:
        """Update local model cache when model switches."""
        self._model = event.model_id
        self.update_value()

    def render_value(self) -> str:
        """Return the model display string."""
        model = self._model
        # Truncate long model names
        if len(model) > 20:
            model = model[:17] + "..."

        return f"[#cba6f7]{model}[/]\n[dim]Click to switch[/dim]"

    def on_click(self) -> None:
        """Emit switched message on click."""
        self.post_message(self.Switched())
