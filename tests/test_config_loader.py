from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import DendrophisConfig


def test_config_loader_default_system_prompt(tmp_path: Path) -> None:
    config_file_path = tmp_path / "config.yaml"
    config_file_path.write_text("llm:\n  model: test-model\n", encoding="utf-8")

    with patch("dendrophis.config.loader.Path.exists", autospec=True) as mock_exists:

        def side_effect(path_instance: Path) -> bool:
            if path_instance.name == "system.md":
                return False
            return path_instance == config_file_path

        mock_exists.side_effect = side_effect

        load_result = ConfigLoader.load(str(config_file_path))
        assert load_result.system_prompt_source == "default"
        default_prompt = DendrophisConfig().system_prompt
        assert load_result.config.system_prompt == default_prompt


def test_config_loader_system_md_override_not_persisted_on_save(tmp_path: Path) -> None:
    config_file_path = tmp_path / "config.yaml"
    config_file_path.write_text("llm:\n  model: test-model\n", encoding="utf-8")
    system_md_file_path = tmp_path / "system.md"
    system_md_content = "Custom prompt from system.md"
    system_md_file_path.write_text(system_md_content, encoding="utf-8")

    previous_directory = os.getcwd()
    try:
        os.chdir(tmp_path)
        load_result = ConfigLoader.load(str(config_file_path))
        assert load_result.system_prompt_source == "system.md"
        assert load_result.config.system_prompt == system_md_content

        load_result.loader.save()
        saved_yaml = config_file_path.read_text(encoding="utf-8")
        assert "Custom prompt from system.md" not in saved_yaml
    finally:
        os.chdir(previous_directory)


def test_env_api_key_override_not_leaked_on_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file_path = tmp_path / "config.yaml"
    config_file_path.write_text('llm:\n  model: test-model\n  api_key: ""\n', encoding="utf-8")
    monkeypatch.setenv("DENDROPHIS_API_KEY", "sk-secret-env-token")

    load_result = ConfigLoader.load(str(config_file_path))
    assert load_result.config.llm.api_key == "sk-secret-env-token"

    load_result.loader._raw["llm"]["temperature"] = 0.5
    load_result.loader.save()

    saved_yaml = config_file_path.read_text(encoding="utf-8")
    assert "sk-secret-env-token" not in saved_yaml
    assert load_result.config.llm.api_key == "sk-secret-env-token"


def test_null_non_optional_fields_preserve_defaults() -> None:
    parsed = DendrophisConfig.from_dict(
        {
            "permissions": {"allowed_tools": None, "denied_tools": None},
            "mcp_servers": None,
            "hooks": {"pre_tool_use": []},
            "tools": {"extra_paths": ["/legacy/ignored"], "max_consecutive_failures": None},
        }
    )
    assert parsed.permissions.allowed_tools == []
    assert parsed.permissions.denied_tools == []
    assert parsed.mcp_servers == {}
    assert parsed.tools.max_consecutive_failures == 3
