"""Base class for interactive tools that require human approval."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from dendrophis.tools.base import BaseTool

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus


class InteractiveBaseTool(BaseTool):
    """Base class handling event-waiting and approval boilerplate for interactive tools."""

    def __init__(
        self,
        event_bus: IEventBus,
        base_tool: BaseTool,
        approval_event_type: type,
        preview_type: str,
    ) -> None:
        super().__init__()
        self._base_tool = base_tool
        self._pending_approvals: dict[str, asyncio.Event] = {}
        self._approval_results: dict[str, bool] = {}
        self._event_bus = event_bus
        self._preview_type = preview_type
        self.silent: bool = False

        self._event_bus.subscribe(approval_event_type, self._handle_approval_event)

    @property
    def self_confirming(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return self._base_tool.name

    @property
    def description(self) -> str:
        return (
            self._base_tool.description
            + f" (Interactive mode: requires human approval via {self._preview_type} preview)"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return self._base_tool.parameters

    def _handle_approval_event(self, event: Any) -> None:
        """Callback for when an approval event is received from the event bus."""
        if event.request_id in self._pending_approvals:
            self._approval_results[event.request_id] = event.approved
            self._pending_approvals[event.request_id].set()

    async def _wait_for_approval(self, request_id: str, proposal_event: Any) -> bool:
        """Publish a proposal event and wait for human approval."""
        approval_event = asyncio.Event()
        self._pending_approvals[request_id] = approval_event
        self._event_bus.publish(proposal_event)
        try:
            await asyncio.wait_for(approval_event.wait(), timeout=300.0)
        finally:
            self._pending_approvals.pop(request_id, None)
        return self._approval_results.pop(request_id, False)
