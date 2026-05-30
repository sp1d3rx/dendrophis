"""SysInfoPanel — displays system information with sparklines."""

from __future__ import annotations

import platform
from typing import Any

import psutil
from textual.app import ComposeResult
from textual.widgets import Sparkline, Static

from dendrophis.ui.widgets.panels.event_panel import EventPanel


class SysInfoPanel(EventPanel):
    """Panel showing system metrics with sparklines."""

    REFRESH_INTERVAL = 5.0

    DEFAULT_CSS = """
    SysInfoPanel Static {
        margin-top: 1;
    }
    SysInfoPanel Sparkline {
        height: 1;
        color: #a6adc8;
    }
    SysInfoPanel Sparkline.warning {
        color: #f9e2af;
    }
    SysInfoPanel Sparkline.critical {
        color: #f38ba8;
    }
    """

    def __init__(self, session: Any, event_bus: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session = session
        self._event_bus = event_bus

        # History for sparklines
        self._max_history = 30
        self._cpu_history: list[float] = []
        self._mem_history: list[float] = []

        # Widgets
        self._header_label = Static()
        self._cpu_label = Static()
        self._cpu_sparkline = Sparkline(data=[0.0], summary_function=max)
        self._mem_label = Static()
        self._mem_sparkline = Sparkline(data=[0.0], summary_function=max)

    def compose(self) -> ComposeResult:
        yield self._header_label
        yield self._cpu_label
        yield self._cpu_sparkline
        yield self._mem_label
        yield self._mem_sparkline

    def _on_refresh_tick(self) -> None:
        """Update metrics and sparklines."""
        try:
            # Update Header
            self._header_label.update(f"[#a6adc8]{platform.system()}[/] [dim]{platform.release()}[/]")

            # CPU
            cpu_usage = psutil.cpu_percent()
            self._cpu_history.append(cpu_usage)
            self._cpu_history = self._cpu_history[-self._max_history :]
            self._cpu_sparkline.data = self._cpu_history
            self._cpu_label.update(f"CPU: [#a6adc8]{cpu_usage:>4.0f}%[/]")

            # CPU threshold coloring (higher is worse)
            if cpu_usage >= 90:
                self._cpu_sparkline.add_class("critical")
                self._cpu_sparkline.remove_class("warning")
            elif cpu_usage >= 70:
                self._cpu_sparkline.add_class("warning")
                self._cpu_sparkline.remove_class("critical")
            else:
                self._cpu_sparkline.remove_class("critical", "warning")

            # Memory
            mem = psutil.virtual_memory()
            mem_avail_mb = mem.available / (1024 * 1024)
            # Use % available for the sparkline to show headroom
            mem_avail_pct = (mem.available / mem.total) * 100
            self._mem_history.append(mem_avail_pct)
            self._mem_history = self._mem_history[-self._max_history :]
            self._mem_sparkline.data = self._mem_history
            self._mem_label.update(f"Mem: [#a6adc8]{mem_avail_mb:.0f} MB avail[/]")

            # Memory threshold coloring (lower available % is worse)
            mem_used_pct = 100 - mem_avail_pct
            if mem_used_pct >= 95:
                self._mem_sparkline.add_class("critical")
                self._mem_sparkline.remove_class("warning")
            elif mem_used_pct >= 80:
                self._mem_sparkline.add_class("warning")
                self._mem_sparkline.remove_class("critical")
            else:
                self._mem_sparkline.remove_class("critical", "warning")

        except Exception as exc:
            self._header_label.update(f"System: Error ({exc})")
            self._cpu_label.update("CPU: N/A")
            self._mem_label.update("Mem: N/A")
