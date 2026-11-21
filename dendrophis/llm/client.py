"""Async LLM client — OpenAI-compatible streaming via httpx."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import json
import logging
import os
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
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
    TurnResult,
    UsageEvent,
)
from dendrophis.llm.calibration import ModelOverrideStore, is_param_rejected
from dendrophis.llm.models import (
    supports_caching_by_id,
    supports_prompt_cache_key_by_id,
    supports_reasoning_effort_by_id,
    supports_tools_by_id,
)
from dendrophis.llm.stream import parse_sse_event, parse_text_tool_calls
from dendrophis.llm.visual_prompt import is_vlm_model, render_text_to_1bit_data_uri

_LOG_PATH = os.environ.get("DENDROPHIS_CHAT_LOG", "")
_MAX_ANTHROPIC_CACHE_BLOCKS = 4

logger = logging.getLogger(__name__)


def _chat_log(direction: str, payload_text: str) -> None:
    if not _LOG_PATH:
        return
    timestamp_str = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    with open(_LOG_PATH, "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp_str}] {direction}\n{payload_text}\n{'─' * 80}\n")


def _has_non_text_blocks(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") != "text" for block in content)


def _content_to_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for item_block in content:
            if isinstance(item_block, dict):
                blocks.append(item_block)
            elif item_block:
                blocks.append({"type": "text", "text": str(item_block)})
        return blocks
    if isinstance(content, str) and content:
        return [{"type": "text", "text": content}]
    if content is not None and str(content):
        return [{"type": "text", "text": str(content)}]
    return []


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        content_parts = []
        for block_item in content:
            if isinstance(block_item, dict):
                content_parts.append(block_item.get("text", ""))
            else:
                content_parts.append(str(block_item))
        return "\n".join(filter(None, content_parts))
    if content is not None:
        return str(content)
    return ""


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
    use_xml_tools: bool
    url: str
    sse_start_mode: str


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
        """Return structured tool calls, falling back to text-parsed ones."""
        if self.tool_calls:
            structured = [tool_call for tool_call in self.tool_calls if tool_call.index != 999]
            if structured:
                return structured
            seen: set[tuple[str, str]] = set()
            deduped = []
            for tool_call in self.tool_calls:
                key = (tool_call.name, tool_call.arguments)
                if key not in seen:
                    seen.add(key)
                    deduped.append(tool_call)
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

    def _pricing(self) -> dict[str, Any]:
        """Return the raw pricing dict from the /models endpoint (USD per token)."""
        metadata = self.extra.get("metadata") or {}
        pricing = metadata.get("pricing") or self.extra.get("pricing") or {}
        return pricing if isinstance(pricing, dict) else {}

    @staticmethod
    def _per_token(pricing: dict[str, Any], *keys: str) -> float:
        """Return the first positive numeric price among keys (USD per token)."""
        for key in keys:
            raw = pricing.get(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (ValueError, TypeError):
                continue
            if value > 0:
                return value
        return 0.0

    @property
    def input_cost_per_token(self) -> float:
        """USD per uncached input/prompt token."""
        return self._per_token(self._pricing(), "prompt", "input", "input_tokens")

    @property
    def output_cost_per_token(self) -> float:
        """USD per output/completion token."""
        return self._per_token(self._pricing(), "completion", "output", "output_tokens")

    @property
    def cache_read_cost_per_token(self) -> float:
        """USD per cached (cache-read) input token. Falls back to the input price."""
        cache_read = self._per_token(self._pricing(), "input_cache_read", "cache_read", "cached")
        return cache_read if cache_read > 0 else self.input_cost_per_token

    @property
    def cost_per_1k(self) -> float:
        """Blended USD per 1k tokens (average of input/output), for display/back-compat."""
        input_val = self.input_cost_per_token
        output_val = self.output_cost_per_token
        if input_val <= 0 and output_val <= 0:
            return 0.0
        avg_per_token = (input_val + output_val) / 2
        return avg_per_token * 1000

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
        if any(feature in supported_features for feature in ("tools", "tool_use", "function_calling")):
            return True
        return any(param_name in supported_params for param_name in ("tools", "tool_choice"))

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
            or data.get("max_model_len")
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
        self.last_visual_tokens_saved: int = 0
        self._visual_counted: set[str] = set()
        self._http = http_client or self._new_http_client()
        self._http_inflight: dict[int, int] = {}
        self._retired_http: list[httpx.AsyncClient] = []

    def _new_http_client(self) -> httpx.AsyncClient:
        """Create a fresh httpx client with the standard Dendrophis timeouts."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                write=30.0,
                read=self._config.timeout,
                pool=5.0,
            )
        )

    def _acquire_http(self) -> httpx.AsyncClient:
        """Return the current http client and mark one in-flight use on it."""
        client = self._http
        self._http_inflight[id(client)] = self._http_inflight.get(id(client), 0) + 1
        return client

    def _release_http(self, client: httpx.AsyncClient) -> list[httpx.AsyncClient]:
        """Mark one in-flight use as done and return retired clients ready for close."""
        key = id(client)
        remaining = self._http_inflight.get(key, 1) - 1
        if remaining > 0:
            self._http_inflight[key] = remaining
            return []
        self._http_inflight.pop(key, None)
        if client in self._retired_http:
            self._retired_http.remove(client)
            return [client]
        return []

    async def _close_retired_http(self, clients: list[httpx.AsyncClient]) -> None:
        for client in clients:
            with contextlib.suppress(Exception):
                await client.aclose()

    def _retire_http(self, old: httpx.AsyncClient) -> None:
        """Swap in a fresh client and retire ``old`` for deferred close."""
        self._http = self._new_http_client()
        self._retired_http.append(old)

    async def aclose(self) -> None:
        await self._close_retired_http(self._retired_http)
        self._retired_http.clear()
        await self._http.aclose()

    async def fetch_models(self) -> list[ModelInfo]:
        url = self._config.base_url.rstrip("/") + "/models"
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "HTTP-Referer": "https://github.com/sp1d3rx/dendrophis",
            "X-Title": "Dendrophis IDE",
        }
        client = self._acquire_http()
        try:
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    models_data = response.json()
                    return [ModelInfo.from_api(model_data) for model_data in models_data.get("data", [])]
            except Exception as exc:
                logger.debug("Failed to fetch models from %s, using built-in list: %s", url, exc)
            return list(WELL_KNOWN_MODELS)
        finally:
            await self._close_retired_http(self._release_http(client))

    # -- Provider context ----------------------------------------------------

    def _make_provider_context(self) -> _ProviderContext:
        """Compute all provider-specific flags and the endpoint URL once."""
        base = self._config.base_url.rstrip("/")
        is_local = "127.0.0.1" in base or "localhost" in base
        is_direct_anthropic = "anthropic.com" in base
        is_openrouter = "openrouter.ai" in base
        is_deepinfra = "deepinfra.com" in base

        if self._config.use_responses_api is not None:
            use_responses_api = bool(self._config.use_responses_api) and is_openrouter
        elif is_openrouter:
            target_families = ("o1", "o3", "o4", "gpt-4o", "gpt-5", "kimi")
            use_responses_api = any(family in self._config.model.lower() for family in target_families)
        else:
            use_responses_api = False

        url = base + ("/responses" if is_openrouter and use_responses_api else "/chat/completions")

        if self._config.thinking_start_mode is not None:
            sse_start_mode = self._config.thinking_start_mode
        else:
            model_name_lower = self._config.model.lower()
            is_thinking_model = "thinking" in model_name_lower or "reasoning" in model_name_lower
            sse_start_mode = "thinking" if is_thinking_model else "text"

        tool_mode = self._config.tool_mode
        if tool_mode == "auto":
            use_xml_tools = not supports_tools_by_id(self._config.model) if is_local else False
        else:
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

    def _coalesce_consecutive_messages(self, messages_to_coalesce: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge consecutive messages of the same role into a single message."""
        coalesced_messages: list[dict[str, Any]] = []
        for message_dict in messages_to_coalesce:
            if not message_dict:
                continue
            role = message_dict.get("role")
            content = message_dict.get("content")
            current_has_media = _has_non_text_blocks(content)

            if role != "tool" and coalesced_messages and coalesced_messages[-1].get("role") == role:
                previous_message = coalesced_messages[-1]
                previous_content = previous_message.get("content")
                previous_has_media = _has_non_text_blocks(previous_content)

                if current_has_media or previous_has_media:
                    previous_message["content"] = _content_to_blocks(previous_content) + _content_to_blocks(content)
                else:
                    previous_text = _content_to_text(previous_content)
                    current_text = _content_to_text(content)
                    previous_message["content"] = (previous_text + "\n\n" + current_text).strip()

                if "tool_calls" in message_dict and isinstance(message_dict["tool_calls"], list):
                    existing_calls = previous_message.setdefault("tool_calls", [])
                    existing_ids = {
                        call_item.get("id")
                        for call_item in existing_calls
                        if isinstance(call_item, dict) and call_item.get("id")
                    }
                    existing_signatures = {
                        (
                            call_item.get("function", {}).get("name"),
                            call_item.get("function", {}).get("arguments"),
                        )
                        for call_item in existing_calls
                        if isinstance(call_item, dict) and isinstance(call_item.get("function"), dict)
                    }
                    for incoming_call in message_dict["tool_calls"]:
                        if not isinstance(incoming_call, dict):
                            continue
                        call_id_val = incoming_call.get("id")
                        function_data = (
                            incoming_call.get("function", {}) if isinstance(incoming_call.get("function"), dict) else {}
                        )
                        signature_tuple = (function_data.get("name"), function_data.get("arguments"))

                        if call_id_val and call_id_val in existing_ids:
                            continue
                        if signature_tuple in existing_signatures:
                            continue

                        existing_calls.append(incoming_call)
                        if call_id_val:
                            existing_ids.add(call_id_val)
                        existing_signatures.add(signature_tuple)
            else:
                new_message = {key: value for key, value in message_dict.items() if key != "content"}
                new_message["content"] = list(content) if current_has_media else _content_to_text(content)
                coalesced_messages.append(new_message)

        return coalesced_messages

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
        last_user_index = -1
        for index, msg in enumerate(messages):
            if msg.get("role") == "user":
                last_user_index = index

        pre_sanitized: list[dict[str, Any]] = []
        mode = self._config.preserve_reasoning
        for index, msg in enumerate(messages):
            if msg.get("role") == "assistant":
                if mode == "never" or (mode == "current" and index < last_user_index):
                    msg_copy = {key: value for key, value in msg.items() if key != "reasoning_content"}
                else:
                    msg_copy = dict(msg)
            else:
                msg_copy = dict(msg)
            pre_sanitized.append(msg_copy)

        strip_keys: set[str] = set()
        if use_xml_tools:
            strip_keys.add("tool_calls")

        keeps_cache_control = is_direct_anthropic or is_openrouter
        if not keeps_cache_control or is_param_rejected(self._config.model, "cache_control"):
            strip_keys.add("cache_control")

        if is_deepinfra:
            validated: list[dict[str, Any]] = []
            msg_idx = 0
            while msg_idx < len(pre_sanitized):
                msg = pre_sanitized[msg_idx]
                role = msg.get("role")

                if role == "tool":
                    msg_idx += 1
                    continue

                raw_text = msg.get("content") if isinstance(msg.get("content"), str) else ""
                has_structured_calls = bool(msg.get("tool_calls"))
                has_embedded_tag = "<tool_call>" in raw_text
                if role == "assistant" and (has_structured_calls or has_embedded_tag):
                    expected = len(msg.get("tool_calls", []))
                    result_end = msg_idx + 1
                    while result_end < len(pre_sanitized) and pre_sanitized[result_end].get("role") == "tool":
                        result_end += 1
                    actual = result_end - (msg_idx + 1)

                    if has_structured_calls and expected > 0 and actual == expected:
                        validated.append({key: value for key, value in msg.items() if key not in strip_keys})
                        validated.extend(
                            {key: value for key, value in pre_sanitized[tool_idx].items() if key not in strip_keys}
                            for tool_idx in range(msg_idx + 1, result_end)
                        )
                        msg_idx = result_end
                        continue
                    if not has_structured_calls and actual == 0 and not raw_text.lstrip().startswith("<tool_call>"):
                        # Plain prose mentioning `<tool_call>` without any tool execution — preserve it.
                        validated.append({key: value for key, value in msg.items() if key not in strip_keys})
                        msg_idx += 1
                        continue
                    msg_idx = result_end
                    continue

                validated.append({key: value for key, value in msg.items() if key not in strip_keys})
                msg_idx += 1
            return self._coalesce_consecutive_messages(validated)

        if not strip_keys:
            coalesced = self._coalesce_consecutive_messages(pre_sanitized)
        else:
            sanitized = [
                {key: value for key, value in message_dict.items() if key not in strip_keys}
                for message_dict in pre_sanitized
            ]
            coalesced = self._coalesce_consecutive_messages(sanitized)

        if use_xml_tools:
            return coalesced
        return self._repair_tool_call_sequences(coalesced)

    @staticmethod
    def _repair_tool_call_sequences(messages_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ensure every assistant tool_call has a paired tool result and drop stray tool messages."""
        repaired_messages: list[dict[str, Any]] = []
        message_index = 0
        total_messages = len(messages_list)

        while message_index < total_messages:
            current_message = messages_list[message_index]
            role_name = current_message.get("role")

            if role_name == "tool":
                message_index += 1
                continue

            tool_calls = current_message.get("tool_calls")
            if role_name == "assistant" and isinstance(tool_calls, list) and tool_calls:
                repaired_messages.append(current_message)
                following_end = message_index + 1
                while following_end < total_messages and messages_list[following_end].get("role") == "tool":
                    following_end += 1

                results_by_call_id: dict[str, dict[str, Any]] = {}
                unmatched_tool_messages: list[dict[str, Any]] = []
                for tool_index in range(message_index + 1, following_end):
                    tool_message = messages_list[tool_index]
                    call_identifier = tool_message.get("tool_call_id")
                    if (
                        isinstance(call_identifier, str)
                        and call_identifier
                        and call_identifier not in results_by_call_id
                    ):
                        results_by_call_id[call_identifier] = tool_message
                    else:
                        unmatched_tool_messages.append(tool_message)

                for call_entry in tool_calls:
                    if not isinstance(call_entry, dict):
                        continue
                    expected_id = call_entry.get("id")
                    function_spec = call_entry.get("function") if isinstance(call_entry.get("function"), dict) else {}
                    function_name = function_spec.get("name") or "unknown"
                    if isinstance(expected_id, str) and expected_id in results_by_call_id:
                        repaired_messages.append(results_by_call_id.pop(expected_id))
                    elif unmatched_tool_messages:
                        adopted_message = dict(unmatched_tool_messages.pop(0))
                        if expected_id:
                            adopted_message["tool_call_id"] = expected_id
                        if not adopted_message.get("name"):
                            adopted_message["name"] = function_name
                        repaired_messages.append(adopted_message)
                    else:
                        repaired_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": expected_id or "",
                                "name": function_name,
                                "content": json.dumps({"status": "missing_tool_result"}),
                            }
                        )
                message_index = following_end
                continue

            repaired_messages.append(current_message)
            message_index += 1

        return repaired_messages

    def _count_visual_savings(self, content: str) -> int:
        """Estimate the tokens saved by rendering content as a 1-bit image."""
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest in self._visual_counted:
            return 0
        if len(self._visual_counted) > 2048:
            self._visual_counted.clear()
        self._visual_counted.add(digest)
        estimated_text_tokens = int(len(content) / 3.5)
        return max(0, estimated_text_tokens - 310)

    def _apply_visual_vlm_transformations(
        self,
        messages_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Transform text system prompt, compaction, or large tool outputs to 1-bit images for VLMs."""
        self.last_visual_tokens_saved = 0

        if not is_vlm_model(self._config.model):
            return messages_list

        transformed_messages: list[dict[str, Any]] = []
        pending_tool_images: list[dict[str, Any]] = []

        def _flush_pending_tool_images() -> None:
            if not pending_tool_images:
                return
            transformed_messages.extend(pending_tool_images)
            pending_tool_images.clear()

        for message_item in messages_list:
            role_name = message_item.get("role", "")
            content_data = message_item.get("content", "")

            if role_name != "tool":
                _flush_pending_tool_images()

            # 1. Visual System Prompt
            if role_name == "system" and self._config.visual_system_prompt and isinstance(content_data, str):
                data_uri = render_text_to_1bit_data_uri(
                    content_data, font_name="Menlo.ttc", font_size=16, save_prefix="visual_system_prompt"
                )
                if data_uri:
                    self.last_visual_tokens_saved += self._count_visual_savings(content_data)
                    transformed_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "System Instructions & Tool Declarations (8-bit Grayscale visual image):",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {"url": data_uri},
                                },
                            ],
                        }
                    )
                    continue

            # 2. Visual Tool Results — emit all `role: tool` stubs first, then attach `role: user` images
            # after the contiguous tool-result batch so parallel tool calls stay syntactically valid.
            if (
                role_name == "tool"
                and self._config.visual_tool_results
                and isinstance(content_data, str)
                and len(content_data) >= self._config.visual_threshold_chars
            ):
                data_uri = render_text_to_1bit_data_uri(
                    content_data, font_name="Menlo.ttc", font_size=16, save_prefix="visual_tool_result"
                )
                if data_uri:
                    self.last_visual_tokens_saved += self._count_visual_savings(content_data)
                    tool_call_id = message_item.get("tool_call_id", "")

                    tool_msg: dict[str, Any] = {
                        "role": "tool",
                        "content": (
                            f"[Tool result complete ({len(content_data)} chars). "
                            "Output image attached in user payload below.]"
                        ),
                    }
                    if tool_call_id:
                        tool_msg["tool_call_id"] = tool_call_id
                    if message_item.get("name"):
                        tool_msg["name"] = message_item["name"]

                    transformed_messages.append(tool_msg)
                    pending_tool_images.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"[Tool Execution Result Image for call {tool_call_id}]:",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {"url": data_uri},
                                },
                            ],
                        }
                    )
                    continue

            # 3. Visual Compaction (Summary)
            if (
                self._config.visual_compaction
                and isinstance(content_data, str)
                and content_data.startswith(("[Context summary", "[Compacted Memory", "[Summary of earlier"))
            ):
                data_uri = render_text_to_1bit_data_uri(
                    content_data, font_name="Menlo.ttc", font_size=16, save_prefix="visual_compaction"
                )
                if data_uri:
                    self.last_visual_tokens_saved += self._count_visual_savings(content_data)
                    transformed_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Compacted Past Conversation Snapshot (8-bit Grayscale visual PNG):",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {"url": data_uri},
                                },
                            ],
                        }
                    )
                    continue

            # 4. Visual User Prompts / Error Logs
            if (
                role_name == "user"
                and self._config.visual_user_prompts
                and isinstance(content_data, str)
                and len(content_data) >= self._config.visual_threshold_chars
            ):
                data_uri = render_text_to_1bit_data_uri(
                    content_data, font_name="Menlo.ttc", font_size=16, save_prefix="visual_user_prompt"
                )
                if data_uri:
                    self.last_visual_tokens_saved += self._count_visual_savings(content_data)
                    transformed_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"User Prompt ({len(content_data)} chars, 8-bit Grayscale visual PNG):",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {"url": data_uri},
                                },
                            ],
                        }
                    )
                    continue

            transformed_messages.append(message_item)

        _flush_pending_tool_images()
        return transformed_messages

    @staticmethod
    def _cap_cache_control_markers(
        messages: list[dict[str, Any]],
        max_blocks: int,
    ) -> list[dict[str, Any]]:
        """Keep at most `max_blocks` `cache_control` markers on messages (preserving earliest + latest)."""
        if max_blocks <= 0:
            return [{key: val for key, val in msg.items() if key != "cache_control"} for msg in messages]
        marked_indices = [idx for idx, msg in enumerate(messages) if "cache_control" in msg]
        if len(marked_indices) <= max_blocks:
            return messages
        keep_set = {marked_indices[0], *marked_indices[-(max_blocks - 1) :]} if max_blocks > 1 else {marked_indices[0]}
        capped: list[dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            if "cache_control" in msg and idx not in keep_set:
                capped.append({key: val for key, val in msg.items() if key != "cache_control"})
            else:
                capped.append(msg)
        return capped

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
        sanitized_messages = self._sanitize_messages(
            messages,
            is_local=provider_context.is_local,
            is_direct_anthropic=provider_context.is_direct_anthropic,
            is_openrouter=provider_context.is_openrouter,
            is_deepinfra=provider_context.is_deepinfra,
            use_responses_api=provider_context.use_responses_api,
            use_xml_tools=provider_context.use_xml_tools,
        )

        transformed_messages = self._apply_visual_vlm_transformations(sanitized_messages)

        model_is_claude = "claude" in self._config.model.lower()
        override_store = ModelOverrideStore()
        cache_control_rejected = is_param_rejected(self._config.model, "cache_control", store=override_store)

        attach_tool_cache_breakpoint = (
            bool(tools)
            and enable_cache_control
            and model_is_claude
            and provider_context.is_direct_anthropic
            and not cache_control_rejected
        )
        if model_is_claude:
            max_msg_cache_blocks = _MAX_ANTHROPIC_CACHE_BLOCKS - (1 if attach_tool_cache_breakpoint else 0)
            transformed_messages = self._cap_cache_control_markers(transformed_messages, max_msg_cache_blocks)

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": transformed_messages,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if self._config.reasoning_effort is not None and not is_param_rejected(
            self._config.model, "reasoning_effort", store=override_store
        ):
            calibrated = override_store.get(self._config.model)
            heuristic_ok = supports_reasoning_effort_by_id(self._config.model)
            calibrated_ok = calibrated is not None and calibrated.supports_reasoning_effort
            if heuristic_ok or calibrated_ok:
                payload["reasoning_effort"] = self._config.reasoning_effort

        if (
            self._config.prompt_cache_key is not None
            and supports_prompt_cache_key_by_id(self._config.model)
            and not is_param_rejected(self._config.model, "prompt_cache_key", store=override_store)
        ):
            payload["prompt_cache_key"] = self._config.prompt_cache_key

        if self._config.stop:
            payload["stop"] = self._config.stop

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

        if provider_context.is_openrouter and provider_context.use_responses_api:
            payload.pop("stream_options", None)
            payload["input"] = self._transform_messages_to_responses_input(payload.pop("messages"))
            if "max_tokens" in payload:
                payload["max_output_tokens"] = payload.pop("max_tokens")
            if supports_reasoning_effort_by_id(self._config.model):
                payload["include_reasoning"] = True

        if tools:
            if provider_context.use_xml_tools:
                tool_defs = "\n".join(json.dumps(tool_schema) for tool_schema in tools)
                tool_section = (
                    "\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n"
                    "You are provided with function signatures within <tools></tools> XML tags:\n"
                    f"<tools>\n{tool_defs}\n</tools>\n\n"
                    "To call a function, you must output a tool call using the following format:\n"
                    "<tool_call>\n"
                    '{"name": "function_name", "arguments": {"arg1": "value1"}}\n'
                    "</tool_call>"
                )
                payload_messages = payload.get("messages", [])
                injected = False
                for msg_idx, msg in enumerate(payload_messages):
                    if msg.get("role") == "system" and isinstance(msg.get("content"), str):
                        payload_messages[msg_idx] = {**msg, "content": msg["content"] + tool_section}
                        injected = True
                        break
                if not injected:
                    payload_messages.insert(0, {"role": "system", "content": tool_section.lstrip()})
                payload["messages"] = payload_messages
            elif provider_context.is_openrouter and provider_context.use_responses_api:
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
                if attach_tool_cache_breakpoint:
                    # Place a single cache breakpoint on the final tool definition (Anthropic max is 4 total).
                    tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice

        return payload

    def _transform_messages_to_responses_input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Transform standard messages to OpenRouter Responses API input format."""
        transformed = []
        for msg_index, msg in enumerate(messages):
            role = msg.get("role", "user")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")

            if role == "tool":
                tool_call_id = msg.get("tool_call_id")
                transformed.append(
                    {
                        "type": "function_call_output",
                        "id": f"result_{msg_index}",
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
                for call_index, tool_call in enumerate(tool_calls):
                    function_data = tool_call.get("function", {})
                    tc_id = tool_call.get("id")
                    sanitized_id = tc_id if tc_id else f"call_{msg_index}_{call_index}"
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
        client = self._acquire_http()
        try:
            async for event in self._stream_raw_on_client(provider_context, payload, client):
                yield event
        finally:
            await self._close_retired_http(self._release_http(client))

    async def _stream_raw_on_client(
        self,
        provider_context: _ProviderContext,
        payload: dict[str, Any],
        client: httpx.AsyncClient,
    ) -> AsyncIterator[StreamEvent | RetryEvent]:
        """Stream one request on a specific http client instance (see _stream_raw for lifecycle)."""
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/sp1d3rx/dendrophis",
            "X-Title": "Dendrophis IDE",
        }

        try:
            serialized_payload = json.dumps(payload)
        except Exception as serialize_error:
            yield ErrorEvent(message=f"Failed to serialize request payload: {serialize_error!s}")
            return

        max_retries = 5
        base_delay = 1.0
        stream_started = False

        for attempt in range(max_retries + 1):
            error_status_code: int | None = None
            error_details_message = ""
            try:
                if _LOG_PATH:
                    _chat_log(
                        "CLIENT [attempt]",
                        f"attempt={attempt}, payload_bytes={len(serialized_payload)}",
                    )

                try:
                    req = client.build_request(
                        "POST", provider_context.url, headers=headers, content=serialized_payload
                    )
                except Exception as exc:
                    yield ErrorEvent(message=f"Failed to build request: {exc!s}")
                    return

                _chat_log("CLIENT [attempt]", "sending request")
                try:
                    http_response = await asyncio.wait_for(client.send(req, stream=True), timeout=self._config.timeout)
                except TimeoutError:
                    _chat_log("CLIENT [attempt]", f"send() timed out after {self._config.timeout}s")
                    self._retire_http(client)
                    yield ErrorEvent(
                        message=f"Server did not respond within {self._config.timeout}s — context may be too large"
                    )
                    return

                _chat_log("CLIENT [attempt]", f"got response status={http_response.status_code}")
                try:
                    if http_response.status_code in (429, 503) and attempt < max_retries:
                        with contextlib.suppress(Exception):
                            await http_response.aclose()
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
                        max_stop_len = max(len(stop_seq) for stop_seq in self._config.stop) if self._config.stop else 0
                        stream_buffer = ""

                        try:
                            async for sse in EventSource(http_response).aiter_sse():
                                stream_started = True
                                if _LOG_PATH:
                                    _chat_log("SERVER → CLIENT", f"event={sse.event!r} data={sse.data!r}")

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
                                        if any(stop_seq in stream_buffer for stop_seq in self._config.stop):
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
        """Stream a chat turn, yielding UI events then a final TurnResult."""
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
        max_retries = 3
        retry_count = 0

        while True:
            try:
                payload = self._build_payload(
                    provider_context, messages, tools, enable_cache_control, current_tool_choice
                )
                if _LOG_PATH:
                    _chat_log("CLIENT → SERVER", json.dumps(payload, indent=2))
                visual_tokens_saved = self.last_visual_tokens_saved
            except Exception as payload_error:
                yield ErrorEvent(message=f"Failed to build request payload: {payload_error!s}")
                return

            turn_accumulator = _TurnAccumulator()

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
                    if isinstance(event, UsageEvent) and visual_tokens_saved:
                        event = replace(event, visual_tokens_saved=visual_tokens_saved)
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
                yield ErrorEvent(message=f"Chat streaming failed after {max_retries} retries: {stream_error!s}")
                return

            try:
                tool_calls = turn_accumulator.resolve_tool_calls()
            except Exception as resolve_error:
                yield ErrorEvent(message=f"Failed to resolve tool calls: {resolve_error!s}")
                return

            if turn_accumulator.is_false_positive(tool_calls) and not false_positive_retry_done:
                false_positive_retry_done = True
                current_tool_choice = "none"
                continue

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
            elif isinstance(event, ToolCallDoneEvent):
                tool_calls.append(event.tool_call)
            elif isinstance(event, TurnResult):
                return event
            elif isinstance(event, ErrorEvent):
                raise RuntimeError(event.message)

        return TurnResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            finish_reason="stop",
        )
