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
from dendrophis.tools.builtins.filesystem.utils import try_unescape
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
            if self.silent:
                return await self._base_tool.execute(
                    file_path=file_path,
                    old_string=old_string,
                    new_string=new_string,
                )

            path = Path(file_path)
            if not (path.exists() and path.is_file()):
                return {"error": f"Path is not a valid file: {file_path}"}

            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")

            if old_string not in content:
                unescaped_old = try_unescape(old_string)
                if unescaped_old != old_string and unescaped_old in content:
                    old_string = unescaped_old
                    new_string = try_unescape(new_string)
                else:
                    return {
                        "error": "old_string not found in file",
                        "hint": "Text must match exactly, using raw characters not escape sequences",
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
                return await self._base_tool.execute(
                    file_path=file_path,
                    old_string=old_string,
                    new_string=new_string,
                )

            return {"error": "Edit denied by user"}

        except Exception as exception_error:
            return {"error": str(exception_error)}
