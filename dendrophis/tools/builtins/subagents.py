"""Tool for invoking subagents from the orchestrator."""

from __future__ import annotations

from typing import Any

from dendrophis.subagents import get_session_executor
from dendrophis.tools.base import BaseTool


class InvokeSubagentTool(BaseTool):
    """Invoke a subagent to perform a task."""

    def _get_executor(self):
        """Get the session's subagent executor."""
        return get_session_executor()

    @property
    def name(self) -> str:
        return "invoke_subagent"

    @property
    def description(self) -> str:
        return (
            "Invoke a specialized subagent (researcher, planner, code-writer, code-reviewer, test-runner, debugger). "
            "Supported context keys: 'diff' (unified diff string for code-reviewer), 'files' (file paths list), "
            "'patterns' (search regexes list), 'path' (scoping directory), 'include' (file glob filter), "
            "'sources' (['files', 'memory', 'codebase']), and 'depth' ('quick' | 'thorough')."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": ["researcher", "planner", "code-writer", "code-reviewer", "test-runner", "debugger"],
                    "description": "Which subagent to invoke",
                },
                "task": {
                    "type": "string",
                    "description": "Description of what the subagent should do",
                },
                "context": {
                    "type": "object",
                    "description": (
                        "Additional context parameters: 'diff' (unified diff for review), 'files' (file list), "
                        "'patterns' (regex list), 'path' (directory scope), 'include' (glob pattern), "
                        "and 'depth' ('quick' | 'thorough')."
                    ),
                },
            },
            "required": ["agent", "task"],
        }

    async def execute(self, agent: str, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute subagent and return result."""
        executor = self._get_executor()
        if executor is None:
            return {
                "success": False,
                "agent": agent,
                "error": "Subagent executor not initialized",
            }

        # Map simple task to proper payload based on agent
        payload = self._build_payload(agent, task, context or {})

        result = await executor.execute(
            agent=agent,
            payload=payload,
            context=context,
        )

        if result.success and result.response:
            return {
                "success": True,
                "agent": agent,
                "result": result.response.result,
                "status": result.response.status,
                "clarification": result.response.clarification,
            }
        # Extract error from response.result if available
        response_error = None
        if result.response and isinstance(result.response.result, dict):
            response_error = result.response.result.get("error")
        error_msg = (
            result.error
            or response_error
            or (f"Handler returned failure: {result.response.status}" if result.response else "No response")
        )
        return {
            "success": False,
            "agent": agent,
            "error": error_msg,
        }

    def _build_payload(self, agent: str, task: str, context: dict[str, Any]) -> dict[str, Any]:
        """Build agent-specific payload."""
        if agent == "researcher":
            return {
                "query": task,
                "sources": context.get("sources", ["files", "memory", "codebase"]),
                "depth": context.get("depth", "quick"),
                "context": context,
            }
        if agent == "code-reviewer":
            return {
                "task": task,
                "diff": context.get("diff", ""),
                "files": context.get("files") or context.get("file_paths", []),
                "changes": context.get("changes", []),
                "focus": context.get("focus", ["correctness", "robustness", "maintainability", "pythonic_style"]),
                "context": context,
            }
        # Default: pass task through
        return {"task": task, **context}


class ClarifyTool(BaseTool):
    """Tool for the code-writer to ask the orchestrator for clarification.

    Use this when you lack sufficient context to proceed. Provide specific,
    numbered questions that the orchestrator can answer one-by-one.
    """

    @property
    def name(self) -> str:
        return "clarify"

    @property
    def description(self) -> str:
        return (
            "Ask the orchestrator for clarification. Use when you need information "
            "that is not available in the codebase. Provide specific numbered questions. "
            "When invoked, the code-writer pauses and returns the questions to the "
            "orchestrator for answers."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Numbered list of specific questions asking for clarification",
                },
            },
            "required": ["questions"],
        }

    async def execute(self, questions: list[str]) -> dict[str, Any]:
        """Execute the clarify tool.

        This doesn't do work itself — it signals that the code-writer needs
        answers to the questions before it can proceed.
        """
        return {
            "success": True,
            "tool": "clarify",
            "questions": questions,
            "message": "Clarification requested — awaiting answers from orchestrator.",
        }
