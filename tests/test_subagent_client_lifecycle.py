"""F6: subagent handlers must deterministically release LLM clients they create.

Pre-fix, handlers never closed the LLM clients they created themselves: a dedicated
client when code_writer_model/code_reviewer_model is configured, and (for planner,
researcher, test-runner, debugger) a brand-new client on every llm access when no
client was injected. Each leaked its httpx connection pool for the process lifetime.
"""

from __future__ import annotations

import pytest

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.llm.client import LLMClient
from dendrophis.session.subagents import SubagentBootstrapper
from dendrophis.subagents import get_registry, get_session_executor, set_session_executor
from dendrophis.subagents.handlers.code_reviewer import CodeReviewerHandler
from dendrophis.subagents.handlers.code_writer import CodeWriterHandler
from dendrophis.subagents.handlers.debugger import DebuggerHandler
from dendrophis.subagents.handlers.planner import PlannerHandler
from dendrophis.subagents.handlers.researcher import ResearcherHandler
from dendrophis.subagents.handlers.test_runner import TestRunnerHandler


def _config(writer_model: str | None = None, reviewer_model: str | None = None) -> DendrophisConfig:
    llm = LLMConfig(
        model="test-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="k",
        code_writer_model=writer_model,
        code_reviewer_model=reviewer_model,
    )
    return DendrophisConfig(llm=llm)


@pytest.mark.anyio
async def test_code_writer_closes_its_dedicated_client() -> None:
    handler = CodeWriterHandler(config=_config(writer_model="writer-model"))
    client = handler.llm
    assert client._http.is_closed is False

    await handler.aclose()

    assert client._http.is_closed is True
    # Owned slot is reset: a later use recreates a fresh (open) client.
    fresh = handler.llm
    assert fresh is not client
    assert fresh._http.is_closed is False
    await fresh.aclose()


@pytest.mark.anyio
async def test_code_reviewer_closes_its_dedicated_client() -> None:
    handler = CodeReviewerHandler(config=_config(reviewer_model="reviewer-model"))
    client = handler.llm
    assert client._http.is_closed is False

    await handler.aclose()

    assert client._http.is_closed is True
    fresh = handler.llm
    assert fresh is not client
    await fresh.aclose()


@pytest.mark.anyio
async def test_aclose_does_not_close_injected_client() -> None:
    injected = LLMClient(_config().llm)
    writer = CodeWriterHandler(llm_client=injected, config=_config())
    reviewer = CodeReviewerHandler(llm_client=injected, config=_config())
    planner = PlannerHandler(llm_client=injected, config=_config())

    await writer.aclose()
    await reviewer.aclose()
    await planner.aclose()

    assert injected._http.is_closed is False, "injected client is owned by the caller"
    await injected.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "make_handler",
    [
        lambda: PlannerHandler(config=_config()),
        lambda: ResearcherHandler(config=_config()),
        lambda: TestRunnerHandler(config=_config()),
        lambda: DebuggerHandler(config=_config()),
    ],
)
async def test_simple_handlers_cache_and_close_lazy_client(make_handler) -> None:
    handler = make_handler()
    first = handler.llm
    second = handler.llm
    assert first is not None
    assert first is second, "lazy client must be cached, not recreated per call"
    assert first._http.is_closed is False

    await handler.aclose()

    assert first._http.is_closed is True


@pytest.mark.anyio
async def test_bootstrapper_aclose_releases_handler_owned_clients() -> None:
    agents = ["researcher", "code-writer", "test-runner", "code-reviewer", "planner", "debugger"]
    registry = get_registry()
    saved_handlers = {name: registry._agents[name].handler for name in agents}
    saved_executor = get_session_executor()

    session_llm = LLMClient(_config().llm)
    bootstrapper = SubagentBootstrapper(
        llm_client=session_llm,
        memory_store=None,
        config=_config(writer_model="writer-model"),
    )
    try:
        bootstrapper.initialize()
        writer = next(h for h in bootstrapper._handlers if isinstance(h, CodeWriterHandler))
        dedicated = writer.llm  # first use creates the dedicated client
        assert dedicated._http.is_closed is False

        await bootstrapper.aclose()

        assert dedicated._http.is_closed is True, "dedicated client must be released with the session"
        assert session_llm._http.is_closed is False, "session-owned client is closed by the session"
    finally:
        set_session_executor(saved_executor)
        for name, handler in saved_handlers.items():
            if handler is not None:
                registry.register_handler(name, handler)

    await session_llm.aclose()
