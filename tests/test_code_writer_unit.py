from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.events import ToolCall
from dendrophis.llm.client import TurnResult
from dendrophis.subagents.handlers.code_writer import (
    CODE_WRITER_SYSTEM_PROMPT,
    CodeWriterHandler,
)
from dendrophis.subagents.messages import SubagentRequest


@pytest.fixture
def local_temp_directory():
    temporary_path = Path.cwd() / f"tmp_test_codewriter_{uuid.uuid4().hex}"
    temporary_path.mkdir(parents=True, exist_ok=True)
    yield temporary_path
    if temporary_path.exists():
        shutil.rmtree(temporary_path)


@pytest.mark.anyio
async def test_code_writer_context_building(local_temp_directory: Path) -> None:
    sample_file_path = local_temp_directory / "sample.py"
    sample_file_path.write_text("def sample_function():\n    return True\n", encoding="utf-8")

    handler = CodeWriterHandler()
    task_description = "Add docstring to sample_function"
    files_list = [str(sample_file_path)]
    context_data = {
        "patterns": ["Use Google style docstrings"],
        "constraints": ["Do not modify function body"],
    }

    context_manager = handler._build_isolated_context(
        task=task_description,
        files=files_list,
        context=context_data,
    )

    # 1. Verify system prompt is injected
    assert len(context_manager.messages) >= 2
    assert context_manager.messages[0]["role"] == "system"
    assert context_manager.messages[0]["content"] == CODE_WRITER_SYSTEM_PROMPT

    # 2. Verify user message consolidates referenced files, patterns, constraints, and task
    user_message = context_manager.messages[1]
    assert user_message["role"] == "user"
    assert "Referenced Files:" in user_message["content"]
    assert "def sample_function():" in user_message["content"]
    assert "Patterns to follow:" in user_message["content"]
    assert "Use Google style docstrings" in user_message["content"]
    assert "Constraints:" in user_message["content"]
    assert "Do not modify function body" in user_message["content"]
    assert "Task Instruction:" in user_message["content"]
    assert task_description in user_message["content"]


@pytest.mark.anyio
async def test_code_writer_tool_loop_execution(local_temp_directory: Path) -> None:
    target_file_path = local_temp_directory / "generated.py"
    relative_target_path = str(target_file_path.relative_to(Path.cwd()))

    mock_llm_client = MagicMock()

    # Turn 1: LLM decides to call write_file
    tool_call_item = ToolCall(
        index=0,
        id="call_write_001",
        name="write_file",
        arguments=json.dumps(
            {
                "file_path": relative_target_path,
                "content": "def calculate_total(prices):\n    return sum(prices)\n",
            }
        ),
    )
    turn_1_result = TurnResult(
        text="",
        reasoning="",
        tool_calls=[tool_call_item],
        finish_reason="tool_calls",
    )

    # Turn 2: LLM is done and provides summary
    turn_2_result = TurnResult(
        text="Successfully created the calculate_total function.",
        reasoning="",
        tool_calls=[],
        finish_reason="stop",
    )

    mock_llm_client.complete = AsyncMock(side_effect=[turn_1_result, turn_2_result])

    handler = CodeWriterHandler(llm_client=mock_llm_client)

    request = SubagentRequest(
        agent="code-writer",
        task_id="task_12345",
        payload={"task": "Create calculate_total in generated.py"},
        context={},
    )

    response = await handler.execute(request)

    assert response.status == "success"
    assert response.result.get("summary") == "Successfully created the calculate_total function."
    assert len(response.result.get("changes", [])) == 1
    assert target_file_path.exists()
    assert "calculate_total" in target_file_path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_code_writer_clarification_flow() -> None:
    mock_llm_client = MagicMock()

    # Turn 1: LLM calls clarify tool
    tool_call_item = ToolCall(
        index=0,
        id="call_clarify_001",
        name="clarify",
        arguments=json.dumps(
            {
                "questions": ["Which database schema should be used?", "What is the table name?"],
            }
        ),
    )
    turn_1_result = TurnResult(
        text="",
        reasoning="",
        tool_calls=[tool_call_item],
        finish_reason="tool_calls",
    )

    mock_llm_client.complete = AsyncMock(return_value=turn_1_result)

    handler = CodeWriterHandler(llm_client=mock_llm_client)

    request = SubagentRequest(
        agent="code-writer",
        task_id="task_clarify_001",
        payload={"task": "Setup the database migration"},
        context={},
    )

    response = await handler.execute(request)

    assert response.status == "needs_clarification"
    assert response.clarification == [
        "Which database schema should be used?",
        "What is the table name?",
    ]


@pytest.mark.anyio
async def test_code_writer_model_resolution() -> None:
    # 1. Falls back to config.llm.model when code_writer_model is None
    custom_configuration = DendrophisConfig(
        llm=LLMConfig(
            model="meta-llama/Llama-3.3-70B-Instruct",
            code_writer_model=None,
            api_key="test-key",
            base_url="https://api.example.com/v1",
        )
    )
    handler_default = CodeWriterHandler(config=custom_configuration)
    resolved_llm_config = handler_default._get_llm_config()
    assert resolved_llm_config.model == "meta-llama/Llama-3.3-70B-Instruct"

    # 2. Uses dedicated code_writer_model when specified
    custom_configuration.llm.code_writer_model = "deepseek/deepseek-coder"
    handler_dedicated = CodeWriterHandler(config=custom_configuration)
    resolved_dedicated_config = handler_dedicated._get_llm_config()
    assert resolved_dedicated_config.model == "deepseek/deepseek-coder"


def test_code_writer_is_tool_error_no_false_positives() -> None:
    # 1. Plain text source code containing error words should NOT be flagged as tool error
    text_result = type(
        "DummyResult",
        (),
        {
            "content": "def handle_error(error_message):\n    logger.error(error_message)\n",
        },
    )()
    assert CodeWriterHandler._is_tool_error(text_result) is False

    # 2. Structured JSON error dictionary should be flagged as tool error
    error_result = type(
        "DummyResult",
        (),
        {
            "content": json.dumps({"error": "File not found: /path/to/missing.py"}),
        },
    )()
    assert CodeWriterHandler._is_tool_error(error_result) is True
