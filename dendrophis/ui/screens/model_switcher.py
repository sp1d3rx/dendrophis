"""ModelSwitcherScreen — searchable list of available models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class ModelSwitcherScreen(ModalScreen[tuple[str, bool]]):
    """Modal for switching the active LLM model using virtualized OptionList."""

    from typing import ClassVar

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss_modal", "Cancel"),
    ]

    DEFAULT_CSS = """
    ModelSwitcherScreen {
        align: center middle;
    }
    #switcher-container {
        width: 80%;
        height: 80%;
        max-width: 120;
        max-height: 40;
        border: thick $accent;
        background: $panel;
        padding: 1;
        overflow: hidden;
    }
    #model-list {
        height: 1fr;
        border: solid $panel-lighten-1;
        margin-top: 1;
        background: $surface;
    }
    #switcher-status {
        color: $text-muted;
        height: 1;
        margin: 0 1;
    }
    #clear-context {
        height: 1;
        margin: 1 0 0 0;
        background: $surface;
        border: none;
        padding: 0 1;
    }
    #clear-context > .toggle--label {
        color: $text;
    }
    #clear-context > .toggle--button {
        color: $accent;
        background: $panel-darken-2;
    }
    #clear-context.-on > .toggle--button {
        color: $success;
        background: $panel-darken-2;
    }
    .panel-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._all_models = []
        self._is_loading = False

    def compose(self) -> ComposeResult:
        with Vertical(id="switcher-container"):
            yield Static("Switch Model (Text-Generation Only)", classes="panel-title")
            yield Input(placeholder="Search text models...", id="model-search")
            yield Label("", id="switcher-status")
            yield OptionList(id="model-list")
            yield Checkbox("Clear conversation on switch", id="clear-context")

    def on_mount(self) -> None:
        """Populate the list and focus the search input on first display."""
        self.query_one("#model-search", Input).focus()

        status = self.query_one("#switcher-status", Label)

        # Initial filter list from whatever models we have
        self._all_models = [m for m in self._session.models if m.is_text_generation]
        if not self._all_models:
            self.run_worker(self._load_models())
        else:
            status.update(f"Showing {len(self._all_models)} cached text models.")
            self._update_list("")
            # Even if we have models, refresh in background if they are just well-known defaults
            if len(self._session.models) <= 6:  # WELL_KNOWN_MODELS size
                self.run_worker(self._load_models())

    async def _load_models(self) -> None:
        """Fetch models in background and update UI."""
        if self._is_loading:
            return
        self._is_loading = True
        status = self.query_one("#switcher-status", Label)
        status.update("Fetching models from API...")

        try:
            await self._session.fetch_models()
            self._all_models = [m for m in self._session.models if m.is_text_generation]
            status.update(f"Loaded {len(self._all_models)} text models.")
            self._update_list(self.query_one("#model-search", Input).value)
        except Exception as e:
            status.update(f"Error loading models: {e}")
        finally:
            self._is_loading = False

    def _update_list(self, filter_text: str) -> None:
        """Rebuild the model list using virtualized Options."""
        option_list = self.query_one("#model-list", OptionList)
        option_list.clear_options()

        filter_text = filter_text.lower()
        sorted_models = sorted(
            self._all_models, key=lambda model: (model.id != self._session.config.llm.model, model.id)
        )

        for model in sorted_models:
            if filter_text in model.id.lower():
                # Create a rich table for the label to keep the nice layout
                table = Table.grid(expand=True)
                table.add_column()  # ID
                table.add_column(justify="right")  # Meta

                ctx_val = model.context_window
                ctx_str = f"{ctx_val // 1024}k" if ctx_val > 0 else "?"

                price_val = model.cost_per_1m
                price_str = f" • ${price_val:.2f}/1M" if price_val > 0 else " • free"

                # Tool support indicator
                tool_str = "[green]✓ tools[/]" if model.supports_tools else "[red]✗ tools[/]"

                table.add_row(model.id, f"{tool_str} [dim]{ctx_str} ctx{price_str}[/dim]")
                option_list.add_option(Option(table, id=model.id))

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the model list as the user types."""
        if event.input.id == "model-search":
            self._update_list(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with (model_id, clear_context) tuple."""
        if event.option.id:
            clear = self.query_one("#clear-context", Checkbox).value
            self.dismiss((str(event.option.id), clear))

    def action_dismiss_modal(self) -> None:
        """Close the switcher without changing the model."""
        self.dismiss()
