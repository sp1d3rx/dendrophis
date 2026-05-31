"""SubagentBootstrapper - initializes the subagent system and registers execution handlers."""

from __future__ import annotations

from typing import Any

from dendrophis.config.schema import DendrophisConfig
from dendrophis.llm.client import LLMClient
from dendrophis.memory.memory import MemoryStore
from dendrophis.subagents import SubagentExecutor, get_registry, set_session_executor
from dendrophis.subagents.handlers import (
    CodeWriterHandler,
    ResearcherHandler,
    TestRunnerHandler,
    code_reviewer_execute,
    debugger_execute,
    planner_execute,
)


class SubagentBootstrapper:
    """Bootstraps subagent handlers and routes invocation calls."""

    def __init__(
        self,
        llm_client: LLMClient,
        memory_store: MemoryStore | None,
        config: DendrophisConfig,
    ) -> None:
        self._llm_client = llm_client
        self._memory_store = memory_store
        self._config = config
        self._executor = SubagentExecutor()

    def initialize(self) -> None:
        """Register subagent handlers and set global session executor."""
        registry = get_registry()

        # Register researcher handler with memory store
        researcher = ResearcherHandler(memory_store=self._memory_store)
        registry.register_handler("researcher", researcher.execute)

        # Register code-writer handler with session's LLM client and config
        code_writer = CodeWriterHandler(
            llm_client=self._llm_client,
            config=self._config,
        )
        registry.register_handler("code-writer", code_writer.execute)

        # Register test-runner handler
        test_runner = TestRunnerHandler()
        registry.register_handler("test-runner", test_runner.execute)

        # Register planner, code-reviewer, debugger (LLM-only handlers)
        registry.register_handler("planner", planner_execute)
        registry.register_handler("code-reviewer", code_reviewer_execute)
        registry.register_handler("debugger", debugger_execute)

        # Register executor globally for tools to access
        set_session_executor(self._executor)

    @property
    def executor(self) -> SubagentExecutor:
        """Return the underlying subagent executor instance."""
        return self._executor

    async def invoke(
        self,
        agent: str,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke a subagent and return its result execution summary."""
        result = await self._executor.execute(
            agent=agent,
            payload=payload,
            context=context or {},
        )

        if result.success and result.response:
            return {
                "success": True,
                "agent": agent,
                "status": result.response.status,
                "result": result.response.result,
            }
        return {
            "success": False,
            "agent": agent,
            "error": result.error or "Unknown error",
        }
