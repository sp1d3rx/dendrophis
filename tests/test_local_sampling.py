"""Tests to verify that local provider configurations include sampling parameters in payload."""

from dendrophis.config.schema import LLMConfig
from dendrophis.llm.client import LLMClient


def test_build_payload_includes_sampling_params_for_local_provider() -> None:
    """Verify that sampling parameters are sent to local OpenAI-compatible providers."""
    local_configuration = LLMConfig(
        base_url="http://127.0.0.1:8005/v1",
        api_key="none",
        model="gemma-4-26B-A4B-it",
        top_k=64,
        min_p=0.05,
        repetition_penalty=1.1,
        frequency_penalty=0.1,
        presence_penalty=0.1,
    )

    client_instance = LLMClient(local_configuration)
    provider_context = client_instance._make_provider_context()

    assert provider_context.is_local is True

    messages_list = [{"role": "user", "content": "Hello"}]
    payload = client_instance._build_payload(
        provider_context=provider_context,
        messages=messages_list,
        tools=None,
        enable_cache_control=False,
        tool_choice="auto",
    )

    assert payload["top_k"] == 64
    assert payload["min_p"] == 0.05
    assert payload["repetition_penalty"] == 1.1
    assert payload["frequency_penalty"] == 0.1
    assert payload["presence_penalty"] == 0.1
