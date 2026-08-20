"""End-to-end test for the CodeWriter agentic handler against a running OMLX server."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Add project root to path so we can import dendrophis
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import yaml

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.llm.client import LLMClient
from dendrophis.subagents.handlers.code_writer import CodeWriterHandler
from dendrophis.subagents.messages import SubagentRequest

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("code_writer_e2e")


def _load_config() -> DendrophisConfig:
    """Load the OMLX config, override to use the loaded main model."""
    config_path = Path(__file__).resolve().parents[1] / "configs" / "omlx.yaml"
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    llm_raw = raw["llm"]
    # Use the main loaded model instead of code_writer_model
    llm_config = LLMConfig(
        model="Qwen3.6-35B-A3B-OptiQ-4bit",
        code_writer_model="Qwen3.6-35B-A3B-OptiQ-4bit",
        base_url=llm_raw["base_url"],
        api_key=llm_raw["api_key"],
        timeout=llm_raw.get("timeout", 300),
        max_tokens=llm_raw.get("max_tokens", 16384),
        temperature=llm_raw.get("temperature", 0.1),
        top_k=llm_raw.get("top_k", 64),
        min_p=llm_raw.get("min_p"),
        reasoning_effort=llm_raw.get("reasoning_effort"),
        thinking_start_mode=llm_raw.get("thinking_start_mode", "text"),
        tool_mode=llm_raw.get("tool_mode", "auto"),
        max_tool_output_tokens=llm_raw.get("max_tool_output_tokens", 2000),
    )
    return DendrophisConfig(llm=llm_config)


@pytest.fixture
def config() -> DendrophisConfig:
    return _load_config()


@pytest.fixture
def llm_client(config: DendrophisConfig) -> LLMClient:
    """Create an LLM client from config."""
    return LLMClient(config.llm)


@pytest.fixture
def code_writer(llm_client: LLMClient, config: DendrophisConfig) -> CodeWriterHandler:
    """Create a CodeWriterHandler."""
    return CodeWriterHandler(llm_client=llm_client, config=config)


@pytest.mark.anyio
async def test_code_writer_simple_task(code_writer: CodeWriterHandler) -> None:
    """Test a simple code-writing task: add a function to a file."""
    # Create a simple test file
    test_file = Path(__file__).parent / "test_cw_temp.py"
    try:
        test_file.write_text("# Test file\n\ndef old_func(x):\n    return x\n", encoding="utf-8")

        task = f"""Add a new function `double(x)` to {test_file} that returns x * 2.
Keep the existing old_func function unchanged."""

        request = SubagentRequest(
            agent="code-writer",
            task_id="e2e-test-001",
            payload={"task": task, "files": [str(test_file)]},
            context={},
        )

        result = await code_writer.execute(request)

        logger.info("=== CodeWriter Result ===")
        logger.info("Status: %s", result.status)
        logger.info("Result: %s", result.result)

        # Verify the file was modified
        content = test_file.read_text(encoding="utf-8")
        assert "double" in content, f"Expected 'double' function in {test_file}, got:\n{content}"
        assert "old_func" in content, f"Expected 'old_func' to still be in {test_file}, got:\n{content}"

        logger.info("PASSED: File contains both functions")

    finally:
        # Cleanup
        if test_file.exists():
            test_file.unlink()


@pytest.mark.anyio
async def test_code_writer_read_only_task(code_writer: CodeWriterHandler) -> None:
    """Test a read-only task: describe a function."""
    task = """Read the file dendrophis/tools/names.py and list all tool names defined there."""

    request = SubagentRequest(
        agent="code-writer",
        task_id="e2e-test-002",
        payload={"task": task},
        context={},
    )

    result = await code_writer.execute(request)

    logger.info("=== CodeWriter Result (Read-Only) ===")
    logger.info("Status: %s", result.status)
    logger.info("Result: %s", result.result)

    # Should succeed with a summary
    assert result.status == "success", f"Expected success, got: {result.status}"
    assert result.result is not None, "Expected non-empty result"
    logger.info("PASSED: Read-only task completed")
