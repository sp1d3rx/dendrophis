"""Event handlers for Session."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dendrophis.caching.understanding import UnderstandingPhaseDetector
from dendrophis.config.loader import ConfigLoader
from dendrophis.context.manager import ContextManager
from dendrophis.events import (
    CancelStreamingRequest,
    CompactRequest,
    ConfigChangeRequest,
    ContextUpdatedEvent,
    EventBus,
    ModelSwitchRequest,
    PrimerInjectRequest,
    PrimerLoadRequest,
    PrimerSaveRequest,
    ReasoningEffortChangedEvent,
    ReasoningEffortChangeRequest,
    SendMessageRequest,
    SessionLoadRequest,
    SessionResetRequest,
    SessionSaveRequest,
    StatsUpdatedEvent,
    TemperatureChangedEvent,
    TemperatureChangeRequest,
    ToolConfirmationResponseEvent,
    TrackFileRequest,
    UntrackFileRequest,
)
from dendrophis.llm.client import LLMClient
from dendrophis.llm.models import supports_caching_by_id, supports_prompt_cache_key_by_id

if TYPE_CHECKING:
    from dendrophis.session.session import Session, SessionStats


class SessionEventHandler:
    """Handles event bus subscriptions and request handling for Session."""

    def __init__(
        self,
        session: Session,
        event_bus: EventBus,
        config_loader: ConfigLoader,
        context: ContextManager,
        llm: LLMClient,
        stats: SessionStats,
        understanding_detector: UnderstandingPhaseDetector,
        debug_logger: Callable[[str], None] | None,
        stream_lock: threading.Lock,
        cancel_flag: threading.Event,
        pending_confirmations: dict[str, bool],
        confirmation_results: dict[str, bool],
    ) -> None:
        self._session = session
        self._event_bus = event_bus
        self._config_loader = config_loader
        self._context = context
        self._llm = llm
        self._stats = stats
        self._understanding_detector = understanding_detector
        self._debug_logger = debug_logger
        self._stream_lock = stream_lock
        self._cancel_flag = cancel_flag
        self._pending_confirmations = pending_confirmations
        self._confirmation_results = confirmation_results

        self._subscribe_to_events()

    def _subscribe_to_events(self) -> None:
        """Subscribe to all request events from the event bus."""
        self._event_bus.subscribe(ToolConfirmationResponseEvent, self._on_confirmation_response)
        self._event_bus.subscribe(ModelSwitchRequest, self._on_model_switch_request)
        self._event_bus.subscribe(TemperatureChangeRequest, self._on_temperature_change_request)
        self._event_bus.subscribe(ReasoningEffortChangeRequest, self._on_reasoning_effort_change_request)
        self._event_bus.subscribe(SessionResetRequest, self._on_session_reset_request)
        self._event_bus.subscribe(SendMessageRequest, self._on_send_message_request)
        self._event_bus.subscribe(SessionSaveRequest, self._on_session_save_request)
        self._event_bus.subscribe(SessionLoadRequest, self._on_session_load_request)
        self._event_bus.subscribe(CompactRequest, self._on_compact_request)
        self._event_bus.subscribe(PrimerSaveRequest, self._on_primer_save_request)
        self._event_bus.subscribe(PrimerLoadRequest, self._on_primer_load_request)
        self._event_bus.subscribe(PrimerInjectRequest, self._on_primer_inject_request)
        self._event_bus.subscribe(ConfigChangeRequest, self._on_config_change_request)
        self._event_bus.subscribe(CancelStreamingRequest, self._on_cancel_streaming_request)
        self._event_bus.subscribe(TrackFileRequest, self._on_track_file_request)
        self._event_bus.subscribe(UntrackFileRequest, self._on_untrack_file_request)

    def _emit(self, event: Any) -> None:
        """Publish an event to the event bus."""
        if self._event_bus:
            self._event_bus.publish(event)

    # -- Event handlers -----------------------------------------------------------

    def _on_confirmation_response(self, event: ToolConfirmationResponseEvent) -> None:
        """Handle human approval response."""
        self._confirmation_results[event.request_id] = event.approved

    def _on_model_switch_request(self, event: ModelSwitchRequest) -> None:
        """Handle model switch request from UI."""
        self._session.switch_model(event.model_id)

    def _on_temperature_change_request(self, event: TemperatureChangeRequest) -> None:
        """Handle temperature change request from UI."""
        config = self._config_loader.config
        config.llm.temperature = event.temperature
        self._llm._config = config.llm
        if "llm" in self._config_loader._raw:
            self._config_loader._raw["llm"]["temperature"] = event.temperature
            self._config_loader.save()
        self._emit(TemperatureChangedEvent(temperature=event.temperature))

    def _on_reasoning_effort_change_request(self, event: ReasoningEffortChangeRequest) -> None:
        """Handle reasoning effort change request from UI."""
        config = self._config_loader.config
        config.llm.reasoning_effort = event.reasoning_effort
        self._llm._config = config.llm
        if "llm" in self._config_loader._raw:
            self._config_loader._raw["llm"]["reasoning_effort"] = event.reasoning_effort
            self._config_loader.save()
        self._emit(ReasoningEffortChangedEvent(reasoning_effort=event.reasoning_effort))

    def _on_session_reset_request(self, event: SessionResetRequest) -> None:
        """Handle session reset request from UI."""
        self._context.reset()
        self._stats.reset()
        self._understanding_detector.reset()
        config = self._config_loader.config
        self._context.update_system_prompt_caching(config.caching.enabled and supports_caching_by_id(config.llm.model))

        # Regenerate prompt_cache_key for fresh start
        if supports_prompt_cache_key_by_id(config.llm.model):
            config.llm.prompt_cache_key = f"dendrophis-{uuid.uuid4().hex[:16]}"

        self._emit(
            ContextUpdatedEvent(
                token_count=self._context.token_count,
                token_pct=self._context.token_pct,
                turn_count=self._context.get_turn_count(),
                full_chat_restored=False,
            )
        )
        self._emit(
            StatsUpdatedEvent(
                prompt_tokens=self._stats.prompt_tokens,
                completion_tokens=self._stats.completion_tokens,
                total_cost_usd=self._stats.total_cost_usd,
                tokens_per_sec=0.0,
                time_to_first_token=0.0,
            )
        )

    async def _on_send_message_request(self, event: SendMessageRequest) -> None:
        """Handle send message request from UI."""
        await self._session.send_message(event.text)

    def _on_session_save_request(self, event: SessionSaveRequest) -> None:
        """Handle session save request from UI."""
        if event.path:
            self._session._session_file = Path(event.path)
        self._session.save_session()

    def _on_session_load_request(self, event: SessionLoadRequest) -> None:
        """Handle session load request from UI."""
        self._session.load_session(event.path)

    async def _on_compact_request(self, event: CompactRequest) -> None:
        """Handle compaction request from UI."""
        await self._session.compact()

    def _on_primer_save_request(self, event: PrimerSaveRequest) -> None:
        """Handle primer save request from UI."""
        self._session.save_project_primer()

    def _on_primer_load_request(self, event: PrimerLoadRequest) -> None:
        """Handle primer load request from UI."""
        self._session.load_project_primer()

    def _on_primer_inject_request(self, event: PrimerInjectRequest) -> None:
        """Handle primer inject request from UI."""
        self._session.inject_primer_files()

    def _on_config_change_request(self, event: ConfigChangeRequest) -> None:
        """Handle generic config change request from UI."""
        if "llm" not in self._config_loader._raw:
            self._config_loader._raw["llm"] = {}
        self._config_loader._raw["llm"][event.key] = event.value
        self._config_loader.save()

    def _on_cancel_streaming_request(self, event: CancelStreamingRequest) -> None:
        """Handle cancel streaming request from UI."""
        self._session.cancel_streaming()

    def _on_track_file_request(self, event: TrackFileRequest) -> None:
        """Handle track file request from UI."""
        self._session.track_file(event.path)

    def _on_untrack_file_request(self, event: UntrackFileRequest) -> None:
        """Handle untrack file request from UI."""
        self._session.untrack_file(event.path)
