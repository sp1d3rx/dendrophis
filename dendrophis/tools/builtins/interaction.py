"""Interactive tools for communicating with the user."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from dendrophis.events import MultipleChoiceRequestEvent, MultipleChoiceResponseEvent
from dendrophis.tools.base import BaseTool
from dendrophis.tools.names import ToolName

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus


class AskMultipleChoiceTool(BaseTool):
    """Tool to ask the user a multiple choice question."""

    USER_RESPONSE_TIMEOUT_SECONDS = 300.0

    def __init__(self, event_bus: IEventBus) -> None:
        self._event_bus = event_bus

    @property
    def permission_controlled(self) -> bool:
        return False

    def execution_timeout(self, arguments: dict[str, Any]) -> float | None:
        return self.USER_RESPONSE_TIMEOUT_SECONDS

    @property
    def name(self) -> str:
        return ToolName.ASK_MULTIPLE_CHOICE

    @property
    def description(self) -> str:
        return "Ask the user a multiple choice question. Halts execution until the user selects an option."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "REQUIRED. The question to ask the user",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "REQUIRED. A list of strings representing the possible options",
                },
            },
            "required": ["question", "options"],
        }

    async def execute(self, question: str, options: list[str]) -> dict[str, Any]:
        """Ask the user a multiple choice question and wait for response."""
        if not options:
            return {"error": "Options list cannot be empty"}

        request_id = str(uuid.uuid4())
        running_loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = running_loop.create_future()

        def _on_response(event: MultipleChoiceResponseEvent) -> None:
            if event.request_id == request_id and not future.done():
                running_loop.call_soon_threadsafe(future.set_result, event.selected_option)

        self._event_bus.subscribe(MultipleChoiceResponseEvent, _on_response)

        try:
            self._event_bus.publish(
                MultipleChoiceRequestEvent(
                    request_id=request_id,
                    question=question,
                    options=options,
                )
            )

            selected = await future

            if selected is None:
                return {"error": "User cancelled or escaped the question prompt"}
            return {"selected_option": selected}
        finally:
            self._event_bus.unsubscribe(MultipleChoiceResponseEvent, _on_response)
