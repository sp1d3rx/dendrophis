from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.events import TextDeltaEvent
from dendrophis.subagents.handlers import (
    CodeReviewerHandler,
    ResearcherHandler,
    TestRunnerHandler,
)
from dendrophis.subagents.messages import SubagentRequest


@pytest.mark.anyio
async def test_researcher_handler_synthesis() -> None:
    mock_llm_client = MagicMock()

    async def mock_stream_chat(messages_list):
        yield TextDeltaEvent(delta="Found the event bus architecture and dispatch mechanisms.")

    mock_llm_client.stream_chat = mock_stream_chat

    handler = ResearcherHandler(llm_client=mock_llm_client)

    request = SubagentRequest(
        agent="researcher",
        task_id="task_research_01",
        payload={"query": "How is the event bus wired?", "depth": "quick"},
        context={"files": ["dendrophis/events/bus.py"]},
    )

    response = await handler.execute(request)

    assert response.status == "success"
    assert response.result.get("query") == "How is the event bus wired?"
    assert "Found the event bus architecture" in response.result.get("synthesis", "")


@pytest.mark.anyio
async def test_researcher_handler_ripgrep_contract_and_patterns() -> None:
    mock_ripgrep_tool = MagicMock()
    mock_ripgrep_tool.execute = AsyncMock(
        return_value={
            "pattern": "invoke_subagent",
            "matches": [
                {
                    "file": "dendrophis/session/session.py",
                    "matches": [
                        {"line": 240, "content": "    async def invoke_subagent("},
                        {"line": 250, "content": "        return await subagent.execute()"},
                    ],
                }
            ],
            "total_files_matched": 1,
        }
    )

    mock_llm_client = MagicMock()

    async def mock_stream_chat(messages_list):
        yield TextDeltaEvent(delta="Found invoke_subagent in dendrophis/session/session.py at line 240.")

    mock_llm_client.stream_chat = mock_stream_chat

    handler = ResearcherHandler(llm_client=mock_llm_client)
    handler.ripgrep_tool = mock_ripgrep_tool

    request = SubagentRequest(
        agent="researcher",
        task_id="task_research_02",
        payload={
            "task": "Survey repo to find where `invoke_subagent` is implemented",
        },
        context={
            "patterns": ["invoke_subagent"],
            "path": "dendrophis",
        },
    )

    response = await handler.execute(request)

    assert response.status == "success"
    code_findings = [item for item in response.result.get("findings", []) if item.get("type") == "code"]
    assert len(code_findings) == 2
    assert code_findings[0]["source"] == "dendrophis/session/session.py:240"
    assert "async def invoke_subagent" in code_findings[0]["summary"]

    search_meta = response.result.get("search_meta", {})
    assert "invoke_subagent" in search_meta.get("patterns_attempted", [])
    assert search_meta.get("match_counts", {}).get("invoke_subagent") == 2
    assert response.result.get("confidence") == "high"


@pytest.mark.anyio
async def test_researcher_handler_filename_candidate_discovery(tmp_path: Path) -> None:
    readme_file_path = tmp_path / "README.md"
    readme_file_path.write_text("# Dendrophis Project\n\nAI coding assistant.", encoding="utf-8")

    mock_glob_tool = MagicMock()
    mock_glob_tool.execute = AsyncMock(return_value={"pattern": "**/README.md", "files": [str(readme_file_path)]})

    mock_read_tool = MagicMock()
    mock_read_tool.execute = AsyncMock(
        return_value={"type": "file", "content": "# Dendrophis Project\n\nAI coding assistant."}
    )

    mock_llm_client = MagicMock()

    async def mock_stream_chat(messages_list):
        yield TextDeltaEvent(delta="Dendrophis is an AI coding assistant as described in README.md.")

    mock_llm_client.stream_chat = mock_stream_chat

    handler = ResearcherHandler(llm_client=mock_llm_client)
    handler.glob_tool = mock_glob_tool
    handler.read_tool = mock_read_tool

    request = SubagentRequest(
        agent="researcher",
        task_id="task_research_03",
        payload={
            "task": "Check README.md for the project overview description",
        },
        context={},
    )

    response = await handler.execute(request)

    assert response.status == "success"
    file_findings = [item for item in response.result.get("findings", []) if item.get("type") == "file"]
    assert len(file_findings) >= 1
    assert "Dendrophis Project" in file_findings[0]["summary"]
    assert "AI coding assistant" in response.result.get("synthesis", "")
    search_meta = response.result.get("search_meta", {})
    assert "file:README.md" in search_meta.get("patterns_attempted", [])


@pytest.mark.anyio
async def test_researcher_handler_deduplicates_overlapping_file_entries(tmp_path: Path) -> None:
    main_py_path = tmp_path / "__main__.py"
    main_py_path.write_text("import sys\nprint('hello')\n", encoding="utf-8")

    mock_glob_tool = MagicMock()
    mock_glob_tool.execute = AsyncMock(return_value={"pattern": "**/__main__.py", "files": [str(main_py_path)]})

    mock_read_tool = MagicMock()
    mock_read_tool.execute = AsyncMock(return_value={"type": "file", "content": "import sys\nprint('hello')\n"})

    mock_llm_client = MagicMock()

    async def mock_stream_chat(messages_list):
        yield TextDeltaEvent(delta="Entry point is __main__.py.")

    mock_llm_client.stream_chat = mock_stream_chat

    handler = ResearcherHandler(llm_client=mock_llm_client)
    handler.glob_tool = mock_glob_tool
    handler.read_tool = mock_read_tool

    # Pass both task mentioning __main__.py AND explicit context["files"]
    request = SubagentRequest(
        agent="researcher",
        task_id="task_research_04",
        payload={
            "task": "Verify entry point in __main__.py",
        },
        context={
            "files": [str(main_py_path)],
        },
    )

    response = await handler.execute(request)

    assert response.status == "success"
    file_findings = [item for item in response.result.get("findings", []) if item.get("type") == "file"]
    # Should only contain 1 deduplicated entry, not 2
    assert len(file_findings) == 1
    assert file_findings[0]["source"] == str(main_py_path)
    # Read tool should only be invoked once for this file
    assert mock_read_tool.execute.await_count == 1


@pytest.mark.anyio
async def test_researcher_handler_plain_english_keyword_derivation() -> None:
    mock_ripgrep_tool = MagicMock()
    mock_ripgrep_tool.execute = AsyncMock(
        return_value={
            "pattern": "subagent",
            "matches": [
                {
                    "file": "dendrophis/session/subagents.py",
                    "matches": [
                        {"line": 40, "content": "def register_subagent(self):"},
                    ],
                }
            ],
            "total_files_matched": 1,
        }
    )

    mock_llm_client = MagicMock()

    async def mock_stream_chat(messages_list):
        yield TextDeltaEvent(delta="Subagents are registered in dendrophis/session/subagents.py.")

    mock_llm_client.stream_chat = mock_stream_chat

    handler = ResearcherHandler(llm_client=mock_llm_client)
    handler.ripgrep_tool = mock_ripgrep_tool

    # Plain-English sentence without explicit patterns, backticks, or snake_case
    request = SubagentRequest(
        agent="researcher",
        task_id="task_research_05",
        payload={
            "task": "Map the package structure: identify the main entry point and subagent invocation",
        },
        context={
            "path": "dendrophis",
        },
    )

    response = await handler.execute(request)

    assert response.status == "success"
    search_meta = response.result.get("search_meta", {})
    # Verify keywords like "entry", "point", "subagent", "invocation" were derived as patterns
    attempted_patterns = search_meta.get("patterns_attempted", [])
    assert len(attempted_patterns) > 0
    assert any("subagent" in pattern_item or "invocation" in pattern_item for pattern_item in attempted_patterns)
    assert search_meta.get("code_findings_discovered", 0) >= 1
    assert search_meta.get("findings_returned") >= 1
    assert "capped" in search_meta


@pytest.mark.anyio
async def test_researcher_handler_directory_layout_structural_fallback(tmp_path: Path) -> None:
    package_dir = tmp_path / "mypackage"
    package_dir.mkdir()
    init_file_path = package_dir / "__init__.py"
    init_file_path.write_text('"""MyPackage core."""\n', encoding="utf-8")

    mock_read_tool = MagicMock()

    async def mock_read_tool_execute(file_path):
        if file_path == str(package_dir):
            return {"type": "directory", "entries": ["__init__.py", "core.py", "utils/"]}
        if file_path == str(init_file_path):
            return {"type": "file", "content": '"""MyPackage core."""\n'}
        return {"error": f"File not found: {file_path}"}

    mock_read_tool.execute = AsyncMock(side_effect=mock_read_tool_execute)

    mock_llm_client = MagicMock()

    async def mock_stream_chat(messages_list):
        yield TextDeltaEvent(delta="Package layout consists of __init__.py, core.py, and utils/.")

    mock_llm_client.stream_chat = mock_stream_chat

    handler = ResearcherHandler(llm_client=mock_llm_client)
    handler.read_tool = mock_read_tool
    handler.ripgrep_tool = None

    # Structural task with no explicit files or patterns
    request = SubagentRequest(
        agent="researcher",
        task_id="task_research_06",
        payload={
            "task": "Survey package layout and structure",
        },
        context={
            "path": str(package_dir),
        },
    )

    response = await handler.execute(request)

    assert response.status == "success"
    directory_findings = [item for item in response.result.get("findings", []) if item.get("type") == "directory"]
    assert len(directory_findings) == 1
    assert "Directory structure" in directory_findings[0]["summary"]
    assert "__init__.py" in directory_findings[0]["summary"]


@pytest.mark.anyio
async def test_code_reviewer_handler_hettinger_and_greybeard() -> None:
    mock_llm_client = MagicMock()

    review_json_payload = {
        "approval": "changes_requested",
        "summary": "The change introduces a single-letter variable and lacks resource cleanup.",
        "issues": [
            {
                "severity": "blocker",
                "file": "dendrophis/worker.py",
                "line": 45,
                "description": "Single-letter variable 'x' violates Hettinger naming standards.",
                "suggestion": "Rename 'x' to 'worker_task'.",
            }
        ],
        "hettinger_notes": ["Reject single-letter variable 'x'."],
        "greybeard_notes": ["Ensure connection is closed in a finally block."],
    }

    async def mock_stream_chat(messages_list):
        yield TextDeltaEvent(delta=json.dumps(review_json_payload))

    mock_llm_client.stream_chat = mock_stream_chat

    handler = CodeReviewerHandler(llm_client=mock_llm_client)

    sample_diff = """--- a/dendrophis/worker.py
+++ b/dendrophis/worker.py
@@ -43,3 +43,4 @@
 def run_worker():
+    x = fetch_task()
     return True
"""
    request = SubagentRequest(
        agent="code-reviewer",
        task_id="task_review_01",
        payload={
            "diff": sample_diff,
            "task": "Review worker task implementation",
        },
        context={},
    )

    response = await handler.execute(request)

    assert response.status == "success"
    assert response.result.get("approval") == "changes_requested"
    assert len(response.result.get("issues", [])) == 1
    assert response.result["issues"][0]["severity"] == "blocker"
    assert "Single-letter variable" in response.result["issues"][0]["description"]
    assert "Reject single-letter variable 'x'." in response.result.get("hettinger_notes", [])
    assert "Ensure connection is closed in a finally block." in response.result.get("greybeard_notes", [])


@pytest.mark.anyio
async def test_code_reviewer_markdown_fenced_json_parsing() -> None:
    mock_llm_client = MagicMock()

    raw_markdown_response = """
Here is my review based on Raymond Hettinger principles and Greybeard robustness:

```json
{
  "approval": "approved",
  "summary": "Clean concept chunking and no single-letter variable names.",
  "issues": [],
  "hettinger_notes": ["Excellent decomposition into single-purpose helpers."],
  "greybeard_notes": ["Resource cleanup is handled properly with context manager."]
}
```

Let me know if you need additional checks!
"""

    async def mock_stream_chat(messages_list):
        yield TextDeltaEvent(delta=raw_markdown_response)

    mock_llm_client.stream_chat = mock_stream_chat

    handler = CodeReviewerHandler(llm_client=mock_llm_client)

    request = SubagentRequest(
        agent="code-reviewer",
        task_id="task_review_02",
        payload={
            "task": "Review refactored batch processing pipeline",
            "changes": [{"action": "refactored", "file": "dendrophis/batch.py"}],
        },
        context={},
    )

    response = await handler.execute(request)

    assert response.status == "success"
    assert response.result.get("approval") == "approved"
    assert "Clean concept chunking" in response.result.get("summary", "")
    assert len(response.result.get("hettinger_notes", [])) == 1
    assert "Excellent decomposition" in response.result["hettinger_notes"][0]


@pytest.mark.anyio
async def test_code_reviewer_with_referenced_files(tmp_path: Path) -> None:
    test_file_path = tmp_path / "service.py"
    test_file_path.write_text("def process_orders(order_items):\n    return len(order_items)\n", encoding="utf-8")

    mock_llm_client = MagicMock()
    captured_messages = []

    async def mock_stream_chat(messages_list):
        captured_messages.extend(messages_list)
        yield TextDeltaEvent(delta=json.dumps({"approval": "approved", "summary": "Looks good.", "issues": []}))

    mock_llm_client.stream_chat = mock_stream_chat

    handler = CodeReviewerHandler(llm_client=mock_llm_client)

    request = SubagentRequest(
        agent="code-reviewer",
        task_id="task_review_03",
        payload={
            "files": [str(test_file_path)],
            "task": "Review service.py for code quality",
        },
        context={},
    )

    response = await handler.execute(request)

    assert response.status == "success"
    assert len(captured_messages) >= 2
    user_prompt_content = captured_messages[1]["content"]
    assert "Referenced Files:" in user_prompt_content
    assert "def process_orders(order_items):" in user_prompt_content


@pytest.mark.anyio
async def test_test_runner_handler_failure_diagnosis() -> None:
    mock_bash_tool = MagicMock()
    mock_llm_client = MagicMock()

    mock_pytest_stdout = """
============================= test session starts ==============================
collected 2 items

test_sample.py .F                                                        [100%]

=================================== FAILURES ===================================
FAILED test_sample.py::test_calculation - AssertionError: assert 10 == 20
=========================== short test summary info ============================
FAILED test_sample.py::test_calculation
========================= 1 failed, 1 passed in 0.10s ==========================
"""
    mock_bash_tool.execute = AsyncMock(
        return_value={
            "stdout": mock_pytest_stdout,
            "stderr": "",
            "returncode": 1,
        }
    )

    async def mock_stream_chat(messages_list):
        yield TextDeltaEvent(delta="Root cause: calculation output was 10 instead of expected 20.")

    mock_llm_client.stream_chat = mock_stream_chat

    handler = TestRunnerHandler(
        bash_tool=mock_bash_tool,
        llm_client=mock_llm_client,
    )

    request = SubagentRequest(
        agent="test-runner",
        task_id="task_test_01",
        payload={"command": "pytest", "target": "tests/test_sample.py"},
        context={},
    )

    response = await handler.execute(request)

    assert response.status == "failure"
    assert response.result.get("summary", {}).get("failed") == 1
    assert response.result.get("summary", {}).get("passed") == 1
    assert "Root cause: calculation output was 10" in response.result.get("diagnosis", "")
