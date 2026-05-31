"""Async LLM client — OpenAI-compatible streaming via httpx."""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from httpx_sse import EventSource

from dendrophis.config.schema import LLMConfig
from dendrophis.events import (
    DoneEvent,
    ErrorEvent,
    ReasoningDeltaEvent,
    RetryEvent,
    StreamEvent,
    TextDeltaEvent,
    ToolCall,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    TurnResult,
)
from dendrophis.llm.models import (
    supports_caching_by_id,
    supports_prompt_cache_key_by_id,
    supports_reasoning_effort_by_id,
    supports_tools_by_id,
)
from dendrophis.llm.stream import parse_sse_event, parse_text_tool_calls

# from dendrophis.utils import _sanitize_tool_id  # REMOVED - no tool ID hashing

_LOG_PATH = os.environ.get("DENDROPHIS_CHAT_LOG", "")


def _chat_log(direction: str, data: str) -> None:
    if not _LOG_PATH:
        return
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    with open(_LOG_PATH, "a") as f:
        f.write(f"[{ts}] {direction}\n{data}\n{'─' * 80}\n")


# ---------------------------------------------------------------------------
# Provider context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ProviderContext:
    """Derived provider flags computed once per request."""

    is_local: bool
    is_direct_anthropic: bool
    is_openrouter: bool
    is_deepinfra: bool
    use_responses_api: bool
    use_xml_tools: bool  # inject tools as XML instead of using the OpenAI tools API
    url: str
    sse_start_mode: str  # "thinking" for Qwen3.5/MLC, else "text"


# ---------------------------------------------------------------------------
# Turn accumulator
# ---------------------------------------------------------------------------


@dataclass
class _TurnAccumulator:
    """Collects streaming events into a complete turn."""

    text: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"

    def update(self, event: Any) -> None:
        if isinstance(event, TextDeltaEvent):
            self.text.append(event.delta)
        elif isinstance(event, ReasoningDeltaEvent):
            self.reasoning.append(event.delta)
        elif isinstance(event, ToolCallDoneEvent):
            self.tool_calls.append(event.tool_call)
        elif isinstance(event, DoneEvent):
            self.finish_reason = event.finish_reason

    def resolve_tool_calls(self) -> list[ToolCall]:
        """Return structured tool calls, falling back to text-parsed ones.

        Backends like omlx send both structured delta.tool_calls (real indices)
        and raw <tool_call> tags in content (parsed as index 999). Prefer
        structured; if all are text-parsed, deduplicate by (name, arguments).
        """
        if self.tool_calls:
            structured = [tc for tc in self.tool_calls if tc.index != 999]
            if structured:
                return structured
            seen: set[tuple[str, str]] = set()
            deduped = []
            for tc in self.tool_calls:
                key = (tc.name, tc.arguments)
                if key not in seen:
                    seen.add(key)
                    deduped.append(tc)
            return deduped
        text = "".join(self.text)
        return parse_text_tool_calls(text) if text else []

    def is_false_positive(self, resolved: list[ToolCall]) -> bool:
        """MLC false-positive: finish=tool_calls but only reasoning emitted."""
        return self.finish_reason == "tool_calls" and not resolved and not self.text and bool(self.reasoning)


# ---------------------------------------------------------------------------
# ModelInfo
# ---------------------------------------------------------------------------


@dataclass
class ModelInfo:
    """Metadata for a single model returned by the /v1/models endpoint."""

    id: str
    context_window: int = 0
    max_completion_tokens: int = 0
    owned_by: str = ""
    type: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_text_generation(self) -> bool:
        if self.context_window > 0:
            low_id = self.id.lower()
            exclude_patterns = (
                "sdxl",
                "sd-",
                "flux",
                "whisper",
                "tts",
                "embed",
                "stable-diffusion",
                "dall-e",
                "cogview",
                "multimodal",
                "vision",
                "blur_background",
                "expand",
                "crystal",
                "depth",
                "upscale",
                "outpaint",
                "inpainting",
            )
            return not any(pattern in low_id for pattern in exclude_patterns)

        metadata = self.extra.get("metadata") or {}
        tags = [str(tag).lower() for tag in metadata.get("tags", [])]
        if any(tag in tags for tag in ("reasoning", "prompt_cache", "roleplay", "chat", "text")):
            return True

        model_type = str(self.type or self.extra.get("type", "")).lower()
        if model_type in ("text-generation", "chat", "text"):
            return True

        well_known = (
            "gpt",
            "claude",
            "llama",
            "qwen",
            "mistral",
            "mixtral",
            "deepseek",
            "nova",
            "olmo",
            "gemma",
            "phi",
            "lfm",
            "devstral",
            "dolphin",
            "command",
            "grok",
            "nemotron",
            "smollm",
            "granite",
            "yi",
            "internlm",
            "falcon",
            "stablelm",
        )
        return any(pattern in self.id.lower() for pattern in well_known)

    @property
    def cost_per_1k(self) -> float:
        metadata = self.extra.get("metadata") or {}
        pricing = metadata.get("pricing") or self.extra.get("pricing") or {}

        if isinstance(pricing, dict):
            try:
                input_val = float(pricing.get("prompt") or pricing.get("input") or pricing.get("input_tokens") or 0)
                output_val = float(
                    pricing.get("completion") or pricing.get("output") or pricing.get("output_tokens") or 0
                )
                if input_val > 0 or output_val > 0:
                    avg = (input_val + output_val) / 2
                    if avg > 0.0001:
                        return avg / 1000 if avg > 0.01 else avg
                    return avg * 1000
            except (ValueError, TypeError):
                pass
        return 0.0

    @property
    def cost_per_1m(self) -> float:
        return self.cost_per_1k * 1000

    @property
    def quant(self) -> str:
        low = self.id.lower()
        for quant_type in ("fp8", "fp16", "bf16", "awq", "gptq", "q8", "q4", "gguf", "int8", "int4"):
            if quant_type in low:
                return quant_type.upper()
        return ""

    @property
    def supports_tools(self) -> bool:
        if ":free" in self.id.lower():
            return False
        if supports_tools_by_id(self.id):
            return True
        metadata = self.extra.get("metadata") or {}
        supported_features = metadata.get("supported_features", [])
        supported_params = self.extra.get("supported_parameters", [])
        if any(f in supported_features for f in ("tools", "tool_use", "function_calling")):
            return True
        return any(p in supported_params for p in ("tools", "tool_choice"))

    @property
    def supports_caching(self) -> bool:
        if supports_caching_by_id(self.id):
            return True
        metadata = self.extra.get("metadata") or {}
        tags = [str(tag).lower() for tag in metadata.get("tags", [])]
        return "prompt_cache" in tags

    @property
    def supports_reasoning_effort(self) -> bool:
        return supports_reasoning_effort_by_id(self.id)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ModelInfo:
        metadata = data.get("metadata") or {}
        context_length = (
            data.get("context_length")
            or metadata.get("context_length")
            or data.get("context_window")
            or data.get("context_res")
            or data.get("max_position_embeddings")
            or data.get("max_context_length")
            or 0
        )
        return cls(
            id=data.get("id", ""),
            context_window=int(context_length),
            max_completion_tokens=int(metadata.get("max_tokens", 0)),
            owned_by=data.get("owned_by", ""),
            type=str(data.get("type") or ""),
            extra=data,
        )


WELL_KNOWN_MODELS = [
    ModelInfo(id="gpt-4o", context_window=128000, owned_by="openai"),
    ModelInfo(id="gpt-4o-mini", context_window=128000, owned_by="openai"),
    ModelInfo(id="claude-3-5-sonnet-20241022", context_window=200000, owned_by="anthropic"),
    ModelInfo(id="meta-llama/Meta-Llama-3.1-405B-Instruct", context_window=128000, owned_by="meta"),
    ModelInfo(id="meta-llama/Meta-Llama-3.1-70B-Instruct", context_window=128000, owned_by="meta"),
    ModelInfo(id="meta-llama/Meta-Llama-3.1-8B-Instruct", context_window=128000, owned_by="meta"),
]


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


class LLMClient:
    """Streaming OpenAI-compatible chat client."""

    def __init__(self, config: LLMConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                write=30.0,
                read=config.timeout,
                pool=5.0,
            )
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch_models(self) -> list[ModelInfo]:
        url = self._config.base_url.rstrip("/") + "/models"
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "HTTP-Referer": "https://github.com/sp1d3rx/dendrophis",
            "X-Title": "Dendrophis IDE",
        }
        try:
            response = await self._http.get(url, headers=headers)
            if response.status_code == 200:
                models_data = response.json()
                return [ModelInfo.from_api(model_data) for model_data in models_data.get("data", [])]
        except Exception:
            pass
        return WELL_KNOWN_MODELS

    # -- Provider context ----------------------------------------------------

    def _make_provider_context(self) -> _ProviderContext:
        """Compute all provider-specific flags and the endpoint URL once."""
        base = self._config.base_url.rstrip("/")
        is_local = "127.0.0.1" in base or "localhost" in base
        is_direct_anthropic = "anthropic.com" in base
        is_openrouter = "openrouter.ai" in base
        is_deepinfra = "deepinfra.com" in base

        use_responses_api = False
        if is_openrouter:
            target_families = ("o1", "o3", "gpt-4o", "kimi")
            if any(family in self._config.model.lower() for family in target_families):
                use_responses_api = True

        url = base + ("/responses" if is_openrouter and use_responses_api else "/chat/completions")

        if self._config.thinking_start_mode is not None:
            sse_start_mode = self._config.thinking_start_mode
        else:
            # Fallback to auto-detect based on model name
            model_name_lower = self._config.model.lower()
            is_thinking_model = "thinking" in model_name_lower or "reasoning" in model_name_lower
            sse_start_mode = "thinking" if is_thinking_model else "text"
        tool_mode = self._config.tool_mode
        use_xml_tools = tool_mode == "xml"

        return _ProviderContext(
            is_local=is_local,
            is_direct_anthropic=is_direct_anthropic,
            is_openrouter=is_openrouter,
            is_deepinfra=is_deepinfra,
            use_responses_api=use_responses_api,
            use_xml_tools=use_xml_tools,
            url=url,
            sse_start_mode=sse_start_mode,
        )

    # -- Message sanitization ------------------------------------------------

    def _sanitize_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        is_local: bool = False,
        is_direct_anthropic: bool = False,
        is_openrouter: bool = False,
        is_deepinfra: bool = False,
        use_responses_api: bool = False,
        use_xml_tools: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip provider-incompatible fields and filter tool messages as needed."""
        strip_keys: set[str] = set()
        if use_xml_tools:
            # MLC returns 422/400 if tool_calls appear in history; tool intent
            # is encoded in <tool_call> tags in the content string instead.
            strip_keys.add("tool_calls")
        if not is_direct_anthropic:
            # cache_control is Anthropic-specific; other providers reject it.
            strip_keys.add("cache_control")

        if is_deepinfra:
            # DeepInfra rejects conversations where tool result count != tool call count.
            # Validate each tool-call sequence: keep only complete pairs where the number
            # of following tool results exactly matches the number of tool_calls in the
            # assistant message. Drop orphaned tool results and incomplete sequences.
            validated: list[dict[str, Any]] = []
            msg_idx = 0
            while msg_idx < len(messages):
                msg = messages[msg_idx]
                role = msg.get("role")

                if role == "tool":
                    # Orphaned tool result (no preceding assistant with tool_calls) — drop.
                    msg_idx += 1
                    continue

                if role == "assistant" and (msg.get("tool_calls") or "<tool_call>" in (msg.get("content") or "")):
                    expected = len(msg.get("tool_calls", []))
                    result_end = msg_idx + 1
                    while result_end < len(messages) and messages[result_end].get("role") == "tool":
                        result_end += 1
                    actual = result_end - (msg_idx + 1)
                    if expected > 0 and actual == expected:
                        validated.append({key: value for key, value in msg.items() if key not in strip_keys})
                        validated.extend(
                            {key: value for key, value in messages[tool_idx].items() if key not in strip_keys}
                            for tool_idx in range(msg_idx + 1, result_end)
                        )
                        msg_idx = result_end
                    else:
                        msg_idx = result_end
                        continue
                validated.append({key: value for key, value in msg.items() if key not in strip_keys})
                msg_idx += 1
            return validated

        if not strip_keys:
            return messages
        return [{key: value for key, value in msg.items() if key not in strip_keys} for msg in messages]

    # -- Payload construction ------------------------------------------------

    def _build_payload(
        self,
        provider_context: _ProviderContext,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        enable_cache_control: bool,
        tool_choice: str,
    ) -> dict[str, Any]:
        """Build the full request payload for the given provider context."""
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": self._sanitize_messages(
                messages,
                is_local=provider_context.is_local,
                is_direct_anthropic=provider_context.is_direct_anthropic,
                is_openrouter=provider_context.is_openrouter,
                is_deepinfra=provider_context.is_deepinfra,
                use_responses_api=provider_context.use_responses_api,
                use_xml_tools=provider_context.use_xml_tools,
            ),
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
            "stream": True,
        }

        payload["stream_options"] = {"include_usage": True}

        # Check if reasoning_effort is supported — heuristic OR calibration positive, not rejected
        from dendrophis.llm.calibration import ModelOverrideStore, is_param_rejected

        if self._config.reasoning_effort is not None and not is_param_rejected(self._config.model, "reasoning_effort"):
            _calibrated = ModelOverrideStore().get(self._config.model)
            _heuristic_ok = supports_reasoning_effort_by_id(self._config.model)
            _calibrated_ok = _calibrated is not None and _calibrated.supports_reasoning_effort
            if _heuristic_ok or _calibrated_ok:
                payload["reasoning_effort"] = self._config.reasoning_effort

        # Mistral/Kimi prompt cache support
        # Also check calibration to see if parameter is rejected
        if (
            self._config.prompt_cache_key is not None
            and supports_prompt_cache_key_by_id(self._config.model)
            and not is_param_rejected(self._config.model, "prompt_cache_key")
        ):
            payload["prompt_cache_key"] = self._config.prompt_cache_key

        if self._config.stop:
            payload["stop"] = self._config.stop

        if not provider_context.is_local:
            if self._config.top_k is not None:
                payload["top_k"] = self._config.top_k
            if self._config.min_p is not None:
                payload["min_p"] = self._config.min_p
            if self._config.repetition_penalty is not None:
                payload["repetition_penalty"] = self._config.repetition_penalty
            if self._config.presence_penalty != 0.0:
                payload["presence_penalty"] = self._config.presence_penalty
            if self._config.frequency_penalty != 0.0:
                payload["frequency_penalty"] = self._config.frequency_penalty

        # OpenRouter Responses API: rename keys and enable reasoning
        if provider_context.is_openrouter and provider_context.use_responses_api:
            payload.pop("stream_options", None)
            payload["input"] = self._transform_messages_to_responses_input(payload.pop("messages"))
            if "max_tokens" in payload:
                payload["max_output_tokens"] = payload.pop("max_tokens")
            if any(f in self._config.model.lower() for f in ("o1", "o3", "kimi")):
                payload["include_reasoning"] = True

        # Tool definitions — format differs by provider
        if tools:
            if provider_context.use_xml_tools:
                # MLC doesn't accept tools in the API; inject as XML into system message
                tool_defs = "\n".join(json.dumps(t) for t in tools)
                tool_section = (
                    "\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n"
                    "You are provided with function signatures within <tools></tools> XML tags:\n"
                    f"<tools>\n{tool_defs}\n</tools>"
                )
                payload_messages = payload.get("messages", [])
                injected = False
                for msg_idx, msg in enumerate(payload_messages):
                    if msg.get("role") == "system":
                        payload_messages[msg_idx] = {**msg, "content": msg["content"] + tool_section}
                        injected = True
                        break
                if not injected:
                    payload_messages.insert(0, {"role": "system", "content": tool_section.lstrip()})
                payload["messages"] = payload_messages
            elif provider_context.is_openrouter and provider_context.use_responses_api:
                # Responses API expects flattened tool definitions
                transformed_tools = []
                for tool in tools:
                    if tool.get("type") == "function":
                        tool_function = tool.get("function", {})
                        transformed_tools.append(
                            {
                                "type": "function",
                                "name": tool_function.get("name"),
                                "description": tool_function.get("description"),
                                "parameters": tool_function.get("parameters"),
                                "strict": tool_function.get("strict") or False,
                            }
                        )
                    else:
                        transformed_tools.append(tool)
                payload["tools"] = transformed_tools
                payload["tool_choice"] = tool_choice
            else:
                # Standard OpenAI format; optionally add cache_control for Anthropic
                model_is_claude = "claude" in self._config.model.lower()
                if enable_cache_control and model_is_claude and provider_context.is_direct_anthropic:
                    tools = [{**tool, "cache_control": {"type": "ephemeral"}} for tool in tools]
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice

        return payload

    def _transform_messages_to_responses_input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Transform standard messages to OpenRouter Responses API input format."""
        transformed = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "user")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")

            if role == "tool":
                tool_call_id = msg.get("tool_call_id")
                transformed.append(
                    {
                        "type": "function_call_output",
                        "id": f"result_{i}",
                        "call_id": tool_call_id if tool_call_id else None,
                        "output": content if isinstance(content, str) else json.dumps(content),
                    }
                )
                continue

            if content:
                text_content = ""
                if isinstance(content, str):
                    text_content = content
                elif isinstance(content, list):
                    parts = []
                    for part in content:
                        if isinstance(part, dict):
                            parts.append(part.get("text", part.get("input_text", "")))
                        else:
                            parts.append(str(part))
                    text_content = "\n".join(filter(None, parts))
                if text_content:
                    transformed.append(
                        {
                            "type": "message",
                            "role": role,
                            "content": [{"type": "input_text", "text": text_content}],
                        }
                    )

            if tool_calls:
                for j, tool_call in enumerate(tool_calls):
                    function_data = tool_call.get("function", {})
                    tc_id = tool_call.get("id")
                    # Tool call IDs now use original provider IDs without modification
                    sanitized_id = tc_id if tc_id else f"call_{i}_{j}"
                    sanitized_call_id = tc_id if tc_id else None
                    transformed.append(
                        {
                            "type": "function_call",
                            "id": sanitized_id,
                            "call_id": sanitized_call_id,
                            "name": function_data.get("name"),
                            "arguments": function_data.get("arguments", "{}"),
                        }
                    )

        return transformed

    # -- HTTP + SSE transport ------------------------------------------------

    async def _stream_raw(
        self,
        provider_context: _ProviderContext,
        payload: dict[str, Any],
    ) -> AsyncIterator[StreamEvent | RetryEvent]:
        """Send one HTTP request and stream SSE events, with retry on transient errors."""
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/sp1d3rx/dendrophis",
            "X-Title": "Dendrophis IDE",
            "Connection": "close",
        }

        max_retries = 5
        base_delay = 1.0
        # TODO: Make these configurable via settings
        stream_started = False

        for attempt in range(max_retries + 1):
            error_status_code: int | None = None
            error_details_message = ""
            try:
                _chat_log(
                    "CLIENT [attempt]",
                    f"attempt={attempt}, payload_bytes={len(json.dumps(payload))}",
                )

                # EAFP: Try to build and send request, handle potential failures
                try:
                    req = self._http.build_request(
                        "POST", provider_context.url, headers=headers, content=json.dumps(payload)
                    )
                except Exception as exc:
                    yield ErrorEvent(message=f"Failed to build request: {exc!s}")
                    return

                _chat_log("CLIENT [attempt]", "sending request")
                try:
                    http_response = await asyncio.wait_for(
                        self._http.send(req, stream=True), timeout=self._config.timeout
                    )
                except TimeoutError:
                    _chat_log("CLIENT [attempt]", f"send() timed out after {self._config.timeout}s")
                    await self._http.aclose()
                    self._http = httpx.AsyncClient(
                        timeout=httpx.Timeout(connect=10.0, write=30.0, read=self._config.timeout, pool=5.0)
                    )
                    yield ErrorEvent(
                        message=f"Server did not respond within {self._config.timeout}s — context may be too large"
                    )
                    return

                _chat_log("CLIENT [attempt]", f"got response status={http_response.status_code}")
                try:
                    # EAFP: Handle rate limiting and server errors with retry
                    if http_response.status_code in (429, 503) and attempt < max_retries:
                        delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
                        yield RetryEvent(
                            message=f"Server busy (HTTP {http_response.status_code})", attempt=attempt + 1, delay=delay
                        )
                        await asyncio.sleep(delay)
                        continue

                    if http_response.status_code != 200:
                        error_status_code = http_response.status_code
                        _chat_log("CLIENT [non-200]", f"status={http_response.status_code}")
                        try:
                            error_response_body = await http_response.aread()
                            # Parse JSON or raw text
                            error_details_message = ""
                            try:
                                parsed_error_payload = json.loads(error_response_body)
                                if isinstance(parsed_error_payload, dict):
                                    if "error" in parsed_error_payload and isinstance(
                                        parsed_error_payload["error"], dict
                                    ):
                                        error_details_message = parsed_error_payload["error"].get("message", "")
                                    elif "detail" in parsed_error_payload:
                                        error_details_message = str(parsed_error_payload["detail"])
                            except Exception as json_error:
                                _chat_log("CLIENT [non-200]", f"JSON parse failed: {json_error}")

                            if not error_details_message:
                                try:
                                    error_details_message = error_response_body.decode(
                                        "utf-8", errors="replace"
                                    ).strip()
                                except Exception as decode_error:
                                    _chat_log("CLIENT [non-200]", f"decode failed: {decode_error}")
                        except Exception as read_error:
                            _chat_log("CLIENT [non-200]", f"failed to read error body: {read_error}")

                        try:
                            await asyncio.wait_for(http_response.aclose(), timeout=2.0)
                        except Exception as close_exception:
                            _chat_log("CLIENT [non-200]", f"aclose failed: {close_exception}")
                    else:
                        in_progress: dict[int, ToolCall] = {}
                        parsing_state = {"mode": provider_context.sse_start_mode, "buffer": "", "pending": ""}
                        max_stop_len = max(len(s) for s in self._config.stop) if self._config.stop else 0
                        stream_buffer = ""

                        # EAFP: Handle SSE streaming with robust error handling
                        try:
                            async for sse in EventSource(http_response).aiter_sse():
                                stream_started = True
                                _chat_log("SERVER → CLIENT", f"event={sse.event!r} data={sse.data!r}")

                                # EAFP: Handle potential parsing errors gracefully
                                try:
                                    events, in_progress, parsing_state = parse_sse_event(
                                        sse, in_progress, parsing_state
                                    )
                                except Exception as parse_error:
                                    yield ErrorEvent(message=f"Failed to parse SSE event: {parse_error!s}")
                                    continue

                                for event in events:
                                    if self._config.stop and isinstance(event, (TextDeltaEvent, ReasoningDeltaEvent)):
                                        stream_buffer += event.delta
                                        if len(stream_buffer) > max_stop_len * 3:
                                            stream_buffer = stream_buffer[-(max_stop_len * 3) :]
                                        if any(s in stream_buffer for s in self._config.stop):
                                            yield DoneEvent(finish_reason="stop")
                                            return

                                    yield event
                            if not stream_started:
                                yield ErrorEvent(message="Server returned an empty response")
                                return
                            return
                        except Exception as sse_error:
                            yield ErrorEvent(message=f"SSE streaming failed: {sse_error!s}")
                            return
                finally:
                    # EAFP: Ensure response is always closed, even if closing fails
                    try:
                        await http_response.aclose()
                    except Exception as close_error:
                        _chat_log("CLIENT [cleanup]", f"Failed to close response: {close_error}")

                if error_status_code is not None:
                    if error_details_message:
                        yield ErrorEvent(
                            message=f"HTTP {error_status_code} from {provider_context.url} - {error_details_message}"
                        )
                    else:
                        yield ErrorEvent(message=f"HTTP {error_status_code} from {provider_context.url}")
                    return

            except httpx.WriteTimeout:
                yield ErrorEvent(message="Request write timed out — context may be too large for this server")
                return
            except httpx.TimeoutException:
                if attempt < max_retries and not stream_started:
                    delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
                    yield RetryEvent(message="Request timed out", attempt=attempt + 1, delay=delay)
                    await asyncio.sleep(delay)
                    continue
                yield ErrorEvent(message="Request timed out")
                return
            except httpx.RequestError as exc:
                if attempt < max_retries and not stream_started:
                    delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
                    yield RetryEvent(message=f"Connection error: {exc}", attempt=attempt + 1, delay=delay)
                    await asyncio.sleep(delay)
                    continue
                yield ErrorEvent(message=f"Connection error: {exc}")
                return
            except Exception as unexpected_error:
                # EAFP: Catch any unexpected errors and provide meaningful feedback
                yield ErrorEvent(message=f"Unexpected error during request: {unexpected_error!s}")
                return

    # -- Public API ----------------------------------------------------------

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        enable_cache_control: bool = True,
        tool_choice: str = "auto",
    ) -> AsyncIterator[StreamEvent | RetryEvent | TurnResult]:
        """Stream a chat turn, yielding UI events then a final TurnResult.

        Handles all provider quirks internally:
        - Provider-specific payload construction
        - MLC false-positive finish_reason="tool_calls" retry
        - Text-based tool call parsing for local servers
        """
        # EAFP: Validate inputs before processing
        if not messages:
            yield ErrorEvent(message="No messages provided for chat completion")
            return

        try:
            provider_context = self._make_provider_context()
        except Exception as exc:
            yield ErrorEvent(message=f"Failed to create provider context: {exc!s}")
            return

        false_positive_retry_done = False
        current_tool_choice = tool_choice
        max_retries = 3  # Limit retries for the overall chat operation
        retry_count = 0

        while True:
            # EAFP: Handle potential payload construction errors
            try:
                payload = self._build_payload(
                    provider_context, messages, tools, enable_cache_control, current_tool_choice
                )
                _chat_log("CLIENT → SERVER", json.dumps(payload, indent=2))
            except Exception as payload_error:
                yield ErrorEvent(message=f"Failed to build request payload: {payload_error!s}")
                return

            turn_accumulator = _TurnAccumulator()

            # EAFP: Stream events with robust error handling
            try:
                async for event in self._stream_raw(provider_context, payload):
                    try:
                        turn_accumulator.update(event)
                    except Exception as update_error:
                        yield ErrorEvent(message=f"Failed to update turn accumulator: {update_error!s}")
                        continue

                    if isinstance(event, ErrorEvent):
                        yield event
                        return
                    yield event

            except Exception as stream_error:
                retry_count += 1
                if retry_count <= max_retries:
                    delay = 1.0 * (2**retry_count) + random.uniform(0, 0.2)
                    yield RetryEvent(
                        message=f"Chat streaming failed: {stream_error!s}", attempt=retry_count, delay=delay
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    yield ErrorEvent(message=f"Chat streaming failed after {max_retries} retries: {stream_error!s}")
                    return

            # EAFP: Handle tool call resolution robustly
            try:
                tool_calls = turn_accumulator.resolve_tool_calls()
            except Exception as resolve_error:
                yield ErrorEvent(message=f"Failed to resolve tool calls: {resolve_error!s}")
                return

            # MLC false-positive: model was mid-<think> when it fired finish_reason=tool_calls
            # Retry once with tool_choice="none" to get a direct text response.
            if turn_accumulator.is_false_positive(tool_calls) and not false_positive_retry_done:
                false_positive_retry_done = True
                current_tool_choice = "none"
                continue

            # EAFP: Validate final result before yielding
            try:
                turn_result = TurnResult(
                    text="".join(turn_accumulator.text) if turn_accumulator.text else "",
                    reasoning="".join(turn_accumulator.reasoning) if turn_accumulator.reasoning else "",
                    tool_calls=tool_calls if tool_calls else [],
                    finish_reason=turn_accumulator.finish_reason if turn_accumulator.finish_reason else "stop",
                )
                yield turn_result
                return
            except Exception as result_error:
                yield ErrorEvent(message=f"Failed to create turn result: {result_error!s}")
                return

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> TurnResult:
        """Non-streaming completion - aggregates all events into a single TurnResult."""
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        async for event in self.stream_chat(messages, tools):
            if isinstance(event, TextDeltaEvent):
                text_parts.append(event.delta)
            elif isinstance(event, ReasoningDeltaEvent):
                reasoning_parts.append(event.delta)
            elif isinstance(event, ToolCallStartEvent):
                tool_calls.append(event.tool_call)
            elif isinstance(event, TurnResult):
                return event
            elif isinstance(event, ErrorEvent):
                raise RuntimeError(event.message)

        # If we get here without a TurnResult, construct one from accumulated parts
        return TurnResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            finish_reason="stop",
        )
