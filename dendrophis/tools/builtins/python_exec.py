"""Tool for executing arbitrary Python code."""

from __future__ import annotations

import io
import sys
import traceback
from typing import Any

from dendrophis.tools.base import BaseTool
from dendrophis.tools.names import ToolName


class PythonExecTool(BaseTool):
    """Tool that executes arbitrary Python code and returns output and local variables."""

    @property
    def name(self) -> str:
        """The unique name of the tool."""
        return ToolName.EXECUTE_CODE

    @property
    def description(self) -> str:
        """A detailed description of what the tool does."""
        return (
            "Execute arbitrary Python code and return the standard output, standard error, and local variables defined."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema defining the tool's parameters."""
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python code to execute.",
                },
                "description": {
                    "type": "string",
                    "description": "REQUIRED. Description of what the code execution does",
                },
            },
            "required": ["code", "description"],
        }

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the Python code."""
        code_string = kwargs.get("code")
        description_string = kwargs.get("description")
        if not code_string:
            raise ValueError("code parameter is required")
        if not description_string:
            raise ValueError("description parameter is required")

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        original_stdout = sys.stdout
        original_stderr = sys.stderr

        local_variables: dict[str, Any] = {}
        global_variables = {"__builtins__": __builtins__}

        success = False
        exception_string = ""

        try:
            sys.stdout = stdout_buffer
            sys.stderr = stderr_buffer
            exec(code_string, global_variables, local_variables)
            success = True
        except Exception:
            exception_string = traceback.format_exc()
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

        serializable_locals = {}
        for local_key, local_value in local_variables.items():
            try:
                if isinstance(local_value, (int, float, str, bool, list, dict, type(None))):
                    serializable_locals[local_key] = local_value
                else:
                    serializable_locals[local_key] = str(local_value)
            except Exception:
                serializable_locals[local_key] = f"<Unrepresentable value: {type(local_value).__name__}>"

        return {
            "success": success,
            "stdout": stdout_buffer.getvalue(),
            "stderr": stderr_buffer.getvalue(),
            "exception": exception_string,
            "local_variables": serializable_locals,
        }


# Create instance
execute_code = PythonExecTool()
