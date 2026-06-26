"""CalibrationPromptScreen — modal screen shown when a model needs calibration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

if TYPE_CHECKING:
    pass


class CalibrationPromptScreen(ModalScreen[bool]):
    """Modal screen prompting the user to calibrate a newly selected model."""

    DEFAULT_CSS = """
    CalibrationPromptScreen {
        align: center middle;
    }
    CalibrationPromptScreen Vertical {
        width: 65;
        height: auto;
        padding: 2 4;
        background: $surface;
        border: round $accent;
    }
    CalibrationPromptScreen Label {
        margin-bottom: 1;
        width: 100%;
    }
    CalibrationPromptScreen Horizontal {
        align: right middle;
        margin-top: 1;
        height: auto;
    }
    CalibrationPromptScreen Button {
        margin-left: 2;
    }
    """

    def __init__(self, model_id: str) -> None:
        super().__init__()
        self._model_id = model_id

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[bold]Model Calibration Recommended[/bold]")
            yield Label(
                f"The model [accent]{self._model_id}[/accent] has not been calibrated.\n\n"
                "Calibration detects capability quirks (such as streaming, tool support, "
                "and prompt caching keys) and configures optimal defaults."
            )
            with Horizontal():
                yield Button("Skip", variant="default", id="skip-btn")
                yield Button("Calibrate Now", variant="primary", id="calibrate-btn")

    def on_mount(self) -> None:
        self.query_one("#calibrate-btn", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "calibrate-btn":
            self.dismiss(True)
        else:
            self.dismiss(False)
