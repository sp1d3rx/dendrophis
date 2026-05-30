"""Screen for human approval of new file writes, showing the content."""

from __future__ import annotations

from pathlib import Path

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from dendrophis.events import EventBus, WriteApprovalEvent, WriteProposalEvent

_EXT_TO_LEXER: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".html": "html",
    ".css": "css",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".sql": "sql",
    ".xml": "xml",
}


def _lexer_for(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return _EXT_TO_LEXER.get(ext, "text")


class WriteConfirmationScreen(ModalScreen[bool]):
    """Modal dialog showing file content for write approval."""

    DEFAULT_CSS = """
    WriteConfirmationScreen {
        align: center middle;
    }
    WriteConfirmationScreen:focus {
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

    from typing import ClassVar

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss_reject", "Reject"),
    ]

    def __init__(self, event: WriteProposalEvent, event_bus: EventBus) -> None:
        super().__init__()
        self.request_id = event.request_id
        self.file_path = event.file_path
        self.content = event.content
        self.event_bus = event_bus

    def compose(self) -> ComposeResult:
        lexer = _lexer_for(self.file_path)
        with Vertical(id="dialog"):
            yield Label("[bold]Write Approval Required[/bold]")
            yield Label(f"New file: {self.file_path}", id="file-info")

            with Vertical(id="content-container"):
                yield Static(Syntax(self.content, lexer=lexer, theme="monokai", word_wrap=False, line_numbers=True))

            with Horizontal(id="buttons"):
                yield Button("Approve (Enter)", variant="primary", id="approve")
                yield Button("Reject (Esc)", variant="error", id="reject")

    def on_mount(self) -> None:
        self.query_one("#approve", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        approved = event.button.id == "approve"
        self.event_bus.publish(WriteApprovalEvent(request_id=self.request_id, approved=approved))
        self.dismiss(approved)

    def action_dismiss_reject(self) -> None:
        self.event_bus.publish(WriteApprovalEvent(request_id=self.request_id, approved=False))
        self.dismiss(False)
