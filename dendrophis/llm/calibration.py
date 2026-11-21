"""Model calibration — detect and validate model capabilities.

This module provides tools to:
- Query model metadata from providers
- Test actual parameter support via probe requests
- Cache results in ~/.config/dendrophis/model_overrides.yaml
- Auto-configure settings based on detected capabilities
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
from ruamel.yaml import YAML

from dendrophis.config.loader import ConfigLoader

logger = logging.getLogger(__name__)

OVERRIDE_FILE = Path.home() / ".config" / "dendrophis" / "model_overrides.yaml"

# Process-level mtime cache for OVERRIDE_FILE so hot paths (e.g. LLMClient._build_payload)
# do not re-parse YAML from disk multiple times per turn.
_OVERRIDE_FILE_CACHE: tuple[float | None, dict[str, ModelCapabilities]] | None = None


def _new_yaml() -> YAML:
    yaml_instance = YAML()
    yaml_instance.preserve_quotes = True
    return yaml_instance


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class ModelCapabilities:
    """Detected capabilities for a model."""

    model_id: str
    provider: str | None = None
    context_window: int = 0
    max_tokens: int | None = None

    supports_streaming: bool = True
    supports_tools: bool = True
    supports_reasoning_effort: bool = True
    supports_caching: bool = False
    supports_prompt_cache: bool = False
    supports_prompt_cache_key: bool = False
    supports_vlm: bool = False

    rejected_params: list[str] = field(default_factory=list)
    requires_params: list[str] = field(default_factory=list)

    test_results: dict[str, bool | str] = field(default_factory=dict)
    test_errors: dict[str, str] = field(default_factory=dict)

    raw_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def provider_name(self) -> str:
        """Extract provider from base_url or model_id."""
        if self.provider:
            return self.provider
        low_id = self.model_id.lower() if self.model_id else ""
        if "deepinfra" in low_id:
            return "deepinfra"
        if "openrouter" in low_id:
            return "openrouter"
        if "mistral" in low_id:
            return "mistral"
        return "unknown"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "context_window": self.context_window,
            "max_tokens": self.max_tokens,
            "supports_streaming": self.supports_streaming,
            "supports_tools": self.supports_tools,
            "supports_reasoning_effort": self.supports_reasoning_effort,
            "supports_caching": self.supports_caching,
            "supports_prompt_cache": self.supports_prompt_cache,
            "supports_prompt_cache_key": self.supports_prompt_cache_key,
            "supports_vlm": self.supports_vlm,
            "rejected_params": self.rejected_params,
            "requires_params": self.requires_params,
            "test_results": self.test_results,
            "test_errors": self.test_errors,
            "raw_metadata": self.raw_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelCapabilities:
        """Create from dictionary."""
        return cls(
            model_id=data.get("model_id", ""),
            provider=data.get("provider"),
            context_window=data.get("context_window", 0),
            max_tokens=data.get("max_tokens"),
            supports_streaming=data.get("supports_streaming", True),
            supports_tools=data.get("supports_tools", True),
            supports_reasoning_effort=data.get("supports_reasoning_effort", True),
            supports_caching=data.get("supports_caching", False),
            supports_prompt_cache=data.get("supports_prompt_cache", False),
            supports_prompt_cache_key=data.get("supports_prompt_cache_key", False),
            supports_vlm=data.get("supports_vlm", False),
            rejected_params=data.get("rejected_params", []),
            requires_params=data.get("requires_params", []),
            test_results=data.get("test_results", {}),
            test_errors=data.get("test_errors", {}),
            raw_metadata=data.get("raw_metadata", {}),
        )

    def get_recommended_config(self) -> dict[str, Any]:
        """Generate recommended config based on capabilities."""
        config: dict[str, Any] = {}

        if self.context_window > 0:
            config["context_limit"] = self.context_window

        if not self.supports_reasoning_effort:
            config["reasoning_effort"] = None

        if not self.supports_tools:
            config["enable_tools"] = False

        if self.supports_prompt_cache_key:
            config["prompt_cache_key"] = "# Set to a session-scoped key like 'dendrophis-{session_id}'"

        if self.rejected_params:
            config["_strip_params"] = self.rejected_params

        return config

    def __str__(self) -> str:
        """Human-readable summary."""
        lines = [f"Model: {self.model_id}"]
        if self.provider:
            lines.append(f"Provider: {self.provider}")
        lines.append(f"Context window: {self.context_window}")

        features = [
            ("Streaming", self.supports_streaming, "✅"),
            ("Tools", self.supports_tools, "✅"),
            ("Reasoning effort", self.supports_reasoning_effort, "✅"),
            ("Caching", self.supports_caching, "✅"),
            ("Prompt cache", self.supports_prompt_cache, "✅"),
            ("Prompt cache key", self.supports_prompt_cache_key, "✅"),
            ("VLM Vision", self.supports_vlm, "✅"),
        ]

        for name, supported, icon in features:
            status = icon if supported else "❌"
            lines.append(f"  {status} {name}: {'supported' if supported else 'NOT supported'}")

        if self.rejected_params:
            lines.append(f"  ⚠  Rejected params: {', '.join(self.rejected_params)}")

        if self.test_errors:
            lines.append("  Test errors:")
            for test_name, error_text in self.test_errors.items():
                lines.append(f"    - {test_name}: {error_text[:60]}...")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Override Storage
# ---------------------------------------------------------------------------


class ModelOverrideStore:
    """Manage cached model calibration results."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or OVERRIDE_FILE
        self._overrides: dict[str, ModelCapabilities] = {}
        self._loaded = False

    def _ensure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, ModelCapabilities]:
        """Load overrides from YAML file, using an mtime cache for OVERRIDE_FILE."""
        global _OVERRIDE_FILE_CACHE
        if self._loaded:
            return self._overrides

        is_default_path = self.path == OVERRIDE_FILE
        current_mtime: float | None = None
        try:
            current_mtime = self.path.stat().st_mtime
        except OSError:
            current_mtime = None

        if is_default_path and _OVERRIDE_FILE_CACHE is not None and _OVERRIDE_FILE_CACHE[0] == current_mtime:
            self._overrides = dict(_OVERRIDE_FILE_CACHE[1])
            self._loaded = True
            return self._overrides

        if current_mtime is None:
            self._overrides = {}
            self._loaded = True
            if is_default_path:
                _OVERRIDE_FILE_CACHE = (None, {})
            return self._overrides

        try:
            yaml_parser = _new_yaml()
            with open(self.path, encoding="utf-8") as file_handle:
                data = yaml_parser.load(file_handle) or {}
            self._overrides = {
                model_id: ModelCapabilities.from_dict(cap_data) for model_id, cap_data in data.get("models", {}).items()
            }
        except Exception as load_error:
            logger.warning("Failed to load model overrides: %s", load_error)
            self._overrides = {}

        if is_default_path:
            _OVERRIDE_FILE_CACHE = (current_mtime, dict(self._overrides))
        self._loaded = True
        return self._overrides

    def save(self) -> None:
        """Save overrides to YAML file."""
        global _OVERRIDE_FILE_CACHE
        self._ensure_dir()
        data = {"models": {model_id: cap.to_dict() for model_id, cap in self._overrides.items()}}
        try:
            yaml_parser = _new_yaml()
            buf = StringIO()
            yaml_parser.dump(data, buf)
            self.path.write_text(buf.getvalue(), encoding="utf-8")
            if self.path == OVERRIDE_FILE:
                try:
                    new_mtime = self.path.stat().st_mtime
                except OSError:
                    new_mtime = None
                _OVERRIDE_FILE_CACHE = (new_mtime, dict(self._overrides))
        except Exception as save_error:
            logger.error("Failed to save model overrides: %s", save_error)

    def get(self, model_id: str) -> ModelCapabilities | None:
        """Get capabilities for a model."""
        self.load()
        return self._overrides.get(model_id)

    def set(self, capabilities: ModelCapabilities) -> None:
        """Store capabilities for a model."""
        self.load()
        self._overrides[capabilities.model_id] = capabilities

    def remove(self, model_id: str) -> None:
        """Remove cached capabilities for a model."""
        self.load()
        self._overrides.pop(model_id, None)

    def list_models(self) -> list[str]:
        """List all cached models."""
        self.load()
        return list(self._overrides.keys())


# ---------------------------------------------------------------------------
# Capability Detection
# ---------------------------------------------------------------------------


def detect_provider(base_url: str) -> str:
    """Detect provider from base URL."""
    base_url_lower = base_url.lower()
    if "deepinfra" in base_url_lower:
        return "deepinfra"
    if "openrouter" in base_url_lower:
        return "openrouter"
    if "mistral" in base_url_lower:
        return "mistral"
    if "anthropic" in base_url_lower:
        return "anthropic"
    if "localhost" in base_url_lower or "127.0.0.1" in base_url_lower:
        return "local"
    return "unknown"


def _get_models_endpoint(base_url: str, provider: str) -> str:
    """Get the models endpoint for a provider."""
    base_url_stripped = base_url.rstrip("/")
    if provider == "deepinfra" or base_url_stripped.endswith("/v1"):
        return f"{base_url_stripped}/models"
    return f"{base_url_stripped}/v1/models"


def _get_chat_completions_endpoint(base_url: str, provider: str) -> str:
    """Get the chat completions endpoint for a provider."""
    base_url_stripped = base_url.rstrip("/")
    if provider == "deepinfra" or base_url_stripped.endswith("/v1"):
        return f"{base_url_stripped}/chat/completions"
    return f"{base_url_stripped}/v1/chat/completions"


def fetch_model_metadata(base_url: str, api_key: str, model_id: str) -> dict[str, Any] | None:
    """Synchronous helper to fetch model metadata from a provider's models endpoint."""
    provider = detect_provider(base_url)
    url = _get_models_endpoint(base_url, provider)
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
        if response.status_code != 200:
            logger.debug("Failed to fetch models from %s: %s", url, response.status_code)
            return None

        data = response.json()
        for model_info in data.get("data", []):
            if model_info.get("id") == model_id:
                return model_info

        logger.debug("Model %s not found in %s", model_id, url)
        return None
    except Exception as metadata_error:
        logger.debug("Error fetching model metadata: %s", metadata_error)
        return None


async def _fetch_model_metadata_async(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model_id: str,
) -> dict[str, Any] | None:
    """Async helper to fetch model metadata without blocking the event loop."""
    provider = detect_provider(base_url)
    url = _get_models_endpoint(base_url, provider)
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = await client.get(url, headers=headers, timeout=10.0)
        if response.status_code != 200:
            logger.debug("Failed to fetch models from %s: %s", url, response.status_code)
            return None

        data = response.json()
        for model_info in data.get("data", []):
            if model_info.get("id") == model_id:
                return model_info

        logger.debug("Model %s not found in %s", model_id, url)
        return None
    except Exception as metadata_error:
        logger.debug("Error fetching model metadata: %s", metadata_error)
        return None


def extract_capabilities_from_metadata(metadata: dict[str, Any]) -> ModelCapabilities:
    """Extract capabilities from provider metadata."""
    model_id = metadata.get("id", "")

    context_window = 0
    if "context_length" in metadata:
        context_window = int(metadata["context_length"])
    elif "max_input_tokens" in metadata:
        context_window = int(metadata["max_input_tokens"])
    else:
        nested_meta = metadata.get("metadata", {})
        if isinstance(nested_meta, dict):
            if "context_length" in nested_meta:
                context_window = int(nested_meta["context_length"])
            elif "max_input_tokens" in nested_meta:
                context_window = int(nested_meta["max_input_tokens"])

    max_tokens = None
    if "max_tokens" in metadata:
        max_tokens = int(metadata["max_tokens"])
    else:
        nested_meta = metadata.get("metadata", {})
        if isinstance(nested_meta, dict) and "max_tokens" in nested_meta:
            max_tokens = int(nested_meta["max_tokens"])

    return ModelCapabilities(
        model_id=model_id,
        context_window=context_window,
        max_tokens=max_tokens,
        raw_metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------


async def check_parameter_support(
    client: httpx.AsyncClient,
    base_url: str,
    model_id: str,
    param_name: str,
    param_value: Any,
    api_key: str,
    provider: str | None = None,
) -> tuple[bool, str | None]:
    """Check if a specific parameter is supported by sending a minimal request."""
    if provider is None:
        provider = detect_provider(base_url)
    url = _get_chat_completions_endpoint(base_url, provider)

    if param_name == "cache_control":
        messages_payload: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "test", "cache_control": param_value}],
            }
        ]
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages_payload,
            "max_tokens": 1,
            "stream": False,
        }
    else:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 1,
            "stream": False,
            param_name: param_value,
        }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = await client.post(url, json=payload, headers=headers, timeout=15.0)
        if response.status_code in (200, 201):
            return True, None
        error_data = response.json() if response.text else {}
        error_msg = (
            error_data.get("error", {}).get("message", response.text[:200])
            if isinstance(error_data, dict) and isinstance(error_data.get("error"), dict)
            else response.text[:200]
        )
        return False, error_msg
    except Exception as test_error:
        return False, str(test_error)


async def _probe_param_status(
    client: httpx.AsyncClient,
    base_url: str,
    model_id: str,
    param_name: str,
    param_value: Any,
    api_key: str,
    provider: str,
) -> tuple[bool, int | None, str | None]:
    """Probe parameter support and include HTTP status so only 400/422 mark rejected_params."""
    url = _get_chat_completions_endpoint(base_url, provider)
    if param_name == "cache_control":
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": "test", "cache_control": {"type": "ephemeral"}}],
            "max_tokens": 1,
            "stream": False,
        }
    else:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 1,
            "stream": False,
            param_name: param_value,
        }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = await client.post(url, json=payload, headers=headers, timeout=15.0)
        if response.status_code in (200, 201):
            return True, response.status_code, None
        error_data = response.json() if response.text else {}
        error_msg = (
            error_data.get("error", {}).get("message", response.text[:200])
            if isinstance(error_data, dict) and isinstance(error_data.get("error"), dict)
            else response.text[:200]
        )
        return False, response.status_code, error_msg
    except Exception as probe_error:
        return False, None, str(probe_error)


async def calibrate_model(
    model_id: str,
    base_url: str = "",
    api_key: str = "",
    force: bool = False,
    store: ModelOverrideStore | None = None,
) -> ModelCapabilities:
    """Calibrate a model: detect metadata, run validation tests, return capabilities."""
    if store is None:
        store = ModelOverrideStore()

    if not force:
        cached = store.get(model_id)
        if cached:
            return cached

    if not api_key:
        api_key = os.environ.get("DENDROPHIS_API_KEY", "")
    if not base_url:
        base_url = os.environ.get("DENDROPHIS_BASE_URL", "")

    if not api_key or not base_url:
        try:
            loader = ConfigLoader.load()
            if not api_key:
                api_key = loader.config.llm.api_key
            if not base_url:
                base_url = loader.config.llm.base_url
        except Exception as cfg_error:
            logger.debug("Calibration config fallback failed: %s", cfg_error)

    if not base_url:
        model_lower = model_id.lower()
        if "deepinfra" in model_lower:
            base_url = "https://api.deepinfra.com/v1/openai"
        elif "openrouter" in model_lower:
            base_url = "https://openrouter.ai/api/v1"
        else:
            base_url = "https://api.openai.com/v1"

    if not api_key:
        raise ValueError("API key required for calibration. Set DENDROPHIS_API_KEY or provide api_key.")

    provider = detect_provider(base_url)
    capabilities = ModelCapabilities(model_id=model_id, provider=provider)

    async with httpx.AsyncClient() as client:
        metadata = await _fetch_model_metadata_async(client, base_url, api_key, model_id)
        if metadata:
            capabilities = extract_capabilities_from_metadata(metadata)
            capabilities.provider = provider

        if capabilities.supports_reasoning_effort:
            supported, status_code, error = await _probe_param_status(
                client, base_url, model_id, "reasoning_effort", "low", api_key, provider
            )
            capabilities.supports_reasoning_effort = supported
            if not supported:
                if status_code in (400, 422):
                    capabilities.rejected_params.append("reasoning_effort")
                capabilities.test_errors["reasoning_effort"] = error or "Unknown error"

        supported, status_code, error = await _probe_param_status(
            client, base_url, model_id, "cache_control", {"type": "ephemeral"}, api_key, provider
        )
        if not supported:
            if status_code in (400, 422):
                capabilities.rejected_params.append("cache_control")
            capabilities.test_errors["cache_control"] = error or "Unknown error"

        supported, status_code, error = await _probe_param_status(
            client, base_url, model_id, "prompt_cache_key", "test-cache-key", api_key, provider
        )
        capabilities.supports_prompt_cache_key = supported
        if not supported:
            if status_code in (400, 422):
                capabilities.rejected_params.append("prompt_cache_key")
            capabilities.test_errors["prompt_cache_key"] = error or "Unknown error"

    model_lower = model_id.lower()

    if any(mistral_family in model_lower for mistral_family in ("mistral", "mixtral", "codestral")):
        capabilities.supports_tools = True

    vlm_keywords = (
        "gemma-4",
        "claude-3-5",
        "claude-3",
        "claude-",
        "gpt-4o",
        "gpt-5",
        "gemini-1.5",
        "gemini-2.0",
        "gemini-2.5",
        "qwen2.5-vl",
        "llava",
        "vision",
        "vlm",
        "pixtral",
    )
    if any(keyword in model_lower for keyword in vlm_keywords):
        capabilities.supports_vlm = True

    if provider == "local" and "mlc" in model_lower:
        capabilities.rejected_params.append("tool_calls")

    store.set(capabilities)
    store.save()

    return capabilities


async def list_available_models(
    base_url: str = "",
    api_key: str = "",
) -> list[dict[str, Any]]:
    """List all available models from a provider asynchronously."""
    if not base_url:
        base_url = os.environ.get("DENDROPHIS_BASE_URL", "")
    if not api_key:
        api_key = os.environ.get("DENDROPHIS_API_KEY", "")

    if not api_key or not base_url:
        try:
            loader = ConfigLoader.load()
            if not api_key:
                api_key = loader.config.llm.api_key
            if not base_url:
                base_url = loader.config.llm.base_url
        except Exception as cfg_error:
            logger.debug("list_available_models config fallback failed: %s", cfg_error)

    if not base_url:
        base_url = "https://api.openai.com/v1"

    if not api_key:
        raise ValueError("API key required. Set DENDROPHIS_API_KEY or provide api_key.")

    provider = detect_provider(base_url)
    url = _get_models_endpoint(base_url, provider)
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("data", [])
        raise RuntimeError(f"Failed to fetch models: {response.status_code} - {response.text}")
    except Exception as fetch_error:
        raise RuntimeError(f"Error fetching models: {fetch_error}") from fetch_error


# ---------------------------------------------------------------------------
# CLI Output Formatting
# ---------------------------------------------------------------------------


def format_model_info(model_id: str, store: ModelOverrideStore | None = None) -> str:
    """Format model information for CLI output."""
    if store is None:
        store = ModelOverrideStore()

    cached = store.get(model_id)
    if cached:
        return str(cached)

    return f"Model: {model_id} (not calibrated yet - run --calibrate)"


def format_model_list(models: list[dict[str, Any]], store: ModelOverrideStore | None = None) -> str:
    """Format list of models for CLI output."""
    if store is None:
        store = ModelOverrideStore()

    lines = ["Available Models:", "-" * 60]

    for model_entry in models:
        model_id = model_entry.get("id", "unknown")
        context = model_entry.get("context_length", model_entry.get("max_input_tokens", 0))
        cached = store.get(model_id)
        calibrated_marker = " ✓" if cached else " "
        lines.append(f"  {calibrated_marker} {model_id} (context: {context})")

    return "\n".join(lines)


def format_recommended_config(model_id: str, store: ModelOverrideStore | None = None) -> str:
    """Format recommended config for a model."""
    if store is None:
        store = ModelOverrideStore()

    capabilities = store.get(model_id)
    if not capabilities:
        return f"No calibration data for {model_id}. Run --calibrate first."

    config = capabilities.get_recommended_config()

    lines = [
        f"Recommended config for: {model_id}",
        "-" * 60,
        "llm:",
    ]

    for key, value in config.items():
        if key.startswith("_"):
            continue
        lines.append(f"  {key}: {value}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runtime Parameter Checking
# ---------------------------------------------------------------------------


def is_param_rejected(model_id: str, param_name: str, store: ModelOverrideStore | None = None) -> bool:
    """Check if a parameter is rejected for a specific model based on calibration results."""
    if store is None:
        store = ModelOverrideStore()

    capabilities = store.get(model_id)
    if capabilities is None:
        return False

    return param_name in capabilities.rejected_params


def get_model_provider(model_id: str) -> str:
    """Detect provider from model_id."""
    model_lower = model_id.lower()
    if "deepinfra" in model_lower:
        return "deepinfra"
    if "openrouter" in model_lower:
        return "openrouter"
    if "mistral" in model_lower:
        return "mistral"
    if "anthropic" in model_lower:
        return "anthropic"
    return "unknown"
