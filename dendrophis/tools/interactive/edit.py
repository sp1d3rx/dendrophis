"""Interactive version of the EditTool that requires human approval via the event bus."""

from __future__ import annotations

import asyncio
import difflib
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dendrophis.events.types import (
    EditApprovalEvent,
    EditProposalEvent,
)
from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem import EditTool

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus


class InteractiveEditTool(BaseTool):
    """An EditTool that proposes changes via the event bus and waits for approval."""

    def __init__(self, event_bus: IEventBus) -> None:
        super().__init__()
        self._base_tool = EditTool()
        self._pending_edits: dict[str, asyncio.Event] = {}
        self._approval_results: dict[str, bool] = {}
        self._event_bus = event_bus
        self.silent: bool = False

        self._event_bus.subscribe(EditApprovalEvent, self._handle_approval_event)

    @property
    def self_confirming(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return self._base_tool.name

    @property
    def description(self) -> str:
        return self._base_tool.description + " (Interactive mode: requires human approval via diff preview)"

    @property
    def parameters(self) -> dict[str, Any]:
        return self._base_tool.parameters

    def _handle_approval_event(self, event: EditApprovalEvent) -> None:
        """Callback for when an EditApprovalEvent is received."""
        if event.request_id in self._pending_edits:
            self._approval_results[event.request_id] = event.approved
            self._pending_edits[event.request_id].set()

    async def execute(self, file_path: str, old_string: str, new_string: str) -> dict[str, Any]:
        try:
            path = Path(file_path)
            if not (path.exists() and path.is_file()):
                return {"error": f"Path is not a valid file: {file_path}"}

            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")

            if old_string not in content:
                return {"error": "old_string not found in file", "hint": "Text must match exactly"}

            count = content.count(old_string)
            if count > 1:
                return {"error": f"Found {count} occurrences", "hint": "Provide more context"}

            new_content = content.replace(old_string, new_string, 1)

            diff_lines = list(
                difflib.unified_diff(
                    content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{file_path}",
                    tofile=f"b/{file_path}",
                )
            )
            diff_text = "".join(diff_lines)

            if not diff_text:
                return {"success": True, "message": "No changes detected."}

            if self.silent:
                # Auto-approved: apply immediately, return diff stats
                added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
                removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
                await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")
                return {
                    "success": True,
                    "file": str(path),
                    "lines_added": added,
                    "lines_removed": removed,
                }

            # Propose via event bus and wait for human approval
            request_id = str(uuid.uuid4())
            approval_event = asyncio.Event()
            self._pending_edits[request_id] = approval_event

            self._event_bus.publish(
                EditProposalEvent(request_id=request_id, file_path=str(path), diff=diff_text, new_content=new_content)
            )

            try:
                await asyncio.wait_for(approval_event.wait(), timeout=300.0)
            except TimeoutError:
                return {"error": "Edit approval timed out after 5 minutes"}
            finally:
                self._pending_edits.pop(request_id, None)

            approved = self._approval_results.pop(request_id, False)
            if approved:
                await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")
                return {
                    "success": True,
                    "file": str(path),
                    "replaced": old_string[:100] + "..." if len(old_string) > 100 else old_string,
                }
            return {"error": "Edit denied by user"}

        except Exception as e:
            return {"error": str(e)}
