"""Screen for answering multiple choice questions from the LLM."""

from __future__ import annotations

import contextlib

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from dendrophis.events import EventBus, MultipleChoiceResponseEvent


class MultipleChoiceScreen(ModalScreen[str | None]):
    """Modal dialog asking a multiple choice question."""

    DEFAULT_CSS = """
    MultipleChoiceScreen {
        align: center middle;
    }
    MultipleChoiceScreen:focus {
        outline: none;
    }
    #dialog {
        width: 60%;
        max-width: 80;
        height: auto;
        max-height: 80%;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    #question-title {
        width: 100%;
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 2;
    }
    .option-button {
        width: 100%;
        margin-bottom: 1;
    }
    #cancel-button {
        width: 100%;
        margin-top: 1;
        background: $error-darken-2;
    }
    """

    from typing import ClassVar

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss_cancel", "Cancel"),
    ]

    def __init__(self, request_id: str, question: str, options: list[str], event_bus: EventBus) -> None:
        super().__init__()
        self.request_id = request_id
        self.question = question
        self.options = options
        self.event_bus = event_bus

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question, id="question-title")

            for index, option in enumerate(self.options):
                yield Button(option, id=f"option_{index}", classes="option-button")

            yield Button("Cancel (Esc)", id="cancel-button", variant="error")

    def on_mount(self) -> None:
        """Focus the first option button by default."""
        if self.options:
            with contextlib.suppress(Exception):
                self.query_one("#option_0", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-button":
            self.action_dismiss_cancel()
            return

        # Extract index from button ID "option_X"
        button_id = event.button.id
        if button_id and button_id.startswith("option_"):
            try:
                index = int(button_id.split("_")[1])
                selected = self.options[index]
                self.event_bus.publish(
                    MultipleChoiceResponseEvent(request_id=self.request_id, selected_option=selected)
                )
                self.dismiss(selected)
            except (ValueError, IndexError):
                self.action_dismiss_cancel()

    def action_dismiss_cancel(self) -> None:
        """Handle escape key or cancel button to reject."""
        self.event_bus.publish(MultipleChoiceResponseEvent(request_id=self.request_id, selected_option=None))
        self.dismiss(None)
