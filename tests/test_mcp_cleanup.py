"""Tests for deterministic resource release in the MCP manager.

Covers the leaks fixed in dendrophis/tools/mcp.py:
- F2: a failed ``__aenter__`` left a partially-entered context (and, for the
  HTTP path, an open httpx.AsyncClient) unreleased because the context was
  only registered in MCPManager._contexts after successful entry.
- F3: HTTPClientContextManager.__aexit__ skipped closing the http client when
  the stream context's __aexit__ raised.
- F4: MCPManager.aclose cancelled pending connect tasks without awaiting
  them, so in-flight connects could register state after cleanup finished.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from dendrophis.config.schema import DendrophisConfig
from dendrophis.tools.mcp import HTTPClientContextManager, MCPManager
from dendrophis.tools.registry import ToolRegistry


def _make_manager(mcp_servers: dict) -> MCPManager:
    config = DendrophisConfig.from_dict({"mcp_servers": mcp_servers})
    config.debug_log = None  # Avoid opening a real log file for stdio servers
    registry = ToolRegistry()
    return MCPManager(config, registry, debug_logger=lambda msg: None)


# ---------------------------------------------------------------------------
# F2: failed entry must release the partial context
# ---------------------------------------------------------------------------


class _FailingHTTPCtx:
    """Stand-in for HTTPClientContextManager whose entry always fails."""

    instances: ClassVar[list[_FailingHTTPCtx]] = []

    def __init__(self, url: str, verify_ssl: bool) -> None:
        self.url = url
        self.verify_ssl = verify_ssl
        self.http_client = None
        self.stream_ctx = None
        self.exited = False
        _FailingHTTPCtx.instances.append(self)

    async def __aenter__(self):
        raise RuntimeError("stream connect failed")

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.exited = True


@pytest.mark.asyncio
async def test_http_entry_failure_releases_registered_context() -> None:
    manager = _make_manager({"http-srv": {"url": "http://127.0.0.1:9/mcp"}})
    _FailingHTTPCtx.instances = []

    with patch("dendrophis.tools.mcp.HTTPClientContextManager", _FailingHTTPCtx):
        await manager._connect_server("http-srv", manager.config.mcp_servers["http-srv"])

    assert len(_FailingHTTPCtx.instances) == 1
    # The partial context is released even though entry failed.
    assert _FailingHTTPCtx.instances[0].exited is True
    # No stale state left behind.
    assert "http-srv" not in manager._contexts
    assert "http-srv" not in manager._sessions
    assert not manager.tool_registry.names()


@pytest.mark.asyncio
async def test_http_client_closed_when_stream_entry_fails() -> None:
    """HTTPClientContextManager.__aenter__ must close the http client it opened."""
    mock_http = AsyncMock()
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.side_effect = RuntimeError("connection refused")

    cm = HTTPClientContextManager("http://127.0.0.1:9/mcp", verify_ssl=True)
    with (
        patch("httpx.AsyncClient", return_value=mock_http),
        patch("mcp.client.streamable_http.streamable_http_client", return_value=mock_stream_ctx),
        pytest.raises(RuntimeError, match="connection refused"),
    ):
        await cm.__aenter__()

    # The already-opened client is released and the manager holds no partial refs.
    mock_http.__aexit__.assert_awaited_once_with(None, None, None)
    assert cm.http_client is None
    assert cm.stream_ctx is None


@pytest.mark.asyncio
async def test_stdio_entry_failure_releases_registered_context() -> None:
    manager = _make_manager({"stdio-srv": {"command": "python", "args": []}})

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.side_effect = RuntimeError("spawn failed")

    with patch("dendrophis.tools.mcp.stdio_client", return_value=mock_ctx):
        await manager._connect_server("stdio-srv", manager.config.mcp_servers["stdio-srv"])

    # The context registered before entry is released by the cleanup path.
    mock_ctx.__aexit__.assert_awaited_once()
    assert "stdio-srv" not in manager._contexts
    assert "stdio-srv" not in manager._sessions
    assert not manager.tool_registry.names()


# ---------------------------------------------------------------------------
# F3: __aexit__ must release both resources even if one fails
# ---------------------------------------------------------------------------


def _make_cm_with_fakes(stream_ctx: AsyncMock, http_client: AsyncMock) -> HTTPClientContextManager:
    cm = HTTPClientContextManager("http://127.0.0.1:9/mcp", verify_ssl=True)
    cm.stream_ctx = stream_ctx
    cm.http_client = http_client
    return cm


@pytest.mark.asyncio
async def test_stream_exit_error_does_not_skip_http_close() -> None:
    mock_http = AsyncMock()
    mock_stream = AsyncMock()
    mock_stream.__aexit__.side_effect = RuntimeError("stream teardown failed")

    cm = _make_cm_with_fakes(mock_stream, mock_http)

    with pytest.raises(RuntimeError, match="stream teardown failed"):
        await cm.__aexit__(None, None, None)

    # Regression assertion: the http client is still closed even though the
    # stream teardown raised.
    mock_http.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_both_exit_errors_are_raised_together() -> None:
    mock_http = AsyncMock()
    mock_http.__aexit__.side_effect = RuntimeError("http teardown failed")
    mock_stream = AsyncMock()
    mock_stream.__aexit__.side_effect = RuntimeError("stream teardown failed")

    cm = _make_cm_with_fakes(mock_stream, mock_http)

    with pytest.raises(ExceptionGroup) as exc_info:
        await cm.__aexit__(None, None, None)

    messages = {str(error) for error in exc_info.value.exceptions}
    assert messages == {"stream teardown failed", "http teardown failed"}


@pytest.mark.asyncio
async def test_clean_exit_closes_both_in_order() -> None:
    mock_http = AsyncMock()
    mock_stream = AsyncMock()

    cm = _make_cm_with_fakes(mock_stream, mock_http)
    await cm.__aexit__(None, None, None)

    mock_stream.__aexit__.assert_awaited_once_with(None, None, None)
    mock_http.__aexit__.assert_awaited_once_with(None, None, None)


# ---------------------------------------------------------------------------
# F4: aclose must await cancelled connect tasks before cleaning up
# ---------------------------------------------------------------------------


class _HangingHTTPCtx:
    """HTTP context whose entry blocks until cancelled."""

    instances: ClassVar[list[_HangingHTTPCtx]] = []

    def __init__(self, url: str, verify_ssl: bool) -> None:
        self.url = url
        self.verify_ssl = verify_ssl
        self.http_client = None
        self.stream_ctx = None
        self.exited = False
        _HangingHTTPCtx.instances.append(self)

    async def __aenter__(self):
        await asyncio.Event().wait()  # Hang until the task is cancelled.
        return (object(), object())

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.exited = True


@pytest.mark.asyncio
async def test_aclose_awaits_cancelled_connect_task_and_cleans_partial_ctx() -> None:
    manager = _make_manager({"hang-srv": {"url": "http://127.0.0.1:9/mcp"}})
    _HangingHTTPCtx.instances = []

    with patch("dendrophis.tools.mcp.HTTPClientContextManager", _HangingHTTPCtx):
        await manager.initialize_servers()
        # Let the connect task run until it hangs inside __aenter__.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Precondition: the in-flight connect has registered a partial context.
        assert len(_HangingHTTPCtx.instances) == 1
        assert "hang-srv" in manager._contexts

        await manager.aclose()

    connect_task = manager._tasks[0]
    # The task was cancelled AND fully awaited by aclose.
    assert connect_task.cancelled() is True
    assert connect_task.done() is True
    # The partial context was released after the task unwound.
    assert _HangingHTTPCtx.instances[0].exited is True
    assert "hang-srv" not in manager._contexts
    assert "hang-srv" not in manager._sessions
    assert not manager.tool_registry.names()
