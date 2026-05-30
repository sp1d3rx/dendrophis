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
from pathlib import Path
from typing import Any

import httpx
from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True


logger = logging.getLogger(__name__)

# Default override file location
OVERRIDE_FILE = Path.home() / ".config" / "dendrophis" / "model_overrides.yaml"


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

    # Feature support
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_reasoning_effort: bool = True
    supports_caching: bool = False
    supports_prompt_cache: bool = False
    supports_prompt_cache_key: bool = False

    # Parameter quirks (provider-specific)
    rejected_params: list[str] = field(default_factory=list)
    requires_params: list[str] = field(default_factory=list)

    # Test results
    test_results: dict[str, bool | str] = field(default_factory=dict)
    test_errors: dict[str, str] = field(default_factory=dict)

    # Metadata from provider
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def provider_name(self) -> str:
        """Extract provider from base_url or model_id."""
        if self.provider:
            return self.provider
        # Infer from model_id
        if "deepinfra" in (self.model_id.lower() if self.model_id else ""):
            return "deepinfra"
        if "openrouter" in (self.model_id.lower() if self.model_id else ""):
            return "openrouter"
        if "mistral" in (self.model_id.lower() if self.model_id else ""):
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

        # Add prompt_cache_key recommendation for models that support it
        if self.supports_prompt_cache_key:
            # Suggest a session-scoped key format
            # Format: userid-chatsessionid (e.g., "user123-chat456") or "dendrophis-{session_id}"
            config["prompt_cache_key"] = "# Set to a session-scoped key like 'dendrophis-{session_id}'"

        # Add rejected params that should be stripped
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
        ]

        for name, supported, icon in features:
            status = icon if supported else "❌"
            lines.append(f"  {status} {name}: {'supported' if supported else 'NOT supported'}")

        if self.rejected_params:
            lines.append(f"  ⚠  Rejected params: {', '.join(self.rejected_params)}")

        if self.test_errors:
            lines.append("  Test errors:")
            for test, error in self.test_errors.items():
                lines.append(f"    - {test}: {error[:60]}...")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Override Storage
# ---------------------------------------------------------------------------


class ModelOverrideStore:
    """Manage cached model calibration results."""

    def __init__(self, path: Path | None = None):
        self.path = path or OVERRIDE_FILE
        self._overrides: dict[str, ModelCapabilities] = {}
        self._loaded = False

    def _ensure_dir(self) -> None:
        """Ensure the config directory exists."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, ModelCapabilities]:
        """Load overrides from YAML file."""
        if self._loaded:
            return self._overrides

        if not self.path.exists():
            self._overrides = {}
            self._loaded = True
            return self._overrides

        try:
            with open(self.path) as f:
                data = _yaml.load(f) or {}
            self._overrides = {
                model_id: ModelCapabilities.from_dict(cap_data) for model_id, cap_data in data.get("models", {}).items()
            }
        except Exception as e:
            logger.warning(f"Failed to load model overrides: {e}")
            self._overrides = {}

        self._loaded = True
        return self._overrides

    def save(self) -> None:
        """Save overrides to YAML file."""
        from io import StringIO

        self._ensure_dir()
        data = {"models": {model_id: cap.to_dict() for model_id, cap in self._overrides.items()}}
        try:
            buf = StringIO()
            _yaml.dump(data, buf)
            self.path.write_text(buf.getvalue())
        except Exception as e:
            logger.error(f"Failed to save model overrides: {e}")

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
    base_url = base_url.lower()
    if "deepinfra" in base_url:
        return "deepinfra"
    if "openrouter" in base_url:
        return "openrouter"
    if "mistral" in base_url:
        return "mistral"
    if "anthropic" in base_url:
        return "anthropic"
    if "localhost" in base_url or "127.0.0.1" in base_url:
        return "local"
    return "unknown"


def _get_models_endpoint(base_url: str, provider: str) -> str:
    """Get the models endpoint for a provider."""
    # DeepInfra uses /models
    if provider == "deepinfra":
        return f"{base_url.rstrip('/')}/models"
    # Most OpenAI-compatible providers use /v1/models
    return f"{base_url.rstrip('/')}/v1/models"


def _get_chat_completions_endpoint(base_url: str, provider: str) -> str:
    """Get the chat completions endpoint for a provider."""
    # DeepInfra uses /chat/completions (not /v1/chat/completions)
    if provider == "deepinfra":
        return f"{base_url.rstrip('/')}/chat/completions"
    # Most OpenAI-compatible providers use /v1/chat/completions
    return f"{base_url.rstrip('/')}/v1/chat/completions"


def fetch_model_metadata(base_url: str, api_key: str, model_id: str) -> dict[str, Any] | None:
    """Fetch model metadata from provider's models endpoint."""
    provider = detect_provider(base_url)
    url = _get_models_endpoint(base_url, provider)

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
        if response.status_code != 200:
            logger.debug(f"Failed to fetch models from {url}: {response.status_code}")
            return None

        data = response.json()
        models = data.get("data", [])

        for m in models:
            if m.get("id") == model_id:
                return m

        logger.debug(f"Model {model_id} not found in {url}")
        return None

    except Exception as e:
        logger.debug(f"Error fetching model metadata: {e}")
        return None


def extract_capabilities_from_metadata(metadata: dict[str, Any]) -> ModelCapabilities:
    """Extract capabilities from provider metadata."""
    model_id = metadata.get("id", "")

    # Extract context window - check both top-level and nested metadata
    context_window = 0
    if "context_length" in metadata:
        context_window = int(metadata["context_length"])
    elif "max_input_tokens" in metadata:
        context_window = int(metadata["max_input_tokens"])
    else:
        # Check nested metadata dict (DeepInfra, OpenRouter)
        nested_meta = metadata.get("metadata", {})
        if isinstance(nested_meta, dict):
            if "context_length" in nested_meta:
                context_window = int(nested_meta["context_length"])
            elif "max_input_tokens" in nested_meta:
                context_window = int(nested_meta["max_input_tokens"])

    # Extract max_tokens
    max_tokens = None
    if "max_tokens" in metadata:
        max_tokens = int(metadata["max_tokens"])
    else:
        nested_meta = metadata.get("metadata", {})
        if isinstance(nested_meta, dict) and "max_tokens" in nested_meta:
            max_tokens = int(nested_meta["max_tokens"])

    # Check for supported features based on metadata
    # This varies by provider - some include supported parameters in metadata
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
    """Check if a specific parameter is supported by sending a minimal request.

    Returns (supported, error_message).
    """
    if provider is None:
        provider = detect_provider(base_url)
    url = _get_chat_completions_endpoint(base_url, provider)

    # Build minimal payload with the parameter
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "test"}],
        "max_tokens": 1,
        "stream": False,  # Non-streaming for simpler testing
    }
    payload[param_name] = param_value

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = await client.post(url, json=payload, headers=headers, timeout=15.0)
        if response.status_code in (200, 201):
            return True, None
        error_data = response.json() if response.text else {}
        error_msg = error_data.get("error", {}).get("message", response.text[:200])
        return False, error_msg
    except Exception as e:
        return False, str(e)


async def calibrate_model(
    model_id: str,
    base_url: str = "",
    api_key: str = "",
    force: bool = False,
    store: ModelOverrideStore | None = None,
) -> ModelCapabilities:
    """Calibrate a model: detect metadata, run validation tests, return capabilities.

    Args:
        model_id: The model identifier
        base_url: Provider base URL (optional, will try to detect)
        api_key: API key for authentication
        force: Force re-calibration even if cached
        store: Override store for caching results

    Returns:
        ModelCapabilities with detected and tested capabilities
    """
    if store is None:
        store = ModelOverrideStore()

    # Check cache first
    if not force:
        cached = store.get(model_id)
        if cached:
            return cached

    # Try to load from config first if not provided
    if not api_key or not base_url:
        api_key = os.environ.get("DENDROPHIS_API_KEY", "")
        if not api_key or not base_url:
            # Try to load from config
            try:
                from dendrophis.config.loader import ConfigLoader

                loader = ConfigLoader.load()
                if not api_key:
                    api_key = loader.config.llm.api_key
                if not base_url:
                    base_url = loader.config.llm.base_url
            except Exception:
                pass

    # Detect provider if base_url still not provided, infer from model_id
    if not base_url:
        # Try to infer from model_id
        model_lower = model_id.lower()
        if "deepinfra" in model_lower:
            base_url = "https://api.deepinfra.com/v1/openai"
        elif "openrouter" in model_lower:
            base_url = "https://openrouter.ai/api/v1"
        else:
            base_url = "https://api.openai.com/v1"

    if not api_key:
        raise ValueError("API key required for calibration. Set DENDROPHIS_API_KEY or provide api_key.")

    if not api_key:
        raise ValueError("API key required for calibration. Set DENDROPHIS_API_KEY or provide api_key.")

    provider = detect_provider(base_url)
    capabilities = ModelCapabilities(model_id=model_id, provider=provider)

    # Step 1: Fetch metadata
    metadata = fetch_model_metadata(base_url, api_key, model_id)
    if metadata:
        capabilities = extract_capabilities_from_metadata(metadata)
        capabilities.provider = provider

    # Step 2: Run validation tests
    async with httpx.AsyncClient() as client:
        # Test reasoning_effort
        if capabilities.supports_reasoning_effort:
            supported, error = await check_parameter_support(
                client, base_url, model_id, "reasoning_effort", "low", api_key, provider
            )
            capabilities.supports_reasoning_effort = supported
            if not supported:
                capabilities.rejected_params.append("reasoning_effort")
                capabilities.test_errors["reasoning_effort"] = error or "Unknown error"

        # Test cache_control (known to cause issues with some Mistral deployments)
        supported, error = await check_parameter_support(
            client, base_url, model_id, "cache_control", {"value": "ephemeral"}, api_key, provider
        )
        if not supported:
            capabilities.rejected_params.append("cache_control")
            capabilities.test_errors["cache_control"] = error or "Unknown error"

        # Test prompt_cache_key (Mistral/Kimi parameter for prompt caching)
        supported, error = await check_parameter_support(
            client, base_url, model_id, "prompt_cache_key", "test-cache-key", api_key, provider
        )
        capabilities.supports_prompt_cache_key = supported
        if not supported:
            capabilities.rejected_params.append("prompt_cache_key")
            capabilities.test_errors["prompt_cache_key"] = error or "Unknown error"

    # Step 3: Apply heuristic fallbacks for common patterns
    model_lower = model_id.lower()

    # Mistral models generally support tools
    if any(x in model_lower for x in ["mistral", "mixtral", "codestral"]):
        capabilities.supports_tools = True

    # Local MLC models don't support tool_calls in history
    if provider == "local":
        capabilities.rejected_params.append("tool_calls")

    # DeepInfra Mistral models may have issues
    if provider == "deepinfra" and "mistral" in model_lower and capabilities.supports_reasoning_effort:
        # We already tested reasoning_effort, but add note about potential issues
        # Some DeepInfra Mistral models reject reasoning_effort
        # Keep as True if test passed, otherwise it's already False
        pass

    # Step 4: Cache results
    store.set(capabilities)
    store.save()

    return capabilities


async def list_available_models(
    base_url: str = "",
    api_key: str = "",
) -> list[dict[str, Any]]:
    """List all available models from a provider."""
    if not base_url:
        base_url = os.environ.get("DENDROPHIS_BASE_URL", "https://api.openai.com/v1")
    if not api_key:
        api_key = os.environ.get("DENDROPHIS_API_KEY", "")
        if not api_key:
            try:
                from dendrophis.config.loader import ConfigLoader

                loader = ConfigLoader.load()
                api_key = loader.config.llm.api_key
                base_url = loader.config.llm.base_url
            except Exception:
                pass

    if not api_key:
        raise ValueError("API key required. Set DENDROPHIS_API_KEY or provide api_key.")

    provider = detect_provider(base_url)
    url = _get_models_endpoint(base_url, provider)
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            return data.get("data", [])
        raise Exception(f"Failed to fetch models: {response.status_code} - {response.text}") from None
    except Exception as e:
        raise Exception(f"Error fetching models: {e}") from e


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

    for m in models:
        model_id = m.get("id", "unknown")
        context = m.get("context_length", m.get("max_input_tokens", 0))

        # Check if calibrated
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
            continue  # Skip internal keys
        lines.append(f"  {key}: {value}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runtime Parameter Checking
# ---------------------------------------------------------------------------


def is_param_rejected(model_id: str, param_name: str, store: ModelOverrideStore | None = None) -> bool:
    """Check if a parameter is rejected for a specific model based on calibration results.

    Args:
        model_id: The model identifier
        param_name: The parameter name to check (e.g., "reasoning_effort", "prompt_cache_key")
        store: Optional override store (defaults to loading from disk)

    Returns:
        True if the parameter is in the model's rejected_params list, False otherwise
    """
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
