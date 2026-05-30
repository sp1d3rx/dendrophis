"""Config loading with ruamel.yaml round-trip support."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from dendrophis.config.defaults import DEFAULT_CONFIG_YAML
from dendrophis.config.schema import DendrophisConfig

_yaml = YAML()
_yaml.preserve_quotes = True


def _config_search_paths() -> list[Path]:
    """Return candidate config file paths in priority order."""
    env = os.environ.get("DENDROPHIS_CONFIG")
    if env:
        return [Path(env)]
    return [
        Path("dendrophis.yaml"),
        Path.home() / ".config" / "dendrophis" / "config.yaml",
    ]


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Overlay DENDROPHIS_* environment variables onto the raw config dict."""
    llm = raw.setdefault("llm", {})
    for env_key, cfg_key in (
        ("DENDROPHIS_API_KEY", "api_key"),
        ("DENDROPHIS_BASE_URL", "base_url"),
        ("DENDROPHIS_MODEL", "model"),
    ):
        value = os.environ.get(env_key)
        if value:
            llm[cfg_key] = value
    return raw


class ConfigLoader:
    """Loads, validates, and saves DendrophisConfig from YAML."""

    def __init__(self, path: Path, raw: dict[str, Any], config: DendrophisConfig) -> None:
        self._path = path
        self._raw = raw
        self.config = config

    @classmethod
    def load(cls, config_path: str | None = None) -> ConfigLoader:
        paths = [Path(config_path)] if config_path else _config_search_paths()

        path: Path | None = None
        for candidate in paths:
            if candidate.exists():
                path = candidate
                break

        if path is None:
            path = Path.home() / ".config" / "dendrophis" / "config.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_CONFIG_YAML)

        raw = _yaml.load(path.read_text()) or {}
        raw = _apply_env_overrides(raw)
        config = DendrophisConfig.model_validate(raw)
        return cls(path=path, raw=raw, config=config)

    def save(self, new_yaml_text: str | None = None) -> None:
        """Persist current config to disk, optionally replacing from raw YAML text."""
        if new_yaml_text is not None:
            new_raw = _yaml.load(new_yaml_text) or {}
            self._raw = new_raw
            self.config = DendrophisConfig.model_validate(dict(new_raw))
        buf = __import__("io").StringIO()
        _yaml.dump(self._raw, buf)
        self._path.write_text(buf.getvalue())

    def reload(self) -> None:
        """Re-read config file from disk and re-validate."""
        raw = _yaml.load(self._path.read_text()) or {}
        raw = _apply_env_overrides(raw)
        self._raw = raw
        self.config = DendrophisConfig.model_validate(raw)

    @property
    def raw_yaml(self) -> str:
        """Return current config serialised as a YAML string."""
        import io

        buf = io.StringIO()
        _yaml.dump(self._raw, buf)
        return buf.getvalue()

    @property
    def path(self) -> Path:
        """Return the resolved path of the config file on disk."""
        return self._path
