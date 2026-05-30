"""Docked debug log widget for MainScreen."""

from __future__ import annotations

from collections import deque
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Label, Log, Static


class DebugLogWidget(Static):
    """Docked debug log widget that appears at the top of the main screen."""

    DEFAULT_CSS = """
    DebugLogWidget {
        height: 12;
        border: solid $primary;
        background: $surface;
        display: none;
    }
    DebugLogWidget.visible {
        display: block;
    }
    DebugLogWidget .title-bar {
        height: auto;
        dock: top;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    DebugLogWidget .title-bar Button {
        width: auto;
        min-width: 3;
        background: transparent;
        border: none;
        content-align: center middle;
        margin: 0 1;
    }
    DebugLogWidget .title-bar Button:hover {
        background: $accent;
    }
    DebugLogWidget Log {
        height: 1fr;
        width: 1fr;
        border: none;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._log: Log | None = None
        self._visible = False
        self._lines: deque[str] = deque(maxlen=200)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="title-bar"):
            yield Label("🐛 Debug Log", classes="title")
            yield Button("📋 Copy", id="copy")
            yield Button("🗑 Clear", id="clear")
            yield Button("✕", id="close")
        self._log = Log(id="debug-log")
        yield self._log

    def on_mount(self) -> None:
        self._log = self.query_one(Log)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.toggle()
        elif event.button.id == "clear" and self._log:
            self._log.clear()
            self._lines.clear()
        elif event.button.id == "copy":
            content = "".join(self._lines)
            if content:
                self.app.copy_to_clipboard(content)
                self.app.notify("Debug log copied to clipboard!", severity="information", timeout=2)

    def write(self, message: str) -> None:
        """Write a message to the debug log."""
        if self._log:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            # Ensure message ends with newline for Log widget
            if not message.endswith("\n"):
                message += "\n"

            full_msg = f"[{timestamp}] {message}"
            self._log.write(full_msg)

            # Keep track of lines for copying (limit to last 200 lines)
            self._lines.append(full_msg)

    def toggle(self) -> None:
        """Toggle the debug log visibility."""
        self._visible = not self._visible
        if self._visible:
            self.add_class("visible")
        else:
            self.remove_class("visible")

    def show(self) -> None:
        """Show the debug log."""
        if not self._visible:
            self.toggle()

    def hide(self) -> None:
        """Hide the debug log."""
        if self._visible:
            self.toggle()

    def is_visible(self) -> bool:
        """Check if debug log is currently visible."""
        return self._visible
