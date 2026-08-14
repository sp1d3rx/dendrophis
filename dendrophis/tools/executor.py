from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dendrophis.tools.base import BaseTool
    from dendrophis.tools.registry import ToolRegistry


@dataclass
class ToolResult:
    """Result of a tool execution."""

    tool_call_id: str
    name: str
    content: str
    success: bool = True


class ToolExecutor:
    """Executes tool calls from LLM responses."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, tool_call: Any) -> ToolResult:
        """Execute a tool call and return its result."""
        # Log tool execution
        if os.environ.get("DENDROPHIS_TOOL_LOG") == "1":
            from dendrophis.session.chat import _tool_log

            _tool_log("=== TOOL EXECUTOR EXECUTE ===")
            _tool_log(f"Executing tool: {tool_call.name}(id={tool_call.id})")
            _tool_log(f"    Arguments: {tool_call.arguments!r}")

        # --- Safety: Automatic Backup ---
        try:
            parsed_arguments = (
                json.loads(tool_call.arguments) if tool_call.arguments and tool_call.arguments.strip() else {}
            )
            file_path = parsed_arguments.get("file_path")

            destructive_patterns = ["write", "edit", "replace", "delete", "remove"]
            if file_path and any(pattern in tool_call.name.lower() for pattern in destructive_patterns):
                target_path = Path(file_path)
                if target_path.exists() and target_path.suffix != ".bak":
                    shutil.copy2(target_path, target_path.with_suffix(target_path.suffix + ".bak"))
        except Exception as backup_error:
            print(f"[WARNING] Failed to create backup: {backup_error}", file=sys.stderr)
        # ---------------------------------

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
            if isinstance(raw_result, dict):
                content = json.dumps(raw_result, indent=2)
            elif isinstance(raw_result, str):
                try:
                    # Validate if it's already a JSON string
                    json.loads(raw_result)
                    content = raw_result
                except json.JSONDecodeError:
                    content = json.dumps({"result": raw_result})
            else:
                content = json.dumps({"result": raw_result})

            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=content,
                success=True,
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
