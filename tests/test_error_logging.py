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
import sys
from pathlib import Path

import pytest

from dendrophis.cli import _list_forks, _list_sessions, _resolve_session
from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.context import tokenizer as tokenizer_module
from dendrophis.context.manager import ContextManager
from dendrophis.llm.client import LLMClient
from dendrophis.memory import project as project_module
from dendrophis.memory.embedder import OpenAIEmbedder
from dendrophis.memory.memory import MemoryStore
from dendrophis.session.persister import SessionPersister
from dendrophis.session.session import SessionStats
from dendrophis.subagents.handlers.planner import PlannerHandler
from dendrophis.subagents.handlers.researcher import ResearcherHandler

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


# --- B: remaining silent swallowing -------------------------------------------
#
# Same class of bug as F11-F13: a failure path drops the exception with no
# visibility. Each test asserts the failure is now logged AND that observable
# behavior is unchanged (fallback still returned, file still skipped, etc.).


def _make_sessions_dir(tmp_path: Path) -> Path:
    sessions_dir = tmp_path / ".config" / "dendrophis" / "sessions"
    sessions_dir.mkdir(parents=True)
    return sessions_dir


def _patch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


class TestCLISessionListing:
    def test_list_sessions_warns_on_unreadable_file(self, tmp_path, capsys, monkeypatch) -> None:
        sessions_dir = _make_sessions_dir(tmp_path)
        (sessions_dir / "session-good1234.json").write_text(
            '{"session_id": "good1234", "fork_name": "", "timestamp": "2026-01-01T00:00:00", '
            '"model": "m", "messages": []}'
        )
        (sessions_dir / "session-bad9999.json").write_text("{ not valid json")
        _patch_home(tmp_path, monkeypatch)

        _list_sessions()

        out = capsys.readouterr()
        assert "good1234" in out.out  # valid session still listed
        assert "session-bad9999.json" in out.err  # corrupt file named in a warning

    def test_list_forks_warns_on_unreadable_file(self, tmp_path, capsys, monkeypatch) -> None:
        sessions_dir = _make_sessions_dir(tmp_path)
        (sessions_dir / "session-good1234.json").write_text(
            '{"session_id": "good1234", "fork_name": "myfork", "timestamp": "2026-01-01T00:00:00", '
            '"model": "m", "messages": []}'
        )
        (sessions_dir / "session-bad9999.json").write_text("{ not valid json")
        _patch_home(tmp_path, monkeypatch)

        _list_forks()

        out = capsys.readouterr()
        assert "myfork" in out.out
        assert "session-bad9999.json" in out.err

    def test_resolve_session_warns_on_unreadable_file(self, tmp_path, capsys, monkeypatch) -> None:
        sessions_dir = _make_sessions_dir(tmp_path)
        good = sessions_dir / "session-bbb22222.json"
        good.write_text(
            '{"session_id": "bbb22222", "fork_name": "target-fork", "timestamp": "2026-01-01T00:00:00", '
            '"model": "m", "messages": []}'
        )
        bad = sessions_dir / "session-aaa11111.json"
        bad.write_text("{ not valid json")
        _patch_home(tmp_path, monkeypatch)

        resolved = _resolve_session("target-fork")

        out = capsys.readouterr()
        assert resolved == str(good)  # search continues past the corrupt file
        assert "session-aaa11111.json" in out.err


class _FailingGetClient:
    """Stand-in httpx client whose get() always fails, forcing the fetch_models fallback."""

    async def get(self, *args, **kwargs):
        raise RuntimeError("network down")

    async def aclose(self) -> None:
        return None


async def test_fetch_models_fallback_logs(caplog: pytest.LogCaptureFixture) -> None:
    config = LLMConfig(base_url="http://127.0.0.1:9/v1", api_key="k", model="gpt-4o")
    client = LLMClient(config=config, http_client=_FailingGetClient())

    with caplog.at_level(logging.DEBUG, logger="dendrophis.llm.client"):
        models = await client.fetch_models()

    assert models  # built-in fallback list still returned
    messages = [record.getMessage() for record in caplog.records]
    assert any("fetch models" in message.lower() for message in messages)


class _FailingEmbeddingsAPI:
    def create(self, **kwargs):
        raise RuntimeError("embedding api down")


class _FailingOpenAIClient:
    embeddings = _FailingEmbeddingsAPI()


def test_embedder_failure_logs(caplog: pytest.LogCaptureFixture) -> None:
    embedder = OpenAIEmbedder(client=_FailingOpenAIClient(), model="text-embedding-3-small")

    with caplog.at_level(logging.DEBUG, logger="dendrophis.memory.embedder"):
        result = embedder.embed("hello world")

    assert result is None  # behavior unchanged
    messages = [record.getMessage() for record in caplog.records]
    assert any("embedding" in message.lower() for message in messages)


def test_tokenizer_tiktoken_failure_logs_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(tokenizer_module, "_enc", None)
    monkeypatch.setattr(tokenizer_module, "_enc_failed", False, raising=False)
    monkeypatch.setitem(sys.modules, "tiktoken", None)  # force `import tiktoken` to raise

    with caplog.at_level(logging.DEBUG, logger="dendrophis.context.tokenizer"):
        first = tokenizer_module.count_tokens("hello world foo bar")
        second = tokenizer_module.count_tokens("hello world foo bar")

    # Heuristic fallback still works for both calls
    assert first > 0
    assert second > 0
    messages = [record.getMessage() for record in caplog.records]
    tiktoken_logs = [message for message in messages if "tiktoken" in message.lower()]
    assert len(tiktoken_logs) == 1  # logged once, not per call


def _config_loader_boom(*args, **kwargs):
    raise RuntimeError("no config available")


def test_researcher_llm_fallback_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import dendrophis.config.loader as loader_module

    monkeypatch.setattr(loader_module.ConfigLoader, "load", classmethod(_config_loader_boom))
    handler = ResearcherHandler()

    with caplog.at_level(logging.DEBUG, logger="dendrophis.subagents.handlers.researcher"):
        assert handler.llm is None

    messages = [record.getMessage() for record in caplog.records]
    assert any("llm client" in message.lower() for message in messages)


def test_planner_llm_fallback_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import dendrophis.config.loader as loader_module

    monkeypatch.setattr(loader_module.ConfigLoader, "load", classmethod(_config_loader_boom))
    handler = PlannerHandler()

    with caplog.at_level(logging.DEBUG, logger="dendrophis.subagents.handlers.planner"):
        assert handler.llm is None

    messages = [record.getMessage() for record in caplog.records]
    assert any("llm client" in message.lower() for message in messages)


def test_load_primer_corrupt_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(project_module, "_PRIMER_DIR", tmp_path)
    project_module._primer_path("projX").write_text("{ corrupt")

    with caplog.at_level(logging.DEBUG, logger="dendrophis.memory.project"):
        assert project_module.load_primer("projX") is None

    messages = [record.getMessage() for record in caplog.records]
    assert any("projX" in message for message in messages)


def test_list_primers_skips_corrupt_with_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(project_module, "_PRIMER_DIR", tmp_path)
    (tmp_path / "good.primer.json").write_text('{"project_id": "good", "project_name": "Good", "updated_at": ""}')
    (tmp_path / "bad.primer.json").write_text("{ corrupt")

    with caplog.at_level(logging.DEBUG, logger="dendrophis.memory.project"):
        results = project_module.list_primers()

    assert [entry[0] for entry in results] == ["good"]  # valid primer still listed
    messages = [record.getMessage() for record in caplog.records]
    assert any("bad.primer.json" in message for message in messages)


def test_file_log_exception_uses_logging(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dendrophis.session import chat as chat_module

    unwritable_path = tmp_path / "read_only" / "test.log"

    def raising_mkdir(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "mkdir", raising_mkdir)

    with caplog.at_level(logging.WARNING, logger="dendrophis.session.chat"):
        chat_module._file_log("test message", unwritable_path)

    captured_output = capsys.readouterr()
    assert captured_output.err == ""
    assert captured_output.out == ""
    assert any("Failed to write debug log" in record.getMessage() for record in caplog.records)


def test_tool_log_exception_uses_logging(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dendrophis.session import chat as chat_module

    monkeypatch.setenv("DENDROPHIS_TOOL_LOG", "1")

    def raising_open(*args, **kwargs):
        raise OSError("Disk failure")

    monkeypatch.setattr("builtins.open", raising_open)

    with caplog.at_level(logging.WARNING, logger="dendrophis.session.chat"):
        chat_module._tool_log("test message", session_id="test_session")

    captured_output = capsys.readouterr()
    assert captured_output.err == ""
    assert captured_output.out == ""
    assert any("Failed to write tool log" in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
async def test_tool_executor_backup_failure_uses_logging(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    from dendrophis.llm.client import ToolCall
    from dendrophis.tools.executor import ToolExecutor
    from dendrophis.tools.registry import ToolRegistry

    registry = ToolRegistry()
    executor = ToolExecutor(registry)

    target_file = tmp_path / "test_edit.txt"
    target_file.write_text("initial content", encoding="utf-8")

    def raising_copy(*args, **kwargs):
        raise OSError("Cannot backup")

    monkeypatch.setattr(shutil, "copy2", raising_copy)

    tool_call = ToolCall(
        index=0,
        id="call_test_123",
        name="edit_file",
        arguments=f'{{"file_path": "{target_file}"}}',
    )

    with caplog.at_level(logging.WARNING, logger="dendrophis.tools.executor"):
        await executor.execute(tool_call)

    captured_output = capsys.readouterr()
    assert captured_output.err == ""
    assert captured_output.out == ""
    assert any("Failed to create backup" in record.getMessage() for record in caplog.records)
