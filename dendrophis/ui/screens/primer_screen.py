"""PrimerScreen — manage tracked files in the project primer."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Static

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class PrimerScreen(Screen):
    """Full-screen primer file manager."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss_modal", "Close"),
    ]

    DEFAULT_CSS = """
    PrimerScreen {
        width: 100%;
        height: 100%;
        background: $surface;
    }
    #primer-container {
        width: 100%;
        height: 1fr;
        padding: 1 2;
    }
    .section-label {
        margin-top: 1;
        margin-bottom: 1;
        border-top: solid $primary 30%;
        padding-top: 1;
    }
    #file-scroll {
        height: 1fr;
        border: solid $panel-lighten-1;
        background: $panel;
        padding: 0 1;
        margin-bottom: 1;
        scrollbar-gutter: stable;
    }
    .file-row {
        height: 3;
        margin: 0;
        color: $text;
    }
    .file-row:focus {
        color: $accent;
    }
    #add-row {
        height: 3;
        margin-top: 1;
    }
    #add-input {
        width: 1fr;
        margin-right: 1;
    }
    #add-btn {
        width: 12;
    }
    #status-label {
        height: 1;
        color: $text-muted;
        margin-bottom: 1;
    }
    #actions {
        height: auto;
        dock: bottom;
        margin-top: 1;
    }
    #save-btn {
        margin-right: 1;
    }
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._file_paths: list[str] = []
        self._id_to_path: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="primer-container"):
            yield Static("Project Primer — Tracked Files", classes="section-label")
            yield Label("", id="status-label")
            with VerticalScroll(id="file-scroll"):
                pass
            with Horizontal(id="add-row"):
                yield Input(placeholder="File path to add (relative to project root)…", id="add-input")
                yield Button("Add File", id="add-btn", variant="primary")
            with Horizontal(id="actions"):
                yield Button("Save", id="save-btn", variant="success")
                yield Button("Cancel (Esc)", id="close-btn")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_list()
        self.query_one("#add-input", Input).focus()

    def _refresh_list(self) -> None:
        from dendrophis.memory.project import detect_project_root, load_primer

        root = detect_project_root()
        scroll = self.query_one("#file-scroll", VerticalScroll)
        scroll.remove_children()

        if root is None:
            self._file_paths = []
            self.query_one("#status-label", Label).update("No project root detected.")
            return

        primer = load_primer(root.name)
        if primer is None or not primer.key_files:
            self._file_paths = []
            self.query_one("#status-label", Label).update("No files tracked yet. Add one below.")
            return

        self._file_paths = [e.path for e in primer.key_files]
        self._id_to_path = {}
        count = len(self._file_paths)
        self.query_one("#status-label", Label).update(f"{count} file(s) tracked — uncheck to remove")
        for i, path in enumerate(self._file_paths):
            widget_id = f"cb-{i}"
            self._id_to_path[widget_id] = path
            scroll.mount(Checkbox(path, value=True, id=widget_id, classes="file-row"))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        checked = sum(1 for cb in self.query(".file-row") if isinstance(cb, Checkbox) and cb.value)
        unchecked = len(self._file_paths) - checked
        status = self.query_one("#status-label", Label)
        if unchecked:
            status.update(f"{checked} file(s) tracked — {unchecked} pending removal (Save to apply)")
        else:
            status.update(f"{checked} file(s) tracked — uncheck to remove")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-btn":
            self._add_file()
        elif event.button.id == "save-btn":
            self._save_and_close()
        elif event.button.id == "close-btn":
            self.dismiss()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "add-input":
            self._add_file()

    def _save_and_close(self) -> None:
        for cb in self.query(".file-row"):
            if isinstance(cb, Checkbox) and not cb.value:
                path = self._id_to_path[cb.id]
                self._session.untrack_file(path)
        self.dismiss()

    def _add_file(self) -> None:
        inp = self.query_one("#add-input", Input)
        path = inp.value.strip()
        if not path:
            return
        status = self.query_one("#status-label", Label)
        ok = self._session.track_file(path)
        if ok:
            inp.value = ""
            self._refresh_list()
        else:
            status.update(f"[red]Could not add '{path}' — file may not exist.[/red]")

    def action_dismiss_modal(self) -> None:
        self.dismiss()
