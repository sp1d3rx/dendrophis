"""A tool that manages a session-scoped todo list."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from dendrophis.events import get_event_bus
from dendrophis.events.types import TodoRequestEvent
from dendrophis.tools.base import BaseTool
from dendrophis.tools.names import ToolName

logger = logging.getLogger(__name__)


class TodoTool(BaseTool):
    """A tool that manages the session todo list."""

    def __init__(self, todo_manager: Any = None) -> None:
        super().__init__()
        self._todo_manager = todo_manager

    @property
    def name(self) -> str:
        return ToolName.TODO

    @property
    def description(self) -> str:
        return (
            "Manage an in-memory todo list for the current session. "
            "You can add, toggle, remove, and list todo items. "
            "Returns the current todo list after the action is applied."
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
        if action == "add" and not text:
            return {"status": "error", "action": action, "error": "'text' is required for action 'add'."}
        if action in ("toggle", "remove") and not todo_id:
            return {
                "status": "error",
                "action": action,
                "error": f"'todo_id' is required for action '{action}'.",
            }
        if action not in ("add", "toggle", "remove", "list"):
            return {"status": "error", "action": action, "error": f"Unknown todo action '{action}'."}

        if self._todo_manager is None:
            event_bus = get_event_bus()
            event_bus.publish(TodoRequestEvent(action=action, text=text, todo_id=todo_id))
            return {"status": "request_sent", "action": action}

        request_id = uuid.uuid4().hex
        try:
            if action == "add" and text is not None:
                self._todo_manager.add(text, request_id=request_id)
            elif action == "toggle" and todo_id is not None:
                self._todo_manager.toggle(todo_id, request_id=request_id)
            elif action == "remove" and todo_id is not None:
                self._todo_manager.remove(todo_id, request_id=request_id)
            elif action == "list":
                self._todo_manager._emit_change(request_id=request_id)
        except ValueError as value_error:
            return {"status": "error", "action": action, "error": str(value_error)}

        return {"status": "ok", "action": action, "todos": self._todo_manager.get_all()}
