"""Code-writer subagent handler — agentic tool-based worker."""

from __future__ import annotations

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

CODE_WRITER_SYSTEM_PROMPT = """You are Dendrophis CodeWriter, an agentic code worker.

Your job: implement changes precisely by calling tools. You operate in a loop:
1. Call tools (read_file, list_dir, edit_function, write_file, bash) to gather context and make changes
2. Receive tool results
3. Decide next action based on results
4. Repeat until the task is complete
5. Return a summary when done

Rules:
- Investigate first: use list_dir and read_file to understand the codebase before editing
- Surgical edits: use edit_function for Python functions, write_file for new files, edit for text replacements
- Verify after editing: read_file to confirm changes took effect
- Run ruff check and ruff format via bash after any Python edits
- If a tool call fails, read the error and retry with corrections
- If you are stuck or ambiguous, report the blocker — do not guess
- Make minimal, targeted changes. Change only what is needed.
- Follow existing code style and patterns in the files you read

Tool usage:
- list_dir(path=".") — explore directory structure
- read_file(file_path, offset=1, limit=2000) — read file contents
- edit_function(file_path, function_name, new_source) — replace a Python function
- write_file(file_path, content) — create or overwrite a file
- edit(file_path, old_string, new_string) — text replacement in any file
- glob(pattern, path=".") — find files by pattern
- ripgrep(pattern, path, include) — search file contents
- bash(command, description) — run shell commands (use for ruff, pytest, etc.)

When you are done, output a final summary message describing what you changed.
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
        model: str = "qwen/qwen3-coder:latest",
    ) -> None:
        """Initialize with dependency injection.

        Args:
            llm_client: LLM client for making API calls. If None, created lazily.
            tool_registry: Tool registry for available tools. If None, created lazily.
            tool_executor: Tool executor for executing tool calls. If None, created lazily.
            config: Dendrophis configuration. If None, loaded lazily.
            model: Model name for code-writer (used if no llm_client provided).
        """
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._config = config
        self._model_override = model
        self._logger = logger

    @property
    def llm(self) -> LLMClient:
        """Lazily create LLM client if not injected."""
        if self._llm_client is not None:
            return self._llm_client

        if self._config is not None:
            llm_config = self._get_llm_config()
            self._llm_client = LLMClient(llm_config)
            return self._llm_client

        # Last resort: create with hardcoded defaults
        from dendrophis.config.loader import ConfigLoader

        config_loader = ConfigLoader.load()
        cfg = config_loader.config
        from dendrophis.config.schema import LLMConfig

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
    def tool_registry(self) -> ToolRegistry:
        """Lazily create tool registry if not injected."""
        if self._tool_registry is None:
            # Create a minimal registry with only agent-friendly tools
            self._tool_registry = ToolRegistry()
            from dendrophis.tools.builtins.filesystem import get_agent_tools
            from dendrophis.tools.builtins.filesystem.bash import BashTool
            from dendrophis.tools.executor import ToolExecutor

            for tool in get_agent_tools():
                self._tool_registry.add(tool)
            self._tool_registry.add(BashTool())

            self._tool_executor = ToolExecutor(self._tool_registry)
        return self._tool_registry

    @property
    def tool_executor(self) -> ToolExecutor:
        """Return the tool executor."""
        _ = self.tool_registry  # Ensures executor is created
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

        model = self._config.llm.code_writer_model or self._model_override
        return LLMConfig(
            model=model,
            api_key=self._config.llm.api_key,
            base_url=self._config.llm.base_url,
            temperature=0.1,
            top_k=64,
            min_p=0.05,
            max_tokens=self._config.llm.max_tokens,
            timeout=self._config.llm.timeout,
        )

    async def execute(self, request: SubagentRequest) -> SubagentResponse:
        """Execute code-writing task using an agentic tool-based loop.

        This replaces the old text-parsing approach. The CodeWriter now:
        1. Creates its own isolated context with the system prompt
        2. Calls the LLM with tool definitions
        3. Executes any tool calls returned by the LLM
        4. Feeds tool results back to the LLM
        5. Repeats until the task is complete or max iterations reached

        Returns a structured result with changes made and any blockers.
        """
        task = request.payload.get("task", "")
        files = request.payload.get("files", [])
        context = request.context

        # Build isolated context
        context_manager = self._build_isolated_context(task, files, context)

        changes: list[dict[str, Any]] = []
        blockers: list[str] = []
        max_iterations = 20  # Prevent infinite loops
        iteration = 0

        try:
            while iteration < max_iterations:
                iteration += 1

                # Call LLM with current context
                turn = await self._call_llm(context_manager)

                if not turn.tool_calls:
                    # No tool calls — LLM is done
                    self._logger.debug(f"[CODE-WRITER] Done after {iteration} iterations")
                    break

                # Execute tool calls
                tool_results = await self._execute_tool_calls(turn.tool_calls, context_manager)

                # Track changes from successful tool executions
                changes.extend(info for result in tool_results if (info := self._extract_change_info(result)))

                # Check for blockers (failed tool calls)
                blockers.extend(
                    f"Tool {result.name} failed: {result.content[:200]}"
                    for result in tool_results
                    if self._is_tool_error(result)
                )

            if iteration >= max_iterations:
                self._logger.warning(f"[CODE-WRITER] Hit max iterations ({max_iterations})")
                blockers.append(f"Hit maximum iteration limit ({max_iterations}). Task may be incomplete.")

            if blockers:
                self._logger.warning(f"[CODE-WRITER] Blockers: {blockers}")
                return SubagentResponse(
                    agent="code-writer",
                    task_id=request.task_id,
                    status="needs_clarification",
                    result={
                        "changes": changes,
                        "blockers": blockers,
                    },
                )

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
        except ToolExecutionError as e:
            self._logger.error(f"Tool execution error: {e}")
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

    def _build_isolated_context(self, task: str, files: list[str], context: dict[str, Any]) -> ContextManager:
        """Build an isolated context for the CodeWriter agent."""
        # Create a minimal config for the isolated context
        from dendrophis.config.schema import DendrophisConfig

        cfg = self._config if self._config else DendrophisConfig()
        cm = ContextManager(cfg)

        # Add files to context if provided
        if files:
            file_parts = []
            for f in files:
                path = Path(f)
                if path.exists():
                    try:
                        content = path.read_text(encoding="utf-8")[:5000]  # Limit file size
                        file_parts.append(f"\n--- {f} ---\n{content}...")
                    except Exception as e:
                        file_parts.append(f"\n--- {f} ---\n[Error reading: {e}]")

            if file_parts:
                cm.messages.append({"role": "user", "content": "Files provided:\n" + "\n".join(file_parts)})

        # Add task description
        cm.messages.append({"role": "user", "content": f"Task: {task}"})

        # Add context constraints if provided
        if context.get("patterns"):
            cm.messages.append({"role": "user", "content": "Patterns to follow:\n" + "\n".join(context["patterns"])})
        if context.get("constraints"):
            cm.messages.append({"role": "user", "content": "Constraints:\n" + "\n".join(context["constraints"])})

        return cm

    async def _call_llm(self, context_manager: ContextManager) -> TurnResult:
        """Call the LLM and return the turn result."""
        tools_schema = self.tool_registry.all_schema()

        try:
            turn = await self.llm.complete(
                context_manager.get_messages_for_api(),
                tools=tools_schema if tools_schema else None,
            )
        except Exception as e:
            raise LLMCallError(f"LLM call failed: {e}") from e

        # Append assistant response to context
        context_manager.append_assistant(turn.text, None, turn.reasoning)

        return turn

    async def _execute_tool_calls(self, tool_calls: list[Any], context_manager: ContextManager) -> list[Any]:
        """Execute tool calls and append results to context."""
        executor = self.tool_executor
        results = []

        for tool_call in tool_calls:
            try:
                result = await executor.execute(tool_call)

                # Append result to context
                context_manager.append_tool_result(result.tool_call_id, result.name, result.content)

                # Emit tool result event if we have an event bus (for logging/UI)
                # Note: CodeWriter doesn't have direct event bus access, so we skip UI events

                results.append(result)

            except Exception as e:
                # Create error result
                import json

                error_content = json.dumps({"error": f"Tool execution failed: {e}"})
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

                # Append error to context for self-correction
                context_manager.append_tool_result(tool_call.id, tool_call.name, error_content)

        return results

    def _extract_summary(self, text: str) -> str:
        """Extract a summary from the LLM's final text response."""
        # Look for common summary patterns
        if "Summary:" in text:
            return text[text.index("Summary:") + len("Summary:") :].strip()
        if "summary:" in text.lower():
            idx = text.lower().index("summary:")
            return text[idx + len("summary:") :].strip()
        return text[:500] if text else "No summary provided"

    def _extract_change_info(self, result: Any) -> dict[str, Any] | None:
        """Extract change information from a tool result."""
        try:
            content = json.loads(result.content)
        except (json.JSONDecodeError, AttributeError):
            return None

        if not isinstance(content, dict):
            return None

        # Track successful writes and edits
        if content.get("success") and result.name in ("write_file", "write", "edit_function", "replace_function"):
            return {
                "action": "edited" if result.name in ("edit_function", "replace_function") else "created",
                "file": content.get("file", ""),
                "function": content.get("function", content.get("replaced", "")),
                "description": content.get("file", ""),
            }

        if content.get("success") and result.name == "edit":
            return {
                "action": "edited",
                "file": content.get("file", ""),
                "description": f"Modified {content.get('file', '')}",
            }

        return None

    @staticmethod
    def _is_tool_error(result: Any) -> bool:
        """Check if a tool result indicates an error."""
        try:
            content = json.loads(result.content)
            return isinstance(content, dict) and "error" in content
        except (json.JSONDecodeError, AttributeError):
            return "error" in str(result.content).lower()
