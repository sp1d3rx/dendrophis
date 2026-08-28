"""Interactive version of the AppendTool that requires human approval via the event bus."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from dendrophis.events.types import AppendApprovalEvent, AppendProposalEvent
from dendrophis.tools.builtins.filesystem import AppendTool
from dendrophis.tools.interactive.base import InteractiveBaseTool

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus


class InteractiveAppendTool(InteractiveBaseTool):
    """An AppendTool that proposes the append via the event bus and waits for approval."""

    def __init__(self, event_bus: IEventBus) -> None:
        super().__init__(
            event_bus=event_bus,
            base_tool=AppendTool(),
            approval_event_type=AppendApprovalEvent,
            preview_type="content",
        )

    async def execute(self, file_path: str, content: str) -> dict[str, Any]:
        try:
            if self.silent:
                return await self._base_tool.execute(file_path=file_path, content=content)

            # Propose via event bus and wait for human approval
            request_id = str(uuid.uuid4())
            proposal_event = AppendProposalEvent(
                request_id=request_id,
                file_path=file_path,
                content=content,
            )

            try:
                approved = await self._wait_for_approval(request_id, proposal_event)
            except TimeoutError:
                return {"error": "Append approval timed out after 5 minutes"}

            if approved:
                return await self._base_tool.execute(file_path=file_path, content=content)

            return {"error": "Append denied by user"}

        except Exception as exception_error:
            return {"error": str(exception_error)}
