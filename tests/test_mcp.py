"""Unit tests for Model Context Protocol (MCP) integration."""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.widgets import Switch

from dendrophis.config.schema import DendrophisConfig
from dendrophis.session.tools import SessionToolExecutor
from dendrophis.tools.mcp import MCPManager, MCPTool
from dendrophis.tools.registry import ToolRegistry
from dendrophis.ui.screens.settings import McpServerEntryRow, McpServerListEditor
from dendrophis.ui.widgets.panels.mcp_status_panel import McpStatusPanel


def test_mcp_config_schema() -> None:
    """Verify that MCP server configs are correctly parsed in DendrophisConfig."""
    config = DendrophisConfig.model_validate(
        {
            "mcp_servers": {
                "test-server": {
                    "command": "python",
                    "args": ["-m", "test_mcp"],
                    "env": {"TEST_VAR": "123"},
                },
                "disabled-server": {
                    "command": "node",
                    "enabled": False,
                },
            }
        }
    )
    assert "test-server" in config.mcp_servers
    assert config.mcp_servers["test-server"].command == "python"
    assert config.mcp_servers["test-server"].args == ["-m", "test_mcp"]
    assert config.mcp_servers["test-server"].env == {"TEST_VAR": "123"}
    assert config.mcp_servers["test-server"].enabled is True

    assert "disabled-server" in config.mcp_servers
    assert config.mcp_servers["disabled-server"].enabled is False


@pytest.mark.asyncio
async def test_mcp_manager_init_and_registration() -> None:
    """Verify that MCPManager connects to servers, registers tools, delegates calls, and closes resources."""
    config = DendrophisConfig.model_validate(
        {
            "mcp_servers": {
                "test-server": {
                    "command": "python",
                    "args": [],
                }
            }
        }
    )

    registry = ToolRegistry()
    manager = MCPManager(config, registry)

    # Mock stdio_client context manager
    mock_read = MagicMock()
    mock_write = MagicMock()

    mock_stdio_ctx = AsyncMock()
    mock_stdio_ctx.__aenter__.return_value = (mock_read, mock_write)

    # Mock ClientSession
    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()

    # Mock list_tools output
    mock_mcp_tool = MagicMock()
    mock_mcp_tool.name = "mcp_echo"
    mock_mcp_tool.description = "Echoes input"
    mock_mcp_tool.inputSchema = {"type": "object", "properties": {"msg": {"type": "string"}}}

    mock_tools_result = MagicMock()
    mock_tools_result.tools = [mock_mcp_tool]

    mock_session.list_tools.return_value = mock_tools_result

    with (
        patch("dendrophis.tools.mcp.stdio_client", return_value=mock_stdio_ctx),
        patch("dendrophis.tools.mcp.ClientSession", return_value=mock_session),
    ):
        await manager.initialize_servers()

        # Wait for background tasks to complete
        await asyncio.gather(*manager._tasks)

        assert "mcp_echo" in registry.names()
        tool = registry.get("mcp_echo")
        assert isinstance(tool, MCPTool)
        assert tool.name == "mcp_echo"
        assert tool.description == "Echoes input"
        assert tool.parameters == {"type": "object", "properties": {"msg": {"type": "string"}}}

        # Test execution
        mock_content = MagicMock()
        mock_content.text = "hello world"
        mock_call_result = MagicMock()
        mock_call_result.content = [mock_content]
        mock_session.call_tool.return_value = mock_call_result

        result = await tool.execute(msg="hello")
        assert result == "hello world"
        mock_session.call_tool.assert_called_once_with("mcp_echo", arguments={"msg": "hello"})

        # Test aclose
        await manager.aclose()
        mock_session.__aexit__.assert_called_once()
        mock_stdio_ctx.__aexit__.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_manager_disabled_server() -> None:
    """Verify that disabled MCP servers are not initialized."""
    config = DendrophisConfig.model_validate(
        {
            "mcp_servers": {
                "disabled-server": {
                    "command": "python",
                    "enabled": False,
                }
            }
        }
    )

    registry = ToolRegistry()
    manager = MCPManager(config, registry)

    mock_stdio_client = MagicMock()
    with patch("dendrophis.tools.mcp.stdio_client", mock_stdio_client):
        await manager.initialize_servers()
        await asyncio.gather(*manager._tasks)

        assert not registry.names()
        mock_stdio_client.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_manager_sync_servers() -> None:
    """Verify that sync_servers dynamically starts new and stops disabled/removed servers."""
    config = DendrophisConfig.model_validate(
        {
            "mcp_servers": {
                "server-a": {
                    "command": "python",
                    "enabled": True,
                },
                "server-b": {
                    "command": "python",
                    "enabled": False,
                },
            }
        }
    )

    registry = ToolRegistry()
    manager = MCPManager(config, registry)

    # Mock stdio_client context managers
    mock_read = MagicMock()
    mock_write = MagicMock()

    mock_stdio_ctx_a = AsyncMock()
    mock_stdio_ctx_a.__aenter__.return_value = (mock_read, mock_write)
    mock_stdio_ctx_b = AsyncMock()
    mock_stdio_ctx_b.__aenter__.return_value = (mock_read, mock_write)

    # Mock ClientSessions
    mock_session_a = AsyncMock()
    mock_session_b = AsyncMock()

    mock_tool_a = MagicMock()
    mock_tool_a.name = "tool_a"
    mock_tool_a.inputSchema = {}
    mock_tools_result_a = MagicMock()
    mock_tools_result_a.tools = [mock_tool_a]
    mock_session_a.list_tools.return_value = mock_tools_result_a

    mock_tool_b = MagicMock()
    mock_tool_b.name = "tool_b"
    mock_tool_b.inputSchema = {}
    mock_tools_result_b = MagicMock()
    mock_tools_result_b.tools = [mock_tool_b]
    mock_session_b.list_tools.return_value = mock_tools_result_b

    # Patch stdio_client and ClientSession
    with (
        patch("dendrophis.tools.mcp.stdio_client") as mock_stdio_client,
        patch("dendrophis.tools.mcp.ClientSession") as mock_session_cls,
    ):
        mock_stdio_client.side_effect = [mock_stdio_ctx_a, mock_stdio_ctx_b]
        mock_session_cls.side_effect = [mock_session_a, mock_session_b]

        # 1. Initial connection
        await manager.initialize_servers()
        await asyncio.gather(*manager._tasks)

        assert "tool_a" in registry.names()
        assert "tool_b" not in registry.names()

        # 2. Modify config: disable server-a, enable server-b
        config.mcp_servers["server-a"].enabled = False
        config.mcp_servers["server-b"].enabled = True

        # Clear tasks list to wait for the new sync task
        manager._tasks.clear()

        await manager.sync_servers()
        if manager._tasks:
            await asyncio.gather(*manager._tasks)

        # 3. Verify server-a tools were removed, and server-b tools were registered
        assert "tool_a" not in registry.names()
        assert "tool_b" in registry.names()

        # Verify server-a was cleaned up
        mock_session_a.__aexit__.assert_called_once()
        mock_stdio_ctx_a.__aexit__.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_disabled_tool_validation() -> None:
    """Verify that disabled tools result in a specific 'disabled' error message."""
    registry = ToolRegistry()

    # Create a dummy tool and add/remove it to mark it as disabled
    class DummyTool:
        def __init__(self, name: str) -> None:
            self.name = name

    dummy = DummyTool("disabled_mcp_tool")
    registry.add(dummy)
    registry.remove("disabled_mcp_tool")

    assert registry.is_disabled("disabled_mcp_tool") is True

    config = DendrophisConfig.model_validate({})

    # Setup SessionToolExecutor
    cancel_flag = threading.Event()
    executor = SessionToolExecutor(
        tool_registry=registry,
        tool_executor=None,
        event_bus=None,
        config=config,
        pending_confirmations={},
        confirmation_results={},
        cancel_flag=cancel_flag,
        emit=lambda event: None,
    )

    # Mock a tool call
    mock_tool_call = MagicMock()
    mock_tool_call.name = "disabled_mcp_tool"
    mock_tool_call.id = "call_123"
    mock_tool_call.arguments = "{}"

    # Verify execution return value
    results = await executor.execute([mock_tool_call])
    assert len(results) == 1
    result = results[0]
    assert result.tool_call_id == "call_123"
    assert result.name == "disabled_mcp_tool"

    content = json.loads(result.content)
    assert "error" in content
    assert "disabled_mcp_tool" in content["error"]
    assert "currently disabled and is not available" in content["error"]


def test_mcp_status_panel_integration() -> None:
    """Consolidated test cases for McpStatusPanel widget."""
    # Case 1: no manager on session
    mock_session = MagicMock()
    mock_session.mcp_manager = None
    mock_event_bus = MagicMock()

    panel = McpStatusPanel(session=mock_session, event_bus=mock_event_bus)
    output = panel.render_value()
    assert output == "[dim]No MCP Manager[/dim]"

    # Case 2: no enabled servers
    mock_manager = MagicMock()
    mock_manager.config.mcp_servers = {}
    mock_session.mcp_manager = mock_manager

    panel = McpStatusPanel(session=mock_session, event_bus=mock_event_bus)
    output = panel.render_value()
    assert output == "[dim]No enabled servers[/dim]"

    # Case 3: rendering different connection states
    mock_server_gkeep = MagicMock(enabled=True)
    mock_server_postgres = MagicMock(enabled=False)
    mock_server_filesystem = MagicMock(enabled=True)

    mock_manager.config.mcp_servers = {
        "gkeep": mock_server_gkeep,
        "postgres": mock_server_postgres,
        "filesystem": mock_server_filesystem,
    }
    mock_manager._sessions = {"gkeep": MagicMock()}

    panel = McpStatusPanel(session=mock_session, event_bus=mock_event_bus)
    output = panel.render_value()

    lines = output.split("\n")
    assert len(lines) == 2
    assert "filesystem" in lines[0]
    assert "○" in lines[0]
    assert "gkeep" in lines[1]
    assert "●" in lines[1]

    # Case 4: subscription behavior
    panel.set_interval = MagicMock()
    panel.on_mount()
    mock_event_bus.bind.assert_called_once_with(panel)

    panel.on_unmount()
    panel._events.unsubscribe_all.assert_called_once()


def test_settings_mcp_integration() -> None:
    """Consolidated test cases for MCP configuration settings UI row and list editor."""
    # Case 1: McpServerEntryRow initialization
    row = McpServerEntryRow(
        server_name="gkeep",
        command="npx",
        arguments=["-y", "mcp-gkeep"],
        env_vars={"GKEEP_TOKEN": "my-secret-token"},
        enabled=True,
        url="http://127.0.0.1:8443/mcp",
    )
    assert row._initial_name == "gkeep"
    assert row._initial_command == "npx"
    assert row._initial_args == ["-y", "mcp-gkeep"]
    assert row._initial_env == {"GKEEP_TOKEN": "my-secret-token"}
    assert row._initial_enabled is True
    assert row._initial_url == "http://127.0.0.1:8443/mcp"

    # Case 2: McpServerEntryRow extraction
    mock_input_name = MagicMock()
    mock_input_name.value = "  postgres-mcp  "

    mock_input_command = MagicMock()
    mock_input_command.value = "  docker  "

    mock_input_url = MagicMock()
    mock_input_url.value = "  http://127.0.0.1:5432/mcp  "

    mock_input_args = MagicMock()
    mock_input_args.value = "run, -i, --rm"

    mock_input_env = MagicMock()
    mock_input_env.value = "DB_HOST=127.0.0.1, DB_PORT=5432"

    mock_switch = MagicMock()
    mock_switch.value = False

    def mock_query_one(selector, expected_type=None):
        if selector == "Switch" or selector is Switch:
            return mock_switch
        selector_str = str(selector)
        if ".mcp-name-input" in selector_str:
            return mock_input_name
        if ".mcp-command" in selector_str:
            return mock_input_command
        if ".mcp-url" in selector_str:
            return mock_input_url
        if ".mcp-args" in selector_str:
            return mock_input_args
        if ".mcp-env" in selector_str:
            return mock_input_env
        return MagicMock()

    row.query_one = mock_query_one

    extracted_data = row.get_data()
    assert extracted_data["name"] == "postgres-mcp"
    assert extracted_data["config"]["command"] == "docker"
    assert extracted_data["config"]["url"] == "http://127.0.0.1:5432/mcp"
    assert extracted_data["config"]["args"] == ["run", "-i", "--rm"]
    assert extracted_data["config"]["env"] == {
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
    }
    assert extracted_data["config"]["enabled"] is False

    # Case 3: McpServerListEditor list accumulation
    initial_configs = {
        "gkeep": MagicMock(command="npx", args=["mcp-gkeep"], env={}, enabled=True),
        "postgres": MagicMock(
            command="docker",
            args=["run"],
            env={"PORT": "5432"},
            enabled=False,
        ),
    }

    editor = McpServerListEditor(
        title="MCP Servers",
        initial_servers=initial_configs,
        id="test-editor",
    )

    mock_row_gkeep = MagicMock()
    mock_row_gkeep.get_data.return_value = {
        "name": "gkeep",
        "config": {
            "command": "npx",
            "args": ["mcp-gkeep"],
            "env": None,
            "enabled": True,
        },
    }

    mock_row_postgres = MagicMock()
    mock_row_postgres.get_data.return_value = {
        "name": "postgres",
        "config": {
            "command": "docker",
            "args": ["run"],
            "env": {"PORT": "5432"},
            "enabled": False,
        },
    }

    editor.query = MagicMock(return_value=[mock_row_gkeep, mock_row_postgres])

    servers_dictionary = editor.get_servers_dict()
    assert "gkeep" in servers_dictionary
    assert servers_dictionary["gkeep"]["command"] == "npx"
    assert servers_dictionary["gkeep"]["enabled"] is True

    assert "postgres" in servers_dictionary
    assert servers_dictionary["postgres"]["command"] == "docker"
    assert servers_dictionary["postgres"]["env"] == {"PORT": "5432"}
    assert servers_dictionary["postgres"]["enabled"] is False
