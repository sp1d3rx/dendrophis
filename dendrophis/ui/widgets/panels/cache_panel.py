"""CachePanel — displays cached token savings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dendrophis.events import ConfigReloadedEvent, EventBus, ModelSwitchedEvent, StatsUpdatedEvent
from dendrophis.ui.widgets.panels.base import BasePanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class CachePanel(BasePanel):
    """Panel showing cached token savings."""

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        # Local cache
        self._cached_tokens: int = 0
        self._prompt_tokens: int = 0
        self._caching_enabled: bool = session.is_caching_enabled()
        self._tier_str: str = self._build_tier_str(session.config.caching)

    def on_mount(self) -> None:
        self._event_bus.subscribe(StatsUpdatedEvent, self._on_stats_updated)
        self._event_bus.subscribe(ConfigReloadedEvent, self._on_config_reloaded)
        self._event_bus.subscribe(ModelSwitchedEvent, self._on_model_switched)
        # Initialize from current session state
        stats = self._session.stats
        self._cached_tokens = stats.cached_tokens
        self._prompt_tokens = stats.prompt_tokens

    def on_unmount(self) -> None:
        """Unsubscribe to prevent memory leaks."""
        self._event_bus.unsubscribe(StatsUpdatedEvent, self._on_stats_updated)
        self._event_bus.unsubscribe(ConfigReloadedEvent, self._on_config_reloaded)
        self._event_bus.unsubscribe(ModelSwitchedEvent, self._on_model_switched)

    def _build_tier_str(self, cfg: Any) -> str:
        """Build tier indicator string from config."""
        tiers = []
        if cfg.tier1_system_prompt or cfg.tier1_tool_definitions:
            tiers.append("T1")
        if cfg.tier2_file_blocks or cfg.tier2_project_understanding:
            tiers.append("T2")
        if cfg.tier3_on_compaction:
            tiers.append("T3")
        return "[#94e2d5]" + "+".join(tiers) + "[/]"

    def _on_stats_updated(self, event: StatsUpdatedEvent) -> None:
        """Update local cache when stats change."""
        self._cached_tokens = event.cached_tokens if hasattr(event, "cached_tokens") else 0
        self._prompt_tokens = event.prompt_tokens
        self.update_value()

    def _on_config_reloaded(self, event: ConfigReloadedEvent) -> None:
        """Update config cache when config reloads."""
        self._caching_enabled = self._session.is_caching_enabled()
        self._tier_str = self._build_tier_str(self._session.config.caching)
        self.update_value()

    def _on_model_switched(self, event: ModelSwitchedEvent) -> None:
        """Update caching enabled when model switches."""
        self._caching_enabled = self._session.is_caching_enabled()
        self.update_value()

    def render_value(self) -> str:
        """Return the cache display string."""
        if not self._caching_enabled:
            return "[dim]N/A[/dim]"

        if self._cached_tokens > 0:
            pct = (self._cached_tokens / self._prompt_tokens * 100) if self._prompt_tokens > 0 else 0
            return f"{self._tier_str} [dim]{self._cached_tokens:,} ({pct:.1f}%)[/]"
        return f"{self._tier_str} [dim]waiting...[/dim]"
