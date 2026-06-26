"""Interactive version of the PythonExecTool that requires human approval via the event bus."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from dendrophis.events.types import PythonExecApprovalEvent, PythonExecProposalEvent
from dendrophis.tools.builtins.python_exec import PythonExecTool
from dendrophis.tools.interactive.base import InteractiveBaseTool

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus


class InteractivePythonExecTool(InteractiveBaseTool):
    """A PythonExecTool that proposes code execution via the event bus and waits for approval."""

    def __init__(
        self,
        event_bus: IEventBus,
        no_interactive: bool = False,
    ) -> None:
        super().__init__(
            event_bus=event_bus,
            base_tool=PythonExecTool(),
            approval_event_type=PythonExecApprovalEvent,
            preview_type="python code",
        )
        self._no_interactive = no_interactive

    async def execute(self, code: str, description: str) -> dict[str, Any]:
        """Execute Python code after human approval."""
        try:
            # Skip interactive flow entirely if no_interactive is set
            if self._no_interactive or self.silent:
                return await self._base_tool.execute(code=code, description=description)

            # Propose via event bus and wait for human approval
            request_id = str(uuid.uuid4())
            proposal_event = PythonExecProposalEvent(
                request_id=request_id,
                code=code,
            )

            try:
                approved = await self._wait_for_approval(request_id, proposal_event)
            except TimeoutError:
                return {"error": "Python execution approval timed out after 5 minutes"}

            if approved:
                return await self._base_tool.execute(code=code, description=description)

            return {"error": "Python execution denied by user"}

        except Exception as error:
            return {"error": str(error)}
