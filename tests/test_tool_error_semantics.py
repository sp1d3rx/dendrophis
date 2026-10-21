"""Regression tests for tool error semantics (audit item 12).

Covers:
- ``is_tool_error`` is the single canonical error predicate (one function
  object shared by tools/executor.py, session/tools.py, and the code-writer
  subagent handler).
- ToolExecutor derives ``success`` with the canonical predicate.
- Error results are single-encoded JSON: ``{"error": "<plain message>"}`` —
  never ``{"error": "{\"error\": ...}"}`` (the old double-encoding bug).
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest

from dendrophis.config.schema import DendrophisConfig
from dendrophis.session import tools as session_tools
from dendrophis.session.tools import SessionToolExecutor
from dendrophis.subagents.handlers.code_writer import CodeWriterHandler
from dendrophis.tools import executor as executor_module
from dendrophis.tools.executor import ToolExecutor, is_tool_error

# --- Canonical predicate -----------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (json.dumps({"error": "boom"}), True),
        (json.dumps({"error": "boom", "hint": "try again"}), True),
        (json.dumps({"result": "ok"}), False),
        ('{"error": "outer"}', True),
        # Double-encoded payload: still an error (outer dict has "error"),
        # but the message value must never be produced by our own code.
        (json.dumps({"error": json.dumps({"error": "inner"})}), True),
        ('{"result": "ok"}', False),
        ("[1, 2, 3]", False),
        ("plain text mentioning an error", False),
        ("def handle_error(msg): pass", False),
        ("", False),
        ("not json at all", False),
    ],
)
def test_is_tool_error_semantics(content: str, expected: bool) -> None:
    assert is_tool_error(content) is expected


def test_is_tool_error_non_string_content() -> None:
    assert is_tool_error(None) is False  # type: ignore[arg-type]


def test_single_canonical_predicate_object() -> None:
    """All import paths must resolve to the SAME function object."""
    from dendrophis.session.tools import is_tool_error as session_is_tool_error

    assert session_is_tool_error is is_tool_error
    assert executor_module.is_tool_error is is_tool_error


def test_code_writer_predicate_matches_canonical() -> None:
    class DummyResult:
        def __init__(self, content: str) -> None:
            self.content = content

    cases = [
        json.dumps({"error": "File not found"}),
        "def handle_error(msg): pass",
        json.dumps({"result": "ok"}),
        "plain text",
    ]
    for content in cases:
        assert CodeWriterHandler._is_tool_error(DummyResult(content)) is is_tool_error(content)


# --- ToolExecutor success derivation ------------------------------------------


class FakeTool:
    def __init__(self, name: str, return_value: Any, raise_exc: Exception | None = None) -> None:
        self.name = name
        self.parameters: dict[str, Any] = {}
        self._return_value = return_value
        self._raise_exc = raise_exc

    async def execute(self, **kwargs: Any) -> Any:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._return_value


class FakeRegistry:
    def __init__(self, tools: dict[str, FakeTool]) -> None:
        self._tools = tools

    def get(self, name: str) -> FakeTool | None:
        return self._tools.get(name)


class FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str = "{}") -> None:
        self.id = id
        self.name = name
        self.arguments = arguments


def _assert_single_encoded_error(result: Any, expected_message: str) -> None:
    """The content must be one JSON object whose error value is a plain string."""
    parsed = json.loads(result.content)
    assert isinstance(parsed, dict)
    assert parsed["error"] == expected_message
    assert isinstance(parsed["error"], str)
    # The message itself must NOT be a JSON document (the double-encoding bug).
    try:
        json.loads(parsed["error"])
    except json.JSONDecodeError:
        pass
    else:
        pytest.fail(f"error message is itself JSON (double-encoded): {parsed['error']!r}")


@pytest.mark.anyio
async def test_executor_dict_error_result() -> None:
    tool = FakeTool("boom", {"error": "something broke"})
    executor = ToolExecutor(FakeRegistry({"boom": tool}))
    result = await executor.execute(FakeToolCall("c1", "boom"))
    assert result.success is False
    _assert_single_encoded_error(result, "something broke")


@pytest.mark.anyio
async def test_executor_dict_ok_result() -> None:
    tool = FakeTool("ok", {"result": "fine"})
    executor = ToolExecutor(FakeRegistry({"ok": tool}))
    result = await executor.execute(FakeToolCall("c1", "ok"))
    assert result.success is True
    assert json.loads(result.content) == {"result": "fine"}


@pytest.mark.anyio
async def test_executor_string_error_result() -> None:
    tool = FakeTool("boom", json.dumps({"error": "string error"}))
    executor = ToolExecutor(FakeRegistry({"boom": tool}))
    result = await executor.execute(FakeToolCall("c1", "boom"))
    assert result.success is False
    _assert_single_encoded_error(result, "string error")


@pytest.mark.anyio
async def test_executor_string_plain_result_wrapped() -> None:
    tool = FakeTool("ok", "just some plain output")
    executor = ToolExecutor(FakeRegistry({"ok": tool}))
    result = await executor.execute(FakeToolCall("c1", "ok"))
    assert result.success is True
    assert json.loads(result.content) == {"result": "just some plain output"}


@pytest.mark.anyio
async def test_executor_exception_result() -> None:
    tool = FakeTool("boom", None, raise_exc=RuntimeError("kaboom"))
    executor = ToolExecutor(FakeRegistry({"boom": tool}))
    result = await executor.execute(FakeToolCall("c1", "boom"))
    assert result.success is False
    _assert_single_encoded_error(result, "Execution failed: kaboom")


# --- SessionToolExecutor confirmation paths (the double-encoding bug sites) ----


class MockTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self._self_confirming = False
        self.silent = False

    @property
    def self_confirming(self) -> bool:
        return self._self_confirming


class MockToolRegistry:
    def __init__(self, tools: dict[str, MockTool]) -> None:
        self._tools = tools

    def get(self, name: str) -> MockTool | None:
        return self._tools.get(name)


class MockToolResult:
    def __init__(self, tool_call_id: str, name: str, content: str) -> None:
        self.tool_call_id = tool_call_id
        self.name = name
        self.content = content


class MockToolExecutor:
    async def execute(self, tool_call: Any) -> MockToolResult:
        return MockToolResult(tool_call.id, tool_call.name, '{"result": "executed"}')


class MockToolCall:
    def __init__(self, id: str, name: str, arguments: str = "{}", index: int = 0) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments
        self.index = index


def _make_session_executor(config: DendrophisConfig) -> SessionToolExecutor:
    return SessionToolExecutor(
        tool_registry=MockToolRegistry({"write": MockTool("write")}),
        tool_executor=MockToolExecutor(),
        event_bus=None,
        config=config,
        pending_confirmations={},
        confirmation_results={},
        cancel_flag=threading.Event(),
        emit=lambda event: None,
    )


@pytest.mark.anyio
async def test_rejection_result_single_encoded() -> None:
    """A rejected confirmation must yield single-encoded error JSON."""
    config = DendrophisConfig()
    config.permissions.require_confirmation = ["write"]
    session_tool_executor = _make_session_executor(config)

    tool_calls = [MockToolCall(id="call_1", name="write", index=0)]
    execute_task = asyncio.create_task(session_tool_executor.execute(tool_calls))

    # Wait for the confirmation request, then reject it.
    while not session_tool_executor._pending_confirmations:
        await asyncio.sleep(0.01)
    request_id = next(iter(session_tool_executor._pending_confirmations))
    session_tool_executor.respond_to_confirmation(request_id, False)

    results = await asyncio.wait_for(execute_task, timeout=5.0)
    assert len(results) == 1
    _assert_single_encoded_error(results[0], "Tool execution rejected by user")


@pytest.mark.anyio
async def test_confirmation_timeout_result_single_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A confirmation timeout must yield single-encoded error JSON."""
    monkeypatch.setattr(session_tools, "CONFIRMATION_TIMEOUT", 0.05)
    config = DendrophisConfig()
    config.permissions.require_confirmation = ["write"]
    session_tool_executor = _make_session_executor(config)

    tool_calls = [MockToolCall(id="call_2", name="write", index=0)]
    results = await asyncio.wait_for(session_tool_executor.execute(tool_calls), timeout=5.0)
    assert len(results) == 1
    _assert_single_encoded_error(results[0], "Tool execution timed out waiting for approval")


@pytest.mark.anyio
async def test_execution_exception_result_single_encoded() -> None:
    """A crashing tool executor must yield single-encoded error JSON."""

    class CrashingExecutor:
        async def execute(self, tool_call: Any) -> Any:
            raise RuntimeError("sandbox exploded")

    config = DendrophisConfig()
    config.permissions.allowed_tools = ["write"]
    session_tool_executor = SessionToolExecutor(
        tool_registry=MockToolRegistry({"write": MockTool("write")}),
        tool_executor=CrashingExecutor(),
        event_bus=None,
        config=config,
        pending_confirmations={},
        confirmation_results={},
        cancel_flag=threading.Event(),
        emit=lambda event: None,
    )

    tool_calls = [MockToolCall(id="call_3", name="write", index=0)]
    results = await session_tool_executor.execute(tool_calls)
    assert len(results) == 1
    _assert_single_encoded_error(results[0], "Execution failed: sandbox exploded")
