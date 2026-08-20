"""Tests for LLMClient error handling and empty completion handling."""

from __future__ import annotations

import pytest

try:
    import respx
except ImportError:
    respx = None

pytestmark = pytest.mark.skipif(respx is None, reason="respx is required for mock http client error tests")

from dendrophis.config.schema import LLMConfig
from dendrophis.events import ErrorEvent
from dendrophis.llm.client import LLMClient


@pytest.mark.anyio
async def test_client_handles_openai_error_payload() -> None:
    """Test that LLMClient extracts standard OpenAI error messages on non-200 status codes."""
    config = LLMConfig(base_url="https://api.example.com/v1", api_key="test-key")
    client = LLMClient(config=config)

    error_response_payload = {
        "error": {
            "message": "Invalid API key or token expired",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    }

    # Mock the HTTP POST request to return a 400 Bad Request with the error payload
    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").respond(
            status_code=400,
            json=error_response_payload,
        )

        events = [event async for event in client.stream_chat(messages=[{"role": "user", "content": "Hello"}])]

        assert len(events) == 1
        error_event = events[0]
        assert isinstance(error_event, ErrorEvent)
        assert "HTTP 400" in error_event.message
        assert "Invalid API key or token expired" in error_event.message


@pytest.mark.anyio
async def test_client_handles_generic_detail_error_payload() -> None:
    """Test that LLMClient extracts detail fields from error responses."""
    config = LLMConfig(base_url="https://api.example.com/v1", api_key="test-key")
    client = LLMClient(config=config)

    error_response_payload = {"detail": "Missing mandatory parameter 'messages'"}

    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").respond(
            status_code=422,
            json=error_response_payload,
        )

        events = [event async for event in client.stream_chat(messages=[{"role": "user", "content": "Hello"}])]

        assert len(events) == 1
        error_event = events[0]
        assert isinstance(error_event, ErrorEvent)
        assert "HTTP 422" in error_event.message
        assert "Missing mandatory parameter 'messages'" in error_event.message


@pytest.mark.anyio
async def test_client_handles_raw_text_error_response() -> None:
    """Test that LLMClient falls back to raw response text when json parsing fails."""
    config = LLMConfig(base_url="https://api.example.com/v1", api_key="test-key")
    client = LLMClient(config=config)

    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").respond(
            status_code=500,
            content=b"Internal Server Error: Database offline",
        )

        events = [event async for event in client.stream_chat(messages=[{"role": "user", "content": "Hello"}])]

        assert len(events) == 1
        error_event = events[0]
        assert isinstance(error_event, ErrorEvent)
        assert "HTTP 500" in error_event.message
        assert "Database offline" in error_event.message


@pytest.mark.anyio
async def test_client_prevents_silent_empty_completion() -> None:
    """Test that LLMClient yields an ErrorEvent when the completion stream returns absolutely nothing."""
    config = LLMConfig(base_url="https://api.example.com/v1", api_key="test-key")
    client = LLMClient(config=config)

    # Return HTTP 200 but yield an empty stream (only close or no data)
    empty_sse_stream = b""

    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").respond(
            status_code=200,
            headers={"Content-Type": "text/event-stream"},
            content=empty_sse_stream,
        )

        events = [event async for event in client.stream_chat(messages=[{"role": "user", "content": "Hello"}])]

        assert len(events) == 1
        error_event = events[0]
        assert isinstance(error_event, ErrorEvent)
        assert "Server returned an empty response" in error_event.message
