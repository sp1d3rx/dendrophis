"""Code-writer subagent handler — agentic tool-based worker."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.llm.client import LLMClient, TurnResult
from dendrophis.tools.executor import ToolExecutor
from dendrophis.tools.registry import ToolRegistry

from ..messages import SubagentRequest, SubagentResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for the CodeWriter agent
# ---------------------------------------------------------------------------

CODE_WRITER_SYSTEM_PROMPT = """You are Dendrophis CodeWriter, an expert autonomous coding subagent.

Your goal is to inspect code, implement requested changes surgically, verify your work, and provide a clear summary.

### Execution Workflow:
1. **Explore & Understand First**:
   - Inspect files before modifying them. Use `read_file(file_path, offset, limit)` to read existing content.
   - Use `glob(pattern)` or `list_dir(path)` to locate files if you are unsure of their exact location.
   - Use `ripgrep(pattern)` to search for function definitions, variable names, or references.

2. **Make Targeted, Surgical Edits**:
   - For replacing exact text in existing files: use `edit(file_path, old_string, new_string)`.
     * Provide 3 to 5 lines of surrounding context in `old_string` to ensure a unique match.
     * DO NOT use escaped representations like `\\n` or `\\t` unless you are searching for literal backslashes.
       Use real newlines.
   - For multiple search/replace edits in one file: use `patch(file_path, edits=[...])`.
   - For replacing whole Python functions: use `edit_function(file_path, function_name, new_source)`.
   - For creating new files: use `write_file(file_path, content)` or `write(file_path, content)`.
   - For appending to the end of files: use `append(file_path, content)`.

3. **Verify Your Work**:
   - After editing, verify your changes by reading the modified section with `read_file`.
   - Run tests or linting with `bash` (e.g. `pytest tests/...` or `ruff check file.py`) when relevant.

4. **Error Recovery & Clarification**:
   - If a tool fails (e.g. "old_string not found" or "Ambiguous edit"), do NOT repeat the same tool call.
     Re-read the file with `read_file`, inspect the actual file contents, adjust your context, and retry.
   - If the task requirements are contradictory, fundamentally ambiguous, or missing critical specifications
     that cannot be deduced from the codebase, call `clarify(questions=[...])`.

5. **Python Coding Standards (Raymond Hettinger Principles)**:
   - **Concept Chunking**: Structure logic into cohesive, bite-sized conceptual chunks at a single level of abstraction.
     Avoid monolithic multi-responsibility functions or deeply nested loops. Extract helper functions and predicates.
   - **No Silent Exception Swallowing**: NEVER swallow exceptions silently (`except: pass` or empty catch blocks).
     Always log exceptions with descriptive context (`logger.exception` or `logger.error(..., exc_info=True)`).
   - Write clean, readable, Pythonic code using built-ins, standard library tools, and clean iterators.
   - STRICT VARIABLE NAMING RULE: DO NOT use any single-letter variable names under any circumstances
     (including loop counters, exceptions, comprehensions, or helper variables). Use descriptive names like
     `index`, `datum`, `item`, `file_path`, `exception_error`, `line_number`, etc.

6. **Completion**:
   - When all changes are implemented and verified, finish the task by returning a concise text explanation
     of what was changed, without calling any more tools.
"""


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class CodeWriterError(Exception):
    """Base exception for code-writer errors."""


class LLMCallError(CodeWriterError):
    """Failed to call the LLM."""


class ToolExecutionError(CodeWriterError):
    """Tool execution failed."""


# ---------------------------------------------------------------------------
# CodeWriterHandler — agentic loop
# ---------------------------------------------------------------------------


class CodeWriterHandler:
    """Handler for code-writer subagent. Runs an agentic tool-based loop."""

    async def __call__(self, request: SubagentRequest) -> SubagentResponse:
        """Make handler callable — delegates to execute."""
        return await self.execute(request)

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_executor: ToolExecutor | None = None,
        config: DendrophisConfig | None = None,
        model: str | None = None,
    ) -> None:
        """Initialize with dependency injection.

        Args:
            llm_client: LLM client for making API calls. If None, created lazily.
            tool_registry: Tool registry for available tools. If None, created lazily.
            tool_executor: Tool executor for executing tool calls. If None, created lazily.
            config: Dendrophis configuration. If None, loaded lazily.
            model: Optional model name override for code-writer.
        """
        self._llm_client = llm_client
        self._dedicated_llm_client: LLMClient | None = None
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._config = config
        self._model_override = model
        self._logger = logger

    @property
    def llm(self) -> LLMClient:
        """Lazily create LLM client if not injected, or use dedicated client if code_writer_model is configured."""
        if self._config is not None and self._config.llm.code_writer_model:
            if self._dedicated_llm_client is None:
                llm_config = self._get_llm_config()
                self._dedicated_llm_client = LLMClient(llm_config)
            return self._dedicated_llm_client

        if self._llm_client is not None:
            return self._llm_client

        if self._config is not None:
            llm_config = self._get_llm_config()
            self._llm_client = LLMClient(llm_config)
            return self._llm_client

        from dendrophis.config.loader import ConfigLoader

        config_loader = ConfigLoader.load()
        cfg = config_loader.config
        from dendrophis.config.schema import LLMConfig

        model_name = self._model_override or cfg.llm.code_writer_model or cfg.llm.model
        llm_config = LLMConfig(
            model=model_name,
            api_key=cfg.llm.api_key,
            base_url=cfg.llm.base_url,
            temperature=0.1,
            top_k=64,
            min_p=0.05,
        )
        self._llm_client = LLMClient(llm_config)
        return self._llm_client

    @property
    def tool_registry(self) -> ToolRegistry:
        """Lazily create tool registry if not injected."""
        if self._tool_registry is None:
            self._tool_registry = ToolRegistry()
            try:
                from dendrophis.tools.builtins.filesystem import get_agent_tools

                if get_agent_tools is not None:
                    for tool in get_agent_tools():
                        self._tool_registry.add(tool)
            except ImportError:
                pass

            try:
                from dendrophis.tools.builtins.filesystem.bash import BashTool

                if BashTool is not None:
                    self._tool_registry.add(BashTool())
            except ImportError:
                pass

            try:
                from dendrophis.tools.builtins.subagents import ClarifyTool

                if ClarifyTool is not None:
                    self._tool_registry.add(ClarifyTool())
            except ImportError:
                pass

            try:
                from dendrophis.tools.builtins.filesystem.glob import GlobTool

                if GlobTool is not None:
                    self._tool_registry.add(GlobTool())
            except ImportError:
                pass

            try:
                from dendrophis.tools.builtins.filesystem.ripgrep import RipgrepTool

                if RipgrepTool is not None:
                    self._tool_registry.add(RipgrepTool())
            except ImportError:
                pass

            from dendrophis.tools.executor import ToolExecutor

            self._tool_executor = ToolExecutor(self._tool_registry)
        return self._tool_registry

    @property
    def tool_executor(self) -> ToolExecutor:
        """Return the tool executor."""
        _ = self.tool_registry
        return self._tool_executor

    @property
    def config(self) -> DendrophisConfig | None:
        """Return the injected config."""
        return self._config

    def _get_llm_config(self) -> Any:
        """Get LLM config for code-writer from injected config."""
        if self._config is None:
            raise ValueError("No config available to create LLM client")

        from dendrophis.config.schema import LLMConfig

        model_name = self._config.llm.code_writer_model or self._model_override or self._config.llm.model
        return LLMConfig(
            model=model_name,
            api_key=self._config.llm.api_key,
            base_url=self._config.llm.base_url,
            temperature=0.1,
            top_k=64,
            min_p=0.05,
            max_tokens=self._config.llm.max_tokens,
            timeout=self._config.llm.timeout,
        )

    async def execute(self, request: SubagentRequest) -> SubagentResponse:
        """Execute code-writing task using an agentic tool-based loop."""
        task = request.payload.get("task", "")
        files = request.payload.get("files", [])
        context = request.context

        # Build isolated context with system prompt and consolidated task details
        context_manager = self._build_isolated_context(task, files, context)

        changes: list[dict[str, Any]] = []
        max_iterations = 20
        iteration = 0
        final_summary: str = ""

        try:
            while iteration < max_iterations:
                iteration += 1

                # Call LLM with current context
                turn = await self._call_llm(context_manager)

                if not turn.tool_calls:
                    # No tool calls — LLM completed the task
                    self._logger.debug(f"[CODE-WRITER] Completed in {iteration} iteration(s)")
                    final_summary = turn.text or "Task completed successfully."
                    break

                # Execute tool calls
                tool_results = await self._execute_tool_calls(turn.tool_calls, context_manager)

                # Check if the code-writer called the clarify tool
                clarify_results = [tool_result for tool_result in tool_results if tool_result.name == "clarify"]
                if clarify_results:
                    questions: list[str] = []
                    for tool_result in clarify_results:
                        try:
                            clarify_payload = json.loads(tool_result.content)
                            questions.extend(clarify_payload.get("questions", []))
                        except (json.JSONDecodeError, AttributeError, TypeError):
                            pass
                    self._logger.info(f"[CODE-WRITER] Clarification requested: {len(questions)} question(s)")
                    return SubagentResponse(
                        agent="code-writer",
                        task_id=request.task_id,
                        status="needs_clarification",
                        result={
                            "changes": changes,
                        },
                        clarification=questions,
                    )

                # Track changes from successful tool executions
                for tool_result in tool_results:
                    change_information = self._extract_change_info(tool_result)
                    if change_information is not None:
                        changes.append(change_information)

            if iteration >= max_iterations and not final_summary:
                self._logger.warning(f"[CODE-WRITER] Hit max iterations ({max_iterations})")
                return SubagentResponse(
                    agent="code-writer",
                    task_id=request.task_id,
                    status="failure",
                    result={
                        "changes": changes,
                        "error": f"Hit maximum iteration limit ({max_iterations}). Task may be incomplete.",
                    },
                )

            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="success",
                result={
                    "changes": changes,
                    "summary": final_summary,
                    "diff_summary": f"Modified {len(changes)} file(s)" if changes else "No files modified",
                    "verification": {"syntax_ok": True, "tests_status": "skipped"},
                },
            )

        except LLMCallError as llm_error:
            self._logger.error(f"LLM call error: {llm_error}")
            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="failure",
                result={"error": str(llm_error)},
            )
        except ToolExecutionError as tool_error:
            self._logger.error(f"Tool execution error: {tool_error}")
            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="failure",
                result={"error": str(tool_error)},
            )
        except Exception as unexpected_error:
            self._logger.error(f"Unexpected error in code-writer: {unexpected_error}", exc_info=True)
            return SubagentResponse(
                agent="code-writer",
                task_id=request.task_id,
                status="failure",
                result={"error": str(unexpected_error)},
            )

    def _build_isolated_context(self, task: str, files: list[str], context: dict[str, Any]) -> ContextManager:
        """Build an isolated context for the CodeWriter agent with system prompt and task content."""
        configuration = copy.deepcopy(self._config) if self._config else DendrophisConfig()
        configuration.system_prompt = CODE_WRITER_SYSTEM_PROMPT
        context_manager = ContextManager(configuration)

        prompt_sections: list[str] = []

        # Add referenced files if provided
        if files:
            file_sections: list[str] = []
            for file_path in files:
                path = Path(file_path)
                if path.exists():
                    try:
                        file_content = path.read_text(encoding="utf-8")[:5000]
                        file_sections.append(f"--- {file_path} ---\n{file_content}\n--- end {file_path} ---")
                    except Exception as read_error:
                        file_sections.append(f"--- {file_path} ---\n[Error reading file: {read_error}]")
                else:
                    file_sections.append(f"--- {file_path} ---\n[File does not exist yet]")

            if file_sections:
                prompt_sections.append("Referenced Files:\n" + "\n\n".join(file_sections))

        # Add contextual patterns and constraints if provided
        if context.get("patterns"):
            patterns_text = "\n".join(f"- {pattern}" for pattern in context["patterns"])
            prompt_sections.append(f"Patterns to follow:\n{patterns_text}")
        if context.get("constraints"):
            constraints_text = "\n".join(f"- {constraint}" for constraint in context["constraints"])
            prompt_sections.append(f"Constraints:\n{constraints_text}")

        # Add primary task instruction
        prompt_sections.append(f"Task Instruction:\n{task}")

        context_manager.append_user("\n\n".join(prompt_sections))
        return context_manager

    async def _call_llm(self, context_manager: ContextManager) -> TurnResult:
        """Call the LLM and return the turn result, appending proper tool_calls payload to context."""
        tools_schema = self.tool_registry.all_schema()

        try:
            turn = await self.llm.complete(
                context_manager.get_messages_for_api(),
                tools=tools_schema if tools_schema else None,
            )
        except Exception as llm_error:
            raise LLMCallError(f"LLM call failed: {llm_error}") from llm_error

        # Format assistant tool calls payload for context storage
        tool_calls_payload = None
        if turn.tool_calls:
            from dendrophis.session.tools import tool_call_to_payload

            tool_calls_payload = [tool_call_to_payload(tool_call) for tool_call in turn.tool_calls]

        # Append assistant response to context
        context_manager.append_assistant(turn.text, tool_calls_payload, turn.reasoning)

        return turn

    async def _execute_tool_calls(self, tool_calls: list[Any], context_manager: ContextManager) -> list[Any]:
        """Execute tool calls and append results to context."""
        executor = self.tool_executor
        results = []

        for tool_call in tool_calls:
            try:
                result = await executor.execute(tool_call)
                context_manager.append_tool_result(result.tool_call_id, result.name, result.content)
                results.append(result)

            except Exception as execution_error:
                error_content = json.dumps({"error": f"Tool execution failed: {execution_error}"})
                error_result = type(
                    "FallbackToolResult",
                    (),
                    {
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": error_content,
                    },
                )()
                results.append(error_result)
                context_manager.append_tool_result(tool_call.id, tool_call.name, error_content)

        return results

    def _extract_change_info(self, result: Any) -> dict[str, Any] | None:
        """Extract structured change information from a tool result."""
        try:
            content = json.loads(result.content)
        except (json.JSONDecodeError, AttributeError, TypeError):
            return None

        if not isinstance(content, dict) or not content.get("success"):
            return None

        tool_name = getattr(result, "name", "")
        file_path = content.get("file", "")

        if tool_name in ("write_file", "write"):
            return {
                "action": "created" if content.get("created", False) else "written",
                "file": file_path,
                "description": f"Wrote {file_path}",
            }

        if tool_name in ("edit", "edit_function", "replace_function"):
            return {
                "action": "edited",
                "file": file_path,
                "function": content.get("function", content.get("replaced", "")),
                "description": f"Edited {file_path}",
            }

        if tool_name in ("patch", "append"):
            return {
                "action": "patched" if tool_name == "patch" else "appended",
                "file": file_path,
                "description": f"{tool_name.capitalize()} applied to {file_path}",
            }

        return None

    @staticmethod
    def _is_tool_error(result: Any) -> bool:
        """Check if a tool result indicates an error without false positives on raw text."""
        try:
            content = json.loads(result.content)
            if isinstance(content, dict):
                return "error" in content
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        return False
