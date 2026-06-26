"""Screen for human approval of Python code execution, showing the code for review."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from dendrophis.events import EventBus, PythonExecApprovalEvent, PythonExecProposalEvent


class PythonExecConfirmationScreen(ModalScreen[bool]):
    """Modal dialog showing Python code for execution approval."""

    DEFAULT_CSS = """
    PythonExecConfirmationScreen {
        align: center middle;
    }
    PythonExecConfirmationScreen:focus {
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
    #code-info {
        text-align: center;
        margin-bottom: 1;
        color: $accent;
    }
    #code-container {
        background: $surface-darken-1;
        margin-bottom: 1;
        height: 1fr;
        overflow: auto;
        border: solid $surface;
    }
    #code-container Static {
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

    def __init__(self, event: PythonExecProposalEvent, event_bus: EventBus) -> None:
        super().__init__()
        self.request_id = event.request_id
        self.code = event.code
        self.event_bus = event_bus

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[bold]Python Execution Approval Required[/bold]")
            yield Label("The agent wants to execute:", id="code-info")

            with Vertical(id="code-container"):
                yield Static(
                    self.code,
                    id="code-display",
                )

            with Horizontal(id="buttons"):
                yield Button("Approve (Enter)", variant="primary", id="approve")
                yield Button("Reject (Esc)", variant="error", id="reject")

    def on_mount(self) -> None:
        self.query_one("#approve", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        approved = event.button.id == "approve"
        self.event_bus.publish(PythonExecApprovalEvent(request_id=self.request_id, approved=approved))
        self.dismiss(approved)

    def action_dismiss_reject(self) -> None:
        self.event_bus.publish(PythonExecApprovalEvent(request_id=self.request_id, approved=False))
        self.dismiss(False)
