"""Interactive version of the PatchTool that requires human approval via the event bus."""

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
from dendrophis.tools.builtins.filesystem.patch import PatchTool
from dendrophis.tools.builtins.filesystem.utils import run_auto_lint
from dendrophis.tools.interactive.base import InteractiveBaseTool

if TYPE_CHECKING:
    from dendrophis.events.protocol import IEventBus


class InteractivePatchTool(InteractiveBaseTool):
    """A PatchTool that proposes multiple search-and-replace changes via the event bus and waits for approval."""

    def __init__(self, event_bus: IEventBus) -> None:
        super().__init__(
            event_bus=event_bus,
            base_tool=PatchTool(),
            approval_event_type=EditApprovalEvent,
            preview_type="diff",
        )

    async def execute(self, file_path: str, edits: list[dict[str, str]]) -> dict[str, Any]:
        try:
            path = Path(file_path)
            if not (path.exists() and path.is_file()):
                return {"error": f"Path is not a valid file: {file_path}"}

            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")

            # Try to unescape doubly-escaped sequences (common LLM mistake: \\n instead of \n)
            def _try_unescape(string_value: str) -> str:
                try:
                    return string_value.encode("raw_unicode_escape").decode("unicode_escape")
                except Exception:
                    return string_value.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")

            new_content = content
            for edit_index, edit in enumerate(edits):
                search_string = edit.get("search", "")
                replace_string = edit.get("replace", "")

                if search_string not in new_content:
                    unescaped_search = _try_unescape(search_string)
                    if unescaped_search != search_string and unescaped_search in new_content:
                        search_string = unescaped_search
                        replace_string = _try_unescape(replace_string)
                    else:
                        return {
                            "error": f"Search block at edit_index {edit_index} not found in file",
                            "hint": "Text must match exactly, using raw characters not escape sequences",
                        }

                count = new_content.count(search_string)
                if count > 1:
                    return {
                        "error": (
                            f"Ambiguous edit at edit_index {edit_index}: found {count} occurrences of the search block"
                        ),
                        "hint": "Provide more context for this search block to make it unique",
                    }

                new_content = new_content.replace(search_string, replace_string, 1)

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
                added_lines = sum(
                    1 for diff_line in diff_lines if diff_line.startswith("+") and not diff_line.startswith("+++")
                )
                removed_lines = sum(
                    1 for diff_line in diff_lines if diff_line.startswith("-") and not diff_line.startswith("---")
                )
                await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")
                lint_errors = await asyncio.to_thread(run_auto_lint, file_path)
                result = {
                    "success": True,
                    "file": str(path),
                    "lines_added": added_lines,
                    "lines_removed": removed_lines,
                }
                if lint_errors:
                    result["lint_errors"] = lint_errors
                    result["hint"] = "Code formatted/auto-fixed. Please fix remaining lint/syntax errors."
                return result

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
                return {"error": "Patch approval timed out after 5 minutes"}

            if approved:
                await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")
                lint_errors = await asyncio.to_thread(run_auto_lint, file_path)
                result = {
                    "success": True,
                    "file": str(path),
                    "applied_edits_count": len(edits),
                }
                if lint_errors:
                    result["lint_errors"] = lint_errors
                    result["hint"] = "Code formatted/auto-fixed. Please fix remaining lint/syntax errors."
                return result
            return {"error": "Patch denied by user"}

        except Exception as exception_error:
            return {"error": str(exception_error)}
