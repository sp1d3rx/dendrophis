"""Tests for session forking functionality."""

from __future__ import annotations

import json
import lzma
from pathlib import Path

import pytest

from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.session.persister import SessionPersister
from dendrophis.session.session import SessionStats


@pytest.fixture
def mock_config() -> DendrophisConfig:
    config = DendrophisConfig()
    config.llm.model = "test-model"
    return config


@pytest.fixture
def mock_context(mock_config: DendrophisConfig) -> ContextManager:
    context_manager = ContextManager(mock_config)
    context_manager.append_user("Hello from user")
    context_manager.append_assistant("Hello from assistant")
    return context_manager


def test_session_persister_fork_save_and_load(
    tmp_path: Path,
    mock_config: DendrophisConfig,
    mock_context: ContextManager,
) -> None:
    session_stats = SessionStats()
    persister = SessionPersister(context=mock_context, stats=session_stats, config=mock_config)
    persister.DEFAULT_SESSIONS_DIR = tmp_path

    original_session_id = "original-session-12345"
    saved_file_path = persister.save(
        session_id=original_session_id,
        fork_name="primed-base",
    )
    assert saved_file_path is not None
    assert saved_file_path.exists()

    # Inspect saved JSON content
    with lzma.open(saved_file_path, "rb") as file_handle:
        data = json.loads(file_handle.read().decode())
    assert data["session_id"] == original_session_id
    assert data["fork_name"] == "primed-base"
    assert len(data["messages"]) == 3

    # Load as fork
    new_context = ContextManager(mock_config)
    new_stats = SessionStats()
    fork_persister = SessionPersister(context=new_context, stats=new_stats, config=mock_config)
    info, loaded_session_id, loaded_session_file = fork_persister.load(
        str(saved_file_path),
        as_fork=True,
    )

    assert info is not None
    assert info["is_fork"] is True
    assert info["fork_name"] == "primed-base"
    assert info["parent_session_id"] == original_session_id
    assert loaded_session_id != original_session_id
    assert loaded_session_file is None
    assert len(new_context.messages) == 3


def test_session_fork_method(
    tmp_path: Path,
    mock_config: DendrophisConfig,
    mock_context: ContextManager,
) -> None:
    from unittest.mock import MagicMock

    from dendrophis.config.loader import ConfigLoader, ConfigLoadResult

    session_stats = SessionStats()
    persister = SessionPersister(context=mock_context, stats=session_stats, config=mock_config)
    persister.DEFAULT_SESSIONS_DIR = tmp_path

    mock_loader = MagicMock(spec=ConfigLoader)
    mock_loader.config = mock_config
    loader_result = ConfigLoadResult(loader=mock_loader, system_prompt_source="default")

    from dendrophis.session.session import Session

    session = Session(
        config_loader=loader_result,
        context=mock_context,
        stats=session_stats,
        persister=persister,
    )
    session._persister = persister

    initial_session_id = session.session_id

    # Fork session with a custom name
    new_session_id = session.fork(name="react-prime")

    assert new_session_id != initial_session_id
    assert session.session_id == new_session_id
    assert session.parent_session_id == initial_session_id
    assert session.fork_name == "react-prime"

    # Check saved files in tmp_path
    session_files = list(tmp_path.glob("session-*.json*"))
    assert len(session_files) >= 1
