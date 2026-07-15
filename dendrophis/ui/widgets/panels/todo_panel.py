"""A sidebar panel for managing and viewing todos."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Checkbox, Input, Label

from dendrophis.events import EventBus, listen
from dendrophis.events.types import TodoRequestEvent, TodoUpdatedEvent
from dendrophis.ui.widgets.panels.event_panel import EventPanel

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class TodoPanel(EventPanel):
    """A sidebar panel for managing and viewing todos."""

    DEFAULT_CSS = """
    TodoPanel {
        height: auto;
        overflow-y: auto;
    }

    #todo-list-container {
        height: auto;
    }

    .todo-item {
        height: auto;
        align: left middle;
        padding: 0;
        margin: 0 0 1 0;
    }

    .todo-item Checkbox {
        min-width: 3;
        width: auto;
        padding: 0;
        margin: 0;
        background: transparent;
    }

    .todo-item Label {
        width: 1fr;
        padding-left: 1;
        padding-right: 1;
    }

    #todo-input-container {
        height: auto;
        margin-top: 1;
        padding: 0 1;
        border-top: solid $panel-darken-2;
    }

    #todo-input {
        width: 1fr;
    }
    """

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        self._todos: list[dict[str, Any]] = []

    def on_unmount(self) -> None:
        self._events.unsubscribe_all()

    @listen
    def _on_todo_updated(self, event: TodoUpdatedEvent) -> None:
        """Handle todo list updates from the manager."""
        self._todos = event.todos
        self.refresh_ui()

    def compose(self) -> ComposeResult:
        """Compose the stable panel structure."""
        yield Vertical(id="todo-list-container")
        yield Vertical(
            Input(placeholder="Add todo...", id="todo-input"),
            id="todo-input-container",
        )

    def refresh_ui(self) -> None:
        """Re-render the todo list inside the stable container."""
        try:
            list_container = self.query_one("#todo-list-container", Vertical)
        except Exception:
            return  # Not mounted yet

        # Clear existing items inside the list container
        list_container.query("*").remove()

        # Mount new items
        if not self._todos:
            list_container.mount(Label("No todos yet.", classes="todo-empty"))
        else:
            for item in self._todos:
                list_container.mount(self._create_todo_row(item))

    def _create_todo_row(self, item: dict[str, Any]) -> Horizontal:
        todo_id = item["id"]
        text = item["text"]
        done = item["done"]

        # Checkbox for toggling
        done_checkbox = Checkbox(value=done, id=f"cb-{todo_id}")

        # Text label
        text_label = Label(text, classes="todo-text")
        if done:
            text_label.styles.text_style = "strike"

        return Horizontal(done_checkbox, text_label, classes="todo-item")

    def _emit_toggle(self, todo_id: str) -> None:
        self._event_bus.publish(TodoRequestEvent(action="toggle", todo_id=todo_id))

    @on(Checkbox.Changed)
    def _on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        checkbox_id = event.checkbox.id
        if checkbox_id and checkbox_id.startswith("cb-"):
            todo_id = checkbox_id[3:]
            self._emit_toggle(todo_id)

    @on(Input.Submitted)
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text:
            self._event_bus.publish(TodoRequestEvent(action="add", text=text))
            event.input.value = ""
        else:
            event.input.value = ""

    def render_value(self) -> str:
        # This is used for the initial/fallback view, but we manage our own internal widget tree.
        return f"Todos: {len(self._todos)}"

    def on_mount(self) -> None:
        super().on_mount()
        self._events = self._event_bus.bind(self)
        self.refresh_ui()

    def update_value(self, *args, **kwargs) -> None:
        """Force a refresh of the panel's display."""
        self.refresh_ui()
