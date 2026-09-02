"""F11/F12/F13: previously-silent exception swallowing is now logged.

- F11: SessionPersister.save()/load() did ``except Exception: return None`` with no log,
  so a failed save (potential data loss) or load was invisible.
- F12: MemoryStore.save_memory() tag upsert/link loops did ``except sqlite3.Error:
  continue`` with a "Log but don't fail" comment but no actual log.
- F13: MemoryStore.save_memory() wrapped increment_score in
  ``contextlib.suppress(Exception)``, swallowing every failure silently.

Each test asserts the failure is now logged AND that the observable behavior is
unchanged (save still returns None, the memory is still persisted).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.memory.memory import MemoryStore
from dendrophis.session.persister import SessionPersister
from dendrophis.session.session import SessionStats

# --- F11: SessionPersister ----------------------------------------------------


def _persister(tmp_path: Path, log_lines: list[str]) -> SessionPersister:
    config = DendrophisConfig()
    config.llm.model = "test-model"
    context = ContextManager(config)
    context.append_user("hello")
    context.append_assistant("hi there")
    persister = SessionPersister(
        context=context,
        stats=SessionStats(),
        config=config,
        debug_logger=log_lines.append,
    )
    # Redirect the (unconditionally created) sessions dir to tmp so we never touch home.
    persister.DEFAULT_SESSIONS_DIR = tmp_path / "sessions"
    return persister


def test_save_failure_logs(tmp_path: Path) -> None:
    log_lines: list[str] = []
    persister = _persister(tmp_path, log_lines)
    # Point at a path whose parent dir doesn't exist -> lzma.open raises.
    bad_path = tmp_path / "no" / "such" / "dir" / "session.json.xz"

    result = persister.save("abc12345", session_file=bad_path)

    assert result is None
    assert any("save_session failed" in line for line in log_lines)


def test_load_failure_logs(tmp_path: Path) -> None:
    log_lines: list[str] = []
    persister = _persister(tmp_path, log_lines)
    bad_file = tmp_path / "corrupt.json"
    bad_file.write_text("{ this is not valid json")

    result = persister.load(str(bad_file))

    assert result == (None, "", None)
    assert any("load_session failed" in line for line in log_lines)


# --- F12 / F13: MemoryStore ---------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(str(tmp_path / "test.db"), nlp=None)


def test_tag_failure_logs_and_still_saves(store: MemoryStore, caplog: pytest.LogCaptureFixture) -> None:
    # Drop the tag tables so per-tag upserts/links raise sqlite3.OperationalError.
    conn = sqlite3.connect(str(store._db_path))
    try:
        conn.execute("DROP TABLE IF EXISTS tag_memories")
        conn.execute("DROP TABLE IF EXISTS tags")
        conn.commit()
    finally:
        conn.close()

    with caplog.at_level(logging.WARNING, logger="dendrophis.memory.memory"):
        entry = store.save_memory("a fact to remember", tags=["alpha", "beta"])

    # The memory itself is still saved.
    assert entry is not None
    fetched = store.get_memory(entry.id)
    assert fetched is not None
    assert fetched.content == "a fact to remember"
    # And each failing tag operation was logged (not silently skipped).
    messages = [record.getMessage() for record in caplog.records]
    assert any("Failed to upsert tag" in message for message in messages)
    assert any("Failed to link tag" in message for message in messages)


def test_score_failure_logs_and_still_saves(
    store: MemoryStore, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(memory_id: str, amount: float = 1.0) -> None:
        raise sqlite3.OperationalError("simulated score update failure")

    monkeypatch.setattr(MemoryStore, "increment_score", boom)

    with caplog.at_level(logging.WARNING, logger="dendrophis.memory.memory"):
        entry = store.save_memory("remember score failure", tags=["x"])

    assert entry is not None
    fetched = store.get_memory(entry.id)
    assert fetched is not None
    assert fetched.content == "remember score failure"
    messages = [record.getMessage() for record in caplog.records]
    assert any("Failed to increment score" in message for message in messages)
