from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
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


class ToolExecutor:
    """Executes tool calls from LLM responses."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, tool_call: Any) -> ToolResult:
        """Execute a tool call and return its result."""
        # Log tool execution
        import sys

        if os.environ.get("DENDROPHIS_TOOL_LOG") == "1":
            from dendrophis.session.chat import _tool_log

            _tool_log("=== TOOL EXECUTOR EXECUTE ===")
            _tool_log(f"Executing tool: {tool_call.name}(id={tool_call.id})")
            _tool_log(f"    Arguments: {tool_call.arguments!r}")

        # --- Safety: Automatic Backup ---
        try:
            args = json.loads(tool_call.arguments) if tool_call.arguments and tool_call.arguments.strip() else {}
            file_path = args.get("file_path")

            destructive_patterns = ["write", "edit", "replace", "delete", "remove"]
            if file_path and any(p in tool_call.name.lower() for p in destructive_patterns):
                from pathlib import Path

                target = Path(file_path)
                if target.exists() and target.suffix != ".bak":
                    shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        except Exception as error:
            print(f"[WARNING] Failed to create backup: {error}", file=sys.stderr)
        # ---------------------------------

        tool = self._registry.get(tool_call.name)
        if not tool:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps({"error": f"Unknown tool: {tool_call.name}"}),
            )

        try:
            # Parse arguments
            args = {}
            if tool_call.arguments and tool_call.arguments.strip():
                args = json.loads(tool_call.arguments)

            # Execute the tool
            result = await tool.execute(**args)

            # Convert result to JSON string.
            # Ensure it is valid JSON by wrapping non-dict results.
            if isinstance(result, dict):
                content = json.dumps(result, indent=2)
            elif isinstance(result, str):
                try:
                    # Validate if it's already a JSON string
                    json.loads(result)
                    content = result
                except json.JSONDecodeError:
                    content = json.dumps({"result": result})
            else:
                content = json.dumps({"result": result})

            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=content,
            )
        except json.JSONDecodeError as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps({"error": f"Invalid JSON arguments: {e}"}),
            )
        except TypeError as e:
            # Likely missing required arguments or invalid argument names
            error_msg = str(e)
            hint = self._build_hint(tool)
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps(
                    {
                        "error": f"Execution failed: {error_msg}",
                        "hint": hint,
                    },
                    indent=2,
                ),
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=json.dumps({"error": f"Execution failed: {e}"}),
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
