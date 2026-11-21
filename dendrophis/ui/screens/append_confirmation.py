"""Screen for human approval of file appends, showing the content to append."""

from __future__ import annotations

from typing import ClassVar

from rich.markup import escape
from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from dendrophis.events import AppendApprovalEvent, AppendProposalEvent, EventBus
from dendrophis.ui.screens.write_confirmation import _lexer_for, truncate_preview


class AppendConfirmationScreen(ModalScreen[bool]):
    """Modal dialog showing content to append for approval."""

    DEFAULT_CSS = """
    AppendConfirmationScreen {
        align: center middle;
    }
    AppendConfirmationScreen:focus {
        outline: none;
    }
    #dialog {
        width: 90%;
        max-width: 120;
        height: 80%;
        background: $panel;
        border: thick $primary;
        padding: 1;
    }
    #dialog Label {
        width: 100%;
        text-align: center;
    }
    #file-info {
        text-align: center;
        margin-bottom: 1;
        color: $accent;
    }
    #content-container {
        background: $surface-darken-1;
        margin-bottom: 1;
        height: 1fr;
        overflow: auto;
        border: solid $surface;
    }
    #content-container Static {
        padding: 1;
        width: 100%;
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

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss_reject", "Reject"),
    ]

    def __init__(self, event: AppendProposalEvent, event_bus: EventBus) -> None:
        super().__init__()
        self.request_id = event.request_id
        self.file_path = event.file_path
        self.content = event.content
        self.event_bus = event_bus

    def compose(self) -> ComposeResult:
        lexer = _lexer_for(self.file_path)
        preview_content = truncate_preview(self.content)
        with Vertical(id="dialog"):
            yield Label("[bold]Append Approval Required[/bold]")
            yield Label(f"Append to: {escape(self.file_path)}", id="file-info")

            with Vertical(id="content-container"):
                yield Static(Syntax(preview_content, lexer=lexer, theme="monokai", word_wrap=False, line_numbers=True))

            with Horizontal(id="buttons"):
                yield Button("Approve (Enter)", variant="primary", id="approve")
                yield Button("Reject (Esc)", variant="error", id="reject")

    def on_mount(self) -> None:
        self.query_one("#approve", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        approved = event.button.id == "approve"
        self.event_bus.publish(AppendApprovalEvent(request_id=self.request_id, approved=approved))
        self.dismiss(approved)

    def action_dismiss_reject(self) -> None:
        self.event_bus.publish(AppendApprovalEvent(request_id=self.request_id, approved=False))
        self.dismiss(False)
