"""A tool that sends requests to manage a session-scoped todo list."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from dendrophis.events import get_event_bus
from dendrophis.events.types import TodoRequestEvent, TodoUpdatedEvent
from dendrophis.tools.base import BaseTool

logger = logging.getLogger(__name__)

_UPDATE_TIMEOUT = 5.0


class TodoTool(BaseTool):
    """A tool that emits events to manage a todo list."""

    def __init__(self, todo_manager: Any = None) -> None:
        super().__init__()
        self._todo_manager = todo_manager

    @property
    def name(self) -> str:
        return "todo"

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
        # Validate up front; the manager silently ignores malformed requests,
        # so this is the only place such errors can surface to the LLM.
        if action == "add" and not text:
            return {"status": "error", "action": action, "error": "'text' is required for action 'add'."}
        if action in ("toggle", "remove") and not todo_id:
            return {
                "status": "error",
                "action": action,
                "error": f"'todo_id' is required for action '{action}'.",
            }

        event_bus = get_event_bus()

        if self._todo_manager is None:
            # Defensive fallback: fire-and-forget (factory always injects the manager).
            event_bus.publish(TodoRequestEvent(action=action, text=text, todo_id=todo_id))
            return {"status": "request_sent", "action": action}

        # The bus dispatches handlers asynchronously, so await the TodoUpdatedEvent
        # that TodoManager publishes after applying the request. Correlate by
        # request_id so concurrent todo ops (parallel tools) don't cross-talk.
        request_id = uuid.uuid4().hex
        updated = asyncio.Event()
        result: dict[str, Any] = {}

        def _on_updated(event: TodoUpdatedEvent) -> None:
            if event.request_id != request_id:
                return
            result["todos"] = event.todos
            updated.set()

        subscription = event_bus.subscribe(TodoUpdatedEvent, _on_updated)
        try:
            event_bus.publish(TodoRequestEvent(action=action, text=text, todo_id=todo_id, request_id=request_id))
            await asyncio.wait_for(updated.wait(), timeout=_UPDATE_TIMEOUT)
        except TimeoutError:
            # The mutation may have landed even if the event didn't round-trip;
            # fall back to reading the manager directly.
            logger.warning("Timed out waiting for TodoUpdatedEvent; reading manager state directly.")
            result["todos"] = self._todo_manager.get_all()
        finally:
            subscription.unsubscribe()

        return {"status": "ok", "action": action, "todos": result["todos"]}
