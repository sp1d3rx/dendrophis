"""Bash tool implementation."""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.names import ToolName


class BashTool(BaseTool):
    """Execute a bash command."""

    @property
    def name(self) -> str:
        return ToolName.BASH

    @property
    def description(self) -> str:
        return (
            "Execute a non-interactive bash command. DO NOT run commands that "
            "require user input (like 'vim' or 'top') as they will hang. "
            "Use with caution."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "REQUIRED. The bash command to execute",
                },
                "description": {
                    "type": "string",
                    "description": "REQUIRED. Description of what the command does",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in milliseconds (default 120000)",
                },
                "full_output": {
                    "type": "boolean",
                    "description": (
                        "Optional. True to retrieve full stdout/stderr without truncation (defaults to false)."
                    ),
                },
            },
            "required": ["command", "description"],
        }

    async def execute(
        self,
        command: str,
        description: str,
        timeout: int = 120000,
        full_output: bool = False,
    ) -> dict[str, Any]:
        try:
            from dendrophis.tools.bash_sandbox import BashSandbox

            simulation = BashSandbox().simulate(command)
            if simulation.dangerous:
                return {"error": f"Dangerous command blocked: {simulation.reason}"}

            # Reject commands that touch /dev/*
            if "/dev/" in command or command.startswith("/dev"):
                return {"error": f"Access to /dev blocked in command: {command}"}

            timeout_seconds = timeout / 1000
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )

            stdout = result.stdout if result.stdout else ""
            stderr = result.stderr if result.stderr else ""

            if not full_output:
                if len(stdout) > 2000:
                    stdout = stdout[:2000] + "\n... [Output truncated. Set 'full_output': true to get complete output]"
                if len(stderr) > 2000:
                    stderr = stderr[:2000] + "\n... [Output truncated. Set 'full_output': true to get complete output]"

            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "categories": [effect.category.value for effect in simulation.effects if hasattr(effect, "category")],
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Command timed out after {timeout}ms"}
        except Exception as exception_error:
            return {"error": str(exception_error)}
