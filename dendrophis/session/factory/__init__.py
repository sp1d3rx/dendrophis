"""SessionFactory - Composition root for Session initialization."""

from __future__ import annotations

import threading
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
from dendrophis.session.chat import ChatOrchestrator, SessionStats
from dendrophis.session.events import SessionEventHandler
from dendrophis.session.persister import SessionPersister
from dendrophis.session.primer import PrimerManager
from dendrophis.session.session import Session
from dendrophis.session.subagents import SubagentBootstrapper
from dendrophis.session.tools import SessionToolExecutor
from dendrophis.skills.manager import SkillManager
from dendrophis.tools import ToolExecutor, create_builtin_registry


class SessionFactory:
    """Composition root for the Dendrophis Session.

    Handles the complex instantiation and wiring of all session components.
    """

    @staticmethod
    def create_session(
        config_loader: ConfigLoader,
        event_bus: EventBus | None = None,
        debug_logger: Callable[[str], None] | None = None,
        no_interactive: bool = False,
    ) -> Session:
        """Create and wire a Session instance with all standard vertical slices.

        Args:
            config_loader: Configuration loader
            event_bus: Event bus for inter-component communication
            debug_logger: Optional debug logger callback
            no_interactive: If True, skip interactive approval flows (useful for tests)
        """
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

        # 2b. Memory Association Generator
        association_generator = None
        if memory_store is not None:
            from dendrophis.memory.association import MemoryAssociationGenerator

            association_generator = MemoryAssociationGenerator(memory_store)

        # 3. Skills
        skill_manager = SkillManager(Path(__file__).parent.parent.parent / "skills")
        skill_manager.load_skills()

        # 4. Tools
        tool_registry = create_builtin_registry(
            bus,
            interactive=True,
            memory_store=memory_store,
            no_interactive=no_interactive,
        )
        tool_executor = ToolExecutor(tool_registry)

        # 4c. MCP Manager
        from dendrophis.tools.mcp import MCPManager

        mcp_manager = MCPManager(config, tool_registry, debug_logger)

        # 4b. Subagent Bootstrapper
        subagent_bootstrapper = SubagentBootstrapper(
            llm_client=llm,
            memory_store=memory_store,
            config=config,
        )
        subagent_bootstrapper.initialize()

        # 5. Intelligence & Detection
        understanding_detector = UnderstandingPhaseDetector(
            min_turns_before_established=config.caching.tier2_project_understanding_min_turns
        )

        # 5b. Session Persistence & Primer
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

        # Shared synchronization primitive variables
        pending_confirmations: dict[str, bool] = {}
        confirmation_results: dict[str, bool] = {}
        cancel_flag = threading.Event()
        stream_lock = threading.Lock()

        # 6. Session Tool Executor (Runs tool calls)
        tool_executor_session = SessionToolExecutor(
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            event_bus=bus,
            config=config,
            pending_confirmations=pending_confirmations,
            confirmation_results=confirmation_results,
            cancel_flag=cancel_flag,
            emit=lambda event: bus.publish(event),
            debug_logger=debug_logger,
        )

        # 7. Chat Orchestration
        chat = ChatOrchestrator(
            context=context,
            llm=llm,
            stats=stats,
            config=config,
            event_bus=bus,
            understanding_detector=understanding_detector,
            tool_registry=tool_registry,
            tool_executor_session=tool_executor_session,
            skill_manager=skill_manager,
            compactor=compact,
            association_generator=association_generator,
            debug_logger=debug_logger,
            stream_lock=stream_lock,
            cancel_flag=cancel_flag,
        )

        # 8. Session Facade
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
            chat=chat,
            subagent_bootstrapper=subagent_bootstrapper,
            mcp_manager=mcp_manager,
        )

        # Wire remaining reference objects
        session._pending_confirmations = pending_confirmations
        session._confirmation_results = confirmation_results
        session._cancel_flag = cancel_flag
        session._stream_lock = stream_lock
        session._tool_executor_session = tool_executor_session

        # Sync orchestrator state references
        chat.models = session.models
        chat.models_by_id = session.models_by_id
        chat.session_id = session.session_id

        # 9. Event Handling (Late binding to the session)
        session_event_handler = SessionEventHandler(
            session=session,
            event_bus=bus,
            config_loader=config_loader,
            context=context,
            llm=llm,
            stats=stats,
            understanding_detector=understanding_detector,
            debug_logger=debug_logger,
            stream_lock=stream_lock,
            cancel_flag=cancel_flag,
            pending_confirmations=pending_confirmations,
            confirmation_results=confirmation_results,
        )

        # Link the handler back to the session
        session._event_handler = session_event_handler

        return session
