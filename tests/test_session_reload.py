"""Tests for deterministic LLM client release on config reload.

Covers the leak fixed in Session.reload_config(): the previous LLMClient was
replaced without ever being closed, leaking its httpx.AsyncClient socket pool
on every hot config reload. The old client is now retired and deterministically
released in Session.aclose().
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

import dendrophis.session.session as session_module
from dendrophis.config.loader import ConfigLoader
from dendrophis.session.session import Session

CONFIG_ENV_VARS = ("DENDROPHIS_API_KEY", "DENDROPHIS_BASE_URL", "DENDROPHIS_MODEL", "DENDROPHIS_CONFIG")


def _write_config(config_file: Path, model: str) -> None:
    config_file.write_text(f"llm:\n  model: {model}\n  api_key: test-key\n  base_url: http://127.0.0.1:9/v1\n")


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for env_var in CONFIG_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    path = tmp_path / "dendrophis.yaml"
    _write_config(path, "model-before")
    return path


def _make_session(config_file: Path, mcp_manager: object | None = None) -> Session:
    load_result = ConfigLoader.load(str(config_file))
    return Session(config_loader=load_result.loader, mcp_manager=mcp_manager)


async def test_reload_retires_previous_client_and_aclose_releases_it(config_file: Path) -> None:
    """Retired client stays usable until session close, then is released."""
    session = _make_session(config_file)
    original_client = session.llm

    _write_config(config_file, "model-after")
    session.reload_config()

    reloaded_client = session.llm
    assert reloaded_client is not original_client
    assert reloaded_client._config.model == "model-after"
    # Behavior preserved: the retired client is still open until aclose()
    # (the chat orchestrator and subagent handlers may still reference it).
    assert original_client._http.is_closed is False
    assert session._retired_llm_clients == [original_client]

    await session.aclose()

    assert original_client._http.is_closed is True
    assert reloaded_client._http.is_closed is True
    assert session._retired_llm_clients == []


async def test_multiple_reloads_release_every_retired_client(config_file: Path) -> None:
    """Every reload retires one client; aclose releases them all."""
    session = _make_session(config_file)
    retired_clients = []
    for model in ("model-1", "model-2", "model-3"):
        previous_client = session.llm
        _write_config(config_file, model)
        session.reload_config()
        retired_clients.append(previous_client)

    for client in retired_clients:
        assert client._http.is_closed is False

    await session.aclose()

    assert session._retired_llm_clients == []
    for client in retired_clients:
        assert client._http.is_closed is True
    assert session.llm._http.is_closed is True


async def test_reload_config_failure_keeps_current_client_active(
    config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If constructing the replacement client fails, nothing is retired."""
    session = _make_session(config_file)
    original_client = session.llm

    def _raise(_llm_config):
        raise RuntimeError("simulated client construction failure")

    monkeypatch.setattr(session_module, "LLMClient", _raise)
    _write_config(config_file, "model-after")

    with pytest.raises(RuntimeError, match="simulated client construction failure"):
        session.reload_config()

    # No partial retirement: current client unchanged and still open.
    assert session.llm is original_client
    assert session._retired_llm_clients == []
    assert original_client._http.is_closed is False

    await session.aclose()
    assert original_client._http.is_closed is True


# ---------------------------------------------------------------------------
# MCP background sync task: exception observation + deterministic drain on close
# ---------------------------------------------------------------------------
class _FakeMCPManager:
    """Minimal MCP manager stand-in recording the sync/close lifecycle."""

    def __init__(self, sync_behavior: str = "ok") -> None:
        self.sync_behavior = sync_behavior
        self.events: list[str] = []
        self.aclose_called = False
        self._hold = asyncio.Event()

    async def sync_servers(self) -> None:
        self.events.append("sync_start")
        if self.sync_behavior == "fail":
            raise RuntimeError("simulated sync failure")
        if self.sync_behavior == "hang":
            try:
                await self._hold.wait()
            except asyncio.CancelledError:
                self.events.append("sync_cancelled")
                raise
        self.events.append("sync_done")

    async def aclose(self) -> None:
        self.aclose_called = True
        self.events.append("manager_aclose")


async def test_reload_sync_failure_is_logged_not_swallowed(config_file: Path, caplog) -> None:
    """A failing background MCP sync must be observed (logged), not lost."""
    manager = _FakeMCPManager(sync_behavior="fail")
    session = _make_session(config_file, mcp_manager=manager)
    original_client = session.llm

    with caplog.at_level(logging.WARNING):
        _write_config(config_file, "model-after")
        session.reload_config()
        task = session._mcp_sync_task
        assert task is not None
        await asyncio.wait([task])

    # Failure observed: the task raised and the done callback logged it.
    assert isinstance(task.exception(), RuntimeError)
    assert any("MCP config sync failed" in record.message for record in caplog.records)
    # Contract unchanged: reload still retired the previous client.
    assert session.llm is not original_client
    assert session._retired_llm_clients == [original_client]


async def test_aclose_drains_inflight_sync_before_manager_close(config_file: Path) -> None:
    """aclose must cancel+await an in-flight sync before closing the manager."""
    manager = _FakeMCPManager(sync_behavior="hang")
    session = _make_session(config_file, mcp_manager=manager)

    _write_config(config_file, "model-after")
    session.reload_config()
    task = session._mcp_sync_task
    assert task is not None
    await asyncio.sleep(0)  # let the sync task start, then block on the hold event
    assert "sync_start" in manager.events
    assert not task.done()  # in flight (hung)

    await session.aclose()

    # The in-flight sync was cancelled and fully unwound before manager close.
    assert task.cancelled()
    assert manager.aclose_called
    assert "sync_cancelled" in manager.events
    assert manager.events.index("sync_cancelled") < manager.events.index("manager_aclose")


async def test_reload_successful_sync_not_flagged(config_file: Path, caplog) -> None:
    """A healthy sync completes normally and is not logged as a failure."""
    manager = _FakeMCPManager(sync_behavior="ok")
    session = _make_session(config_file, mcp_manager=manager)
    original_client = session.llm

    with caplog.at_level(logging.WARNING):
        _write_config(config_file, "model-after")
        session.reload_config()
        task = session._mcp_sync_task
        assert task is not None
        await asyncio.wait([task])

    assert task.done()
    assert not task.cancelled()
    assert task.exception() is None
    assert "sync_done" in manager.events
    # No spurious failure log for a healthy sync.
    assert not any("MCP config sync failed" in record.message for record in caplog.records)
    # Contract unchanged: reload still retired the previous client.
    assert session.llm is not original_client
    assert session._retired_llm_clients == [original_client]
