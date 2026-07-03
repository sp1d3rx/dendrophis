"""Session — facade root tying all subsystems together.

Responsibilities:
- Provide facade interface for chat loop orchestration
- Manage model state list and active model settings
- Coordinate model switching lifecycle
- Reload configuration and release resources
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dendrophis.caching.understanding import UnderstandingPhaseDetector
from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.events import (
    ConfigReloadedEvent,
    ContextUpdatedEvent,
    EventBus,
    ModelSwitchedEvent,
    StatsUpdatedEvent,
    get_event_bus,
)
from dendrophis.llm.client import LLMClient, ModelInfo
from dendrophis.llm.compactor import compact
from dendrophis.llm.models import supports_prompt_cache_key_by_id
from dendrophis.memory.memory import MemoryStore
from dendrophis.session.chat import ChatOrchestrator, SessionStats
from dendrophis.session.events import SessionEventHandler
from dendrophis.session.persister import SessionPersister
from dendrophis.session.primer import PrimerManager
from dendrophis.session.subagents import SubagentBootstrapper
from dendrophis.session.tools import SessionToolExecutor
from dendrophis.skills.manager import SkillManager


class Session:
    """Connects config, LLM, context, and tools into a single conversation unit.

    Provides a clean facade delegating execution loop to ChatOrchestrator, and
    subagent registration/invocation to SubagentBootstrapper.
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
        chat: ChatOrchestrator | None = None,
        subagent_bootstrapper: SubagentBootstrapper | None = None,
        mcp_manager: Any | None = None,
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
        self.mcp_manager = mcp_manager
        self._tool_executor = tool_executor
        self._understanding_detector = understanding_detector or UnderstandingPhaseDetector(
            min_turns_before_established=self.config.caching.tier2_project_understanding_min_turns
        )
        self._compactor = compactor

        # Extracted components
        self._primer_manager = primer_manager
        self._persister = persister

        self.models: list[ModelInfo] = []
        self.models_by_id: dict[str, ModelInfo] = {}
        self.session_id: str = uuid.uuid4().hex

        # Set session-scoped prompt cache key for models that support it
        if self.config.llm.prompt_cache_key is None and supports_prompt_cache_key_by_id(self.config.llm.model):
            self.config.llm.prompt_cache_key = f"dendrophis-{self.session_id[:16]}"

        # Initialize event bus
        self._event_bus = event_bus or get_event_bus()

        self._debug_logger = debug_logger
        self._session_file: Path | None = None
        self._stream_lock = threading.Lock()
        self._cancel_flag = threading.Event()
        self._pending_confirmations: dict[str, bool] = {}
        self._confirmation_results: dict[str, bool] = {}

        # These are linked by the Factory
        self._event_handler: SessionEventHandler | None = None
        self._tool_executor_session: SessionToolExecutor | None = None

        # Set up orchestrator and subagent bootstrapper (DI-provided or fallbacks)
        self._chat = chat
        self._subagent_bootstrapper = subagent_bootstrapper

        # If orchestrator not provided, initialize fallback
        if self._chat is None:
            association_generator = None
            if self._memory_store is not None:
                from dendrophis.memory.association import MemoryAssociationGenerator

                association_generator = MemoryAssociationGenerator(self._memory_store)

            self._chat = ChatOrchestrator(
                context=self.context,
                llm=self.llm,
                stats=self.stats,
                config=self.config,
                event_bus=self._event_bus,
                understanding_detector=self._understanding_detector,
                tool_registry=self._tool_registry,
                tool_executor_session=self._tool_executor_session,
                skill_manager=self._skill_manager,
                compactor=self._compactor or compact,
                association_generator=association_generator,
                debug_logger=self._debug_logger,
                models=self.models,
                models_by_id=self.models_by_id,
                session_id=self.session_id,
                stream_lock=self._stream_lock,
                cancel_flag=self._cancel_flag,
            )

        # If subagent bootstrapper not provided, initialize fallback
        if self._subagent_bootstrapper is None:
            self._subagent_bootstrapper = SubagentBootstrapper(
                llm_client=self.llm,
                memory_store=self._memory_store,
                config=self.config,
            )
            self._subagent_bootstrapper.initialize()

    @property
    def memory_store(self) -> MemoryStore | None:
        """Public accessor for the memory store used by UI and tools."""
        return self._memory_store

    def _emit(self, event: Any) -> None:
        """Publish an event to the event bus."""
        if self._event_bus:
            self._event_bus.publish(event)

    def is_streaming(self) -> bool:
        """Check if currently streaming."""
        if self._chat:
            return self._chat.is_streaming()
        return False

    def is_caching_enabled(self) -> bool:
        """Return True if caching is enabled in config and supported by active model."""
        if self._chat:
            return self._chat.is_caching_enabled()
        return False

    def cancel_streaming(self) -> None:
        """Cancel the current streaming operation."""
        if self._chat:
            self._chat.cancel_flag.set()
        else:
            self._cancel_flag.set()

    def get_understanding_stats(self) -> dict[str, Any]:
        """Return understanding phase detection statistics."""
        return self._understanding_detector.get_stats()

    async def fetch_models(self) -> None:
        """Fetch available models and update context_limit from active model's context_window."""
        self.models = await self.llm.fetch_models()
        self.models_by_id = {model_info.id: model_info for model_info in self.models}
        if self._chat:
            self._chat.models = self.models
            self._chat.models_by_id = self.models_by_id

        active = self.models_by_id.get(self.config.llm.model)
        if active and active.context_window > 0:
            self.config.llm.context_limit = active.context_window
            self.context._config = self.config
            self._emit(ModelSwitchedEvent(model_id=active.id, context_window=active.context_window))
        # Update system prompt caching based on model support
        if self._chat:
            self.context.update_system_prompt_caching(self._chat.is_caching_enabled())

    def switch_model(self, model_id: str) -> None:
        """Switch the active model and persist the change to config."""
        self.config.llm.model = model_id
        self.llm._config = self.config.llm
        # Persist to config.yaml
        if "llm" in self.config_loader._raw:
            self.config_loader._raw["llm"]["model"] = model_id
            self.config_loader.save()

        active = self.models_by_id.get(model_id)
        if active and active.context_window > 0:
            self.config.llm.context_limit = active.context_window
            self.context._config = self.config
            context_window = active.context_window
        else:
            context_window = self.config.llm.context_limit

        self._emit(ModelSwitchedEvent(model_id=model_id, context_window=context_window))
        # Update system prompt caching based on new model's support
        if self._chat:
            self.context.update_system_prompt_caching(self._chat.is_caching_enabled())

    def set_tools(self, registry: Any, executor: Any) -> None:
        """Replace the tool registry and executor (used in tests)."""
        self._tool_registry = registry
        self._tool_executor = executor
        if self._chat:
            self._chat.tool_registry = registry
        # Update the session tool executor cleanly via its public update_tools method
        if self._tool_executor_session is not None:
            self._tool_executor_session.update_tools(registry, executor)

    async def invoke_subagent(
        self,
        agent: str,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke a subagent and return the execution summary."""
        if self._subagent_bootstrapper is None:
            return {"success": False, "error": "Subagent bootstrapper not initialized"}
        return await self._subagent_bootstrapper.invoke(agent, payload, context)

    async def send_message(self, text: str) -> None:
        """Send a user message and stream the response via events."""
        if self._chat:
            await self._chat.send_message(text)

    # =========================================================================
    # Session I/O (Delegated to SessionPersister)
    # =========================================================================

    def save_session(self) -> Path | None:
        """Save the current session to a JSON file.

        Returns:
            The path to the saved file, or None if there are no messages to save.
        """
        return self._persister.save(self.session_id, self._session_file)

    def load_session(self, path: str) -> dict[str, Any] | None:
        """Load a session from a JSON file.

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
        """Compact the context by summarizing older messages."""
        if self._chat:
            return await self._chat.compact()
        return await compact(self.context, self.llm, enable_caching=self.config.caching.enabled)

    # =========================================================================
    # Project Primer (Delegated to PrimerManager)
    # =========================================================================

    def save_project_primer(self) -> str | None:
        """Capture current project understanding as a primer file."""
        if not self.config.caching.pr_enabled:
            return None
        if self._primer_manager:
            project_id = self._primer_manager.save_project_primer()
            if project_id is not None:
                self._publish_primer_loaded()
            return project_id
        return None

    def load_project_primer(self) -> dict[str, Any] | None:
        """Load the project primer for the current working directory."""
        if not self.config.caching.pr_enabled:
            return None
        if self._primer_manager:
            return self._primer_manager.load_project_primer()
        return None

    def inject_primer_files(self) -> dict[str, Any]:
        """Re-read all primer-tracked files from disk and inject into context.

        Returns:
            Dict with 'injected' (count) and 'total' (file count).
        """
        if not self.config.caching.pr_enabled:
            return {"injected": 0, "total": 0}
        if self._primer_manager:
            result = self._primer_manager.inject_primer_files()
            self._publish_primer_loaded()
            return result
        return {"injected": 0, "total": 0}

    def track_file(self, path: str) -> bool:
        """Add a file to the project primer. Returns True on success."""
        if not self.config.caching.pr_enabled:
            return False
        if self._primer_manager:
            success = self._primer_manager.track_file(path)
            if success:
                self._publish_primer_loaded()
            return success
        return False

    def untrack_file(self, path: str) -> bool:
        """Remove a file from the project primer. Returns True on success."""
        if not self.config.caching.pr_enabled:
            return False
        if self._primer_manager:
            success = self._primer_manager.untrack_file(path)
            if success:
                self._publish_primer_loaded()
            return success
        return False

    def _publish_primer_loaded(self) -> None:
        """Publish PrimerLoadedEvent to notify UI/components of primer changes."""
        if not self.config.caching.pr_enabled:
            return
        if self._event_bus and self._primer_manager:
            from dendrophis.events import PrimerLoadedEvent

            info = self._primer_manager.load_project_primer()
            if info:
                self._event_bus.publish(
                    PrimerLoadedEvent(
                        project_id=info["project_id"],
                        project_name=info["project_name"],
                        file_count=info["file_count"],
                        turn_count=info.get("turn_count", 0),
                        understanding=info.get("understanding", ""),
                    )
                )
            else:
                self._event_bus.publish(
                    PrimerLoadedEvent(
                        project_id=None,
                        project_name=None,
                        file_count=0,
                        turn_count=0,
                        understanding=None,
                    )
                )

    # =========================================================================
    # Configuration & Cleanup
    def reset(self) -> None:
        """Synchronously reset the session context, stats, and understanding detector."""
        import uuid
        from dendrophis.llm.models import supports_caching_by_id, supports_prompt_cache_key_by_id

        self.context.reset()
        self.stats.reset()
        self._understanding_detector.reset()
        self.context.update_system_prompt_caching(
            self.config.caching.enabled and supports_caching_by_id(self.config.llm.model)
        )

        if supports_prompt_cache_key_by_id(self.config.llm.model):
            self.config.llm.prompt_cache_key = f"dendrophis-{uuid.uuid4().hex[:16]}"

    def reload_config(self) -> None:
        """Re-read config from disk and reinitialise the LLM client."""
        self.config_loader.reload()
        self.config = self.config_loader.config
        self.llm = LLMClient(self.config.llm)

        if getattr(self, "mcp_manager", None):
            import asyncio

            self._mcp_sync_task = asyncio.create_task(self.mcp_manager.sync_servers())

        self._emit(ConfigReloadedEvent())

    async def aclose(self) -> None:
        """Close the LLM client and release resources."""
        if getattr(self, "mcp_manager", None):
            await self.mcp_manager.aclose()
        await self.llm.aclose()
