"""Tool for analyzing Python files and returning function information."""

from __future__ import annotations

import ast
import asyncio
import io
from pathlib import Path
from typing import Any

import ruamel.yaml as yaml

from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem.utils import is_blocked_path
from dendrophis.tools.names import ToolName


class FunctionAnalyzerTool(BaseTool):
    """Tool that analyzes Python files and returns function information."""

    @property
    def name(self) -> str:
        """The unique name of the tool."""
        return ToolName.ANALYZE_FUNCTIONS

    @property
    def description(self) -> str:
        """A detailed description of what the tool does."""
        return "Analyze Python file and return function locations and indentation as YAML"

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema defining the tool's parameters."""
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the Python file to analyze"},
                "format": {
                    "type": "string",
                    "enum": ["yaml", "json"],
                    "description": "Output format (default: yaml)",
                },
            },
            "required": ["file_path"],
        }

    async def execute(self, **kwargs: Any) -> Any:
        """Execute the tool with the given arguments."""
        file_path = kwargs.get("file_path")
        output_format = kwargs.get("format", "yaml")
        if not file_path:
            raise ValueError("file_path parameter is required")

        error_message = is_blocked_path(file_path)
        if error_message:
            return {"error": error_message}

        try:
            target_path = Path(file_path)
            content = await asyncio.to_thread(target_path.read_text, encoding="utf-8")
            source_lines = content.splitlines()
            tree = ast.parse(content, filename=file_path)

            functions = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start_line = node.lineno
                    end_line = node.end_lineno if node.end_lineno else node.lineno
                    def_line = source_lines[start_line - 1]
                    indent_level = len(def_line) - len(def_line.lstrip())

                    functions.append(
                        {
                            "name": node.name,
                            "start_line": start_line,
                            "end_line": end_line,
                            "indent_level": indent_level,
                        }
                    )

            functions.sort(key=lambda function_info: function_info["start_line"])

            if output_format == "yaml":
                yaml_instance = yaml.YAML()
                yaml_instance.default_flow_style = False
                stream = io.StringIO()
                yaml_instance.dump(functions, stream)
                return stream.getvalue()
            return functions

        except FileNotFoundError as file_error:
            raise FileNotFoundError(f"File not found: {file_path}") from file_error
        except SyntaxError as syntax_error:
            raise SyntaxError(f"Syntax error in file {file_path}: {syntax_error!s}") from syntax_error
        except Exception as analysis_error:
            raise RuntimeError(f"Error analyzing file {file_path}: {analysis_error!s}") from analysis_error


analyze_functions = FunctionAnalyzerTool()
