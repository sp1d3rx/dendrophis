"""Interactive version of the WriteTool that requires human approval via the event bus."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dendrophis.events.types import WriteApprovalEvent, WriteProposalEvent
from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem import WriteTool

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus


class InteractiveWriteTool(BaseTool):
    """A WriteTool that proposes the new file content via the event bus and waits for approval."""

    def __init__(self, event_bus: IEventBus) -> None:
        super().__init__()
        self._base_tool = WriteTool()
        self._pending_writes: dict[str, asyncio.Event] = {}
        self._approval_results: dict[str, bool] = {}
        self._event_bus = event_bus
        self.silent: bool = False

        self._event_bus.subscribe(WriteApprovalEvent, self._handle_approval_event)

    @property
    def self_confirming(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return self._base_tool.name

    @property
    def description(self) -> str:
        return self._base_tool.description + " (Interactive mode: requires human approval via content preview)"

    @property
    def parameters(self) -> dict[str, Any]:
        return self._base_tool.parameters

    def _handle_approval_event(self, event: WriteApprovalEvent) -> None:
        if event.request_id in self._pending_writes:
            self._approval_results[event.request_id] = event.approved
            self._pending_writes[event.request_id].set()

    async def execute(self, file_path: str, content: str) -> dict[str, Any]:
        try:
            path = Path(file_path)

            try:
                resolved = path.resolve()
                cwd = Path.cwd().resolve()
                if not str(resolved).startswith(str(cwd)):
                    return {"error": f"File path must be within working directory: {file_path}"}
            except Exception:
                pass

            if self.silent:
                # Auto-approved: write immediately, return stats
                path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(path.write_text, content, encoding="utf-8")
                return {
                    "success": True,
                    "file": str(path),
                    "lines_written": len(content.splitlines()),
                    "written_bytes": len(content.encode("utf-8")),
                }

            request_id = str(uuid.uuid4())
            approval_event = asyncio.Event()
            self._pending_writes[request_id] = approval_event

            self._event_bus.publish(WriteProposalEvent(request_id=request_id, file_path=str(path), content=content))

            try:
                await asyncio.wait_for(approval_event.wait(), timeout=300.0)
            except TimeoutError:
                return {"error": "Write approval timed out after 5 minutes"}
            finally:
                self._pending_writes.pop(request_id, None)

            approved = self._approval_results.pop(request_id, False)
            if approved:
                path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(path.write_text, content, encoding="utf-8")
                return {
                    "success": True,
                    "file": str(path),
                    "lines_written": len(content.splitlines()),
                    "written_bytes": len(content.encode("utf-8")),
                }
            return {"error": "Write denied by user"}

        except Exception as error:
            return {"error": str(error)}
