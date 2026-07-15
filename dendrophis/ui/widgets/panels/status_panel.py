"""StatusPanel — displays current activity status with colored indicators."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.events import (
    EventBus,
    MessageSentEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
    TextDeltaEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    WaitingForInputEvent,
    listen,
)
from dendrophis.ui.widgets.panels.base import TextPanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class StatusPanel(TextPanel):
    """Panel showing current activity status with colored indicators."""

    REFRESH_INTERVAL = 0.5  # Handle flashing via refresh interval

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._event_bus = event_bus

        # Activity states
        self._message_sent = False
        self._streaming = False
        self._waiting_input = True
        self._tool_calling = False
        self._flash_on = False

    def on_mount(self) -> None:
        super().on_mount()  # Start refresh interval
        self._events = self._event_bus.bind(self)

    def on_unmount(self) -> None:
        """Unsubscribe to prevent memory leaks."""
        self._events.unsubscribe_all()

    @listen
    def _on_streaming_started(self, event: StreamingStartedEvent) -> None:
        self._message_sent = True
        self._waiting_input = False
        self._tool_calling = False
        self.update_value()

    @listen
    def _on_text_delta(self, event: TextDeltaEvent) -> None:
        self._streaming = True
        self.update_value()

    @listen
    def _on_streaming_finished(self, event: StreamingFinishedEvent) -> None:
        self._streaming = False
        self.update_value()

    @listen
    def _on_tool_execution_started(self, event: ToolExecutionStartedEvent) -> None:
        self._tool_calling = True
        self._waiting_input = False
        self.update_value()

    @listen
    def _on_tool_execution_finished(self, event: ToolExecutionFinishedEvent) -> None:
        self._tool_calling = False
        self.update_value()

    @listen
    def _on_message_sent(self, event: MessageSentEvent) -> None:
        self._message_sent = True
        self._waiting_input = False
        self._tool_calling = False
        self.update_value()

    @listen
    def _on_waiting_for_input(self, event: WaitingForInputEvent) -> None:
        self._waiting_input = True
        self._message_sent = False
        self._tool_calling = False
        self.update_value()

    def render_value(self) -> str:
        """Render the status display."""
        # Toggle flash state on each render if it was triggered by interval
        self._flash_on = not self._flash_on
        flash_on = self._flash_on

        # Dot indicators
        s_dot = "[#89b4fa]●[/]" if self._message_sent else "[dim]○[/]"
        c_dot = "[#a6e3a1]●[/]" if self._streaming else "[dim]○[/]"
        w_dot = "[#f9e2af]●[/]" if self._waiting_input else "[dim]○[/]"
        t_dot = "[#f38ba8]●[/]" if self._tool_calling else "[dim]○[/]"

        # Flashing effect for active items
        if flash_on:
            if self._message_sent:
                s_dot = "[#b4befe]●[/]"
            if self._streaming:
                c_dot = "[#94e2d5]●[/]"
            if self._waiting_input:
                w_dot = "[#fef08a]●[/]"
            if self._tool_calling:
                t_dot = "[#eba0ac]●[/]"

        return f"{s_dot} Send    {c_dot} Stream\n{w_dot} Ready   {t_dot} Tool"
