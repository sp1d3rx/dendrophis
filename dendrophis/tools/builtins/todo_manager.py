"""Session-scoped Todo Manager that reacts to events."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

from dendrophis.events import EventBus, get_event_bus, listen
from dendrophis.events.types import TodoRequestEvent, TodoUpdatedEvent, WaitingForInputEvent


@dataclass(frozen=True, slots=True)
class TodoItem:
    """A single todo item."""

    id: str
    text: str
    done: bool = False
    completed_turns: int = 0


class TodoManager:
    """Manages an in-memory list of todo items for a session by reacting to events."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._todos: list[TodoItem] = []
        self._event_bus = event_bus or get_event_bus()
        # Subscribe to requests and turn completion
        self._events = self._event_bus.bind(self)

    @listen
    def _handle_request(self, event: TodoRequestEvent) -> None:
        """Handle incoming todo requests."""
        try:
            if event.action == "add":
                if not event.text:
                    return
                self.add(event.text)
            elif event.action == "toggle":
                if not event.todo_id:
                    return
                self.toggle(event.todo_id)
            elif event.action == "remove":
                if not event.todo_id:
                    return
                self.remove(event.todo_id)
            elif event.action == "list":
                # For 'list', we just ensure the UI has the latest state
                self._emit_change()
        except Exception:
            # In a real system, we might emit an error event
            pass

    @listen
    def _handle_waiting_input(self, event: WaitingForInputEvent) -> None:
        """Called at the end of a turn when the system is waiting for input.

        Increments completed_turns for done items, and deletes them if they've been
        completed for 2 or more turns.
        """
        updated = False
        new_todos = []
        for item in self._todos:
            if item.done:
                new_turns = item.completed_turns + 1
                if new_turns >= 2:
                    updated = True
                    # This item is deleted automatically
                    continue
                new_todos.append(
                    TodoItem(
                        id=item.id,
                        text=item.text,
                        done=item.done,
                        completed_turns=new_turns,
                    )
                )
                updated = True
            else:
                new_todos.append(item)

        if updated:
            self._todos = new_todos
            self._emit_change()

    def add(self, text: str) -> None:
        item = TodoItem(id=uuid.uuid4().hex, text=text.strip())
        self._todos.append(item)
        self._emit_change()

    def toggle(self, todo_id: str) -> None:
        for index, item in enumerate(self._todos):
            if item.id == todo_id:
                self._todos[index] = TodoItem(id=item.id, text=item.text, done=not item.done)
                self._emit_change()
                return
        raise ValueError(f"Todo item {todo_id} not found.")

    def remove(self, todo_id: str) -> None:
        original_len = len(self._todos)
        self._todos = [item for item in self._todos if item.id != todo_id]
        if len(self._todos) < original_len:
            self._emit_change()
        else:
            raise ValueError(f"Todo item {todo_id} not found.")

    def _emit_change(self) -> None:
        """Notify listeners that the todo list has changed."""
        self._event_bus.publish(TodoUpdatedEvent(todos=self.get_all()))

    def get_all(self) -> list[dict[str, Any]]:
        """Return all todo items as a list of dicts."""
        return [asdict(item) for item in self._todos]
