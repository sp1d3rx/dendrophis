"""Tool for analyzing Python files and returning function information."""

from __future__ import annotations

import ast
from typing import Any

import ruamel.yaml as yaml

from dendrophis.tools.base import BaseTool


class FunctionAnalyzerTool(BaseTool):
    """Tool that analyzes Python files and returns function information."""

    @property
    def name(self) -> str:
        """The unique name of the tool."""
        return "analyze_functions"

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

        try:
            # Read the file content
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # Parse the AST
            tree = ast.parse(content, filename=file_path)

            # Find all function definitions
            functions = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Get the start line (line number of the 'def' statement)
                    start_line = node.lineno

                    # Get the end line (last line of the function body)
                    end_line = node.end_lineno if node.end_lineno else node.lineno

                    # Calculate indent level from the source code
                    lines = content.splitlines()
                    def_line = lines[start_line - 1]  # lineno is 1-indexed
                    indent_level = len(def_line) - len(def_line.lstrip())

                    functions.append(
                        {
                            "name": node.name,
                            "start_line": start_line,
                            "end_line": end_line,
                            "indent_level": indent_level,
                        }
                    )

            # Sort by line number for consistent output
            functions.sort(key=lambda x: x["start_line"])

            # Return in requested format
            if output_format == "yaml":
                y = yaml.YAML()
                y.default_flow_style = False
                import io

                stream = io.StringIO()
                y.dump(functions, stream)
                return stream.getvalue()
            return functions

        except FileNotFoundError as e:
            raise FileNotFoundError(f"File not found: {file_path}") from e
        except SyntaxError as e:
            raise SyntaxError(f"Syntax error in file {file_path}: {e!s}") from e
        except Exception as e:
            raise Exception(f"Error analyzing file {file_path}: {e!s}") from e


# Create an instance to be imported
analyze_functions = FunctionAnalyzerTool()
