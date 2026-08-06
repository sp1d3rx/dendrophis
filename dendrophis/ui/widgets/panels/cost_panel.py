"""CostPanel — displays current session cost."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.events import EventBus, ModelSwitchedEvent, StatsUpdatedEvent, listen
from dendrophis.ui.widgets.panels.base import TextPanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class CostPanel(TextPanel):
    """Panel showing cumulative session cost and model pricing."""

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        # Local cache
        self._total_cost_usd: float = 0.0
        self._input_rate: float = 0.0  # USD per token, uncached input
        self._cache_rate: float = 0.0  # USD per token, cache-read input
        self._output_rate: float = 0.0  # USD per token, output
        self._model_loaded: bool = False

    def on_mount(self) -> None:
        super().on_mount()
        self._events = self._event_bus.bind(self)
        # Initialize from current session state
        self._total_cost_usd = self._session.stats.total_cost_usd
        self._on_model_switched(
            ModelSwitchedEvent(
                model_id=self._session.config.llm.model, context_window=self._session.config.llm.context_limit
            )
        )

    def on_unmount(self) -> None:
        self._events.unsubscribe_all()

    @listen
    def _on_stats_updated(self, event: StatsUpdatedEvent) -> None:
        """Update cost cache when stats change."""
        self._total_cost_usd = event.total_cost_usd
        self.update_value()

    @listen
    def _on_model_switched(self, event: ModelSwitchedEvent) -> None:
        """Update model info cache when model switches."""
        model = next((model_item for model_item in self._session.models if model_item.id == event.model_id), None)
        if model:
            self._input_rate = model.input_cost_per_token
            self._cache_rate = model.cache_read_cost_per_token
            self._output_rate = model.output_cost_per_token
            self._model_loaded = True
        else:
            self._model_loaded = False
        self.update_value()

    def render_value(self) -> str:
        """Return the cost display string."""
        if not self._model_loaded:
            return "[panel-value]Loading model data...[/]"

        if self._input_rate <= 0 and self._output_rate <= 0:
            return f"[#fab387]${self._total_cost_usd:.4f}[/]\n[panel-value](free)[/]"

        def _per_1m(rate: float) -> str:
            return f"${rate * 1_000_000:.2f}"

        return (
            f"[#fab387]${self._total_cost_usd:.4f}[/]\n"
            f"[panel-value]in {_per_1m(self._input_rate)} "
            f"\u2022 cached {_per_1m(self._cache_rate)} "
            f"\u2022 out {_per_1m(self._output_rate)} /1M[/]"
        )
