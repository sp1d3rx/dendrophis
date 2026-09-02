"""F2/F7/F8: EventBridge send lifecycle and WebObservabilityServer task lifecycle.

F7: broadcast() schedules client.send_text() as fire-and-forget tasks. A send to a
closed websocket raises *inside* the task, so the exception must be retrieved (else
asyncio logs 'Task exception was never retrieved') and the dead client retired (else
every later broadcast keeps retrying it).

F2: each send is bounded by _SEND_TIMEOUT_S. A blackholed peer (a connection that never
RSTs) would otherwise leave send_text() awaiting forever, so its done callback never
fires and the client is never retired -> one stuck send task per broadcast (unbounded).

F8: start_background() must not lose a start-failure exception (e.g. port in use), and
stop() must actually await the shutdown (bounded) rather than fire-and-forget cancel().
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

from dendrophis.web.bridge import EventBridge
from dendrophis.web.server import WebObservabilityServer


class _FakeWS:
    """Minimal websocket stand-in: send_text is async, like FastAPI's."""

    def __init__(self, fail: bool = False, hang: bool = False) -> None:
        self._fail = fail
        self._hang = hang
        self.sent: list[str] = []
        self.send_started = False
        self.cancelled = False

    async def send_text(self, message: str) -> None:
        self.send_started = True
        if self._fail:
            raise RuntimeError("connection closed")
        if self._hang:
            try:
                await asyncio.Event().wait()  # blackhole: never completes on its own
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        self.sent.append(message)


async def _drain(n: int = 5) -> None:
    """Yield to the loop so scheduled (fire-and-forget) tasks get to run."""
    for _ in range(n):
        await asyncio.sleep(0)


def _bridge_with(*clients: _FakeWS) -> EventBridge:
    bridge = EventBridge()
    for client in clients:
        bridge.register_client(client)
    return bridge


# --- F7: broadcast send cleanup ------------------------------------------------


async def test_broadcast_delivers_to_live_client() -> None:
    good = _FakeWS()
    bridge = _bridge_with(good)

    bridge.broadcast("THOUGHT_LOG", {"text": "hi"})
    await _drain()

    assert good in bridge._clients
    assert good.sent
    assert "THOUGHT_LOG" in good.sent[0]
    assert bridge._send_tasks == set(), "no dangling send tasks should remain"


async def test_broadcast_drops_failing_client() -> None:
    good = _FakeWS()
    bad = _FakeWS(fail=True)
    bridge = _bridge_with(good, bad)

    bridge.broadcast("THOUGHT_LOG", {"text": "hi"})
    await _drain()

    assert bad not in bridge._clients, "client whose send failed must be retired"
    assert good in bridge._clients
    assert good.sent
    assert "THOUGHT_LOG" in good.sent[0]
    assert bridge._send_tasks == set()


async def test_broadcast_only_failing_clients_clears_all() -> None:
    bad1 = _FakeWS(fail=True)
    bad2 = _FakeWS(fail=True)
    bridge = _bridge_with(bad1, bad2)

    bridge.broadcast("THOUGHT_LOG", {"text": "hi"})
    await _drain()

    assert bridge._clients == set()
    assert bridge._send_tasks == set()


async def test_broadcast_drops_blackholed_client_after_timeout(monkeypatch) -> None:
    """F2: a blackholed peer must not accumulate stuck send tasks forever."""
    import dendrophis.web.bridge as br

    # Shrink the send timeout so the test runs in milliseconds, not ten seconds.
    monkeypatch.setattr(br, "_SEND_TIMEOUT_S", 0.05, raising=False)
    good = _FakeWS()
    blackhole = _FakeWS(hang=True)
    bridge = _bridge_with(good, blackhole)

    bridge.broadcast("THOUGHT_LOG", {"text": "hi"})
    await _drain()

    # Within the timeout the blackholed client is still present; its send is pending.
    assert blackhole.send_started
    assert blackhole in bridge._clients

    # Past the timeout the stuck send is abandoned and the client retired.
    await asyncio.sleep(0.2)
    assert blackhole not in bridge._clients, "blackholed client must be dropped after the send timeout"
    assert blackhole.cancelled, "the stuck send must be cancelled, not left awaiting forever"
    assert good in bridge._clients
    assert good.sent
    assert bridge._send_tasks == set(), "no stuck send tasks should remain after the timeout"


# --- F8: server task lifecycle -------------------------------------------------


async def test_stop_no_task_is_noop() -> None:
    server = WebObservabilityServer(bridge=MagicMock())
    await server.stop()  # must not raise
    assert server._task is None


async def test_stop_waits_for_graceful_shutdown() -> None:
    server = WebObservabilityServer(bridge=MagicMock())

    class _FakeUvicorn:
        def __init__(self) -> None:
            self.should_exit = False

    server._server = _FakeUvicorn()

    async def running_start() -> None:
        # Mimic uvicorn: keep running until the graceful shutdown flag is observed.
        while not server._server.should_exit:
            await asyncio.sleep(0.01)

    server.start = running_start
    task = server.start_background()
    await asyncio.sleep(0.05)
    assert not task.done()

    await server.stop()

    assert task.done()
    assert not task.cancelled(), "graceful path should complete, not force-cancel"
    assert server._server.should_exit is True
    assert server._task is None


async def test_stop_force_cancels_stalled_server(monkeypatch) -> None:
    import dendrophis.web.server as srv

    monkeypatch.setattr(srv, "_SHUTDOWN_TIMEOUT_S", 0.05)
    server = WebObservabilityServer(bridge=MagicMock())

    async def stubborn_start() -> None:
        await asyncio.Event().wait()  # never exits on its own

    server.start = stubborn_start
    task = server.start_background()
    await asyncio.sleep(0.01)
    assert not task.done()

    await server.stop()

    assert task.cancelled(), "stalled server must be force-cancelled after the timeout"
    assert server._task is None


async def test_start_failure_is_logged_not_lost(caplog) -> None:
    server = WebObservabilityServer(bridge=MagicMock())

    async def failing_start() -> None:
        raise RuntimeError("address already in use")

    server.start = failing_start

    with caplog.at_level(logging.WARNING, logger="dendrophis.web.server"):
        server.start_background()
        await _drain()

    assert any("address already in use" in record.message for record in caplog.records)
