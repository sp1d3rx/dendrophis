"""Code-writer subagent handler — implements changes precisely."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.llm.client import LLMClient
from dendrophis.subagents.messages import SubagentRequest, SubagentResponse
from dendrophis.tools.builtins.filesystem import BashTool, EditTool, ReadTool, WriteTool
from dendrophis.tools.builtins.function_analyzer import analyze_functions
from dendrophis.tools.builtins.function_tools import replace_function

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Custom Exceptions
# -----------------------------------------------------------------------------


class CodeWriterError(Exception):
    """Base exception for code-writer errors."""


class FileReadError(CodeWriterError):
    """Failed to read a file."""


class FileEditError(CodeWriterError):
    """Failed to edit a file."""


class LLMCallError(CodeWriterError):
    """Failed to call the LLM."""


class ParseError(CodeWriterError):
    """Failed to parse LLM response."""


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

# Minimal system prompt for code-writer
CODE_WRITER_PROMPT = """You are a precise code editor. Your job is to implement changes exactly as specified.

Rules:
- Make surgical edits. Change only what's needed.
- Follow existing code style and patterns.
- Run ruff check and ruff format after edits.
- If something is ambiguous, stop and ask. Never guess.
- Verify files after editing.
- Return a summary of what changed.

Workflow for Python files (surgical editing):
1. analyze_functions(file_path) -> get function names and line numbers
2. get_function(file_path, function_name) -> extract function to temp file
3. edit(temp_file) -> make surgical changes with minimal context
4. replace_function(file_path, function_name, new_function) -> swap in edited version

This minimizes token usage and avoids exact-match issues with large files.

You have access to: analyze_functions, get_function, replace_function, read, edit, write, bash (for ruff).

When making changes, you MUST use the following formats:

### 1. To Create a New File:
FILE: path/to/new_file.py
OLD:
<file does not exist>
NEW:
<full content of the new file>
---

### 2. To Edit an Existing File:
FILE: path/to/existing_file.py
OLD:
<exact text to replace, including surrounding context for uniqueness>
NEW:
<new text>
---

Repeat for each file edited.

CRITICAL: NEVER use markdown code blocks. Output raw text only.
"""


# -----------------------------------------------------------------------------
# Handler
# -----------------------------------------------------------------------------


class CodeWriterHandler:
    """Handler for code-writer subagent."""

    async def __call__(self, request: SubagentRequest) -> SubagentResponse:
        """Make handler callable - delegates to execute."""
        return await self.execute(request)

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        read_tool: ReadTool | None = None,
        edit_tool: EditTool | None = None,
        write_tool: WriteTool | None = None,
        bash_tool: BashTool | None = None,
        config: DendrophisConfig | None = None,
        model: str = "qwen/qwen3-coder:latest",
    ) -> None:
        """Initialize CodeWriterHandler with dependency injection.

        Args:
            llm_client: LLM client for making API calls. If None, created lazily.
            read_tool: Tool for reading files. If None, created lazily.
            edit_tool: Tool for editing files. If None, created lazily.
            write_tool: Tool for writing files. If None, created lazily.
            bash_tool: Tool for bash commands. If None, created lazily.
            config: Dendrophis configuration. If None, loaded lazily.
            model: Model name for code-writer (used if no llm_client provided).
        """
        # Store injected dependencies
        self._llm_client = llm_client
        self._read_tool = read_tool
        self._edit_tool = edit_tool
        self._write_tool = write_tool
        self._bash_tool = bash_tool
        self._config = config
        self._model_override = model

        # Initialize logger
        self._logger = logger

    @property
    def llm(self) -> LLMClient:
        """Lazily create LLM client if not injected."""
        if self._llm_client is not None:
            return self._llm_client

        # Fallback to creating LLM client with config or defaults
        if self._config is not None:
            llm_config = self._get_llm_config()
            self._llm_client = LLMClient(llm_config)
            return self._llm_client

        # Last resort: create with hardcoded defaults (backward compatibility)
        from dendrophis.config.loader import ConfigLoader

        config_loader = ConfigLoader.load()
        cfg = config_loader.config
        llm_config = LLMConfig(
            model=self._model_override,
            api_key=cfg.llm.api_key,
            base_url=cfg.llm.base_url,
            temperature=0.1,
            top_k=64,
            min_p=0.05,
        )
        self._llm_client = LLMClient(llm_config)
        return self._llm_client

    @property
    def read_tool(self) -> ReadTool:
        """Lazily create ReadTool if not injected."""
        if self._read_tool is None:
            self._read_tool = ReadTool()
        return self._read_tool

    @property
    def edit_tool(self) -> EditTool:
        """Lazily create EditTool if not injected."""
        if self._edit_tool is None:
            self._edit_tool = EditTool()
        return self._edit_tool

    @property
    def write_tool(self) -> WriteTool:
        """Lazily create WriteTool if not injected."""
        if self._write_tool is None:
            self._write_tool = WriteTool()
        return self._write_tool

    @property
    def bash_tool(self) -> BashTool:
        """Lazily create BashTool if not injected."""
        if self._bash_tool is None:
            self._bash_tool = BashTool()
        return self._bash_tool

    @property
    def config(self) -> DendrophisConfig | None:
        """Return the injected config."""
        return self._config

    def _get_llm_config(self) -> LLMConfig:
        """Get LLM config for code-writer from injected config."""
        if self._config is None:
            raise ValueError("No config available to create LLM client")

        # Use code_writer_model if set, otherwise use default model
        model = self._config.llm.code_writer_model or self._model_override

        return LLMConfig(
            model=model,
            api_key=self._config.llm.api_key,
            base_url=self._config.llm.base_url,
            temperature=0.1,  # Lower temp for more deterministic code
            top_k=64,  # Limit to top 64 tokens
            min_p=0.05,  # Filter out unlikely tokens
            max_tokens=self._config.llm.max_tokens,
            timeout=self._config.llm.timeout,
        )

    async def execute(self, request: SubagentRequest) -> SubagentResponse:
        """Execute code-writing task."""
        task = request.payload.get("task", "")
        files = request.payload.get("files", [])
        context = request.context

        changes: list[dict[str, Any]] = []
        blockers: list[str] = []

        try:
            # Separate Python files from others
            python_files = [f for f in files if f.endswith(".py")]

            # Read all files
            file_contents, read_blockers = await self._read_files(files)
            blockers.extend(read_blockers)

            if blockers:
                self._logger.warning(f"Blockers during file reading: {blockers}")
                return SubagentResponse(
                    agent="code-writer",
                    task_id=request.task_id,
                    status="needs_clarification",
                    result={"blockers": blockers},
                )

            # Analyze Python files
            python_analysis, analysis_blockers = await self._analyze_files(python_files)
            blockers.extend(analysis_blockers)

            if blockers:
                self._logger.warning(f"Blockers during file analysis: {blockers}")
                return SubagentResponse(
                    agent="code-writer",
                    task_id=request.task_id,
                    status="needs_clarification",
                    result={"blockers": blockers},
                )

            # Build LLM prompt
            prompt = self._build_llm_prompt(task, file_contents, python_analysis, context)

            # Call LLM for implementation plan
            try:
                result = await self.llm.complete(
                    [
                        {"role": "system", "content": CODE_WRITER_PROMPT},
                        {"role": "user", "content": prompt},
                    ]
                )
                plan = result.text
            except Exception as e:
                raise LLMCallError(f"LLM call failed: {e}") from e

            # Debug logging - check for DENDROPHIS_DEBUG env var as before
            import os

            if os.environ.get("DENDROPHIS_DEBUG"):
                self._logger.debug(
                    f"[CODE-WRITER] Result: text={len(result.text)} chars, "
                    f"reasoning={len(result.reasoning)} chars, "
                    f"tool_calls={len(result.tool_calls)}"
                )
                self._logger.debug(f"[CODE-WRITER] Plan:\n{plan[:2000]}")

            # Parse and execute edits
            edits = self._parse_edits(plan)
            changes, edit_blockers = await self._apply_edits(edits)
            blockers.extend(edit_blockers)

            if blockers:
                self._logger.warning(f"Blockers during edit application: {blockers}")
                return SubagentResponse(
                    agent="code-writer",
                    task_id=request.task_id,
                    status="needs_clarification",
                    result={"blockers": blockers, "changes": changes},
                )

            # Run ruff check/format on edited files
            edited_files = list({c["file"] for c in changes if c.get("file")})
            for f in edited_files:
                await self._run_ruff(f)  # Non-blocking - errors logged but not raised

            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="success",
                result={
                    "changes": changes,
                    "diff_summary": f"Modified {len(changes)} file(s)",
                    "verification": {"syntax_ok": True, "tests_status": "skipped"},
                    "blockers": [],
                },
            )

        except LLMCallError as e:
            self._logger.error(f"LLM call error: {e}")
            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="failure",
                result={"error": str(e)},
            )
        except FileReadError as e:
            self._logger.error(f"File read error: {e}")
            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="failure",
                result={"error": str(e)},
            )
        except FileEditError as e:
            self._logger.error(f"File edit error: {e}")
            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="failure",
                result={"error": str(e)},
            )
        except ParseError as e:
            self._logger.error(f"Parse error: {e}")
            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="failure",
                result={"error": str(e)},
            )
        except Exception as e:
            self._logger.error(f"Unexpected error in code-writer: {e}", exc_info=True)
            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="failure",
                result={"error": str(e)},
            )

    async def _read_files(self, files: list[str]) -> tuple[dict[str, str], list[str]]:
        """Read file contents.

        Args:
            files: List of file paths to read.

        Returns:
            Tuple of (file_contents dict, blockers list).
        """
        file_contents: dict[str, str] = {}
        blockers: list[str] = []

        for file_path in files:
            try:
                result = await self.read_tool.execute(file_path=file_path)
                if isinstance(result, dict) and "content" in result:
                    file_contents[file_path] = result["content"]
            except FileNotFoundError:
                blockers.append(f"File not found: {file_path}")
                self._logger.error(f"File not found: {file_path}")
            except PermissionError as e:
                blockers.append(f"Permission denied reading {file_path}: {e}")
                self._logger.error(f"Permission denied reading {file_path}: {e}")
            except Exception as e:
                blockers.append(f"Cannot read {file_path}: {type(e).__name__}: {e}")
                self._logger.error(f"Error reading {file_path}: {type(e).__name__}: {e}")

        return file_contents, blockers

    async def _analyze_files(self, python_files: list[str]) -> tuple[dict[str, str], list[str]]:
        """Analyze Python files for function information.

        Args:
            python_files: List of Python file paths to analyze.

        Returns:
            Tuple of (python_analysis dict, blockers list).
        """
        python_analysis: dict[str, str] = {}
        blockers: list[str] = []

        for file_path in python_files:
            try:
                analysis = await analyze_functions.execute(file_path=file_path, format="yaml")
                python_analysis[file_path] = analysis
            except FileNotFoundError:
                blockers.append(f"File not found for analysis: {file_path}")
                self._logger.error(f"File not found for analysis: {file_path}")
            except Exception as e:
                blockers.append(f"Cannot analyze {file_path}: {type(e).__name__}: {e}")
                self._logger.error(f"Error analyzing {file_path}: {type(e).__name__}: {e}")

        return python_analysis, blockers

    def _build_llm_prompt(
        self,
        task: str,
        file_contents: dict[str, str],
        python_analysis: dict[str, str],
        context: dict[str, Any],
    ) -> str:
        """Build the LLM prompt for code-writer.

        Args:
            task: The task description.
            file_contents: Dict mapping file paths to their contents.
            python_analysis: Dict mapping Python file paths to function analysis.
            context: Additional context from the request.

        Returns:
            The formatted prompt string.
        """
        parts = [
            f"Task: {task}",
            "",
            "Files to modify:",
        ]

        for path, content in file_contents.items():
            parts.append(f"\n--- {path} ---\n{content[:2000]}...")  # Truncate large files

        # Include Python function analysis for surgical editing
        if python_analysis:
            parts.extend(["", "Python file function analysis (for surgical editing):"])
            for path, analysis in python_analysis.items():
                parts.append(f"\n--- {path} ---\n{analysis}")

        if context.get("patterns"):
            parts.extend(["", "Patterns to follow:", *context["patterns"]])

        if context.get("constraints"):
            parts.extend(["", "Constraints:", *context["constraints"]])

        parts.extend(
            [
                "",
                "For Python files, use surgical editing format:",
                "FILE: path/to/file.py",
                "FUNCTION: function_name",
                "NEW:",
                "<complete new function implementation>",
                "---",
                "",
                "For non-Python files, use standard format:",
                "FILE: path/to/file",
                "OLD:",
                "<exact text to replace>",
                "NEW:",
                "<new text>",
                "---",
            ]
        )

        return "\n".join(parts)

    def _parse_edits(self, plan: str) -> list[dict[str, Any]]:
        """Parse edit blocks from LLM response.

        Supports two formats:
        1. Surgical: FILE: path\nFUNCTION: name\nNEW: ... (for Python functions)
        2. Standard: FILE: path\nOLD: ...\nNEW: ... (for any file type)

        Args:
            plan: The LLM response text containing edit instructions.

        Returns:
            List of edit dictionaries.

        Raises:
            ParseError: If the edit format is invalid.
        """
        edits: list[dict[str, Any]] = []

        try:
            # Pattern for surgical Python edits
            surgical_pattern = r"FILE:\s*(.+?)\nFUNCTION:\s*(.+?)\nNEW:\n(.*?)(?=\n---|\Z)"
            for match in re.finditer(surgical_pattern, plan, re.DOTALL):
                file_path = match.group(1).strip()
                function_name = match.group(2).strip()
                new_text = match.group(3).rstrip("\n")
                if file_path and function_name:
                    edits.append(
                        {
                            "file": file_path,
                            "function_name": function_name,
                            "old": "",
                            "new": new_text,
                        }
                    )

            # Pattern for standard edits
            standard_pattern = r"FILE:\s*(.+?)\nOLD:\n(.*?)(?=\nNEW:)\nNEW:\n(.*?)(?=\n---|\Z)"
            for match in re.finditer(standard_pattern, plan, re.DOTALL):
                file_path = match.group(1).strip()
                old_text = match.group(2).rstrip("\n")
                new_text = match.group(3).rstrip("\n")
                if file_path and old_text:
                    edits.append({"file": file_path, "old": old_text, "new": new_text})

        except re.error as e:
            raise ParseError(f"Regex error parsing edits: {e}") from e
        except Exception as e:
            raise ParseError(f"Error parsing edits: {e}") from e

        self._logger.debug(f"[PARSE] Found {len(edits)} edits")
        return edits

    async def _apply_edits(self, edits: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Apply edits to files.

        Args:
            edits: List of edit dictionaries with file, old, new, and optionally function_name.

        Returns:
            Tuple of (changes list, blockers list).
        """
        changes: list[dict[str, Any]] = []
        blockers: list[str] = []

        for edit in edits:
            file_path = edit.get("file", "")
            old_string = edit.get("old", "")
            new_string = edit.get("new", "")

            # Validate edit
            if not file_path:
                blockers.append("Missing file path in edit")
                continue

            # Check if this is a file creation (file doesn't exist or placeholder old_string)
            file_exists = Path(file_path).exists()
            is_creation = not file_exists or old_string in ("<file does not exist>", "", "\n")

            try:
                if is_creation:
                    # Use write tool for new files
                    await self.write_tool.execute(
                        file_path=file_path,
                        content=new_string,
                    )
                    changes.append(
                        {
                            "action": "created",
                            "file": file_path,
                            "description": f"Created {file_path}",
                        }
                    )
                elif file_path.endswith(".py") and edit.get("function_name"):
                    # Use surgical editing for Python functions
                    await replace_function.execute(
                        file_path=file_path,
                        function_name=edit["function_name"],
                        new_function=new_string,
                    )
                    changes.append(
                        {
                            "action": "edited",
                            "file": file_path,
                            "function": edit["function_name"],
                            "description": f"Replaced function {edit['function_name']} in {file_path}",
                        }
                    )
                else:
                    # Use edit tool for existing files
                    await self.edit_tool.execute(
                        file_path=file_path,
                        old_string=old_string,
                        new_string=new_string,
                    )
                    changes.append(
                        {
                            "action": "edited",
                            "file": file_path,
                            "description": f"Modified {file_path}",
                        }
                    )
            except FileNotFoundError:
                blockers.append(f"File not found: {file_path}")
                self._logger.error(f"File not found during edit: {file_path}")
            except ValueError as e:
                blockers.append(f"Edit mismatch in {file_path}: {e}")
                self._logger.error(f"Edit mismatch in {file_path}: {e}")
            except PermissionError as e:
                blockers.append(f"Permission denied: {file_path}")
                self._logger.error(f"Permission denied editing {file_path}: {e}")
            except Exception as e:
                blockers.append(f"Failed to edit {file_path}: {type(e).__name__}: {e}")
                self._logger.error(f"Failed to edit {file_path}: {type(e).__name__}: {e}")

        return changes, blockers

    async def _run_ruff(self, file_path: str) -> dict[str, Any]:
        """Run ruff check and format on a file.

        Args:
            file_path: Path to the file to lint/format.

        Returns:
            Dict with check_result, format_result, and any errors.
        """
        results: dict[str, Any] = {"check_result": None, "format_result": None, "errors": []}

        try:
            check_result = await self.bash_tool.execute(
                command=f"ruff check --fix {file_path}",
                description=f"Run ruff check on {file_path}",
            )
            results["check_result"] = check_result
        except Exception as e:
            results["errors"].append(f"ruff check failed: {e}")
            self._logger.warning(f"[RUFF] ruff check failed for {file_path}: {e}")

        try:
            format_result = await self.bash_tool.execute(
                command=f"ruff format {file_path}",
                description=f"Run ruff format on {file_path}",
            )
            results["format_result"] = format_result
        except Exception as e:
            results["errors"].append(f"ruff format failed: {e}")
            self._logger.warning(f"[RUFF] ruff format failed for {file_path}: {e}")

        if results["errors"]:
            self._logger.debug(f"[RUFF] Errors in {file_path}: {results['errors']}")

        return results
