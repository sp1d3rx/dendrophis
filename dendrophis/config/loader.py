"""Config loading with ruamel.yaml round-trip support."""

from __future__ import annotations

import copy
import io
import os
from pathlib import Path
from typing import Any, NamedTuple

from ruamel.yaml import YAML

from dendrophis.config.defaults import DEFAULT_CONFIG_YAML
from dendrophis.config.schema import DendrophisConfig
from dendrophis.utils import ensure_secure_dir, ensure_secure_file


def _make_yaml() -> YAML:
    yaml_parser = YAML()
    yaml_parser.preserve_quotes = True
    return yaml_parser


class ConfigLoadResult(NamedTuple):
    """Result of config loading with system prompt source info."""

    loader: ConfigLoader
    system_prompt_source: str  # 'system.md' or 'default'

    def __getattr__(self, name: str) -> Any:
        return getattr(self.loader, name)


def _config_search_paths() -> list[Path]:
    """Return candidate config file paths in priority order."""
    env_config_path = os.environ.get("DENDROPHIS_CONFIG")
    if env_config_path:
        return [Path(env_config_path)]
    return [
        Path("dendrophis.yaml"),
        Path("configs/dendrophis.yaml"),
        Path.home() / ".config" / "dendrophis" / "config.yaml",
    ]


def _build_effective_dict(disk_raw: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Overlay DENDROPHIS_* env vars and local system.md onto a copy of disk_raw without mutating disk_raw."""
    effective_raw: dict[str, Any] = copy.deepcopy(dict(disk_raw))
    llm_section = dict(effective_raw.get("llm") or {})
    for env_key, cfg_key in (
        ("DENDROPHIS_API_KEY", "api_key"),
        ("DENDROPHIS_BASE_URL", "base_url"),
        ("DENDROPHIS_MODEL", "model"),
    ):
        env_value = os.environ.get(env_key)
        if env_value:
            llm_section[cfg_key] = env_value
    effective_raw["llm"] = llm_section

    system_prompt_source = "default"
    system_md_path = Path("system.md")
    if system_md_path.exists():
        system_prompt_source = "system.md"
        effective_raw["system_prompt"] = system_md_path.read_text(encoding="utf-8")

    return effective_raw, system_prompt_source


class ConfigLoader:
    """Loads, validates, and saves DendrophisConfig from YAML."""

    def __init__(
        self,
        path: Path,
        raw: dict[str, Any],
        config: DendrophisConfig,
        system_prompt_source: str = "default",
    ) -> None:
        self._path = path
        self._raw = raw
        self.config = config
        self.system_prompt_source = system_prompt_source

    @classmethod
    def load(cls, config_path: str | None = None) -> ConfigLoadResult:
        is_explicit_config = config_path is not None or "DENDROPHIS_CONFIG" in os.environ
        candidate_paths = [Path(config_path)] if config_path else _config_search_paths()

        resolved_path: Path | None = None
        for candidate_path in candidate_paths:
            if candidate_path.exists():
                resolved_path = candidate_path
                break

        if resolved_path is None:
            if is_explicit_config:
                explicit_config_name = config_path or os.environ.get("DENDROPHIS_CONFIG")
                raise FileNotFoundError(f"Config file not found: {explicit_config_name}")
            resolved_path = Path.home() / ".config" / "dendrophis" / "config.yaml"
            ensure_secure_dir(resolved_path.parent)
            resolved_path.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
            ensure_secure_file(resolved_path)

        yaml_parser = _make_yaml()
        disk_raw = yaml_parser.load(resolved_path.read_text(encoding="utf-8")) or {}
        effective_raw, system_prompt_source = _build_effective_dict(disk_raw)
        config = DendrophisConfig.from_dict(effective_raw)
        loader = cls(
            path=resolved_path,
            raw=disk_raw,
            config=config,
            system_prompt_source=system_prompt_source,
        )
        return ConfigLoadResult(loader=loader, system_prompt_source=system_prompt_source)

    def save(self, new_yaml_text: str | None = None) -> None:
        """Persist current config to disk, optionally replacing from raw YAML text."""
        yaml_parser = _make_yaml()
        if new_yaml_text is not None:
            parsed_raw = yaml_parser.load(new_yaml_text) or {}
            self._raw = parsed_raw

        buffer = io.StringIO()
        yaml_parser.dump(self._raw, buffer)
        self._path.write_text(buffer.getvalue(), encoding="utf-8")
        ensure_secure_file(self._path)

        effective_raw, self.system_prompt_source = _build_effective_dict(self._raw)
        self.config = DendrophisConfig.from_dict(effective_raw)

    def reload(self) -> None:
        """Re-read config file from disk and re-validate."""
        yaml_parser = _make_yaml()
        disk_raw = yaml_parser.load(self._path.read_text(encoding="utf-8")) or {}
        self._raw = disk_raw
        effective_raw, self.system_prompt_source = _build_effective_dict(disk_raw)
        self.config = DendrophisConfig.from_dict(effective_raw)

    @property
    def raw_yaml(self) -> str:
        """Return current config serialised as a YAML string."""
        yaml_parser = _make_yaml()
        buffer = io.StringIO()
        yaml_parser.dump(self._raw, buffer)
        return buffer.getvalue()

    @property
    def path(self) -> Path:
        """Return the resolved path of the config file on disk."""
        return self._path
