"""Tools for function-level code operations."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from dendrophis.tools.base import BaseTool


class GetFunctionTool(BaseTool):
    """Extract a function's source code from a Python file."""

    @property
    def name(self) -> str:
        return "get_function"

    @property
    def description(self) -> str:
        return "Extract a function's source code by name from a Python file"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the Python file"},
                "function_name": {"type": "string", "description": "Name of the function to extract"},
            },
            "required": ["file_path", "function_name"],
        }

    async def execute(self, **kwargs: Any) -> Any:
        file_path = kwargs.get("file_path")
        function_name = kwargs.get("function_name")

        if not file_path or not function_name:
            raise ValueError("file_path and function_name are required")

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()

        # Parse AST to find function
        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError as e:
            raise SyntaxError(f"Syntax error in {file_path}: {e}") from e

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                start_line = node.lineno
                end_line = node.end_lineno if node.end_lineno else start_line

                # Extract the function source
                function_lines = lines[start_line - 1 : end_line]
                function_source = "\n".join(function_lines)

                # Create temp file path: .temp/<file_stem>/<function_name>.py
                temp_dir = Path(".temp") / path.stem
                temp_dir.mkdir(parents=True, exist_ok=True)
                temp_file = temp_dir / f"{function_name}.py"
                temp_file.write_text(function_source, encoding="utf-8")

                return {
                    "temp_file": str(temp_file),
                    "function_name": function_name,
                    "source_file": file_path,
                }

        raise ValueError(f"Function '{function_name}' not found in {file_path}")


class ReplaceFunctionTool(BaseTool):
    """Replace a function in a Python file with new implementation."""

    @property
    def name(self) -> str:
        return "replace_function"

    @property
    def description(self) -> str:
        return "Replace a function's implementation by reading from a file"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the Python file containing the function to replace"},
                "function_name": {"type": "string", "description": "Name of the function to replace"},
                "new_function_file": {"type": "string", "description": "Path to file containing the new function implementation"},
            },
            "required": ["file_path", "function_name", "new_function_file"],
        }

    async def execute(self, **kwargs: Any) -> Any:
        file_path = kwargs.get("file_path")
        function_name = kwargs.get("function_name")
        new_function_file = kwargs.get("new_function_file")

        if not all([file_path, function_name, new_function_file]):
            raise ValueError("file_path, function_name, and new_function_file are required")

        # Read the new function from file
        new_func_path = Path(new_function_file)
        if not new_func_path.exists():
            raise FileNotFoundError(f"New function file not found: {new_function_file}")
        new_function = new_func_path.read_text(encoding="utf-8")

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()

        # Parse AST to find function
        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError as e:
            raise SyntaxError(f"Syntax error in {file_path}: {e}") from e

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                start_line = node.lineno
                end_line = node.end_lineno if node.end_lineno else start_line

                # Build new content
                new_lines = lines[: start_line - 1] + new_function.splitlines() + lines[end_line:]
                new_content = "\n".join(new_lines)

                # Write back
                path.write_text(new_content, encoding="utf-8")
                return {"success": True, "replaced": function_name, "lines": f"{start_line}-{end_line}"}

        raise ValueError(f"Function '{function_name}' not found in {file_path}")


# Create instances
get_function = GetFunctionTool()
replace_function = ReplaceFunctionTool()
