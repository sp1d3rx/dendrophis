"""ApiKeyPrompt — modal screen shown when api_key is missing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class ApiKeyPromptScreen(ModalScreen[str]):
    """Blocks startup until the user provides an API key."""

    DEFAULT_CSS = """
    ApiKeyPromptScreen {
        align: center middle;
    }
    ApiKeyPromptScreen Vertical {
        width: 60;
        height: auto;
        padding: 2 4;
        background: $surface;
        border: round $accent;
    }
    ApiKeyPromptScreen Label { margin-bottom: 1; }
    ApiKeyPromptScreen Input { margin-bottom: 1; }
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[bold]No API key configured.[/bold]")
            yield Label(
                f"Provider: [accent]{self._session.config.llm.base_url}[/accent]\n"
                "Enter your API key below, or set [italic]DENDROPHIS_API_KEY[/italic] env var."
            )
            yield Input(placeholder="sk-...", password=True, id="key-input")
            yield Label("", id="error-label")
            yield Button("Continue", variant="primary", id="ok-btn")

    def on_mount(self) -> None:
        self.query_one("#key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._save(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok-btn":
            self._save(self.query_one("#key-input", Input).value)

    def _save(self, key: str) -> None:
        key = key.strip()
        if not key:
            self.query_one("#error-label", Label).update("[red]API key cannot be empty.[/red]")
            return
        self._session.config_loader._raw.setdefault("llm", {})["api_key"] = key
        self._session.config_loader.save()
        self._session.config.llm.api_key = key
        self._session.llm._config.api_key = key
        self.dismiss(key)
