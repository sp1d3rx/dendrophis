"""Screen for human approval of file edits, showing a diff."""

from __future__ import annotations

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from dendrophis.events import EditApprovalEvent, EditProposalEvent, EventBus


class EditConfirmationScreen(ModalScreen[bool]):
    """Modal dialog showing a diff for file edit approval."""

    DEFAULT_CSS = """
    EditConfirmationScreen {
        align: center middle;
    }
    EditConfirmationScreen:focus {
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
    #diff-container {
        background: $surface-darken-1;
        margin-bottom: 1;
        height: 1fr;
        overflow: auto;
        border: solid $surface;
    }
    #diff-container Syntax {
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

    from typing import ClassVar

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss_reject", "Reject"),
    ]

    def __init__(self, event: EditProposalEvent, event_bus: EventBus) -> None:
        super().__init__()
        self.request_id = event.request_id
        self.file_path = event.file_path
        self.diff_text = event.diff
        self.event_bus = event_bus

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[bold]Edit Approval Required[/bold]")
            yield Label(f"File: {self.file_path}", id="file-info")

            with Vertical(id="diff-container"):
                yield Static(Syntax(self.diff_text, lexer="diff", theme="monokai", word_wrap=True))

            with Horizontal(id="buttons"):
                yield Button("Approve (Enter)", variant="primary", id="approve")
                yield Button("Reject (Esc)", variant="error", id="reject")

    def on_mount(self) -> None:
        """Focus the approve button by default."""
        self.query_one("#approve", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        approved = event.button.id == "approve"
        self.event_bus.publish(EditApprovalEvent(request_id=self.request_id, approved=approved))
        self.dismiss(approved)

    def action_dismiss_reject(self) -> None:
        """Handle escape key to reject."""
        self.event_bus.publish(EditApprovalEvent(request_id=self.request_id, approved=False))
        self.dismiss(False)
