"""Sidebar widget for showing session stats and info."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import VerticalScroll

from dendrophis.events import EventBus
from dendrophis.ui.widgets.panels import PanelRegistry
from dendrophis.ui.widgets.panels.base import Panel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class Sidebar(VerticalScroll):
    """Sidebar for stats and model info."""

    DEFAULT_CSS = """
    Sidebar {
        background: $surface-darken-2;
        padding: 1;
        scrollbar-size: 1 1;
    }
    Sidebar.dock-left {
        dock: left;
        border-right: solid $primary 20%;
        border-left: none;
    }
    Sidebar.dock-right {
        dock: right;
        border-left: solid $primary 20%;
        border-right: none;
    }
    """

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus

    def on_mount(self) -> None:
        """Apply layout and styling parameters from configuration."""
        sidebar_config = self._session.config.ui.sidebar
        self.styles.width = sidebar_config.width

        if sidebar_config.position == "left":
            self.add_class("dock-left")
            self.remove_class("dock-right")
        else:
            self.add_class("dock-right")
            self.remove_class("dock-left")

    def compose(self) -> ComposeResult:
        """Compose sidebar panels based on config order."""
        configured_panels = self._session.config.ui.sidebar.panels

        for panel_name in configured_panels:
            panel_class = PanelRegistry.get(panel_name)
            if panel_class is None:
                continue
            panel = panel_class(self._session, self._event_bus)
            panel.id = f"{panel_class.panel_id}_panel"
            panel.add_class(f"panel-{panel_class.panel_id}")
            panel.border_title = self._panel_title(panel_class.panel_id)
            yield panel

    @staticmethod
    def _panel_title(name: str) -> str:
        """Derive the display title for a panel."""
        overrides = {"sys_info": "System"}
        if name in overrides:
            return overrides[name]
        return name.replace("_", " ").title()

    def refresh_all(self) -> None:
        """Trigger a manual refresh of all child panels."""
        for panel in self.query(Panel):
            panel.update_value()
