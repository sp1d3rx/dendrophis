from __future__ import annotations

from typing import Any

import pytest

from dendrophis.config.loader import ConfigLoader
from dendrophis.session.factory import SessionFactory
from dendrophis.tools.base import BaseTool
from dendrophis.tools.builtins.filesystem import BashTool
from dendrophis.tools.registry import ToolRegistry


class DummyTool(BaseTool):
    """A dummy tool for testing injection."""

    @property
    def name(self) -> str:
        return "dummy_tool"

    @property
    def description(self) -> str:
        return "Dummy tool description"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        return {"success": True}


@pytest.mark.anyio
async def test_session_factory_di() -> None:
    # Create configuration loader
    config_loader = ConfigLoader.load(config_path="dendrophis.yaml")

    # Create custom registry and add a dummy tool
    custom_registry = ToolRegistry()
    dummy_instance = DummyTool()
    custom_registry.add(dummy_instance)

    # Create session
    session_instance = SessionFactory.create_session(
        config_loader=config_loader,
    )

    # Inject tool registry via set_tools
    from dendrophis.tools import ToolExecutor

    custom_executor = ToolExecutor(custom_registry)
    session_instance.set_tools(custom_registry, custom_executor)

    # Check that session uses the custom registry
    assert session_instance._tool_registry is custom_registry
    assert session_instance._tool_registry.get("dummy_tool") is dummy_instance


@pytest.mark.anyio
async def test_bash_tool_restricted_prefixes() -> None:
    # Instantiate bash tool
    restricted_tool = BashTool()

    # Whitelisted commands
    result_whitelisted = await restricted_tool.execute(
        command="echo hello",
        description="Print hello",
    )
    assert result_whitelisted.get("success") is True
    assert "hello" in result_whitelisted.get("stdout", "")

    # Blocked commands
    result_blocked = await restricted_tool.execute(
        command="rm -rf /",
        description="Delete everything",
    )
    assert "Dangerous command blocked" in result_blocked.get("error", "")


@pytest.mark.anyio
async def test_subagent_tool_injection() -> None:
    # Test that CodeWriterHandler creates its own tools via registry
    from dendrophis.subagents.handlers import CodeWriterHandler

    handler_writer = CodeWriterHandler()
    registry = handler_writer.tool_registry
    executor = handler_writer.tool_executor

    # Verify tools are registered and executor is the cached instance
    assert registry is not None
    assert executor is not None
    # Verify executor is cached (same instance on repeated access)
    assert handler_writer.tool_executor is executor


def test_session_tool_executor_update_tools() -> None:
    import threading

    from dendrophis.session.tools import SessionToolExecutor
    from dendrophis.tools import ToolExecutor
    from dendrophis.tools.registry import ToolRegistry

    toolRegistry = ToolRegistry()
    dummyTool = DummyTool()
    toolRegistry.add(dummyTool)
    tool_executor = ToolExecutor(toolRegistry)

    sessionExecutor = SessionToolExecutor(
        tool_registry=None,
        tool_executor=None,
        event_bus=None,
        config=None,
        pending_confirmations={},
        confirmation_results={},
        cancel_flag=threading.Event(),
        emit=lambda event: None,
    )

    assert sessionExecutor._tool_registry is None
    assert sessionExecutor._tool_executor is None

    sessionExecutor.update_tools(toolRegistry, tool_executor)
    assert sessionExecutor._tool_registry is toolRegistry
    assert sessionExecutor._tool_executor is tool_executor
