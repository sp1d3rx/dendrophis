"""End-to-end test for the CodeWriter agentic handler against a running OMLX server."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Add project root to path so we can import dendrophis
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

# This is a live end-to-end test and the only pyyaml consumer in the main
# project (pyyaml is not a declared dependency here). Skip the whole module
# cleanly at collection time if it's not installed, instead of erroring.
yaml = pytest.importorskip("yaml")

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.llm.client import LLMClient
from dendrophis.subagents.handlers.code_writer import CodeWriterHandler
from dendrophis.subagents.messages import SubagentRequest

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("code_writer_e2e")


@pytest.fixture(scope="module", autouse=True)
def _require_omlx_server() -> None:
    """Skip the whole module unless a running OMLX server is reachable.

    Beyond pyyaml (checked via importorskip at collection), this e2e test needs
    a live OMLX server configured in configs/omlx.yaml. Probe it once per module
    at test time; any HTTP response (even an error status) means the server is
    up, while connection errors / timeouts mean skip.
    """
    import urllib.error
    import urllib.request

    config_path = Path(__file__).resolve().parents[1] / "configs" / "omlx.yaml"
    if not config_path.exists():
        pytest.skip("configs/omlx.yaml not found; OMLX e2e test skipped")
    try:
        with open(config_path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        base_url = str(raw["llm"]["base_url"]).rstrip("/")
    except Exception as exc:
        pytest.skip(f"could not read OMLX base_url from configs/omlx.yaml: {exc}")
    try:
        urllib.request.urlopen(base_url + "/models", timeout=2)
        reachable = True
    except urllib.error.HTTPError:
        reachable = True  # the server responded (even with an error status)
    except Exception:
        reachable = False
    if not reachable:
        pytest.skip(f"OMLX server not reachable at {base_url}; e2e test skipped")


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
