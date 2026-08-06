"""Bridge between Dendrophis EventBus and WebSocket clients."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from dendrophis.events.bus import EventBus
from dendrophis.events.types import (
    MemoryAssociationEvent,
    MemorySavedEvent,
    MemorySearchResponse,
    MessageSentEvent,
    ModelSwitchedEvent,
    PrimerLoadedEvent,
    ReasoningDeltaEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
    ToolCallStartEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolResultEvent,
    TrackFileRequest,
    UntrackFileRequest,
    WriteProposalEvent,
)

logger = logging.getLogger(__name__)


class EventBridge:
    """Subscribes to EventBus events and broadcasts formatted JSON to WebSocket clients."""

    def __init__(self, event_bus: EventBus | None = None, history_size: int = 200) -> None:
        self.event_bus = event_bus
        self._clients: set[Any] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._loop: asyncio.AbstractEventLoop | None = None
        # Keep strong refs to in-flight send tasks so they aren't GC'd mid-send.
        self._send_tasks: set[asyncio.Task[Any]] = set()

        # State tracking for visualization graphs & file trees
        self._subagents: dict[str, dict[str, Any]] = {
            "dendrophis-core": {
                "id": "dendrophis-core",
                "name": "Dendrophis Root",
                "status": "active",
                "task": "Coordinator",
                "parent": None,
            }
        }
        self._tracked_files: dict[str, str] = {}

    def attach_event_bus(self, event_bus: EventBus) -> None:
        """Attach to an EventBus instance and register event handlers."""
        self.event_bus = event_bus

        # Subscribe to general events
        event_bus.subscribe(StreamingStartedEvent, self._handle_stream_start)
        event_bus.subscribe(StreamingFinishedEvent, self._handle_stream_finish)
        event_bus.subscribe(ReasoningDeltaEvent, self._handle_reasoning)
        event_bus.subscribe(ToolCallStartEvent, self._handle_tool_start)
        event_bus.subscribe(ToolExecutionStartedEvent, self._handle_tool_exec_start)
        event_bus.subscribe(ToolExecutionFinishedEvent, self._handle_tool_exec_finish)
        event_bus.subscribe(ToolResultEvent, self._handle_tool_result)
        event_bus.subscribe(MemorySavedEvent, self._handle_memory_saved)
        event_bus.subscribe(MemoryAssociationEvent, self._handle_memory_association)
        event_bus.subscribe(MemorySearchResponse, self._handle_memory_search)
        event_bus.subscribe(PrimerLoadedEvent, self._handle_primer_loaded)
        event_bus.subscribe(TrackFileRequest, self._handle_track_file)
        event_bus.subscribe(UntrackFileRequest, self._handle_untrack_file)
        event_bus.subscribe(WriteProposalEvent, self._handle_file_write_proposal)
        event_bus.subscribe(ModelSwitchedEvent, self._handle_model_switched)
        event_bus.subscribe(MessageSentEvent, self._handle_message_sent)

    def register_client(self, websocket: Any) -> list[dict[str, Any]]:
        """Register a connected WebSocket client and return recent event history."""
        self._clients.add(websocket)

        # Return snapshot of initial graph & file states followed by history
        initial_states = [
            {
                "type": "SUBAGENT_STATE",
                "payload": {
                    "agents": list(self._subagents.values()),
                    "action": "snapshot",
                },
                "timestamp": datetime.now(UTC).isoformat(),
            },
            {
                "type": "FILESYSTEM_CHANGE",
                "payload": {
                    "files": self._tracked_files,
                    "action": "snapshot",
                },
                "timestamp": datetime.now(UTC).isoformat(),
            },
            {
                "type": "MEMORY_RETRIEVAL",
                "payload": {
                    "id": "mem-core-sys",
                    "content": "System Prompt & Core Directives",
                    "relevance": 1.0,
                    "action": "retrieved",
                },
                "timestamp": datetime.now(UTC).isoformat(),
            },
            {
                "type": "MEMORY_RETRIEVAL",
                "payload": {
                    "id": "mem-session-state",
                    "content": "Active Session Context",
                    "relevance": 0.85,
                    "action": "retrieved",
                },
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ]
        return initial_states + list(self._history)

    def unregister_client(self, websocket: Any) -> None:
        """Unregister a disconnected WebSocket client."""
        self._clients.discard(websocket)

    def broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        """Construct a standardized event package and send to all connected clients."""
        event_data = {
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._history.append(event_data)

        if not self._clients:
            return

        message = json.dumps(event_data)
        dead_clients = set()

        for client in self._clients:
            try:
                # FastAPI WebSocket send_text
                task = asyncio.create_task(client.send_text(message))
                self._send_tasks.add(task)
                task.add_done_callback(self._send_tasks.discard)
            except Exception:
                dead_clients.add(client)

        for dead in dead_clients:
            self.unregister_client(dead)

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    def _handle_stream_start(self, event: StreamingStartedEvent) -> None:
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🚀 Started turn for prompt: '{event.user_message[:80]}...'",
                "level": "info",
            },
        )
        self.broadcast(
            "MEMORY_RETRIEVAL",
            {
                "id": f"prompt-ctx-{len(self._history)}",
                "content": f"Turn Context: '{event.user_message[:60]}'",
                "relevance": 0.9,
                "action": "retrieved",
            },
        )
        self._update_agent_status("dendrophis-core", "thinking", task=event.user_message[:60])

    def _handle_stream_finish(self, event: StreamingFinishedEvent) -> None:
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": "✅ Streaming finished.",
                "level": "success",
            },
        )
        self._update_agent_status("dendrophis-core", "idle", task="Waiting for input")

    def _handle_reasoning(self, event: ReasoningDeltaEvent) -> None:
        if event.delta:
            self.broadcast(
                "THOUGHT_LOG",
                {
                    "text": event.delta,
                    "level": "thought",
                },
            )

    def _handle_tool_start(self, event: ToolCallStartEvent) -> None:
        agent_id = f"subagent-{event.name}"
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🛠️ Initiated tool call [{event.name}] (ID: {event.id[:8]})",
                "level": "info",
            },
        )
        # Register or update subagent in graph
        self._subagents[agent_id] = {
            "id": agent_id,
            "name": f"Tool: {event.name}",
            "status": "executing",
            "task": f"Executing {event.name}",
            "parent": "dendrophis-core",
        }
        self.broadcast(
            "SUBAGENT_STATE",
            {
                "agents": list(self._subagents.values()),
                "active_agent": agent_id,
                "action": "update",
            },
        )

    def _handle_tool_exec_start(self, event: ToolExecutionStartedEvent) -> None:
        tool_name = getattr(event, "tool_name", "tool")
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"⚡ Running tool: {tool_name}",
                "level": "info",
            },
        )

    def _handle_tool_exec_finish(self, event: ToolExecutionFinishedEvent) -> None:
        tool_name = getattr(event, "tool_name", "tool")
        agent_id = f"subagent-{tool_name}"
        if agent_id in self._subagents:
            self._subagents[agent_id]["status"] = "idle"
        self.broadcast(
            "SUBAGENT_STATE",
            {
                "agents": list(self._subagents.values()),
                "active_agent": "dendrophis-core",
                "action": "update",
            },
        )

    def _handle_tool_result(self, event: ToolResultEvent) -> None:
        level = "success" if event.consecutive_failures == 0 else "error"
        status_icon = "✓" if level == "success" else "❌"
        desc = f" ({event.description})" if event.description else ""
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"{status_icon} Tool [{event.name}]{desc} completed.",
                "level": level,
            },
        )

        # File system tracking if file tool
        if event.name in ("write_to_file", "replace_file_content", "multi_replace_file_content", "view_file"):
            action_map = {
                "write_to_file": "created",
                "replace_file_content": "modified",
                "multi_replace_file_content": "modified",
                "view_file": "accessed",
            }
            path = event.arguments or event.description or "workspace"
            self._tracked_files[path] = action_map.get(event.name, "modified")
            self.broadcast(
                "FILESYSTEM_CHANGE",
                {
                    "path": path,
                    "action": action_map.get(event.name, "modified"),
                    "files": self._tracked_files,
                },
            )

        # Memory tracking if memory tool
        if "memory" in event.name or event.name in ("save_memory", "search_memory", "remember"):
            self.broadcast(
                "MEMORY_RETRIEVAL",
                {
                    "id": f"mem-tool-{event.tool_call_id[:8]}",
                    "content": (event.content or event.arguments or "Memory Query Results")[:120],
                    "relevance": 0.88,
                    "action": "retrieved",
                },
            )

    def _handle_memory_saved(self, event: MemorySavedEvent) -> None:
        self.broadcast(
            "MEMORY_RETRIEVAL",
            {
                "id": event.memory_id,
                "content": event.content[:120],
                "relevance": 1.0,
                "action": "saved",
                "tags": event.tags,
            },
        )
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🧠 Memory saved: '{event.content[:60]}...'",
                "level": "info",
            },
        )

    def _handle_memory_association(self, event: MemoryAssociationEvent) -> None:
        self.broadcast(
            "MEMORY_RETRIEVAL",
            {
                "id": event.memory_id,
                "content": event.memory_summary,
                "relevance": event.relevance_score,
                "action": "association",
                "confidence": event.confidence,
            },
        )
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"💡 Memory association: {event.memory_summary}",
                "level": "info",
            },
        )

    def _handle_memory_search(self, event: MemorySearchResponse) -> None:
        for res in event.results[:5]:
            self.broadcast(
                "MEMORY_RETRIEVAL",
                {
                    "id": str(res.get("id", "mem")),
                    "content": str(res.get("content", ""))[:120],
                    "relevance": float(res.get("score", 0.8)),
                    "action": "retrieved",
                },
            )

    def _handle_primer_loaded(self, event: PrimerLoadedEvent) -> None:
        project_name = event.project_name or "Project Primer"
        self.broadcast(
            "MEMORY_RETRIEVAL",
            {
                "id": f"primer-{event.project_id or 'default'}",
                "content": f"{project_name} ({event.file_count} files tracked)",
                "relevance": 0.95,
                "action": "retrieved",
            },
        )
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"📚 Project primer loaded: {project_name}",
                "level": "info",
            },
        )

    def _handle_track_file(self, event: TrackFileRequest) -> None:
        self._tracked_files[event.path] = "tracked"
        self.broadcast(
            "FILESYSTEM_CHANGE",
            {
                "path": event.path,
                "action": "tracked",
                "files": self._tracked_files,
            },
        )

    def _handle_untrack_file(self, event: UntrackFileRequest) -> None:
        self._tracked_files.pop(event.path, None)
        self.broadcast(
            "FILESYSTEM_CHANGE",
            {
                "path": event.path,
                "action": "untracked",
                "files": self._tracked_files,
            },
        )

    def _handle_file_write_proposal(self, event: WriteProposalEvent) -> None:
        self._tracked_files[event.file_path] = "modified"
        self.broadcast(
            "FILESYSTEM_CHANGE",
            {
                "path": event.file_path,
                "action": "proposal",
                "files": self._tracked_files,
            },
        )

    def _handle_model_switched(self, event: ModelSwitchedEvent) -> None:
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🔄 Model switched to {event.model_id} (Context: {event.context_window} tokens)",
                "level": "info",
            },
        )

    def _handle_message_sent(self, event: MessageSentEvent) -> None:
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"💬 User: {event.message[:100]}",
                "level": "user",
            },
        )

    def _update_agent_status(self, agent_id: str, status: str, task: str = "") -> None:
        if agent_id in self._subagents:
            self._subagents[agent_id]["status"] = status
            if task:
                self._subagents[agent_id]["task"] = task
            self.broadcast(
                "SUBAGENT_STATE",
                {
                    "agents": list(self._subagents.values()),
                    "active_agent": agent_id,
                    "action": "update",
                },
            )
