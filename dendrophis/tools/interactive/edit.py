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
from dendrophis.tools.builtins.filesystem import EditTool
from dendrophis.tools.interactive.base import InteractiveBaseTool

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus


class InteractiveEditTool(InteractiveBaseTool):
    """An EditTool that proposes changes via the event bus and waits for approval."""

    def __init__(self, event_bus: IEventBus) -> None:
        super().__init__(
            event_bus=event_bus,
            base_tool=EditTool(),
            approval_event_type=EditApprovalEvent,
            preview_type="diff",
        )

    async def execute(self, file_path: str, old_string: str, new_string: str) -> dict[str, Any]:
        try:
            path = Path(file_path)
            if not (path.exists() and path.is_file()):
                return {"error": f"Path is not a valid file: {file_path}"}

            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")

            if old_string not in content:
                return {
                    "error": "old_string not found in file",
                    "hint": "Text must match exactly",
                }

            count = content.count(old_string)
            if count > 1:
                return {
                    "error": f"Found {count} occurrences",
                    "hint": "Provide more context",
                }

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
                added = sum(
                    1 for diff_line in diff_lines if diff_line.startswith("+") and not diff_line.startswith("+++")
                )
                removed = sum(
                    1 for diff_line in diff_lines if diff_line.startswith("-") and not diff_line.startswith("---")
                )
                await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")
                return {
                    "success": True,
                    "file": str(path),
                    "lines_added": added,
                    "lines_removed": removed,
                }

            # Propose via event bus and wait for human approval
            request_id = str(uuid.uuid4())
            proposal_event = EditProposalEvent(
                request_id=request_id,
                file_path=str(path),
                diff=diff_text,
                new_content=new_content,
            )

            try:
                approved = await self._wait_for_approval(request_id, proposal_event)
            except TimeoutError:
                return {"error": "Edit approval timed out after 5 minutes"}

            if approved:
                await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")
                return {
                    "success": True,
                    "file": str(path),
                    "replaced": (old_string[:100] + "..." if len(old_string) > 100 else old_string),
                }
            return {"error": "Edit denied by user"}

        except Exception as exception_error:
            return {"error": str(exception_error)}
