from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dendrophis.tools.base import BaseTool
    from dendrophis.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Result of a tool execution."""

    tool_call_id: str
    name: str
    content: str
    success: bool = True


def is_tool_error(content: str) -> bool:
    """Return True if a tool result content indicates a structured failure.

    The single canonical error predicate for the whole codebase: a result is an
    error iff its content parses to a JSON object with an "error" key. Plain
    text (including text that merely mentions the word "error") is never an
    error. Re-exported by ``dendrophis.session.tools`` for existing importers.
    """
    try:
        parsed_payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed_payload, dict) and "error" in parsed_payload


class ToolExecutor:
    """Executes tool calls from LLM responses."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, tool_call: Any) -> ToolResult:
        """Execute a tool call and return its result."""
        # Log tool execution
        if os.environ.get("DENDROPHIS_TOOL_LOG") == "1":
            from dendrophis.utils import _tool_log

            _tool_log("=== TOOL EXECUTOR EXECUTE ===")
            _tool_log(f"Executing tool: {tool_call.name}(id={tool_call.id})")
            _tool_log(f"    Arguments: {tool_call.arguments!r}")

        # File-modifying tools (write/edit/...) create their own .bak backups
        # before writing, so no generic backup heuristic is applied here.

        tool = self._registry.get(tool_call.name)
        if not tool:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps({"error": f"Unknown tool: {tool_call.name}"}),
                success=False,
            )

        try:
            # Parse arguments
            call_arguments = {}
            if tool_call.arguments and tool_call.arguments.strip():
                call_arguments = json.loads(tool_call.arguments)

            # Execute the tool
            raw_result = await tool.execute(**call_arguments)

            # Convert result to JSON string.
            # Ensure it is valid JSON by wrapping non-dict results.
            # Success is derived with the canonical is_tool_error predicate so
            # the executor and every downstream consumer agree on what an
            # error result is.
            if isinstance(raw_result, dict):
                content = json.dumps(raw_result, indent=2)
                success = not is_tool_error(content)
            elif isinstance(raw_result, str):
                try:
                    # Validate if it's already a JSON string
                    json.loads(raw_result)
                except json.JSONDecodeError:
                    content = json.dumps({"result": raw_result})
                    success = True
                else:
                    content = raw_result
                    success = not is_tool_error(raw_result)
            else:
                content = json.dumps({"result": raw_result})
                success = True

            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=content,
                success=success,
            )
        except json.JSONDecodeError as json_decode_error:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps({"error": f"Invalid JSON arguments: {json_decode_error}"}),
                success=False,
            )
        except TypeError as type_error:
            # Likely missing required arguments or invalid argument names
            error_message = str(type_error)
            argument_hint = self._build_hint(tool)
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps(
                    {
                        "error": f"Execution failed: {error_message}",
                        "hint": argument_hint,
                    },
                    indent=2,
                ),
                success=False,
            )
        except Exception as execution_error:
            logger.exception("Unhandled exception executing tool '%s'", tool_call.name)
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps({"error": f"Execution failed: {execution_error}"}),
                success=False,
            )

    def _build_hint(self, tool: BaseTool) -> str:
        """Build a hint about required arguments from the tool schema."""
        params = tool.parameters.get("properties", {})
        required = tool.parameters.get("required", [])

        if not params:
            return "This tool takes no arguments."

        lines = ["Arguments required:"]
        for name, schema in params.items():
            is_req = name in required
            desc = schema.get("description", "No description")
            req_marker = "(required)" if is_req else "(optional)"
            lines.append(f"  - {name}: {desc} {req_marker}")

        return "\n".join(lines)
