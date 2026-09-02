"""Tests for ModelInfo parsing and capability heuristics."""

from __future__ import annotations

from dendrophis.config.schema import LLMConfig
from dendrophis.llm.client import WELL_KNOWN_MODELS, LLMClient, ModelInfo


def test_model_info_from_api_with_max_model_len() -> None:
    """Test that max_model_len is parsed correctly from model API response."""
    raw_model_data = {
        "id": "Agents-A1-5bit",
        "object": "model",
        "created": 1783274712,
        "owned_by": "omlx",
        "max_model_len": 262144,
    }

    model_info = ModelInfo.from_api(raw_model_data)

    assert model_info.id == "Agents-A1-5bit"
    assert model_info.context_window == 262144
    assert model_info.is_text_generation is True


def test_tool_mode_auto_resolution() -> None:
    """Verify that auto tool mode correctly chooses XML or native tools."""
    # 1. Local connection, model lacks native tool calling (e.g. Agents-A1-5bit).
    # Should resolve to use_xml_tools=True
    config_agents = LLMConfig(
        base_url="http://127.0.0.1:8000/v1", api_key="test", model="Agents-A1-5bit", tool_mode="auto"
    )
    client_agents = LLMClient(config=config_agents)
    context_agents = client_agents._make_provider_context()
    assert context_agents.use_xml_tools is True

    # 2. Local connection, model supports native tool calling (e.g. Qwen2.5-Coder).
    # Should resolve to use_xml_tools=False
    config_qwen = LLMConfig(
        base_url="http://127.0.0.1:8000/v1",
        api_key="test",
        model="Qwen2.5-Coder-14B-Instruct",
        tool_mode="auto",
    )
    client_qwen = LLMClient(config=config_qwen)
    context_qwen = client_qwen._make_provider_context()
    assert context_qwen.use_xml_tools is False

    # 3. Remote connection -> Should resolve to use_xml_tools=False regardless of model
    config_remote = LLMConfig(
        base_url="https://api.openai.com/v1", api_key="test", model="Agents-A1-5bit", tool_mode="auto"
    )
    client_remote = LLMClient(config=config_remote)
    context_remote = client_remote._make_provider_context()
    assert context_remote.use_xml_tools is False


class _FailingHttpClient:
    """Stand-in httpx client whose get() always fails, forcing the fetch_models fallback."""

    async def get(self, *args, **kwargs):
        raise RuntimeError("network down")

    async def aclose(self) -> None:
        return None


async def test_fetch_models_fallback_returns_copy_not_module_constant() -> None:
    """C4: fetch_models fallback must not hand back the module-level WELL_KNOWN_MODELS
    by reference; a consumer (session.models) must not be able to corrupt the constant."""
    config = LLMConfig(base_url="http://127.0.0.1:9/v1", api_key="k", model="gpt-4o")
    client = LLMClient(config=config, http_client=_FailingHttpClient())

    result = await client.fetch_models()

    # Correct content, in order ...
    assert [m.id for m in result] == [m.id for m in WELL_KNOWN_MODELS]
    # ... but a distinct list object, not the shared module constant.
    assert result is not WELL_KNOWN_MODELS

    # Mutating the returned list must not corrupt the module-level constant.
    original_len = len(WELL_KNOWN_MODELS)
    result.pop()
    assert len(WELL_KNOWN_MODELS) == original_len
