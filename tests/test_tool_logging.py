from pathlib import Path

import pytest

from dendrophis.session.chat import _tool_log


def test_tool_log_disabled_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DENDROPHIS_TOOL_LOG", raising=False)
    monkeypatch.chdir(tmp_path)
    test_session_identifier = "test_disabled"
    expected_log_file = tmp_path / f"tool_log_{test_session_identifier}.txt"

    _tool_log("test message", session_id=test_session_identifier)

    assert not expected_log_file.exists()


def test_tool_log_enabled_with_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DENDROPHIS_TOOL_LOG", "1")
    monkeypatch.chdir(tmp_path)
    test_session_identifier = "test_enabled"
    expected_log_file = tmp_path / f"tool_log_{test_session_identifier}.txt"

    _tool_log("test message", session_id=test_session_identifier)

    assert expected_log_file.exists()
    assert "test message" in expected_log_file.read_text(encoding="utf-8")
