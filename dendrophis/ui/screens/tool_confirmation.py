"""Screen for human approval of sensitive tool calls."""

from __future__ import annotations

import json

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from dendrophis.events import EventBus, ToolConfirmationResponseEvent


class ToolConfirmationScreen(ModalScreen[bool]):
    """Modal dialog asking for tool execution approval."""

    DEFAULT_CSS = """
    ToolConfirmationScreen {
        align: center middle;
    }
    ToolConfirmationScreen:focus {
        outline: none;
    }
    #dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 80%;
        background: $panel;
        border: thick $primary;
        padding: 1;
    }
    #dialog Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }
    #tool-name {
        color: $accent;
        text-style: bold;
    }
    #args-container {
        background: $surface-darken-1;
        margin-bottom: 1;
        height: auto;
        max-height: 60%;
        overflow: auto;
    }
    #args-container Syntax {
        padding: 1;
        color: $text;
    }
    #buttons {
        width: 100%;
        height: auto;
        align: center middle;
    }
    #buttons Button {
        margin: 0 1;
    }
    """

    from typing import ClassVar

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss_reject", "Reject"),
    ]

    def __init__(self, request_id: str, tool_name: str, arguments: str, event_bus: EventBus) -> None:
        super().__init__()
        self.request_id = request_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.event_bus = event_bus

    def compose(self) -> ComposeResult:
        MAX_DISPLAY_LEN = 2000  # Limit displayed args to prevent UI lockup

        display_args = self.arguments
        try:
            args_dict = json.loads(self.arguments)
            display_args = json.dumps(args_dict, indent=2)
        except Exception:
            pass

        # Truncate if too large
        if len(display_args) > MAX_DISPLAY_LEN:
            display_args = display_args[:MAX_DISPLAY_LEN] + "\n\n[...truncated for display...]"

        display_text = display_args
        language = "json"

        if self.tool_name == "bash":
            try:
                args_dict = json.loads(self.arguments)
                command = args_dict.get("command", "")
                if command:
                    display_text = command
                    language = "bash"
            except Exception:
                pass

        with Vertical(id="dialog"):
            yield Label("[bold]Tool Execution Approval[/bold]")
            yield Label("The agent wants to run:", id="tool-name")
            yield Label(f"[bold cyan]{self.tool_name}[/bold cyan]")
            with Vertical(id="args-container"):
                yield Static(Syntax(display_text, lexer=language, theme="monokai", word_wrap=True))
            with Horizontal(id="buttons"):
                yield Button("Approve (Enter)", variant="primary", id="approve")
                yield Button("Reject (Esc)", variant="error", id="reject")

    def on_mount(self) -> None:
        """Focus the approve button by default."""
        self.query_one("#approve", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        approved = event.button.id == "approve"
        self.event_bus.publish(ToolConfirmationResponseEvent(request_id=self.request_id, approved=approved))
        self.dismiss(approved)

    def action_dismiss_reject(self) -> None:
        """Handle escape key to reject."""
        self.event_bus.publish(ToolConfirmationResponseEvent(request_id=self.request_id, approved=False))
        self.dismiss(False)
