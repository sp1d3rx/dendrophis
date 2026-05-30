"""TempPanel — displays and allows adjusting temperature."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.events import EventBus, TemperatureChangedEvent, TemperatureChangeRequest
from dendrophis.ui.widgets.panels.base import BasePanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


TEMPERATURES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


class TempPanel(BasePanel):
    """Panel showing LLM temperature. Click to cycle."""

    DEFAULT_CSS = """
    TempPanel {
        border: solid #b8e994;
    }
    TempPanel:hover {
        border: solid #a6e3a1;
    }
    """

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        self._temperature: float = session.config.llm.temperature

        # Subscribe to temperature change events
        self._event_bus.subscribe(TemperatureChangedEvent, self._on_temperature_changed)

    def _on_temperature_changed(self, event: TemperatureChangedEvent) -> None:
        """Update local state when temperature changes via event bus."""
        self._temperature = event.temperature
        self.update_value()

    def render_value(self) -> str:
        """Return the temperature display string."""
        return f"[#f5c2e7]{self._temperature:.2f}[/]"

    def on_click(self) -> None:
        """Cycle to next temperature on click."""
        current = self._temperature
        idx = TEMPERATURES.index(current) if current in TEMPERATURES else 0
        next_idx = (idx + 1) % len(TEMPERATURES)
        next_temp = TEMPERATURES[next_idx]
        # Publish request event instead of directly mutating session
        self._event_bus.publish(TemperatureChangeRequest(temperature=next_temp))

    def action_cycle_temp(self) -> None:
        """Keyboard action to cycle temperature."""
        self.on_click()
