"""Auto-discovery registry for sidebar panels.

Scans the panels/ directory for subclasses of Panel and
registers them so they can be discovered at runtime.
"""

from __future__ import annotations

import contextlib
import importlib
import pkgutil
from typing import ClassVar

from dendrophis.ui.widgets.panels.base import Panel


class PanelRegistry:
    """Registry of discovered panel classes.

    Panels are discovered via the ``discover()`` method which scans the
    panels package for modules and imports them.
    """

    _panels: ClassVar[dict[str, type[Panel]]] = {}

    def __init__(self) -> None:
        raise NotImplementedError("Use class methods, do not instantiate.")

    @classmethod
    def register(cls, panel_class: type[Panel]) -> None:
        """Register a panel class by its panel_id."""
        cls._panels.setdefault(panel_class.panel_id, panel_class)

    @classmethod
    def get(cls, panel_id: str) -> type[Panel] | None:
        """Look up a panel class by id. Returns None if not found."""
        return cls._panels.get(panel_id)

    @classmethod
    def all(cls) -> dict[str, type[Panel]]:
        """Return a copy of all registered panels."""
        return dict(cls._panels)

    @classmethod
    def ids(cls) -> list[str]:
        """Return all registered panel ids, sorted."""
        return sorted(cls._panels)

    @classmethod
    def clear(cls) -> None:
        """Clear the registry (useful for testing)."""
        cls._panels.clear()

    @classmethod
    def discover(cls) -> None:
        """Scan the panels package and import all modules.

        This populates the registry by importing each module, which in turn
        triggers the definition of panel subclasses.
        """
        from dendrophis.ui.widgets.panels.base import Panel, TextPanel
        from dendrophis.ui.widgets.panels.event_panel import EventPanel

        panels_pkg = importlib.import_module("dendrophis.ui.widgets.panels")
        for _importer, modname, _ispkg in pkgutil.iter_modules(panels_pkg.__path__, panels_pkg.__name__ + "."):
            if modname in ("dendrophis.ui.widgets.panels", "dendrophis.ui.widgets.panels.registry"):
                continue
            with contextlib.suppress(ImportError):
                mod = importlib.import_module(modname)
                for obj in mod.__dict__.values():
                    if isinstance(obj, type) and issubclass(obj, Panel) and obj not in (Panel, TextPanel, EventPanel):
                        cls.register(obj)
