"""F5: LLMClient send-timeout transport reset must not break in-flight requests.

Pre-fix, a send() timeout immediately called ``self._http.aclose()`` and swapped in a
fresh client. If any other request (e.g. fetch_models, or a concurrent stream on the
same client) was in flight, its live connection was torn down. The retired client is
now only closed once its last in-flight use releases it (or in aclose).
"""

from __future__ import annotations

import asyncio

import pytest

from dendrophis.config.schema import LLMConfig
from dendrophis.events import ErrorEvent
from dendrophis.llm.client import LLMClient, _ProviderContext


class _StubHttpClient:
    """Stand-in httpx client whose send() never completes in time."""

    def __init__(self) -> None:
        self.aclose_count = 0

    def build_request(self, *args, **kwargs):
        return object()

    async def send(self, req, stream: bool = True):
        await asyncio.sleep(30)
        raise AssertionError("send() should have been cancelled by wait_for")

    async def aclose(self) -> None:
        self.aclose_count += 1


def _config() -> LLMConfig:
    return LLMConfig(model="test-model", base_url="http://127.0.0.1:9/v1", api_key="k", timeout=0.05)


def _provider_ctx() -> _ProviderContext:
    return _ProviderContext(
        is_local=True,
        is_direct_anthropic=False,
        is_openrouter=False,
        is_deepinfra=False,
        use_responses_api=False,
        use_xml_tools=False,
        url="http://127.0.0.1:9/v1/chat/completions",
        sse_start_mode="text",
    )


async def _drain(client: LLMClient) -> list:
    return [event async for event in client._stream_raw(_provider_ctx(), {})]


@pytest.mark.anyio
async def test_timeout_does_not_close_client_while_concurrent_request_in_flight() -> None:
    client = LLMClient(_config())
    stub = _StubHttpClient()
    client._http = stub

    # Simulate a second in-flight request (e.g. fetch_models) sharing the client.
    other = client._acquire_http()

    events = await _drain(client)

    assert any(isinstance(e, ErrorEvent) for e in events)
    assert client._http is not stub, "expected a fresh client after the timeout"
    assert stub.aclose_count == 0, "must not close a client with in-flight requests"
    assert stub in client._retired_http

    # Once the last in-flight user releases, the retired client is returned for closing.
    released = client._release_http(other)
    assert released == [stub]
    await client._close_retired_http(released)
    assert stub.aclose_count == 1

    await client.aclose()


@pytest.mark.anyio
async def test_timeout_closes_old_client_when_no_other_requests_in_flight() -> None:
    client = LLMClient(_config())
    stub = _StubHttpClient()
    client._http = stub

    events = await _drain(client)

    assert any(isinstance(e, ErrorEvent) for e in events)
    assert client._http is not stub
    # No other in-flight users: the retired client is closed immediately on release.
    assert stub.aclose_count == 1
    assert client._retired_http == []

    await client.aclose()


@pytest.mark.anyio
async def test_aclose_closes_retired_clients() -> None:
    client = LLMClient(_config())
    stub = _StubHttpClient()
    client._http = stub
    other = client._acquire_http()

    await _drain(client)
    assert stub.aclose_count == 0  # still in flight

    await client.aclose()
    assert stub.aclose_count == 1
    assert client._retired_http == []
    # Releasing after close must not double-close.
    assert client._release_http(other) == []
