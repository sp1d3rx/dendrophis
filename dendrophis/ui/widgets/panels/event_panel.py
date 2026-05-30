"""EventPanel ABC for sidebar panels that manage their own composition."""

from __future__ import annotations

from dendrophis.ui.widgets.panels.base import PanelBase


class EventPanel(PanelBase):
    """Base class for event-driven panels with custom composition.

    Unlike ``BasePanel``, panels that subclass ``EventPanel`` manage
    their own internal widget tree via ``compose()`` and update
    themselves in response to events rather than having their content
    replaced by ``render_value()``.

    Panels are auto-discovered by the ``PanelRegistry`` via
    ``__init_subclass__``.
    """

    def _on_refresh_tick(self) -> None:
        """Called at REFRESH_INTERVAL. Subclasses override as needed."""
        pass
