"""Interactive version of the AppendTool that requires human approval via the event bus."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dendrophis.events.types import AppendApprovalEvent, AppendProposalEvent
from dendrophis.tools.builtins.filesystem import AppendTool
from dendrophis.tools.builtins.filesystem.utils import run_auto_lint
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
            path = Path(file_path)
            resolved = path.resolve()
            cwd = Path.cwd().resolve()
            if not resolved.is_relative_to(cwd):
                return {"error": f"File path must be within working directory: {file_path}"}

            if self.silent:
                # Auto-approved: append immediately, return stats
                path.parent.mkdir(parents=True, exist_ok=True)

                def _append() -> int:
                    with path.open("a", encoding="utf-8") as f:
                        f.write(content)
                    return len(content.encode("utf-8"))

                written_bytes = await asyncio.to_thread(_append)
                lint_errors = await asyncio.to_thread(run_auto_lint, file_path)
                result = {
                    "success": True,
                    "file": str(path),
                    "appended_bytes": written_bytes,
                    "appended_lines": len(content.splitlines()),
                }
                if lint_errors:
                    result["lint_errors"] = lint_errors
                    result["hint"] = "Code formatted/auto-fixed. Please fix remaining lint/syntax errors."
                return result

            # Propose via event bus and wait for human approval
            request_id = str(uuid.uuid4())
            proposal_event = AppendProposalEvent(
                request_id=request_id,
                file_path=str(path),
                content=content,
            )

            try:
                approved = await self._wait_for_approval(request_id, proposal_event)
            except TimeoutError:
                return {"error": "Append approval timed out after 5 minutes"}

            if approved:
                path.parent.mkdir(parents=True, exist_ok=True)

                def _append() -> int:
                    with path.open("a", encoding="utf-8") as f:
                        f.write(content)
                    return len(content.encode("utf-8"))

                written_bytes = await asyncio.to_thread(_append)
                lint_errors = await asyncio.to_thread(run_auto_lint, file_path)
                result = {
                    "success": True,
                    "file": str(path),
                    "appended_bytes": written_bytes,
                    "appended_lines": len(content.splitlines()),
                }
                if lint_errors:
                    result["lint_errors"] = lint_errors
                    result["hint"] = "Code formatted/auto-fixed. Please fix remaining lint/syntax errors."
                return result
            return {"error": "Append denied by user"}

        except Exception as error:
            return {"error": str(error)}
