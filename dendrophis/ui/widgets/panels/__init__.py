"""Sidebar panels package."""

from __future__ import annotations

from dendrophis.ui.widgets.panels.registry import PanelRegistry

# Auto-discover all panel modules on import
PanelRegistry.discover()

__all__ = ["PanelRegistry"]
