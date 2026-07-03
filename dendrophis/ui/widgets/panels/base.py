"""Base panel hierarchy for sidebar panels."""

from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from typing import ClassVar

from textual.widgets import Static


class PanelMetaclass(type(Static), ABCMeta):
    pass


class Panel(Static, ABC, metaclass=PanelMetaclass):
    """Fundamental abstract class for all sidebar panels.

    Handles CSS, and basic metadata.
    """

    REFRESH_INTERVAL: float = 0.0  # 0 = event-driven only

    # Auto-assigned by PanelRegistry
    panel_id: ClassVar[str] = ""
    panel_name: ClassVar[str] = ""

    DEFAULT_CSS = """
    Panel {
        height: auto;
        margin-bottom: 1;
        padding: 0 1;
        background: $surface-darken-1;
        border: solid $panel-darken-2;
        color: $text-muted;
    }
    Panel:hover {
        background: $surface;
        border: solid $primary;
    }
    """

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        name = cls.__name__
        if name.endswith("Panel"):
            name = name[:-5]
        # Convert CamelCase to snake_case
        parts = []
        for i, c in enumerate(name):
            if i > 0 and name[i - 1].islower() and c.isupper():
                parts.append("_")
            parts.append(c.lower())
        cls.panel_id = "".join(parts)
        cls.panel_name = cls.panel_id

    def on_mount(self) -> None:
        if self.REFRESH_INTERVAL > 0:
            self.set_interval(self.REFRESH_INTERVAL, self._on_refresh_tick)

    def _on_refresh_tick(self) -> None:
        """Called at REFRESH_INTERVAL. Subclasses override as needed."""
        pass


class TextPanel(Panel):
    """Simple panel that updates its content via a string return value.

    Subclasses implement render_value().
    """

    def on_mount(self) -> None:
        super().on_mount()
        # Initial update
        self.update_value()

    def update_value(self, *args, **kwargs) -> None:
        """Force a full update of the content from render_value()."""
        try:
            content = self.render_value()
        except Exception:
            content = "—"
        self.update(content)

    @abstractmethod
    def render_value(self) -> str:
        """Return the string to display as the panel's current value."""
