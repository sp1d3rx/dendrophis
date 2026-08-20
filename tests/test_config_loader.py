from pathlib import Path
from unittest.mock import patch

from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import DendrophisConfig


def test_config_loader_default_system_prompt(tmp_path: Path) -> None:
    config_file_path = tmp_path / "config.yaml"
    config_file_path.write_text("llm:\n  model: test-model\n")

    with patch("dendrophis.config.loader.Path.exists", autospec=True) as mock_exists:
        # Return False when checking for system.md
        def side_effect(path_instance: Path) -> bool:
            if path_instance.name == "system.md":
                return False
            return path_instance == config_file_path

        mock_exists.side_effect = side_effect

        load_result = ConfigLoader.load(str(config_file_path))
        assert load_result.system_prompt_source == "default"
        default_prompt = DendrophisConfig().system_prompt
        assert load_result.config.system_prompt == default_prompt


def test_config_loader_system_md_override(tmp_path: Path) -> None:
    config_file_path = tmp_path / "config.yaml"
    config_file_path.write_text("llm:\n  model: test-model\n")
    system_md_file_path = tmp_path / "system.md"
    system_md_content = "Custom prompt from system.md"
    system_md_file_path.write_text(system_md_content)

    # Direct integration test with temporary working directory
    import os
    previous_directory = os.getcwd()
    try:
        os.chdir(tmp_path)
        load_result = ConfigLoader.load(str(config_file_path))
        assert load_result.system_prompt_source == "system.md"
        assert load_result.config.system_prompt == system_md_content
    finally:
        os.chdir(previous_directory)
