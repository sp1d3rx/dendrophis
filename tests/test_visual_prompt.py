"""Unit tests for Visual VLM feature settings, calibration, and payload transformation."""

from __future__ import annotations

import pytest

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.events import DoneEvent, TextDeltaEvent, TurnResult, UsageEvent
from dendrophis.llm.calibration import ModelCapabilities
from dendrophis.llm.client import LLMClient
from dendrophis.llm.visual_prompt import is_vlm_model, render_text_to_1bit_data_uri
from dendrophis.session.chat import ChatOrchestrator, SessionStats


def test_vlm_model_detection() -> None:
    assert is_vlm_model("gemma-4-26B-A4B-it-MLX-8bit") is True
    assert is_vlm_model("claude-3-5-sonnet-20241022") is True
    assert is_vlm_model("gpt-4o-2024-08-06") is True
    assert is_vlm_model("gemini-1.5-pro") is True
    assert is_vlm_model("meta-llama/Meta-Llama-3.1-70B-Instruct") is False


def test_render_1bit_data_uri() -> None:
    sample_text = "Tool Execution Result: 15 matching lines found in ripgrep search."
    data_uri = render_text_to_1bit_data_uri(sample_text, font_size=12)
    assert data_uri is not None
    assert data_uri.startswith("data:image/png;base64,")


def test_model_capabilities_vlm_serialization() -> None:
    caps = ModelCapabilities(model_id="gemma-4-26b", supports_vlm=True)
    serialized_dict = caps.to_dict()
    assert serialized_dict["supports_vlm"] is True

    deserialized = ModelCapabilities.from_dict(serialized_dict)
    assert deserialized.supports_vlm is True


def test_visual_payload_transformation() -> None:
    cfg = LLMConfig(
        model="gemma-4-26B-A4B-it-MLX-8bit",
        visual_system_prompt=True,
        visual_tool_results=True,
        visual_threshold_chars=50,
    )
    client = LLMClient(config=cfg)

    input_messages = [
        {"role": "system", "content": "You are Dendrophis AI coding assistant."},
        {"role": "user", "content": "Run search"},
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "This is a long tool output result text string that exceeds fifty characters threshold.",
        },
    ]

    transformed = client._apply_visual_vlm_transformations(input_messages)
    assert len(transformed) == 4

    # System prompt should be converted to visual image user message block
    assert isinstance(transformed[0]["content"], list)
    assert transformed[0]["content"][1]["type"] == "image_url"

    # User message remains unchanged
    assert transformed[1]["content"] == "Run search"

    # Tool output should have text stub in role: tool and image in role: user
    assert transformed[2]["role"] == "tool"
    assert "[Tool result complete" in transformed[2]["content"]

    assert transformed[3]["role"] == "user"
    assert isinstance(transformed[3]["content"], list)
    assert transformed[3]["content"][1]["type"] == "image_url"


def test_visual_user_prompts_transformation() -> None:
    cfg = LLMConfig(
        model="gemma-4-26B-A4B-it-MLX-8bit",
        visual_user_prompts=True,
        visual_threshold_chars=50,
    )
    client = LLMClient(config=cfg)

    long_error_msg = (
        "Traceback (most recent call last):\n"
        "  File 'main.py', line 45, in <module>\n"
        "    raise ValueError('Long error traceback')"
    )
    input_messages = [
        {"role": "user", "content": long_error_msg},
    ]

    transformed = client._apply_visual_vlm_transformations(input_messages)
    assert isinstance(transformed[0]["content"], list)
    assert transformed[0]["content"][1]["type"] == "image_url"


def test_visual_glob_tool_result_transformation() -> None:
    """Test glob tool result visual rendering and file path location query."""
    import base64
    import json
    from io import BytesIO

    from PIL import Image

    glob_payload_json = json.dumps(
        {
            "files": [
                "README.md",
                "docs/DESIGN.md",
                "docs/FEATURES.md",
                "project_summary.md",
                "dendrophis/skills/tcss.md",
                "dendrophis/skills/panels.md",
                "AGENTS.md",
            ],
            "count": 7,
        }
    )

    config_instance = LLMConfig(
        model="gemma-4-26B-A4B-it-MLX-8bit",
        visual_tool_results=True,
        visual_threshold_chars=50,
    )
    llm_client_instance = LLMClient(config=config_instance)

    messages_input = [
        {"role": "user", "content": "Where is the DESIGN.md architecture doc located?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_glob_99",
                    "type": "function",
                    "function": {"name": "glob", "arguments": '{"pattern": "**/*.md"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_glob_99",
            "content": glob_payload_json,
        },
    ]

    transformed_messages = llm_client_instance._apply_visual_vlm_transformations(messages_input)
    assert len(transformed_messages) == 4

    tool_message_transformed = transformed_messages[2]
    assert tool_message_transformed["role"] == "tool"
    assert tool_message_transformed["tool_call_id"] == "call_glob_99"
    assert "[Tool result complete" in tool_message_transformed["content"]

    user_image_transformed = transformed_messages[3]
    assert user_image_transformed["role"] == "user"

    content_list = user_image_transformed["content"]
    assert isinstance(content_list, list)
    assert content_list[0]["type"] == "text"
    assert "Tool Execution Result Image" in content_list[0]["text"]

    image_payload_block = content_list[1]
    assert image_payload_block["type"] == "image_url"

    data_uri_string = image_payload_block["image_url"]["url"]
    assert data_uri_string.startswith("data:image/png;base64,")

    # Verify PNG image decoding and dimensions
    base64_data_encoded = data_uri_string.split("data:image/png;base64,")[1]
    raw_png_bytes = base64.b64decode(base64_data_encoded)
    pil_image_decoded = Image.open(BytesIO(raw_png_bytes))

    assert pil_image_decoded.mode == "L"
    assert pil_image_decoded.width > 300
    assert pil_image_decoded.height > 100


def test_coalesce_message_tool_call_deduplication() -> None:
    """Test that message coalescing deduplicates identical consecutive tool calls."""
    config_instance = LLMConfig(model="gemma-4-26B-A4B-it-MLX-8bit")
    client_instance = LLMClient(config=config_instance)

    duplicated_assistant_messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_duplicate_1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"file_path": "docs/DESIGN.md"}'},
                }
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_duplicate_1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"file_path": "docs/DESIGN.md"}'},
                },
                {
                    "id": "call_duplicate_2",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"file_path": "docs/DESIGN.md"}'},
                },
            ],
        },
    ]

    coalesced = client_instance._coalesce_consecutive_messages(duplicated_assistant_messages)
    assert len(coalesced) == 1
    assert len(coalesced[0]["tool_calls"]) == 1
    assert coalesced[0]["tool_calls"][0]["id"] == "call_duplicate_1"


def test_render_caching_and_deterministic_filenames() -> None:
    """Test that render_text_to_1bit_data_uri caches results and uses deterministic filenames."""
    sample_system_prompt = "You are Dendrophis system assistant prompt string."

    data_uri_first = render_text_to_1bit_data_uri(sample_system_prompt, save_prefix="test_system_prompt_cache")
    data_uri_second = render_text_to_1bit_data_uri(sample_system_prompt, save_prefix="test_system_prompt_cache")

    assert data_uri_first is not None
    assert data_uri_first == data_uri_second


# --- Visual token savings accounting (audit item 17) ------------------------


def test_visual_savings_dedup_across_transform_passes() -> None:
    """The same content must not be re-counted on every tool-loop step.

    The transform re-runs over the full message list on every loop iteration;
    each unique piece of content must be counted exactly once.
    """
    cfg = LLMConfig(
        model="gemma-4-26B-A4B-it-MLX-8bit",
        visual_system_prompt=True,
        visual_tool_results=True,
        visual_threshold_chars=50,
    )
    client = LLMClient(config=cfg)

    system_prompt = "S" * 1200  # ~342 estimated tokens -> 32 saved
    tool_output = "T" * 1400  # ~400 estimated tokens -> 90 saved
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "c1", "content": tool_output},
    ]

    client._apply_visual_vlm_transformations(messages)
    first_pass = client.last_visual_tokens_saved
    assert first_pass > 0

    # Next tool-loop step: same full message list, nothing new to count.
    client._apply_visual_vlm_transformations(messages)
    assert client.last_visual_tokens_saved == 0

    # A new tool result contributes only its own savings.
    messages.append({"role": "tool", "tool_call_id": "c2", "content": "N" * 1400})
    client._apply_visual_vlm_transformations(messages)
    second_pass = client.last_visual_tokens_saved
    assert 0 < second_pass < first_pass


@pytest.mark.anyio
async def test_usage_event_carries_visual_savings() -> None:
    """stream_chat must attach the payload-build savings to the UsageEvent it yields."""
    cfg = LLMConfig(model="gemma-4-26B-A4B-it-MLX-8bit", visual_system_prompt=True)
    client = LLMClient(config=cfg)
    client._make_provider_context = lambda: object()
    client._build_payload = lambda pc, msgs, tools, cache, tc: {"model": "m", "messages": msgs}
    client.last_visual_tokens_saved = 77  # simulate savings counted during payload build

    async def fake_stream_raw(provider_context, payload):
        yield UsageEvent(prompt_tokens=10, completion_tokens=5)
        yield TextDeltaEvent(delta="hi")
        yield DoneEvent(finish_reason="stop")

    client._stream_raw = fake_stream_raw

    events = [event async for event in client.stream_chat([{"role": "user", "content": "hi"}])]
    usage_events = [e for e in events if isinstance(e, UsageEvent)]
    assert len(usage_events) == 1
    assert usage_events[0].visual_tokens_saved == 77


class _Recorder:
    """Event-bus stub that records published events in order."""

    def __init__(self) -> None:
        self.events: list = []

    def publish(self, event) -> None:
        self.events.append(event)


class _FakeContext:
    """Minimal ContextManager stand-in for the send_message path."""

    def __init__(self) -> None:
        self.messages: list[dict] = [{"role": "system", "content": "sys"}]
        self.token_count = 10

    def append_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def append_assistant(self, content, tool_calls=None, reasoning_content=None) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def get_messages_for_api(self) -> list[dict]:
        return list(self.messages)

    def get_turn_count(self) -> int:
        return 1

    def needs_compaction(self) -> bool:
        return False

    @property
    def token_pct(self) -> float:
        return self.token_count / 1000

    def sync_token_count(self, prompt: int, completion: int) -> None:
        self.token_count = prompt + completion


class _UsageLLM:
    """Yields two UsageEvents carrying visual savings, then a final stop turn.

    Deliberately has NO last_visual_tokens_saved attribute: the orchestrator
    must read the value from the event, not via getattr on the LLM client.
    """

    async def stream_chat(self, messages, tools=None, enable_cache_control=False):
        yield UsageEvent(prompt_tokens=100, completion_tokens=10, visual_tokens_saved=30)
        yield UsageEvent(prompt_tokens=150, completion_tokens=10, visual_tokens_saved=20)
        yield TurnResult(text="done", reasoning="", tool_calls=[], finish_reason="stop")


def _config() -> DendrophisConfig:
    cfg = DendrophisConfig(llm=LLMConfig(model="test-model", base_url="http://127.0.0.1:9/v1", api_key="k"))
    cfg.caching.enabled = False  # skip understanding / file-cache side paths
    return cfg


@pytest.mark.anyio
async def test_orchestrator_accumulates_visual_savings_from_events() -> None:
    """The orchestrator must accumulate visual savings from UsageEvent fields."""
    recorder = _Recorder()
    stats = SessionStats()
    orchestrator = ChatOrchestrator(
        context=_FakeContext(),
        llm=_UsageLLM(),
        stats=stats,
        config=_config(),
        event_bus=recorder,
        understanding_detector=None,
        tool_registry=None,
        tool_executor_session=None,
        skill_manager=None,
        compactor=None,
    )
    await orchestrator.send_message("go")
    assert stats.visual_tokens_saved == 50


def test_session_stats_reset_clears_visual_tokens_saved() -> None:
    """/reset must clear the visual savings counter along with the other stats."""
    stats = SessionStats()
    stats.visual_tokens_saved = 123
    stats.prompt_tokens = 5
    stats.reset()
    assert stats.visual_tokens_saved == 0
    assert stats.prompt_tokens == 0
