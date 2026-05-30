from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dendrophis.caching.understanding import UnderstandingPhaseDetector
from dendrophis.config.loader import ConfigLoader
from dendrophis.context.manager import ContextManager
from dendrophis.events import (
    EventBus,
    get_event_bus,
)
from dendrophis.llm.client import LLMClient
from dendrophis.llm.compactor import compact
from dendrophis.memory.embedder import create_embedder
from dendrophis.memory.memory import MemoryStore
from dendrophis.session.events import SessionEventHandler
from dendrophis.session.persister import SessionPersister
from dendrophis.session.primer import PrimerManager
from dendrophis.session.session import Session, SessionStats
from dendrophis.session.tools import SessionToolExecutor
from dendrophis.skills.manager import SkillManager
from dendrophis.tools import ToolExecutor, create_builtin_registry


class SessionFactory:
    """
    Composition root for the Dendrophis Session.
    Handles the complex instantiation and wiring of all session components.
    """

    @staticmethod
    def create_session(
        config_loader: ConfigLoader,
        event_bus: EventBus | None = None,
        debug_logger: Callable[[str], None] | None = None,
    ) -> Session:
        config = config_loader.config
        bus = event_bus or get_event_bus()

        # 1. Core Infrastructure
        context = ContextManager(config)
        llm = LLMClient(config.llm)
        stats = SessionStats()

        # 2. Memory & Embedding
        embedder = create_embedder(
            "openai",
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
            openai_model="text-embedding-3-small",
        )
        memory_store = MemoryStore(config.memory_db, embedder=embedder)

        # 3. Skills
        skill_manager = SkillManager(Path(__file__).parent.parent.parent / "skills")
        skill_manager.load_skills()

        # 4. Tools
        tool_registry = create_builtin_registry(bus, interactive=True, memory_store=memory_store)
        tool_executor = ToolExecutor(tool_registry)

        # 5. Intelligence & Detection
        understanding_detector = UnderstandingPhaseDetector(
            min_turns_before_established=config.caching.tier2_project_understanding_min_turns
        )

        # 5b. Session Persistence & Primer
        # Create these before Session so Session can receive them via DI
        primer_manager = PrimerManager(
            context=context,
            understanding_detector=understanding_detector,
            debug_logger=debug_logger,
        )
        persister = SessionPersister(
            context=context,
            stats=stats,
            config=config,
            debug_logger=debug_logger,
        )

        # 6. Session Orchestration (The tricky part)
        # We create the session with all components except the handler to avoid circularity
        session = Session(
            config_loader=config_loader,
            event_bus=bus,
            context=context,
            llm=llm,
            stats=stats,
            memory_store=memory_store,
            skill_manager=skill_manager,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            understanding_detector=understanding_detector,
            compactor=compact,
            debug_logger=debug_logger,
            primer_manager=primer_manager,
            persister=persister,
        )

        # 7. Event Handling (Late binding to the session)
        # We need to pass the objects that the Session needs for its internal loops
        session_event_handler = SessionEventHandler(
            session=session,
            event_bus=bus,
            config_loader=config_loader,
            context=context,
            llm=llm,
            stats=stats,
            understanding_detector=understanding_detector,
            debug_logger=debug_logger,
            stream_lock=session._stream_lock,
            cancel_flag=session._cancel_flag,
            pending_confirmations=session._pending_confirmations,
            confirmation_results=session._confirmation_results,
        )

        # Link the handler back to the session
        session._event_handler = session_event_handler

        # 8. Tool Executor Session (Internal to Session)
        session._tool_executor_session = SessionToolExecutor(
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            event_bus=bus,
            config=config,
            pending_confirmations=session._pending_confirmations,
            confirmation_results=session._confirmation_results,
            cancel_flag=session._cancel_flag,
            emit=session._emit,
            debug_logger=debug_logger,
        )

        # 9. Fetch models (initialization step)
        # Note: In a production system, we might want to await this
        # but since this is a factory, we'll let the session manage the async call.

        return session
