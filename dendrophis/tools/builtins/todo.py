"""A tool that sends requests to manage a session-scoped todo list."""

from __future__ import annotations

from typing import Any

from dendrophis.events import get_event_bus
from dendrophis.events.types import TodoRequestEvent
from dendrophis.tools.base import BaseTool


class TodoTool(BaseTool):
    """A tool that emits events to manage a todo list."""

    def __init__(self, todo_manager: Any = None) -> None:
        super().__init__()

    @property
    def name(self) -> str:
        return "todo"

    @property
    def description(self) -> str:
        return (
            "Manage an in-memory todo list for the current session. "
            "You can add, toggle, remove, and list todo items by emitting events."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "toggle", "remove", "list"],
                    "description": "The action to perform on the todo list.",
                },
                "text": {
                    "type": "string",
                    "description": "The text of the todo item (required for 'add').",
                },
                "todo_id": {
                    "type": "string",
                    "description": "The unique ID of the todo item (required for 'toggle' and 'remove').",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, text: str | None = None, todo_id: str | None = None) -> Any:
        # Emitting the request event.
        # The TodoManager (running elsewhere) will pick this up.
        get_event_bus().publish(TodoRequestEvent(action=action, text=text, todo_id=todo_id))

        # We return a placeholder. The actual result will come via TodoUpdatedEvent
        # which the UI will listen to. For the LLM, we acknowledge the request.
        return {"status": "request_sent", "action": action}
