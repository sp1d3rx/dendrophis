"""Tests for subagent configuration, directory expansion, and handler wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from dendrophis.config.schema import DendrophisConfig
from dendrophis.llm.client import LLMClient
from dendrophis.subagents.handlers.debugger import DebuggerHandler
from dendrophis.subagents.handlers.planner import PlannerHandler
from dendrophis.subagents.handlers.researcher import ResearcherHandler
from dendrophis.subagents.messages import SubagentRequest


@pytest.fixture
def mock_llm_client() -> LLMClient:
    mock_client = MagicMock(spec=LLMClient)
    mock_client._config = MagicMock()
    mock_client._config.base_url = "http://127.0.0.1:9999/v1"
    mock_client.stream_chat = AsyncMock()
    return mock_client


@pytest.fixture
def sample_config() -> DendrophisConfig:
    return DendrophisConfig.from_dict(
        {
            "llm": {
                "base_url": "http://127.0.0.1:9999/v1",
                "model": "test-model",
            }
        }
    )


def test_planner_handler_uses_injected_client(mock_llm_client: LLMClient, sample_config: DendrophisConfig) -> None:
    handler = PlannerHandler(llm_client=mock_llm_client, config=sample_config)
    assert handler.llm is mock_llm_client


def test_debugger_handler_uses_injected_client(mock_llm_client: LLMClient, sample_config: DendrophisConfig) -> None:
    handler = DebuggerHandler(llm_client=mock_llm_client, config=sample_config)
    assert handler.llm is mock_llm_client


def test_researcher_extracts_knowledge_gaps() -> None:
    synthesis_markdown = (
        "## Summary\n"
        "Here is the summary of findings.\n\n"
        "### Knowledge Gaps\n"
        "- Missing authentication tests\n"
        "- Unknown database schema details\n"
    )
    extracted_gaps = ResearcherHandler._extract_knowledge_gaps(synthesis_markdown)
    assert len(extracted_gaps) == 2
    assert "Missing authentication tests" in extracted_gaps
    assert "Unknown database schema details" in extracted_gaps


@pytest.mark.asyncio
async def test_researcher_expands_directory_context(sample_config: DendrophisConfig) -> None:
    handler = ResearcherHandler(config=sample_config)
    request = SubagentRequest(
        agent="researcher",
        task_id="task-test",
        payload={"query": "version", "sources": ["files"]},
        context={"files": ["dendrophis/"]},
    )

    response = await handler.execute(request)
    assert response.status == "success"
    findings_list = response.result.get("findings", [])
    directory_findings = [finding_item for finding_item in findings_list if finding_item.get("type") == "directory"]
    file_findings = [finding_item for finding_item in findings_list if finding_item.get("type") == "file"]

    assert len(directory_findings) >= 1
    assert len(file_findings) >= 1
