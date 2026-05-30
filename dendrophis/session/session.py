"""Session — composition root tying all subsystems together.

Responsibilities:
- Orchestrate the main chat loop (send_message → stream → tools → repeat)
- Manage LLM client and model state
- Coordinate between ContextManager, LLM, Tools, and EventBus
- Handle session lifecycle (creation, streaming state, cleanup)
- Manage configuration and stats

Note: Session I/O is handled by SessionPersister.
      Project primer management is handled by PrimerManager.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dendrophis.caching.understanding import UnderstandingPhaseDetector
from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.events import (
    AuthFailedEvent,
    ConfigReloadedEvent,
    ContextUpdatedEvent,
    ErrorEvent,
    EventBus,
    MessageSentEvent,
    ModelSwitchedEvent,
    ReasoningDeltaEvent,
    StatsUpdatedEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
    TextDeltaEvent,
    ToolResultEvent,
    TurnResult,
    UsageEvent,
    WaitingForInputEvent,
    get_event_bus,
)
from dendrophis.llm.client import LLMClient, ModelInfo
from dendrophis.llm.models import supports_caching_by_id, supports_prompt_cache_key_by_id, supports_tools_by_id
from dendrophis.memory.memory import MemoryStore
from dendrophis.session.events import SessionEventHandler
from dendrophis.session.persister import SessionPersister
from dendrophis.session.primer import PrimerManager
from dendrophis.session.tools import SessionToolExecutor, is_tool_error, tool_call_to_payload
from dendrophis.skills.manager import SkillManager

# Constants for streaming
TPS_SAMPLE_INTERVAL = 8


def _file_log(message: str, log_path: Path) -> None:
    """Write a message to the debug log file."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except Exception as exc:
        import sys

        print(f"Failed to write debug log: {exc}", file=sys.stderr)


def _tool_log(message: str, session_id: str = "global") -> None:
    """Write a message to the tool execution log file."""
    log_path = Path(f"tool_log_{session_id}.txt")
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    try:
        with open(log_path, "a") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except Exception as exc:
        import sys

        print(f"Failed to write tool log: {exc}", file=sys.stderr)


# =========================================================================
# SessionStats (Stats Tracking)
# =========================================================================


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


class Session:
    """Connects config, LLM, context, and tools into a single conversation unit.
    Session is ephemeral — created fresh each run. Only memory.db persists.
    Uses event bus for decoupled communication with UI and other components.

    Responsibilities (after extraction):
    - Orchestrate the main chat loop (send_message -> stream -> tools -> repeat)
    - Manage LLM client and model state
    - Coordinate between ContextManager, LLM, Tools, and EventBus
    - Handle session lifecycle (creation, streaming state, cleanup)
    - Manage configuration and stats

    Delegated responsibilities:
    - Session I/O: SessionPersister
    - Project primer: PrimerManager
    """

    def __init__(
        self,
        config_loader: ConfigLoader,
        event_bus: EventBus | None = None,
        debug_logger: Callable[[str], None] | None = None,
        context: ContextManager | None = None,
        llm: LLMClient | None = None,
        stats: SessionStats | None = None,
        memory_store: MemoryStore | None = None,
        skill_manager: SkillManager | None = None,
        tool_registry: Any | None = None,
        tool_executor: Any | None = None,
        understanding_detector: UnderstandingPhaseDetector | None = None,
        compactor: Callable[[Any, Any, bool], Any] | None = None,
        primer_manager: PrimerManager | None = None,
        persister: SessionPersister | None = None,
    ) -> None:
        self.config_loader = config_loader
        self.config: DendrophisConfig = config_loader.config

        # DI-provided or default
        self.context = context or ContextManager(self.config)
        self.llm = llm or LLMClient(self.config.llm)
        self.stats = stats or SessionStats()
        self._memory_store = memory_store
        self._skill_manager = skill_manager
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._understanding_detector = understanding_detector or UnderstandingPhaseDetector(
            min_turns_before_established=self.config.caching.tier2_project_understanding_min_turns
        )
        self._compactor = compactor

        # Extracted components (DI-provided or created by Factory)
        self._primer_manager = primer_manager
        self._persister = persister

        self.models: list[ModelInfo] = []
        self.session_id: str = uuid.uuid4().hex

        # Set session-scoped prompt cache key for models that support it
        if self.config.llm.prompt_cache_key is None and supports_prompt_cache_key_by_id(self.config.llm.model):
            self.config.llm.prompt_cache_key = f"dendrophis-{self.session_id[:16]}"

        # Initialize event bus
        self._event_bus = event_bus or get_event_bus()

        self._debug_logger = debug_logger
        self._session_file: Path | None = None
        self._streaming = False
        self._stream_lock = threading.Lock()
        self._cancel_flag = threading.Event()
        self._pending_confirmations: dict[str, bool] = {}
        self._confirmation_results: dict[str, bool] = {}

        # These are linked by the Factory
        self._event_handler: SessionEventHandler | None = None
        self._tool_executor_session: SessionToolExecutor | None = None

        # Memory association generator - surfaces "this reminds me of..." moments
        self._association_generator: Any = None
        if self._memory_store is not None:
            from dendrophis.memory.association import MemoryAssociationGenerator

            self._association_generator = MemoryAssociationGenerator(self._memory_store)

        # Subagent executor - manages researcher, planner, code-writer, etc.
        self._subagent_executor: Any = None
        self._initialize_subagents()

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _log(self, message: str) -> None:
        """Log a debug message to file."""
        log_path = Path(self.config.debug_log).expanduser()
        _file_log(message, log_path)

    def _emit(self, event: Any) -> None:
        """Publish an event to the event bus."""
        if self._event_bus:
            self._event_bus.publish(event)

    def _initialize_subagents(self) -> None:
        """Initialize subagent system and register handlers."""
        from dendrophis.subagents import SubagentExecutor, get_registry
        from dendrophis.subagents.handlers import (
            CodeWriterHandler,
            ResearcherHandler,
            TestRunnerHandler,
            code_reviewer_execute,
            debugger_execute,
            planner_execute,
        )

        self._subagent_executor = SubagentExecutor()
        registry = get_registry()

        # Register researcher handler with memory store
        researcher = ResearcherHandler(memory_store=self._memory_store)
        registry.register_handler("researcher", researcher.execute)

        # Register code-writer handler with session's LLM client and config
        code_writer = CodeWriterHandler(
            llm_client=self.llm,
            config=self.config,
        )
        registry.register_handler("code-writer", code_writer.execute)

        # Register test-runner handler
        test_runner = TestRunnerHandler()
        registry.register_handler("test-runner", test_runner.execute)

        # Register planner, code-reviewer, debugger (LLM-only handlers)
        registry.register_handler("planner", planner_execute)
        registry.register_handler("code-reviewer", code_reviewer_execute)
        registry.register_handler("debugger", debugger_execute)

        # Register executor globally for tools to access
        from dendrophis.subagents import set_session_executor

        set_session_executor(self._subagent_executor)

    # =========================================================================
    # Configuration & Capabilities
    # =========================================================================

    def is_caching_enabled(self) -> bool:
        """Return True if caching is enabled in config and supported by the active model."""
        return self.config.caching.enabled and supports_caching_by_id(self.config.llm.model)

    def current_model_supports_tools(self) -> bool:
        """Return True if the active model supports tool/function calling."""
        current_id = self.config.llm.model
        model = next((m for m in self.models if m.id == current_id), None)
        if model:
            return model.supports_tools
        # Model list not yet fetched — fall back to ID-only heuristic
        return supports_tools_by_id(current_id)

    def _get_current_model_cost_per_1k(self) -> float:
        """Get the cost per 1k tokens for the current model."""
        current_model_id = self.config.llm.model
        model = next((model_info for model_info in self.models if model_info.id == current_model_id), None)
        if model:
            return model.cost_per_1k
        return 0.0

    # =========================================================================
    # Test Helpers
    # =========================================================================

    def set_tools(self, registry: Any, executor: Any) -> None:
        """Replace the tool registry and executor (used in tests)."""
        self._tool_registry = registry
        self._tool_executor = executor
        # Update the session tool executor with new registry
        self._tool_executor_session._tool_registry = registry
        self._tool_executor_session._tool_executor = executor

    async def invoke_subagent(
        self,
        agent: str,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke a subagent directly via the session's executor.

        Args:
            agent: Name of the subagent (researcher, code-writer, etc.)
            payload: Task-specific data for the subagent
            context: Additional context (files, memories, etc.)

        Returns:
            Result dict with success, status, result/error keys
        """
        if self._subagent_executor is None:
            return {"success": False, "error": "Subagent executor not initialized"}

        result = await self._subagent_executor.execute(
            agent=agent,
            payload=payload,
            context=context or {},
        )

        if result.success and result.response:
            return {
                "success": True,
                "agent": agent,
                "status": result.response.status,
                "result": result.response.result,
            }
        return {
            "success": False,
            "agent": agent,
            "error": result.error or "Unknown error",
        }

    # =========================================================================
    # Streaming State
    # =========================================================================

    def cancel_streaming(self) -> None:
        """Cancel the current streaming operation."""
        self._cancel_flag.set()

    def is_streaming(self) -> bool:
        """Check if currently streaming."""
        return self._streaming

    def get_understanding_stats(self) -> dict[str, Any]:
        """Return understanding phase detection statistics."""
        return self._understanding_detector.get_stats()

    # =========================================================================
    # LLM Management
    # =========================================================================

    async def fetch_models(self) -> None:
        """Fetch available models and update context_limit from active model's context_window."""
        self.models = await self.llm.fetch_models()
        active = next((model_info for model_info in self.models if model_info.id == self.config.llm.model), None)
        if active and active.context_window > 0:
            self.config.llm.context_limit = active.context_window
            self.context._config = self.config
            self._emit(ModelSwitchedEvent(model_id=active.id, context_window=active.context_window))
        # Update system prompt caching based on model support
        self.context.update_system_prompt_caching(self.is_caching_enabled())

    def switch_model(self, model_id: str) -> None:
        """Switch the active model and persist the change to config."""
        self.config.llm.model = model_id
        self.llm._config = self.config.llm
        # Persist to config.yaml
        if "llm" in self.config_loader._raw:
            self.config_loader._raw["llm"]["model"] = model_id
            self.config_loader.save()
        active = next((model_info for model_info in self.models if model_info.id == model_id), None)
        if active and active.context_window > 0:
            self.config.llm.context_limit = active.context_window
            self.context._config = self.config
            context_window = active.context_window
        else:
            context_window = self.config.llm.context_limit

        self._emit(ModelSwitchedEvent(model_id=model_id, context_window=context_window))
        # Update system prompt caching based on new model's support
        self.context.update_system_prompt_caching(self.is_caching_enabled())

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

    # =========================================================================
    # Main Chat Loop
    # =========================================================================

    async def send_message(self, text: str) -> None:
        """Send a user message and stream the response via events.
        This method publishes events to the event bus instead of yielding them.
        The UI should subscribe to events rather than awaiting this method.
        """
        # EAFP: Validate input
        if not text or not isinstance(text, str):
            self._emit(ErrorEvent(message="Message text must be a non-empty string"))
            return
        # Handle slash commands for skills
        if text.startswith("/"):
            cmd_parts = text[1:].split()
            if not cmd_parts:
                return
            cmd = cmd_parts[0].lower()
            args = cmd_parts[1:]
            if cmd in self._skill_manager._all_skills:
                self._skill_manager.activate(cmd, args=args)
                # Inject instructions into context immediately
                instructions = self._skill_manager.get_instructions()
                self.context.append_user(f"[System: Skill '{cmd}' activated]{args if args else ''}\n{instructions}")
                msg_text = f"Skill '{cmd}' activated"
                if args:
                    msg_text += f" {' '.join(args)}"
                self._emit(MessageSentEvent(message_text=f"{msg_text}."))
                return
            if cmd in ("stop", "normal"):
                self._skill_manager.active_skills.clear()
                self.context.append_user("[System: Skills deactivated. Returning to normal mode.]")
                self._emit(MessageSentEvent(message_text="Normal mode activated."))
                return
            self._emit(ErrorEvent(message=f"Unknown command: /{cmd}"))
            return
        with self._stream_lock:
            if self._streaming:
                self._emit(ErrorEvent(message="Already streaming a response"))
                return
            self._streaming = True
            self._cancel_flag.clear()
        try:
            # EAFP: Handle context operations with error handling
            try:
                self.context.append_user(text)
            except Exception as context_error:
                self._emit(ErrorEvent(message=f"Failed to add message to context: {context_error!s}"))
                return
            # Phase 2: Update understanding phase detection with EAFP
            try:
                if self.is_caching_enabled() and self.config.caching.tier2_project_understanding:
                    was_established = self._understanding_detector.is_established()
                    self._understanding_detector.record_user_message(text, self.context.get_turn_count())
                    if not was_established and self._understanding_detector.is_established():
                        self.context.update_understanding_cache(
                            self._understanding_detector.get_understanding_checkpoint_turn()
                        )
                    # Emit event to update UI
                    from dendrophis.events.types import UnderstandingStatsUpdatedEvent

                    self._emit(
                        UnderstandingStatsUpdatedEvent(
                            established=self._understanding_detector.is_established(),
                            checkpoint_turn=self._understanding_detector.get_understanding_checkpoint_turn(),
                            min_turns_required=self._understanding_detector.min_turns_before_established,
                            current_turn=self.context.get_turn_count(),
                        )
                    )
            except Exception as understanding_error:
                self._log(f"Understanding detection failed: {understanding_error}")
                # Non-critical failure - continue without understanding update
            self.stats.start_turn()
            self._emit(StreamingStartedEvent(user_message=text))

            # Generate memory association - "this makes me think of..."
            self._log(f"[ASSOC-DEBUG] Generator exists: {self._association_generator is not None}")
            if self._association_generator is not None:
                try:
                    self._log("[ASSOC-DEBUG] Calling on_turn...")
                    association = self._association_generator.on_turn(text)
                    self._log(f"[ASSOC-DEBUG] Result: {association is not None}")
                    if association is not None:
                        # Inject into context so the LLM actually sees it
                        from dendrophis.memory.association import MemoryAssociationGenerator

                        assoc_text = MemoryAssociationGenerator.format_association(association)
                        self._log(f"[ASSOC-DEBUG] Injecting: {assoc_text[:80]}...")
                        self.context.append_system(f"[Memory: {assoc_text}]")
                        self._emit(association)
                except Exception as association_error:
                    self._log(f"[ASSOC-DEBUG] Exception: {association_error}")
                    # Non-critical - continue without association

            # EAFP: Handle context compaction with error handling
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
                # Non-critical failure - continue without compaction
            # Start periodic stats emission in background with EAFP
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
                    except Exception as stats_error:
                        self._log(f"Failed to cancel stats task: {stats_error}")
            # Phase 2: Update file caches after completion with EAFP
            try:
                if self.is_caching_enabled() and self.config.caching.tier2_file_blocks:
                    self.context.update_file_caches()
            except Exception as cache_error:
                self._log(f"File cache update failed: {cache_error}")
                # Non-critical failure - continue without cache update
            # EAFP: Handle stats and event emission with error handling
            try:
                self.stats.finish_turn()
                self._emit(StreamingFinishedEvent())
                self._emit(WaitingForInputEvent())  # System is now waiting for user input
            except Exception as final_error:
                self._emit(ErrorEvent(message=f"Failed to finalize turn: {final_error!s}"))
        except asyncio.CancelledError:
            self._emit(ErrorEvent(message="Streaming cancelled"))
            self._emit(WaitingForInputEvent())
        except Exception as exc:
            import traceback

            self._log(f"Unexpected error: {exc}\n{traceback.format_exc()}")
            self._emit(ErrorEvent(message=f"Unexpected error: {exc}"))
            self._emit(WaitingForInputEvent())
        finally:
            with self._stream_lock:
                self._streaming = False

    async def _run_completion_loop(self) -> None:
        """Orchestrate the turn loop: stream -> record -> execute tools -> repeat."""
        tools_schema = (
            self._tool_registry.all_schema() if self._tool_registry and self.current_model_supports_tools() else None
        )
        max_consecutive_failures = self.config.tools.max_calls
        consecutive_failures = 0
        while not self._cancel_flag.is_set():
            self._log(f"_run_completion_loop consecutive_failures={consecutive_failures}")
            last_message = self.context.get_messages_for_api()[-1]["content"]
            self._emit(MessageSentEvent(message_text=last_message))
            turn: TurnResult | None = None
            # Accumulate partial text for cancellation handling
            partial_text: str = ""
            partial_reasoning: str = ""
            async for event in self.llm.stream_chat(
                self.context.get_messages_for_api(),
                tools=tools_schema,
                enable_cache_control=self.is_caching_enabled() and self.config.caching.tier1_tool_definitions,
            ):
                if self._cancel_flag.is_set():
                    # Save partial response before returning
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
            # Write assistant turn to context.
            # For local servers (MLC/Qwen3.5), synthesize <tool_call> tags into content
            # so the model sees its own intent when tool_calls are stripped from history.
            assistant_text = turn.text
            if not assistant_text and turn.tool_calls:

                def _tc_json(tc: Any) -> str:
                    args = json.loads(tc.arguments) if tc.arguments and tc.arguments.strip() else {}
                    return f"<tool_call>{json.dumps({'name': tc.name, 'arguments': args})}</tool_call>"

                assistant_text = "".join(_tc_json(tc) for tc in turn.tool_calls)
            tool_calls_payload = [tool_call_to_payload(tc) for tc in turn.tool_calls] if turn.tool_calls else None
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

            for tc in turn.tool_calls:
                self._log(f"  Tool: {tc.name}, id={tc.id}, args={tc.arguments!r}")
                _tool_log(f"  Tool Call: {tc.name}(id={tc.id})", self.session_id)
                _tool_log(f"    Arguments: {tc.arguments!r}", self.session_id)
                if self._debug_logger:
                    self._debug_logger(f"[LLM Tool Call] {tc.name}(id={tc.id}): {tc.arguments}")

            if not self._tool_executor:
                _tool_log("No tool executor available - skipping execution", self.session_id)
                return

            _tool_log("Calling SessionToolExecutor.execute()", self.session_id)
            results = await self._tool_executor_session.execute(turn.tool_calls)
            _tool_log("SessionToolExecutor.execute() completed", self.session_id)
            tc_by_id = {tc.id: tc for tc in turn.tool_calls}
            if any(is_tool_error(r.content) for r in results):
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

                # Don't sanitize tool names for LLM context - they should remain unchanged
                # sanitized_name = _sanitize_tool_name(result.name)
                _tool_log("Appending tool result to context", self.session_id)
                self.context.append_tool_result(result.tool_call_id, result.name, result.content)
                _tool_log("Tool result appended to context", self.session_id)

                tc = tc_by_id.get(result.tool_call_id)
                description = ""
                arguments = ""
                if tc:
                    arguments = tc.arguments
                    with contextlib.suppress(Exception):
                        description = json.loads(tc.arguments).get("description", "")

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

    # =========================================================================
    # Session I/O (Delegated to SessionPersister)
    # =========================================================================

    def save_session(self) -> Path | None:
        """Save the current session to a JSON file.
        Delegates to SessionPersister.

        Returns:
            The path to the saved file, or None if there are no messages to save.
        """
        return self._persister.save(self.session_id, self._session_file)

    def load_session(self, path: str) -> dict[str, Any] | None:
        """Load a session from a JSON file.
        Delegates to SessionPersister.

        Args:
            path: Path to the session JSON file.

        Returns:
            Dict with info about loaded session, or None if loading failed.
        """
        info, session_id, session_file = self._persister.load(path)
        if info:
            self.session_id = session_id
            self._session_file = session_file
            # Update LLM config with loaded model
            if info.get("model"):
                self.llm._config = self.config.llm
            # Emit events for UI to refresh
            self._emit(
                ContextUpdatedEvent(
                    token_count=self.context.token_count,
                    token_pct=self.context.token_pct,
                    turn_count=self.context.get_turn_count(),
                    full_chat_restored=True,
                )
            )
            self._emit(
                StatsUpdatedEvent(
                    prompt_tokens=self.stats.prompt_tokens,
                    completion_tokens=self.stats.completion_tokens,
                    total_cost_usd=self.stats.total_cost_usd,
                    tokens_per_sec=0.0,
                    time_to_first_token=0.0,
                )
            )
        return info

    # =========================================================================
    # Context Compaction (Delegated to Compactor)
    # =========================================================================

    async def compact(self) -> dict[str, Any]:
        """Compact the context by summarizing older messages.
        Delegates to the compactor function.

        Returns:
            Dict with compaction details (messages_compacted, kept_recent, compacted, reason).
        """
        if self._compactor:
            return await self._compactor(self.context, self.llm, enable_caching=self.config.caching.enabled)
        return {"compacted": False, "reason": "No compactor configured"}

    # =========================================================================
    # Project Primer (Delegated to PrimerManager)
    # =========================================================================

    def save_project_primer(self) -> str | None:
        """Capture current project understanding as a primer file.
        Delegates to PrimerManager.
        """
        if self._primer_manager:
            return self._primer_manager.save_project_primer()
        return None

    def load_project_primer(self) -> dict[str, Any] | None:
        """Load the project primer for the current working directory.
        Delegates to PrimerManager.
        """
        if self._primer_manager:
            return self._primer_manager.load_project_primer()
        return None

    def inject_primer_files(self) -> dict[str, Any]:
        """Re-read all primer-tracked files from disk and inject into context.
        Delegates to PrimerManager.

        Returns:
            Dict with 'injected' (count) and 'total' (file count).
        """
        if self._primer_manager:
            return self._primer_manager.inject_primer_files()
        return {"injected": 0, "total": 0}

    def track_file(self, path: str) -> bool:
        """Add a file to the project primer. Returns True on success.
        Delegates to PrimerManager.
        """
        if self._primer_manager:
            return self._primer_manager.track_file(path)
        return False

    def untrack_file(self, path: str) -> bool:
        """Remove a file from the project primer. Returns True on success.
        Delegates to PrimerManager.
        """
        if self._primer_manager:
            return self._primer_manager.untrack_file(path)
        return False

    # =========================================================================
    # Configuration & Cleanup
    # =========================================================================

    def reload_config(self) -> None:
        """Re-read config from disk and reinitialise the LLM client."""
        self.config_loader.reload()
        self.config = self.config_loader.config
        self.llm = LLMClient(self.config.llm)
        self._emit(ConfigReloadedEvent())

    async def aclose(self) -> None:
        """Close the LLM client and release resources."""
        await self.llm.aclose()
