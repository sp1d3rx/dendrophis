"""Unit tests for SessionToolExecutor verifying it in isolation."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from dendrophis.config.schema import DendrophisConfig
from dendrophis.events import (
    ToolConfirmationRequestEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
)
from dendrophis.session.tools import SessionToolExecutor


class MockTool:
    """Mock tool implementation for testing."""

    def __init__(self, name: str, self_confirming: bool = False) -> None:
        self.name = name
        self._self_confirming = self_confirming
        self.silent = False

    @property
    def self_confirming(self) -> bool:
        return self._self_confirming


class MockToolRegistry:
    """Mock tool registry for testing."""

    def __init__(self, tools: dict[str, MockTool]) -> None:
        self.tools = tools

    def get(self, name: str) -> MockTool | None:
        return self.tools.get(name)


class MockToolResult:
    """Mock tool result for testing."""

    def __init__(self, tool_call_id: str, name: str, content: str) -> None:
        self.tool_call_id = tool_call_id
        self.name = name
        self.content = content


class MockToolExecutor:
    """Mock tool executor for testing."""

    async def execute(self, tool_call: Any) -> MockToolResult:
        return MockToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=f'{{"result": "executed {tool_call.name}"}}',
        )


class MockToolCall:
    """Mock tool call for testing."""

    def __init__(self, id: str, name: str, arguments: str = "{}", index: int = 0) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments
        self.index = index


@pytest.mark.anyio
async def test_execute_allowed_tool() -> None:
    """Test that allowed tools execute immediately without confirmation."""
    tool_registry = MockToolRegistry({"read": MockTool("read", self_confirming=False)})
    tool_executor = MockToolExecutor()
    events_list: list[Any] = []

    def emit_event(event: Any) -> None:
        events_list.append(event)

    pending_confirmations: dict[str, bool] = {}
    confirmation_results: dict[str, bool] = {}
    cancel_flag = threading.Event()

    # Permission config allowing "read"
    config = DendrophisConfig()
    config.permissions.allowed_tools = ["read"]

    session_tool_executor = SessionToolExecutor(
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        event_bus=None,
        config=config,
        pending_confirmations=pending_confirmations,
        confirmation_results=confirmation_results,
        cancel_flag=cancel_flag,
        emit=emit_event,
    )

    tool_calls = [
        MockToolCall(
            id="call_001",
            name="read",
            arguments='{"file_path": "test.txt"}',
            index=0,
        )
    ]

    results = await session_tool_executor.execute(tool_calls)
    assert len(results) == 1
    assert results[0].tool_call_id == "call_001"
    assert results[0].name == "read"
    assert "executed read" in results[0].content

    # Check that ToolExecutionStartedEvent and ToolExecutionFinishedEvent were emitted
    assert len(events_list) == 2
    assert isinstance(events_list[0], ToolExecutionStartedEvent)
    assert isinstance(events_list[1], ToolExecutionFinishedEvent)


@pytest.mark.anyio
async def test_execute_denied_tool() -> None:
    """Test that denied tools return an immediate error and do not execute."""
    tool_registry = MockToolRegistry({"write": MockTool("write", self_confirming=False)})
    tool_executor = MockToolExecutor()
    events_list: list[Any] = []

    def emit_event(event: Any) -> None:
        events_list.append(event)

    pending_confirmations: dict[str, bool] = {}
    confirmation_results: dict[str, bool] = {}
    cancel_flag = threading.Event()

    # Permission config denying "write"
    config = DendrophisConfig()
    config.permissions.denied_tools = ["write"]

    session_tool_executor = SessionToolExecutor(
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        event_bus=None,
        config=config,
        pending_confirmations=pending_confirmations,
        confirmation_results=confirmation_results,
        cancel_flag=cancel_flag,
        emit=emit_event,
    )

    tool_calls = [
        MockToolCall(
            id="call_002",
            name="write",
            arguments='{"file_path": "test.txt", "content": "hello"}',
            index=0,
        )
    ]

    results = await session_tool_executor.execute(tool_calls)
    assert len(results) == 1
    assert results[0].tool_call_id == "call_002"
    assert "error" in results[0].content
    assert "not permitted" in results[0].content

    # No execution events should be emitted, as it was blocked immediately
    assert len(events_list) == 0


@pytest.mark.anyio
async def test_execute_confirmation_flow_approved() -> None:
    """Test that a tool requiring confirmation executes after being approved."""
    tool_registry = MockToolRegistry({"write": MockTool("write", self_confirming=False)})
    tool_executor = MockToolExecutor()
    events_list: list[Any] = []

    def emit_event(event: Any) -> None:
        events_list.append(event)

    pending_confirmations: dict[str, bool] = {}
    confirmation_results: dict[str, bool] = {}
    cancel_flag = threading.Event()

    # Config with write requiring confirmation
    config = DendrophisConfig()
    config.permissions.require_confirmation = ["write"]

    session_tool_executor = SessionToolExecutor(
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        event_bus=None,
        config=config,
        pending_confirmations=pending_confirmations,
        confirmation_results=confirmation_results,
        cancel_flag=cancel_flag,
        emit=emit_event,
    )

    tool_calls = [
        MockToolCall(
            id="call_003",
            name="write",
            arguments='{"file_path": "test.txt", "content": "hello"}',
            index=0,
        )
    ]

    # Run execute in a task since it will yield to poll for confirmation
    execute_task = asyncio.create_task(session_tool_executor.execute(tool_calls))

    # Wait for the confirmation request event to be emitted
    while not events_list:
        await asyncio.sleep(0.01)

    assert isinstance(events_list[0], ToolConfirmationRequestEvent)
    request_id = events_list[0].request_id
    assert request_id in pending_confirmations

    # Approve the confirmation
    confirmation_results[request_id] = True

    # Await the execution result
    results = await execute_task
    assert len(results) == 1
    assert results[0].tool_call_id == "call_003"
    assert "executed write" in results[0].content

    # Check events
    assert len(events_list) == 3
    assert isinstance(events_list[0], ToolConfirmationRequestEvent)
    assert isinstance(events_list[1], ToolExecutionStartedEvent)
    assert isinstance(events_list[2], ToolExecutionFinishedEvent)


@pytest.mark.anyio
async def test_execute_missing_required_arguments() -> None:
    """Test that a tool call with missing required arguments fails immediately without confirmation."""

    class MockToolWithParams:
        def __init__(self, name: str) -> None:
            self.name = name
            self.self_confirming = False
            self.silent = False
            self.parameters = {
                "type": "object",
                "properties": {"required_arg": {"type": "string"}},
                "required": ["required_arg"],
            }

    tool_registry = MockToolRegistry({"write": MockToolWithParams("write")})
    tool_executor = MockToolExecutor()
    events_list: list[Any] = []

    def emit_event(event: Any) -> None:
        events_list.append(event)

    pending_confirmations: dict[str, bool] = {}
    confirmation_results: dict[str, bool] = {}
    cancel_flag = threading.Event()

    # Config with write requiring confirmation
    config = DendrophisConfig()
    config.permissions.require_confirmation = ["write"]

    session_tool_executor = SessionToolExecutor(
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        event_bus=None,
        config=config,
        pending_confirmations=pending_confirmations,
        confirmation_results=confirmation_results,
        cancel_flag=cancel_flag,
        emit=emit_event,
    )

    # Tool call with missing "required_arg"
    tool_calls = [
        MockToolCall(
            id="call_004",
            name="write",
            arguments='{"other_arg": "value"}',
            index=0,
        )
    ]

    results = await session_tool_executor.execute(tool_calls)
    assert len(results) == 1
    assert results[0].tool_call_id == "call_004"
    assert "Missing required parameter" in results[0].content

    # No confirmation request event should be emitted
    assert not events_list


@pytest.mark.anyio
async def test_execute_parallel_tool_calls_ordering() -> None:
    """Test that parallel tool call execution preserves the original order of tool calls in results."""
    tool_registry = MockToolRegistry(
        {
            "write": MockTool("write", self_confirming=False),
            "read": MockTool("read", self_confirming=False),
        }
    )
    tool_executor = MockToolExecutor()
    events_list: list[Any] = []

    def emit_event(event: Any) -> None:
        events_list.append(event)

    pending_confirmations: dict[str, bool] = {}
    confirmation_results: dict[str, bool] = {}
    cancel_flag = threading.Event()

    # Config: write requires confirmation, read is allowed immediately
    config = DendrophisConfig()
    config.permissions.require_confirmation = ["write"]
    config.permissions.allowed_tools = ["write", "read"]

    session_tool_executor = SessionToolExecutor(
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        event_bus=None,
        config=config,
        pending_confirmations=pending_confirmations,
        confirmation_results=confirmation_results,
        cancel_flag=cancel_flag,
        emit=emit_event,
    )

    # First tool requires confirmation (write), second tool is auto-allowed (read)
    tool_calls = [
        MockToolCall(
            id="call_write_001",
            name="write",
            arguments='{"file_path": "test.txt", "content": "hello"}',
            index=0,
        ),
        MockToolCall(
            id="call_read_002",
            name="read",
            arguments='{"file_path": "test.txt"}',
            index=1,
        ),
    ]

    execute_task = asyncio.create_task(session_tool_executor.execute(tool_calls))

    # Wait for the confirmation request for write tool
    while not events_list:
        await asyncio.sleep(0.01)

    assert isinstance(events_list[0], ToolConfirmationRequestEvent)
    request_identifier = events_list[0].request_id

    # Approve write tool call
    confirmation_results[request_identifier] = True

    results = await execute_task
    assert len(results) == 2

    # Results MUST be in original tool_calls order: write (index 0) first, read (index 1) second
    assert results[0].tool_call_id == "call_write_001"
    assert results[0].name == "write"
    assert results[1].tool_call_id == "call_read_002"
    assert results[1].name == "read"

