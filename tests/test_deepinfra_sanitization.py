"""Tests for DeepInfra tool message sanitization.

This test ensures that tool result messages are properly filtered out for
DeepInfra to prevent "Not the same number of function calls and responses" errors.
"""

import pytest

from dendrophis.llm.client import LLMClient


@pytest.fixture
def mock_deepinfra_config():
    """Create a mock LLMConfig for DeepInfra."""
    from dendrophis.config.schema import LLMConfig

    return LLMConfig(
        base_url="https://api.deepinfra.com/v1",
        api_key="test-key",
        model="mistralai/Mistral-7B-Instruct-v0.2",
        max_tokens=100,
        temperature=0.7,
        timeout=120.0,
    )


def test_sanitize_messages_filters_tool_results_for_deepinfra(mock_deepinfra_config):
    """Test that tool result messages and tool call assistant messages are filtered out for DeepInfra."""
    client = LLMClient(mock_deepinfra_config)

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What's in this file?"},
        {"role": "assistant", "content": '<tool_call>{"name": "read", "arguments": "{}"}</tool_call>'},
        {"role": "tool", "tool_call_id": "tc-123", "name": "read", "content": "file content"},
        {"role": "assistant", "content": "The file contains: file content"},
    ]

    ctx = client._make_provider_context()
    sanitized = client._sanitize_messages(
        messages,
        is_local=ctx.is_local,
        is_direct_anthropic=ctx.is_direct_anthropic,
        is_openrouter=ctx.is_openrouter,
        is_deepinfra=ctx.is_deepinfra,
        use_responses_api=ctx.use_responses_api,
    )

    # Tool result message and assistant message with tool_call should both be filtered out
    assert len(sanitized) == 3
    assert all(msg.get("role") != "tool" for msg in sanitized)
    # tool_calls field should be stripped from assistant messages
    for msg in sanitized:
        assert "tool_calls" not in msg
    # Assistant messages with tool_call should be filtered out entirely
    roles = [msg.get("role") for msg in sanitized]
    assert roles == ["system", "user", "assistant"]
    # The remaining assistant message should not contain tool_call
    assert "<tool_call>" not in sanitized[-1]["content"]


def test_sanitize_messages_keeps_tool_results_for_openai():
    """Test that tool result messages are kept for standard OpenAI-compatible providers."""
    from dendrophis.config.schema import LLMConfig

    # Use a generic OpenAI-compatible base URL (not DeepInfra, OpenRouter, or local)
    config = LLMConfig(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4",
        max_tokens=100,
        temperature=0.7,
        timeout=120.0,
    )

    client = LLMClient(config)

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What's in this file?"},
        {
            "role": "assistant",
            "content": "Calling tool...",
            "tool_calls": [
                {
                    "id": "tc-123",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc-123", "name": "read", "content": "file content"},
        {"role": "assistant", "content": "The file contains: file content"},
    ]

    ctx = client._make_provider_context()
    sanitized = client._sanitize_messages(
        messages,
        is_local=ctx.is_local,
        is_direct_anthropic=ctx.is_direct_anthropic,
        is_openrouter=ctx.is_openrouter,
        is_deepinfra=ctx.is_deepinfra,
        use_responses_api=ctx.use_responses_api,
    )

    # All messages should be kept for standard OpenAI
    assert len(sanitized) == 5
    # Tool result should still be present
    roles = [msg.get("role") for msg in sanitized]
    assert "tool" in roles


def test_sanitize_messages_mismatched_tool_counts_for_deepinfra(mock_deepinfra_config):
    """Test the specific scenario: more tool results than tool calls for DeepInfra.

    This reproduces the bug where file autocomplete adds a read tool result
    without a corresponding LLM tool call request.
    """
    client = LLMClient(mock_deepinfra_config)

    # Simulates: glob tool call + result, then read tool result (from autocomplete) without request
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "tell me where the entry point is"},
        {"role": "assistant", "content": '<tool_call>{"name": "glob", "arguments": "{}"}</tool_call>'},
        {"role": "tool", "tool_call_id": "pWcBB1KrS", "name": "glob", "content": '["main.py"]'},
        {"role": "assistant", "content": "I found the main.py file. I'll read the file..."},
        {"role": "tool", "tool_call_id": "ba7VP4zAW", "name": "read", "content": "file content"},
    ]

    ctx = client._make_provider_context()
    sanitized = client._sanitize_messages(
        messages,
        is_local=ctx.is_local,
        is_direct_anthropic=ctx.is_direct_anthropic,
        is_openrouter=ctx.is_openrouter,
        is_deepinfra=ctx.is_deepinfra,
        use_responses_api=ctx.use_responses_api,
    )

    # All tool result messages and assistant messages with tool calls should be filtered out
    assert len(sanitized) == 3
    roles = [msg.get("role") for msg in sanitized]
    assert "tool" not in roles
    # Only non-tool messages remain: system, user, assistant (without tool_call)
    assert roles == ["system", "user", "assistant"]
    # The remaining assistant message should not contain tool_call
    assert "<tool_call>" not in sanitized[-1]["content"]


def test_sanitize_messages_preserve_reasoning():
    """Test that reasoning_content is preserved or stripped based on preserve_reasoning."""
    from dendrophis.config.schema import LLMConfig

    # With preserve_reasoning="always" (default)
    config_preserve = LLMConfig(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4",
        preserve_reasoning="always",
    )
    client_preserve = LLMClient(config_preserve)

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi", "reasoning_content": "Thinking process..."},
    ]

    ctx_preserve = client_preserve._make_provider_context()
    sanitized_preserve = client_preserve._sanitize_messages(
        messages,
        is_local=ctx_preserve.is_local,
        is_direct_anthropic=ctx_preserve.is_direct_anthropic,
        is_openrouter=ctx_preserve.is_openrouter,
        is_deepinfra=ctx_preserve.is_deepinfra,
        use_responses_api=ctx_preserve.use_responses_api,
    )
    assert len(sanitized_preserve) == 3
    assert sanitized_preserve[-1]["reasoning_content"] == "Thinking process..."

    # With preserve_reasoning="never"
    config_strip = LLMConfig(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4",
        preserve_reasoning="never",
    )
    client_strip = LLMClient(config_strip)

    ctx_strip = client_strip._make_provider_context()
    sanitized_strip = client_strip._sanitize_messages(
        messages,
        is_local=ctx_strip.is_local,
        is_direct_anthropic=ctx_strip.is_direct_anthropic,
        is_openrouter=ctx_strip.is_openrouter,
        is_deepinfra=ctx_strip.is_deepinfra,
        use_responses_api=ctx_strip.use_responses_api,
    )
    assert len(sanitized_strip) == 3
    assert "reasoning_content" not in sanitized_strip[-1]


def test_sanitize_messages_strips_older_reasoning():
    """Test that reasoning_content is stripped for older turns but kept for current turn."""
    from dendrophis.config.schema import LLMConfig

    config = LLMConfig(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4",
        preserve_reasoning="current",
    )
    client = LLMClient(config)

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "First message"},
        {"role": "assistant", "content": "First response", "reasoning_content": "Thinking 1"},
        {"role": "user", "content": "Second message"},
        {"role": "assistant", "content": "Second response", "reasoning_content": "Thinking 2"},
    ]

    ctx = client._make_provider_context()
    sanitized = client._sanitize_messages(
        messages,
        is_local=ctx.is_local,
        is_direct_anthropic=ctx.is_direct_anthropic,
        is_openrouter=ctx.is_openrouter,
        is_deepinfra=ctx.is_deepinfra,
        use_responses_api=ctx.use_responses_api,
    )

    assert len(sanitized) == 5
    # First response (before the last user message) should have its reasoning stripped
    assert "reasoning_content" not in sanitized[2]
    # Second response (after the last user message) should keep its reasoning
    assert sanitized[4]["reasoning_content"] == "Thinking 2"
