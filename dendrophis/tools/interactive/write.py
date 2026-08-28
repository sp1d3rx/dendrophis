"""Interactive version of the WriteTool that requires human approval via the event bus."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from dendrophis.events.types import WriteApprovalEvent, WriteProposalEvent
from dendrophis.tools.builtins.filesystem import WriteTool
from dendrophis.tools.interactive.base import InteractiveBaseTool

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus


class InteractiveWriteTool(InteractiveBaseTool):
    """A WriteTool that proposes the new file content via the event bus and waits for approval."""

    def __init__(self, event_bus: IEventBus) -> None:
        super().__init__(
            event_bus=event_bus,
            base_tool=WriteTool(),
            approval_event_type=WriteApprovalEvent,
            preview_type="content",
        )

    async def execute(self, file_path: str, content: str) -> dict[str, Any]:
        try:
            if self.silent:
                return await self._base_tool.execute(file_path=file_path, content=content)

            # Propose via event bus and wait for human approval
            request_id = str(uuid.uuid4())
            proposal_event = WriteProposalEvent(
                request_id=request_id,
                file_path=file_path,
                content=content,
            )

            try:
                approved = await self._wait_for_approval(request_id, proposal_event)
            except TimeoutError:
                return {"error": "Write approval timed out after 5 minutes"}

            if approved:
                return await self._base_tool.execute(file_path=file_path, content=content)

            return {"error": "Write denied by user"}

        except Exception as exception_error:
            return {"error": str(exception_error)}
