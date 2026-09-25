"""Tests for the Dendrophis Web Observability interface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from dendrophis.cli import build_parser
from dendrophis.events.bus import EventBus
from dendrophis.events.types import (
    MemorySavedEvent,
    ReasoningDeltaEvent,
    StreamingStartedEvent,
    ToolCallStartEvent,
    TrackFileRequest,
)
from dendrophis.web.bridge import EventBridge
from dendrophis.web.server import create_app


def test_cli_web_parser_flags() -> None:
    """Test that --web and --web-port flags are properly parsed by CLI parser."""
    parser = build_parser()
    args = parser.parse_args(["--web", "--web-port", "9320"])
    assert args.web is True
    assert args.web_port == 9320


def test_cli_web_default_port() -> None:
    """Test that default web port is 9320."""
    parser = build_parser()
    args = parser.parse_args(["--web"])
    assert args.web is True
    assert args.web_port == 9320


@pytest.mark.asyncio
async def test_event_bridge_broadcasting() -> None:
    """Test that EventBridge transforms EventBus events into JSON payloads."""
    bus = EventBus(max_workers=2)
    loop = asyncio.get_running_loop()
    bus.set_event_loop(loop)

    bridge = EventBridge(history_size=50)
    bridge.attach_event_bus(bus)

    # Mock WebSocket client
    mock_ws = AsyncMock()
    initial = bridge.register_client(mock_ws)

    # Initial history snapshot should have SUBAGENT_STATE and FILESYSTEM_CHANGE
    assert len(initial) >= 2
    assert initial[0]["type"] == "SUBAGENT_STATE"
    assert initial[1]["type"] == "FILESYSTEM_CHANGE"

    # Emit streaming start event
    bus.publish(StreamingStartedEvent(user_message="Hello Dendrophis"))
    await asyncio.sleep(0.05)

    # Emit reasoning delta
    bus.publish(ReasoningDeltaEvent(delta="Analyzing problem structure..."))
    await asyncio.sleep(0.05)

    # Emit tool call event
    bus.publish(ToolCallStartEvent(index=0, id="call_12345", name="view_file"))
    await asyncio.sleep(0.05)

    # Emit memory saved event
    bus.publish(
        MemorySavedEvent(
            memory_id="mem_01",
            content="User prefers Python 3.11",
            tags=["preference"],
            source="session",
        )
    )
    await asyncio.sleep(0.05)

    # Emit track file request
    bus.publish(TrackFileRequest(path="dendrophis/cli.py"))
    await asyncio.sleep(0.05)

    # Verify history in bridge recorded events
    types = [item["type"] for item in bridge._history]
    assert "THOUGHT_LOG" in types
    assert "SUBAGENT_STATE" in types
    assert "MEMORY_RETRIEVAL" in types
    assert "FILESYSTEM_CHANGE" in types


def test_fastapi_app_creation() -> None:
    """Test FastAPI application creation and endpoint routes."""
    bridge = EventBridge()
    app = create_app(bridge)
    assert app is not None
    assert app.title == "Dendrophis Web Observability Interface"


def test_is_allowed_origin() -> None:
    """Test origin validation for CSWSH protection."""
    from dendrophis.web.server import _is_allowed_origin

    # Allowed: loopback origins
    assert _is_allowed_origin(None) is True
    assert _is_allowed_origin("http://localhost:9320") is True
    assert _is_allowed_origin("http://127.0.0.1:9320") is True
    assert _is_allowed_origin("http://[::1]:9320") is True

    # Forbidden: foreign origins
    assert _is_allowed_origin("https://evil.example.com") is False
    assert _is_allowed_origin("http://attacker.local") is False

    # Allowed: explicit allowlist
    assert (
        _is_allowed_origin(
            "https://trusted.internal",
            allowed_origins={"https://trusted.internal"},
        )
        is True
    )


def test_ensure_secure_dir_and_file(tmp_path: Path) -> None:
    """Test directory (0700) and file (0600) permission hardening."""
    import stat

    from dendrophis.utils import ensure_secure_dir, ensure_secure_file

    secure_directory = tmp_path / "nested_directory"
    ensure_secure_dir(secure_directory)
    assert secure_directory.is_dir()
    directory_mode = stat.S_IMODE(secure_directory.stat().st_mode)
    assert directory_mode == 0o700

    secure_file = secure_directory / "sensitive.txt"
    secure_file.write_text("classified")
    ensure_secure_file(secure_file)
    assert secure_file.is_file()
    file_mode = stat.S_IMODE(secure_file.stat().st_mode)
    assert file_mode == 0o600
