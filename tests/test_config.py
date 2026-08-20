"""Tests for config loading and error handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from dendrophis.config.loader import ConfigLoader


def test_load_explicit_missing_config_raises_file_not_found(tmp_path: Path) -> None:
    nonexistent_path = str(tmp_path / "does_not_exist.yaml")
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        ConfigLoader.load(config_path=nonexistent_path)


def test_load_explicit_env_missing_config_raises_file_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent_path = str(tmp_path / "does_not_exist.yaml")
    monkeypatch.setenv("DENDROPHIS_CONFIG", nonexistent_path)
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        ConfigLoader.load()


def test_load_valid_config(tmp_path: Path) -> None:
    config_file = tmp_path / "custom_config.yaml"
    config_file.write_text(
        "llm:\n"
        "  provider: openai\n"
        "  model: gpt-4o\n"
        "  api_key: test-key\n"
    )
    result = ConfigLoader.load(config_path=str(config_file))
    assert result.config.llm.model == "gpt-4o"
    assert result.config.llm.api_key == "test-key"
