"""Debug log screen — floating resizable window for debug output."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Log


class DebugLogScreen(ModalScreen):
    """A floating, resizable debug log window."""

    DEFAULT_CSS = """
    DebugLogScreen {
        align: center middle;
    }
    
    DebugLogScreen > Vertical {
        width: 80%;
        height: 60%;
        border: thick $background 80%;
        background: $surface;
    }
    
    DebugLogScreen > Vertical:focus {
        border: thick $accent;
    }
    
    DebugLogScreen .title-bar {
        height: auto;
        dock: top;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    
    DebugLogScreen .title-bar Button {
        width: auto;
        min-width: 3;
        background: transparent;
        border: none;
        content-align: center middle;
        margin: 0 1;
    }
    
    DebugLogScreen .title-bar Button:hover {
        background: $accent;
    }
    
    DebugLogScreen .title-bar #clear {
        color: $text-muted;
    }
    
    DebugLogScreen Log {
        height: 1fr;
        width: 1fr;
        border: none;
        padding: 0 1;
    }
    """

    from typing import ClassVar

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+d", "close", "Close"),
        ("escape", "close", "Close"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._log: Log | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(classes="title-bar"):
                yield Label("🐛 Debug Log", classes="title")
                yield Button("🗑 Clear", id="clear")
                yield Button("✕", id="close")
            self._log = Log(id="debug-log")
            yield self._log

    def on_mount(self) -> None:
        self.query_one(Log).focus()
        self._log = self.query_one(Log)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.dismiss()
        elif event.button.id == "clear" and self._log:
            self._log.clear()

    def action_close(self) -> None:
        self.dismiss()

    def write(self, message: str) -> None:
        """Write a message to the debug log."""
        if self._log:
            from datetime import datetime

            timestamp = datetime.now().strftime(r"%H:%M:%S.%f")[:-3]
            # Ensure message ends with newline for Log widget
            if not message.endswith("\n"):
                message += "\n"
            self._log.write(f"[{timestamp}] {message}")
