"""ChatOrchestrator - owns the chat turn loop and transient streaming state."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dendrophis.caching.understanding import UnderstandingPhaseDetector
from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.events import (
    AuthFailedEvent,
    ContextUpdatedEvent,
    ErrorEvent,
    EventBus,
    MessageSentEvent,
    ReasoningDeltaEvent,
    StatsUpdatedEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
    TextDeltaEvent,
    ToolResultEvent,
    TurnResult,
    UsageEvent,
    WaitingForInputEvent,
)
from dendrophis.llm.client import LLMClient, ModelInfo
from dendrophis.llm.models import supports_caching_by_id, supports_tools_by_id
from dendrophis.memory.association import MemoryAssociationGenerator
from dendrophis.session.tools import SessionToolExecutor, is_tool_error, tool_call_to_payload
from dendrophis.skills.manager import SkillManager
from dendrophis.tools.registry import ToolRegistry

# Constants for streaming
TPS_SAMPLE_INTERVAL = 8


def _file_log(message: str, log_path: Path) -> None:
    """Write a message to the debug log file."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except Exception as exception:
        import sys

        print(f"Failed to write debug log: {exception}", file=sys.stderr)


def _tool_log(message: str, session_id: str = "global") -> None:
    """Write a message to the tool execution log file."""
    log_path = Path(f"tool_log_{session_id}.txt")
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    try:
        with open(log_path, "a") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except Exception as exception:
        import sys

        print(f"Failed to write tool log: {exception}", file=sys.stderr)


@dataclass
class SessionStats:
    """Cumulative and per-turn token/speed statistics for a session."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_cost_usd: float = 0.0
    tokens_per_sec: float = 0.0
    time_to_first_token: float = 0.0
    # transient stream timing — reset each turn
    _stream_start: float = field(default=0.0, repr=False)
    _first_token_at: float = field(default=0.0, repr=False)
    _turn_completion: int = field(default=0, repr=False)

    def start_turn(self) -> None:
        """Reset per-turn timing counters at the start of a new message."""
        self._stream_start = time.monotonic()
        self._first_token_at = 0.0
        self._turn_completion = 0

    def record_token(self) -> None:
        """Record arrival of one completion token; samples TPS every 8 tokens."""
        if self._first_token_at == 0.0:
            self._first_token_at = time.monotonic()
            self.time_to_first_token = self._first_token_at - self._stream_start
        self._turn_completion += 1
        if self._turn_completion % TPS_SAMPLE_INTERVAL == 0:
            self.tokens_per_sec = self.current_tps

    @property
    def current_tps(self) -> float:
        """Dynamically calculate tokens per second."""
        if self._first_token_at == 0.0:
            return 0.0
        elapsed = time.monotonic() - self._first_token_at
        if elapsed > 0:
            return self._turn_completion / elapsed
        return 0.0

    def finish_turn(self) -> None:
        """Finalise TPS for the completed turn."""
        if self._turn_completion > 0 and self._first_token_at > 0:
            elapsed = time.monotonic() - self._first_token_at
            if elapsed > 0:
                self.tokens_per_sec = self._turn_completion / elapsed

    def update(self, prompt: int, completion: int, cost_per_1k: float = 0.0, cached: int = 0) -> None:
        """Accumulate token counts and estimated cost."""
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cached_tokens += cached
        self.total_cost_usd += (prompt + completion) / 1000 * cost_per_1k

    def reset(self) -> None:
        """Reset all cumulative statistics."""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_tokens = 0
        self.total_cost_usd = 0.0
        self.tokens_per_sec = 0.0
        self.time_to_first_token = 0.0
        self._stream_start = 0.0
        self._first_token_at = 0.0
        self._turn_completion = 0


@dataclass
class ChatOrchestrator:
    """Orchestrates the chat session execution turn and LLM completion loops."""

    context: ContextManager
    llm: LLMClient
    stats: SessionStats
    config: DendrophisConfig
    event_bus: EventBus
    understanding_detector: UnderstandingPhaseDetector
    tool_registry: ToolRegistry
    tool_executor_session: SessionToolExecutor
    skill_manager: SkillManager
    compactor: Callable[[Any, Any, bool], Any]
    association_generator: MemoryAssociationGenerator | None = None
    debug_logger: Callable[[str], None] | None = None
    models: list[ModelInfo] = field(default_factory=list)
    models_by_id: dict[str, ModelInfo] = field(default_factory=dict)
    session_id: str = ""
    stream_lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    _streaming: bool = False

    def _log(self, message: str) -> None:
        """Log a debug message to file."""
        log_path = Path(self.config.debug_log).expanduser()
        _file_log(message, log_path)

    def _emit(self, event: Any) -> None:
        """Publish an event to the event bus."""
        self.event_bus.publish(event)

    def is_streaming(self) -> bool:
        """Check if currently streaming."""
        return self._streaming

    def is_caching_enabled(self) -> bool:
        """Return True if caching is enabled in config and supported by active model."""
        return self.config.caching.enabled and supports_caching_by_id(self.config.llm.model)

    def current_model_supports_tools(self) -> bool:
        """Return True if the active model supports tool/function calling."""
        current_id = self.config.llm.model
        model_info = self.models_by_id.get(current_id)
        if model_info:
            return model_info.supports_tools
        return supports_tools_by_id(current_id)

    def _get_current_model_cost_per_1k(self) -> float:
        """Get the cost per 1k tokens for the current model."""
        current_model_id = self.config.llm.model
        model_info = self.models_by_id.get(current_model_id)
        if model_info:
            return model_info.cost_per_1k
        return 0.0

    async def _emit_stats_periodically(self) -> None:
        """Periodically emit stats events while streaming."""
        while self._streaming:
            await asyncio.sleep(1.0)
            if self._streaming:
                self._emit(
                    StatsUpdatedEvent(
                        prompt_tokens=self.stats.prompt_tokens,
                        completion_tokens=self.stats.completion_tokens,
                        total_cost_usd=self.stats.total_cost_usd,
                        tokens_per_sec=self.stats.current_tps,
                        time_to_first_token=self.stats.time_to_first_token,
                    )
                )

    async def compact(self) -> dict[str, Any]:
        """Compact the context by summarizing older messages."""
        return await self.compactor(self.context, self.llm, enable_caching=self.config.caching.enabled)

    async def send_message(self, text: str) -> None:
        """Send a user message and orchestrate response streaming and tool execution."""
        if not text or not isinstance(text, str):
            self._emit(ErrorEvent(message="Message text must be a non-empty string"))
            return

        # Handle slash commands for skills
        if text.startswith("/"):
            command_parts = text[1:].split()
            if not command_parts:
                return
            command = command_parts[0].lower()
            arguments = command_parts[1:]
            if command in self.skill_manager._all_skills:
                self.skill_manager.activate(command, args=arguments)
                instructions = self.skill_manager.get_instructions()
                self.context.append_user(
                    f"[System: Skill '{command}' activated]{arguments if arguments else ''}\n{instructions}"
                )
                message_text = f"Skill '{command}' activated"
                if arguments:
                    message_text += f" {' '.join(arguments)}"
                self._emit(MessageSentEvent(message_text=f"{message_text}."))
                return
            if command in ("stop", "normal"):
                self.skill_manager.active_skills.clear()
                self.context.append_user("[System: Skills deactivated. Returning to normal mode.]")
                self._emit(MessageSentEvent(message_text="Normal mode activated."))
                return
            self._emit(ErrorEvent(message=f"Unknown command: /{command}"))
            return

        with self.stream_lock:
            if self._streaming:
                self._emit(ErrorEvent(message="Already streaming a response"))
                return
            self._streaming = True
            self.cancel_flag.clear()

        try:
            try:
                self.context.append_user(text)
            except Exception as context_error:
                self._emit(ErrorEvent(message=f"Failed to add message to context: {context_error!s}"))
                return

            try:
                if self.is_caching_enabled() and self.config.caching.tier2_project_understanding:
                    was_established = self.understanding_detector.is_established()
                    self.understanding_detector.record_user_message(text, self.context.get_turn_count())
                    if not was_established and self.understanding_detector.is_established():
                        self.context.update_understanding_cache(
                            self.understanding_detector.get_understanding_checkpoint_turn()
                        )
                    from dendrophis.events.types import UnderstandingStatsUpdatedEvent

                    self._emit(
                        UnderstandingStatsUpdatedEvent(
                            established=self.understanding_detector.is_established(),
                            checkpoint_turn=self.understanding_detector.get_understanding_checkpoint_turn(),
                            min_turns_required=self.understanding_detector.min_turns_before_established,
                            current_turn=self.context.get_turn_count(),
                        )
                    )
            except Exception as understanding_error:
                self._log(f"Understanding detection failed: {understanding_error}")

            self.stats.start_turn()
            self._emit(StreamingStartedEvent(user_message=text))

            # Generate memory association - "this makes me think of..."
            if self.association_generator is not None:
                try:
                    association = self.association_generator.on_turn(text)
                    if association is not None:
                        assoc_text = MemoryAssociationGenerator.format_association(association)
                        self.context.append_system(f"[Memory: {assoc_text}]")
                        self._emit(association)
                except Exception as association_error:
                    self._log(f"Memory association failed: {association_error}")

            try:
                if self.context.needs_compaction():
                    await self.compact()
                    self._emit(
                        ContextUpdatedEvent(
                            token_count=self.context.token_count,
                            token_pct=self.context.token_pct,
                            turn_count=self.context.get_turn_count(),
                        )
                    )
            except Exception as compaction_error:
                self._emit(ErrorEvent(message=f"Context compaction failed: {compaction_error!s}"))

            stats_task = None
            try:
                stats_task = asyncio.create_task(self._emit_stats_periodically())
                await self._run_completion_loop()
            except Exception as completion_error:
                self._emit(ErrorEvent(message=f"Completion loop failed: {completion_error!s}"))
                raise
            finally:
                if stats_task:
                    try:
                        stats_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await stats_task
                    except Exception as stats_task_error:
                        self._log(f"Failed to cancel stats task: {stats_task_error}")

            try:
                if self.is_caching_enabled() and self.config.caching.tier2_file_blocks:
                    self.context.update_file_caches()
            except Exception as cache_error:
                self._log(f"File cache update failed: {cache_error}")

            try:
                self.stats.finish_turn()
                self._emit(StreamingFinishedEvent())
                self._emit(WaitingForInputEvent())
            except Exception as finalise_error:
                self._emit(ErrorEvent(message=f"Failed to finalize turn: {finalise_error!s}"))
        except asyncio.CancelledError:
            self._emit(ErrorEvent(message="Streaming cancelled"))
            self._emit(WaitingForInputEvent())
        except Exception as exception:
            import traceback

            self._log(f"Unexpected error: {exception}\n{traceback.format_exc()}")
            self._emit(ErrorEvent(message=f"Unexpected error: {exception}"))
            self._emit(WaitingForInputEvent())
        finally:
            with self.stream_lock:
                self._streaming = False

    async def _run_completion_loop(self) -> None:
        """Orchestrate the turn loop: stream -> record -> execute tools -> repeat."""
        tools_schema = (
            self.tool_registry.all_schema() if self.tool_registry and self.current_model_supports_tools() else None
        )
        max_consecutive_failures = self.config.tools.max_calls
        consecutive_failures = 0
        while not self.cancel_flag.is_set():
            self._log(f"_run_completion_loop consecutive_failures={consecutive_failures}")
            last_message = self.context.get_messages_for_api()[-1]["content"]
            self._emit(MessageSentEvent(message_text=last_message))
            turn: TurnResult | None = None
            partial_text: str = ""
            partial_reasoning: str = ""
            async for event in self.llm.stream_chat(
                self.context.get_messages_for_api(),
                tools=tools_schema,
                enable_cache_control=self.is_caching_enabled() and self.config.caching.tier1_tool_definitions,
            ):
                if self.cancel_flag.is_set():
                    if partial_text or partial_reasoning:
                        self._log(
                            f"Cancelling with partial response: "
                            f"text_len={len(partial_text)}, "
                            f"reasoning_len={len(partial_reasoning)}"
                        )
                        self.context.append_assistant(partial_text, None, None)
                        self._emit(TextDeltaEvent(delta=" [response cancelled]"))
                    return
                if isinstance(event, TurnResult):
                    turn = event
                elif isinstance(event, TextDeltaEvent):
                    partial_text += event.delta
                    self.stats.record_token()
                    self._emit(event)
                elif isinstance(event, ReasoningDeltaEvent):
                    partial_reasoning += event.delta
                    self.stats.record_token()
                    self._emit(event)
                elif isinstance(event, UsageEvent):
                    self.context.sync_token_count(event.prompt_tokens, event.completion_tokens)
                    self._emit(
                        ContextUpdatedEvent(
                            token_count=self.context.token_count,
                            token_pct=self.context.token_pct,
                            turn_count=self.context.get_turn_count(),
                        )
                    )
                    cost_per_1k = self._get_current_model_cost_per_1k()
                    self.stats.update(event.prompt_tokens, event.completion_tokens, cost_per_1k, event.cached_tokens)
                    self._emit(event)
                    self._emit(
                        StatsUpdatedEvent(
                            prompt_tokens=self.stats.prompt_tokens,
                            completion_tokens=self.stats.completion_tokens,
                            total_cost_usd=self.stats.total_cost_usd,
                            tokens_per_sec=self.stats.tokens_per_sec,
                            time_to_first_token=self.stats.time_to_first_token,
                        )
                    )
                elif isinstance(event, (AuthFailedEvent, ErrorEvent)):
                    self._emit(event)
                    self._emit(StreamingFinishedEvent())
                    return
                else:
                    self._emit(event)
            if turn is None:
                return
            self._log(f"Turn complete: finish_reason={turn.finish_reason}, tool_calls={len(turn.tool_calls)}")
            assistant_text = turn.text
            if not assistant_text and turn.tool_calls:

                def _tc_json(tool_call: Any) -> str:
                    if tool_call.arguments and tool_call.arguments.strip():
                        arguments_dict = json.loads(tool_call.arguments)
                    else:
                        arguments_dict = {}
                    return f"<tool_call>{json.dumps({'name': tool_call.name, 'arguments': arguments_dict})}</tool_call>"

                assistant_text = "".join(_tc_json(tool_call) for tool_call in turn.tool_calls)

            if turn.tool_calls:
                tool_calls_payload = [tool_call_to_payload(tool_call) for tool_call in turn.tool_calls]
            else:
                tool_calls_payload = None
            self.context.append_assistant(assistant_text, tool_calls_payload, None)
            is_tool_finish = bool(turn.tool_calls) and turn.finish_reason in ("tool_calls", "stop")
            if not is_tool_finish:
                self._log(f"Exiting: finish_reason={turn.finish_reason}, has_tools={bool(turn.tool_calls)}")
                if turn.finish_reason == "length" and not turn.text and not turn.tool_calls:
                    self._emit(
                        ErrorEvent(
                            message=(
                                "Response cut off: model hit the token limit before producing any output. "
                                "Try increasing max_tokens in dendrophis.yaml."
                            )
                        )
                    )
                return
            self._log(f"Executing {len(turn.tool_calls)} tool calls")
            _tool_log("=== TOOL EXECUTION START ===", self.session_id)
            _tool_log(f"Session: {self.session_id}", self.session_id)
            _tool_log(f"Model: {self.config.llm.model}", self.session_id)
            _tool_log(f"Executing {len(turn.tool_calls)} tool calls", self.session_id)

            for tool_call in turn.tool_calls:
                self._log(f"  Tool: {tool_call.name}, id={tool_call.id}, args={tool_call.arguments!r}")
                _tool_log(f"  Tool Call: {tool_call.name}(id={tool_call.id})", self.session_id)
                _tool_log(f"    Arguments: {tool_call.arguments!r}", self.session_id)
                if self.debug_logger:
                    self.debug_logger(f"[LLM Tool Call] {tool_call.name}(id={tool_call.id}): {tool_call.arguments}")

            if not self.tool_executor_session:
                _tool_log("No tool executor available - skipping execution", self.session_id)
                return

            _tool_log("Calling SessionToolExecutor.execute()", self.session_id)
            results = await self.tool_executor_session.execute(turn.tool_calls)
            _tool_log("SessionToolExecutor.execute() completed", self.session_id)
            tool_calls_by_id = {tool_call.id: tool_call for tool_call in turn.tool_calls}
            if any(is_tool_error(result.content) for result in results):
                consecutive_failures += 1
                if consecutive_failures > max_consecutive_failures:
                    self._log(f"ERROR: Exceeded max consecutive failures ({max_consecutive_failures})")
                    self._emit(
                        ErrorEvent(message=f"Exceeded maximum consecutive failures ({max_consecutive_failures})")
                    )
                    self._emit(StreamingFinishedEvent())
                    return
            else:
                consecutive_failures = 0
            for result in results:
                self._log(f"Tool result: {result.name}")
                _tool_log(f"Tool Result: {result.name}", self.session_id)
                content_preview = result.content[:100]
                _tool_log(f"  Content: {content_preview}{'...' if len(result.content) > 100 else ''}", self.session_id)

                _tool_log("Appending tool result to context", self.session_id)
                self.context.append_tool_result(result.tool_call_id, result.name, result.content)
                _tool_log("Tool result appended to context", self.session_id)

                tool_call = tool_calls_by_id.get(result.tool_call_id)
                description = ""
                arguments = ""
                if tool_call:
                    arguments = tool_call.arguments
                    with contextlib.suppress(Exception):
                        arguments_dict = json.loads(tool_call.arguments)
                        description = arguments_dict.get("description", "")

                _tool_log("Emitting ToolResultEvent", self.session_id)
                self._emit(
                    ToolResultEvent(
                        tool_call_id=result.tool_call_id,
                        name=result.name,
                        content=result.content,
                        description=description,
                        arguments=arguments,
                        consecutive_failures=consecutive_failures if is_tool_error(result.content) else 0,
                    )
                )
                _tool_log("ToolResultEvent emitted", self.session_id)
            self._log("Tool execution complete, looping back")
