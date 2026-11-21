"""Bash tool implementation."""

from __future__ import annotations

import asyncio
import re
import subprocess
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.bash_sandbox import BashSandbox
from dendrophis.tools.names import ToolName

# Matches /dev/null so it can be ignored by the /dev/* block check.
_NULL_REDIRECT = re.compile(r"/dev/null\b")
# Matches actual root /dev or /dev/... references, not relative paths like tests/dev/foo.py.
_DEV_PATH_PATTERN = re.compile(r"(?:^|[\s><=|;&('\"])/dev(?:/|$|\s|['\"])")


class BashTool(BaseTool):
    """Execute a bash command."""

    def __init__(self) -> None:
        self._precomputed_simulations: dict[str, Any] = {}

    def set_precomputed_simulation(self, command: str, simulation: Any) -> None:
        """Store a precomputed BashSandbox simulation from the permission layer."""
        if len(self._precomputed_simulations) >= 64:
            self._precomputed_simulations.clear()
        self._precomputed_simulations[command] = simulation

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

    def execution_timeout(self, arguments: dict[str, Any]) -> float | None:
        raw_timeout = arguments.get("timeout") if isinstance(arguments, dict) else None
        timeout_ms = raw_timeout if isinstance(raw_timeout, (int, float)) and raw_timeout > 0 else 120000
        return max(1.0, float(timeout_ms) / 1000.0)

    async def execute(
        self,
        command: str,
        description: str,
        timeout: int = 120000,
        full_output: bool = False,
    ) -> dict[str, Any]:
        try:
            simulation = self._precomputed_simulations.pop(command, None)
            if simulation is None:
                simulation = BashSandbox().simulate(command)
            if simulation.dangerous:
                return {"error": f"Dangerous command blocked: {simulation.reason}"}

            stripped = _NULL_REDIRECT.sub("", command)
            if _DEV_PATH_PATTERN.search(stripped):
                return {
                    "error": (
                        f"Access to /dev blocked in command: {command}. "
                        "Only /dev/null is allowed; redirect stderr with `>/dev/null` "
                        "or write to a temp file instead."
                    )
                }

            effective_timeout = timeout if isinstance(timeout, (int, float)) and timeout > 0 else 120000
            timeout_seconds = effective_timeout / 1000
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
