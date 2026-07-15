"""McpStatusPanel — displays status of enabled MCP servers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.events import ConfigReloadedEvent, EventBus, listen
from dendrophis.ui.widgets.panels.base import TextPanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class McpStatusPanel(TextPanel):
    """Panel showing active MCP server statuses."""

    REFRESH_INTERVAL = 2.0

    DEFAULT_CSS = """
    McpStatusPanel {
        border: solid #cba6f7;
    }
    McpStatusPanel:hover {
        border: solid #89b4fa;
    }
    """

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus

    def on_mount(self) -> None:
        super().on_mount()
        self._events = self._event_bus.bind(self)

    def on_unmount(self) -> None:
        self._events.unsubscribe_all()

    @listen
    def _on_config_reloaded(self, event: ConfigReloadedEvent) -> None:
        self.update_value()

    def _on_refresh_tick(self) -> None:
        self.update_value()

    def render_value(self) -> str:
        """Render statuses of enabled MCP servers."""
        manager = getattr(self._session, "mcp_manager", None)
        if not manager:
            return "[dim]No MCP Manager[/dim]"

        enabled_servers = {
            name: server_config for name, server_config in manager.config.mcp_servers.items() if server_config.enabled
        }
        if not enabled_servers:
            return "[dim]No enabled servers[/dim]"

        lines = []
        for name in sorted(enabled_servers.keys()):
            if name in manager._sessions:
                lines.append(f"[#a6e3a1]●[/] {name}")
            else:
                lines.append(f"[#f38ba8]○[/] {name}")
        return "\n".join(lines)
