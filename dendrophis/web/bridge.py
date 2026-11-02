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
    CompactionCompletedEvent,
    CompactionFailedEvent,
    CompactionStartedEvent,
    ContextUpdatedEvent,
    EditProposalEvent,
    ErrorEvent,
    FileCreatedEvent,
    FileModifiedEvent,
    MemoryAssociationEvent,
    MemorySavedEvent,
    MemorySearchResponse,
    MemoryStatsUpdatedEvent,
    MessageSentEvent,
    ModelSwitchedEvent,
    PrimerLoadedEvent,
    ReasoningDeltaEvent,
    RetryEvent,
    StatsUpdatedEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
    SubagentProgressEvent,
    SubagentTaskFinishedEvent,
    SubagentTaskStartedEvent,
    TextDeltaEvent,
    TodoChangedEvent,
    TodoUpdatedEvent,
    ToolCallStartEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolResultEvent,
    TrackFileRequest,
    UntrackFileRequest,
    UsageEvent,
    WriteProposalEvent,
)

logger = logging.getLogger(__name__)

# Bound on a single WebSocket send. A blackholed peer (a connection that never RSTs)
# would otherwise leave client.send_text() awaiting forever, so its done callback
# never fires and the client is never retired -> one stuck send task per broadcast
# (unbounded). After this many seconds the send is abandoned and the client dropped.
_SEND_TIMEOUT_S = 10.0

_FILE_TOOL_ACTIONS: dict[str, str] = {
    "read": "accessed",
    "read_file": "accessed",
    "view_file": "accessed",
    "list_dir": "accessed",
    "edit": "modified",
    "edit_function": "modified",
    "replace_function": "modified",
    "patch": "modified",
    "replace_file_content": "modified",
    "multi_replace_file_content": "modified",
    "append": "modified",
    "write": "created",
    "write_file": "created",
    "write_to_file": "created",
}


def _extract_file_path_from_args(raw_arguments: str, fallback_description: str = "") -> str | None:
    """Extract a target file path from JSON tool arguments or fallback string."""
    if raw_arguments:
        stripped_arguments = raw_arguments.strip()
        if stripped_arguments.startswith("{"):
            try:
                parsed_args = json.loads(stripped_arguments)
                if isinstance(parsed_args, dict):
                    for key_name in ("file_path", "path", "target_file", "directory", "dir_path"):
                        candidate = parsed_args.get(key_name)
                        if isinstance(candidate, str) and candidate.strip():
                            return candidate.strip()
            except Exception:
                pass
        elif "\n" not in stripped_arguments and len(stripped_arguments) < 260:
            return stripped_arguments
    if fallback_description and "\n" not in fallback_description and len(fallback_description) < 260:
        return fallback_description.strip()
    return None


def _extract_diff_from_content(raw_content: str) -> str:
    """Extract unified diff string from a JSON tool result if present."""
    if not raw_content:
        return ""
    try:
        parsed_result = json.loads(raw_content)
        if isinstance(parsed_result, dict):
            diff_value = parsed_result.get("diff")
            if isinstance(diff_value, str):
                return diff_value
    except Exception:
        pass
    return ""


class EventBridge:
    """Subscribes to EventBus events and broadcasts formatted JSON to WebSocket clients."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        history_size: int = 200,
        initial_model: str = "dendrophis-agent",
        context_limit: int = 131072,
    ) -> None:
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
                "kind": "root",
                "status": "active",
                "task": "Coordinator",
                "parent": None,
                "calls": 0,
            }
        }
        self._tracked_files: dict[str, str] = {}
        self._file_details: dict[str, dict[str, Any]] = {}
        self._tool_executions: deque[dict[str, Any]] = deque(maxlen=100)
        self._active_tool_id: str | None = None
        self._stream_sequence: int = 0
        self._active_thought_stream_id: str | None = None
        self._active_thought_text: str = ""
        self._active_response_stream_id: str | None = None
        self._active_response_text: str = ""

        self._session_stats: dict[str, Any] = {
            "model": initial_model,
            "context_limit": context_limit,
            "context_tokens": 0,
            "context_pct": 0.0,
            "turn_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "cache_hit_pct": 0.0,
            "total_cost_usd": 0.0,
            "tokens_per_sec": 0.0,
            "time_to_first_token": 0.0,
            "compactions_count": 0,
            "tokens_reclaimed": 0,
            "total_memories": 0,
            "todos": [],
        }

    def attach_event_bus(self, event_bus: EventBus) -> None:
        """Attach to an EventBus instance and register event handlers."""
        self.event_bus = event_bus

        # Stream & turn lifecycle
        event_bus.subscribe(StreamingStartedEvent, self._handle_stream_start)
        event_bus.subscribe(StreamingFinishedEvent, self._handle_stream_finish)
        event_bus.subscribe(ReasoningDeltaEvent, self._handle_reasoning)
        event_bus.subscribe(TextDeltaEvent, self._handle_text_delta)
        event_bus.subscribe(ErrorEvent, self._handle_error)
        event_bus.subscribe(RetryEvent, self._handle_retry)

        # Tool lifecycle
        event_bus.subscribe(ToolCallStartEvent, self._handle_tool_start)
        event_bus.subscribe(ToolExecutionStartedEvent, self._handle_tool_exec_start)
        event_bus.subscribe(ToolExecutionFinishedEvent, self._handle_tool_exec_finish)
        event_bus.subscribe(ToolResultEvent, self._handle_tool_result)

        # Subagent lifecycle
        event_bus.subscribe(SubagentTaskStartedEvent, self._handle_subagent_started)
        event_bus.subscribe(SubagentProgressEvent, self._handle_subagent_progress)
        event_bus.subscribe(SubagentTaskFinishedEvent, self._handle_subagent_finished)

        # Memory & primer events
        event_bus.subscribe(MemorySavedEvent, self._handle_memory_saved)
        event_bus.subscribe(MemoryAssociationEvent, self._handle_memory_association)
        event_bus.subscribe(MemorySearchResponse, self._handle_memory_search)
        event_bus.subscribe(MemoryStatsUpdatedEvent, self._handle_memory_stats)
        event_bus.subscribe(PrimerLoadedEvent, self._handle_primer_loaded)

        # Filesystem events
        event_bus.subscribe(TrackFileRequest, self._handle_track_file)
        event_bus.subscribe(UntrackFileRequest, self._handle_untrack_file)
        event_bus.subscribe(WriteProposalEvent, self._handle_file_write_proposal)
        event_bus.subscribe(EditProposalEvent, self._handle_file_edit_proposal)
        event_bus.subscribe(FileCreatedEvent, self._handle_file_created)
        event_bus.subscribe(FileModifiedEvent, self._handle_file_modified)

        # Session, stats, context & compaction events
        event_bus.subscribe(ModelSwitchedEvent, self._handle_model_switched)
        event_bus.subscribe(MessageSentEvent, self._handle_message_sent)
        event_bus.subscribe(StatsUpdatedEvent, self._handle_stats_updated)
        event_bus.subscribe(UsageEvent, self._handle_usage_updated)
        event_bus.subscribe(ContextUpdatedEvent, self._handle_context_updated)
        event_bus.subscribe(CompactionStartedEvent, self._handle_compaction_started)
        event_bus.subscribe(CompactionCompletedEvent, self._handle_compaction_completed)
        event_bus.subscribe(CompactionFailedEvent, self._handle_compaction_failed)
        event_bus.subscribe(TodoUpdatedEvent, self._handle_todo_updated)
        event_bus.subscribe(TodoChangedEvent, self._handle_todo_updated)

    def register_client(self, websocket: Any) -> list[dict[str, Any]]:
        """Register a connected WebSocket client and return recent event history."""
        self._clients.add(websocket)
        now_iso = datetime.now(UTC).isoformat()

        # Return snapshot of initial graph & file states followed by history
        initial_states = [
            {
                "type": "SUBAGENT_STATE",
                "payload": {
                    "agents": list(self._subagents.values()),
                    "action": "snapshot",
                },
                "timestamp": now_iso,
            },
            {
                "type": "FILESYSTEM_CHANGE",
                "payload": {
                    "files": self._tracked_files,
                    "details": self._file_details,
                    "action": "snapshot",
                },
                "timestamp": now_iso,
            },
            {
                "type": "SESSION_STATS",
                "payload": dict(self._session_stats),
                "timestamp": now_iso,
            },
            {
                "type": "TOOL_TIMELINE",
                "payload": {
                    "executions": list(self._tool_executions),
                    "action": "snapshot",
                },
                "timestamp": now_iso,
            },
            {
                "type": "MEMORY_RETRIEVAL",
                "payload": {
                    "id": "mem-core-sys",
                    "content": "System Prompt & Core Directives",
                    "relevance": 1.0,
                    "action": "primer",
                    "tags": ["system", "core"],
                },
                "timestamp": now_iso,
            },
            {
                "type": "MEMORY_RETRIEVAL",
                "payload": {
                    "id": "mem-session-state",
                    "content": "Active Session Context",
                    "relevance": 0.85,
                    "action": "retrieved",
                    "tags": ["session", "context"],
                },
                "timestamp": now_iso,
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

        # Coalesce streaming THOUGHT_LOG deltas with the same stream_id in history
        stream_identifier = payload.get("stream_id") if event_type == "THOUGHT_LOG" else None
        updated_existing = False
        if stream_identifier and self._history:
            for history_entry in reversed(self._history):
                if (
                    history_entry.get("type") == "THOUGHT_LOG"
                    and history_entry.get("payload", {}).get("stream_id") == stream_identifier
                ):
                    history_entry["payload"] = payload
                    history_entry["timestamp"] = event_data["timestamp"]
                    updated_existing = True
                    break

        if not updated_existing:
            self._history.append(event_data)

        if not self._clients:
            return

        message = json.dumps(event_data)

        # Iterate a copy: a failed send retires the client via its done callback,
        # which would otherwise mutate the set mid-iteration.
        for client in list(self._clients):
            try:
                task = asyncio.create_task(self._send_with_timeout(client, message))
            except Exception:
                self.unregister_client(client)
                continue
            self._send_tasks.add(task)
            task.add_done_callback(lambda done_task, target_client=client: self._on_send_done(target_client, done_task))

    async def _send_with_timeout(self, client: Any, message: str) -> None:
        """Send one message to a client with a bounded wait."""
        await asyncio.wait_for(client.send_text(message), timeout=_SEND_TIMEOUT_S)

    def _on_send_done(self, client: Any, task: asyncio.Task[Any]) -> None:
        """Done callback for a broadcast send."""
        self._send_tasks.discard(task)
        if task.cancelled():
            return
        send_exception = task.exception()
        if send_exception is not None:
            logger.debug("WebSocket send failed, dropping client: %s", send_exception)
            self.unregister_client(client)

    def _reset_stream_buffers(self) -> None:
        """Reset active thought and response stream accumulators at boundaries."""
        self._active_thought_stream_id = None
        self._active_thought_text = ""
        self._active_response_stream_id = None
        self._active_response_text = ""

    def _record_file_activity(
        self,
        file_path: str,
        action: str,
        diff_text: str = "",
        tool_name: str = "",
    ) -> None:
        """Record file activity in tracked files and detailed metadata."""
        if not file_path:
            return
        self._tracked_files[file_path] = action
        existing_detail = self._file_details.get(file_path, {})
        touch_count = int(existing_detail.get("touch_count", 0)) + 1
        updated_diff = diff_text or existing_detail.get("diff", "")
        self._file_details[file_path] = {
            "path": file_path,
            "action": action,
            "touch_count": touch_count,
            "tool_name": tool_name or existing_detail.get("tool_name", ""),
            "diff": updated_diff[:8000] if updated_diff else "",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.broadcast(
            "FILESYSTEM_CHANGE",
            {
                "path": file_path,
                "action": action,
                "files": self._tracked_files,
                "details": self._file_details,
            },
        )

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    def _handle_stream_start(self, event: StreamingStartedEvent) -> None:
        self._reset_stream_buffers()
        prompt_preview = event.user_message[:120]
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🚀 Turn started: {prompt_preview}",
                "level": "info",
            },
        )
        self.broadcast(
            "MEMORY_RETRIEVAL",
            {
                "id": f"prompt-ctx-{len(self._history)}",
                "content": f"Turn Context: '{event.user_message[:80]}'",
                "relevance": 0.9,
                "action": "retrieved",
                "tags": ["turn-prompt"],
            },
        )
        self._update_agent_status("dendrophis-core", "thinking", task=event.user_message[:80])

    def _handle_stream_finish(self, event: StreamingFinishedEvent) -> None:
        self._reset_stream_buffers()
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": "✅ Turn completed.",
                "level": "success",
            },
        )
        self._update_agent_status("dendrophis-core", "idle", task="Waiting for input")

    def _handle_reasoning(self, event: ReasoningDeltaEvent) -> None:
        if not event.delta:
            return
        if self._active_thought_stream_id is None:
            self._stream_sequence += 1
            self._active_thought_stream_id = f"thought-{self._stream_sequence}"
            self._active_thought_text = ""
        self._active_thought_text += event.delta
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": self._active_thought_text,
                "delta": event.delta,
                "stream_id": self._active_thought_stream_id,
                "level": "thought",
            },
        )

    def _handle_text_delta(self, event: TextDeltaEvent) -> None:
        if not event.delta:
            return
        if self._active_response_stream_id is None:
            self._stream_sequence += 1
            self._active_response_stream_id = f"response-{self._stream_sequence}"
            self._active_response_text = ""
        self._active_response_text += event.delta
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": self._active_response_text,
                "delta": event.delta,
                "stream_id": self._active_response_stream_id,
                "level": "assistant",
            },
        )

    def _handle_error(self, event: ErrorEvent) -> None:
        self._reset_stream_buffers()
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"⚠ Error: {event.message}",
                "level": "error",
            },
        )

    def _handle_retry(self, event: RetryEvent) -> None:
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🔄 Retry #{event.attempt} in {event.delay:.1f}s: {event.message}",
                "level": "warning",
            },
        )

    def _handle_tool_start(self, event: ToolCallStartEvent) -> None:
        self._reset_stream_buffers()
        agent_id = f"subagent-{event.name}"
        self._active_tool_id = event.id
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🛠️ Tool call [{event.name}] ({event.id[:8]})",
                "level": "tool",
                "tool_name": event.name,
                "tool_call_id": event.id,
            },
        )
        existing_node = self._subagents.get(agent_id, {})
        call_count = int(existing_node.get("calls", 0)) + 1
        self._subagents[agent_id] = {
            "id": agent_id,
            "name": f"Tool: {event.name}",
            "kind": "tool",
            "status": "executing",
            "task": f"Executing {event.name}",
            "parent": "dendrophis-core",
            "calls": call_count,
        }
        self._tool_executions.append(
            {
                "id": event.id,
                "name": event.name,
                "kind": "tool",
                "status": "running",
                "arguments": "",
                "output": "",
                "diff": "",
                "duration_seconds": None,
                "started_at": datetime.now(UTC).isoformat(),
            }
        )
        self.broadcast(
            "SUBAGENT_STATE",
            {
                "agents": list(self._subagents.values()),
                "active_agent": agent_id,
                "action": "update",
            },
        )
        self.broadcast(
            "TOOL_TIMELINE",
            {
                "executions": list(self._tool_executions),
                "action": "update",
            },
        )

    def _handle_tool_exec_start(self, event: ToolExecutionStartedEvent) -> None:
        tool_name = getattr(event, "tool_name", "tool")
        raw_arguments = getattr(event, "arguments", "") or ""
        description_text = getattr(event, "description", "") or ""

        # Attach arguments to the latest matching running tool entry
        for execution_entry in reversed(self._tool_executions):
            if execution_entry["name"] == tool_name and not execution_entry.get("arguments"):
                execution_entry["arguments"] = raw_arguments
                execution_entry["description"] = description_text
                break
        else:
            self._tool_executions.append(
                {
                    "id": f"exec-{len(self._tool_executions) + 1}",
                    "name": tool_name,
                    "kind": "tool",
                    "status": "running",
                    "arguments": raw_arguments,
                    "description": description_text,
                    "output": "",
                    "diff": "",
                    "duration_seconds": None,
                    "started_at": datetime.now(UTC).isoformat(),
                }
            )

        agent_id = f"subagent-{tool_name}"
        extracted_path = _extract_file_path_from_args(raw_arguments, description_text)
        task_label = (
            f"{tool_name}: {extracted_path}" if extracted_path else (description_text or f"Running {tool_name}")
        )
        if agent_id in self._subagents:
            self._subagents[agent_id]["status"] = "executing"
            self._subagents[agent_id]["task"] = task_label[:80]

        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"⚡ Executing {task_label[:100]}",
                "level": "tool",
                "tool_name": tool_name,
            },
        )
        self.broadcast(
            "TOOL_TIMELINE",
            {
                "executions": list(self._tool_executions),
                "action": "update",
            },
        )

    def _handle_tool_exec_finish(self, event: ToolExecutionFinishedEvent) -> None:
        tool_name = getattr(event, "tool_name", "tool")
        agent_id = f"subagent-{tool_name}"
        if agent_id in self._subagents:
            self._subagents[agent_id]["status"] = "idle"

        for execution_entry in reversed(self._tool_executions):
            if (event.tool_call_id and execution_entry["id"] == event.tool_call_id) or (
                execution_entry["name"] == tool_name and execution_entry["status"] == "running"
            ):
                execution_entry["status"] = "success" if event.success else "error"
                execution_entry["duration_seconds"] = round(float(event.duration_seconds or 0.0), 3)
                if event.error_message:
                    execution_entry["error"] = event.error_message
                break

        self.broadcast(
            "SUBAGENT_STATE",
            {
                "agents": list(self._subagents.values()),
                "active_agent": "dendrophis-core",
                "action": "update",
            },
        )
        self.broadcast(
            "TOOL_TIMELINE",
            {
                "executions": list(self._tool_executions),
                "action": "update",
            },
        )

    def _handle_tool_result(self, event: ToolResultEvent) -> None:
        level = "success" if event.consecutive_failures == 0 else "error"
        status_icon = "✓" if level == "success" else "❌"
        description_suffix = f" ({event.description})" if event.description else ""
        extracted_diff = _extract_diff_from_content(event.content)

        # Update matching tool execution record with output and arguments
        matched_entry = None
        for execution_entry in reversed(self._tool_executions):
            if execution_entry["id"] == event.tool_call_id or (
                execution_entry["name"] == event.name and not execution_entry.get("output")
            ):
                matched_entry = execution_entry
                break

        if matched_entry is None:
            matched_entry = {
                "id": event.tool_call_id or f"res-{len(self._tool_executions) + 1}",
                "name": event.name,
                "kind": "tool",
                "status": level,
                "arguments": event.arguments or "",
                "description": event.description or "",
                "output": "",
                "diff": "",
                "duration_seconds": None,
                "started_at": datetime.now(UTC).isoformat(),
            }
            self._tool_executions.append(matched_entry)

        if event.arguments and not matched_entry.get("arguments"):
            matched_entry["arguments"] = event.arguments
        matched_entry["status"] = level
        matched_entry["output"] = (event.content or "")[:8000]
        if extracted_diff:
            matched_entry["diff"] = extracted_diff[:8000]

        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"{status_icon} Tool [{event.name}]{description_suffix} completed.",
                "level": level,
                "tool_name": event.name,
                "tool_call_id": event.tool_call_id,
            },
        )
        self.broadcast(
            "TOOL_TIMELINE",
            {
                "executions": list(self._tool_executions),
                "action": "update",
            },
        )

        # File system tracking for all Dendrophis and legacy file tools
        if event.name in _FILE_TOOL_ACTIONS:
            file_action = _FILE_TOOL_ACTIONS[event.name]
            target_path = _extract_file_path_from_args(event.arguments, event.description) or "workspace"
            self._record_file_activity(
                file_path=target_path,
                action=file_action,
                diff_text=extracted_diff,
                tool_name=event.name,
            )

        # Memory tracking if memory tool
        if "memory" in event.name or event.name in ("save_memory", "search_memory", "recall_memory", "remember"):
            self.broadcast(
                "MEMORY_RETRIEVAL",
                {
                    "id": f"mem-tool-{event.tool_call_id[:8]}",
                    "content": (event.content or event.arguments or "Memory Query Results")[:160],
                    "relevance": 0.88,
                    "action": "retrieved",
                    "tags": [event.name],
                },
            )

    def _handle_subagent_started(self, event: SubagentTaskStartedEvent) -> None:
        self._reset_stream_buffers()
        node_id = f"agent-{event.agent_name}"
        task_summary = ""
        if isinstance(event.payload, dict):
            task_summary = str(
                event.payload.get("task")
                or event.payload.get("prompt")
                or event.payload.get("question")
                or event.payload.get("description")
                or ""
            )[:100]
        existing_node = self._subagents.get(node_id, {})
        call_count = int(existing_node.get("calls", 0)) + 1
        self._subagents[node_id] = {
            "id": node_id,
            "name": f"Agent: {event.agent_name}",
            "kind": "subagent",
            "status": "executing",
            "task": task_summary or f"Task {event.task_id[:8]}",
            "parent": "dendrophis-core",
            "calls": call_count,
        }
        self._tool_executions.append(
            {
                "id": event.task_id,
                "name": f"subagent:{event.agent_name}",
                "kind": "subagent",
                "status": "running",
                "arguments": json.dumps(event.payload, default=str)[:4000] if event.payload else "",
                "description": task_summary,
                "output": "",
                "diff": "",
                "duration_seconds": None,
                "started_at": datetime.now(UTC).isoformat(),
            }
        )
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🤖 Subagent [{event.agent_name}] started: {task_summary}",
                "level": "subagent",
            },
        )
        self.broadcast(
            "SUBAGENT_STATE",
            {
                "agents": list(self._subagents.values()),
                "active_agent": node_id,
                "action": "update",
            },
        )
        self.broadcast(
            "TOOL_TIMELINE",
            {
                "executions": list(self._tool_executions),
                "action": "update",
            },
        )

    def _handle_subagent_progress(self, event: SubagentProgressEvent) -> None:
        node_id = f"agent-{event.agent_name}"
        if node_id in self._subagents:
            self._subagents[node_id]["task"] = event.log_line[:80]
            self._subagents[node_id]["status"] = "thinking"
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"↳ [{event.agent_name}] {event.log_line}",
                "level": "subagent",
            },
        )
        self.broadcast(
            "SUBAGENT_STATE",
            {
                "agents": list(self._subagents.values()),
                "active_agent": node_id,
                "action": "update",
            },
        )

    def _handle_subagent_finished(self, event: SubagentTaskFinishedEvent) -> None:
        node_id = f"agent-{event.agent_name}"
        if node_id in self._subagents:
            self._subagents[node_id]["status"] = "idle" if event.success else "error"
            self._subagents[node_id]["task"] = "Completed" if event.success else (event.error_message or "Failed")[:80]

        for execution_entry in reversed(self._tool_executions):
            if execution_entry["id"] == event.task_id:
                execution_entry["status"] = "success" if event.success else "error"
                execution_entry["output"] = (
                    json.dumps(event.result, indent=2, default=str)[:8000]
                    if event.result
                    else (event.error_message or "")
                )
                break

        status_icon = "✓" if event.success else "❌"
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"{status_icon} Subagent [{event.agent_name}] finished.",
                "level": "success" if event.success else "error",
            },
        )
        self.broadcast(
            "SUBAGENT_STATE",
            {
                "agents": list(self._subagents.values()),
                "active_agent": "dendrophis-core",
                "action": "update",
            },
        )
        self.broadcast(
            "TOOL_TIMELINE",
            {
                "executions": list(self._tool_executions),
                "action": "update",
            },
        )

    def _handle_memory_saved(self, event: MemorySavedEvent) -> None:
        self.broadcast(
            "MEMORY_RETRIEVAL",
            {
                "id": event.memory_id,
                "content": event.content[:240],
                "relevance": 1.0,
                "action": "saved",
                "tags": event.tags,
            },
        )
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🧠 Memory saved [{', '.join(event.tags)}]: '{event.content[:80]}...'",
                "level": "info",
            },
        )

    def _handle_memory_association(self, event: MemoryAssociationEvent) -> None:
        self.broadcast(
            "MEMORY_RETRIEVAL",
            {
                "id": event.memory_id,
                "content": event.memory_summary or event.memory_content[:200],
                "relevance": event.relevance_score,
                "action": "association",
                "confidence": event.confidence,
                "tags": [event.confidence, event.source],
            },
        )
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"💡 Memory association ({event.confidence}): {event.memory_summary}",
                "level": "info",
            },
        )

    def _handle_memory_search(self, event: MemorySearchResponse) -> None:
        for search_result in event.results[:8]:
            result_tags = search_result.get("tags") if isinstance(search_result, dict) else []
            self.broadcast(
                "MEMORY_RETRIEVAL",
                {
                    "id": str(search_result.get("id", "mem")),
                    "content": str(search_result.get("content", ""))[:240],
                    "relevance": float(search_result.get("score", 0.8)),
                    "action": "retrieved",
                    "tags": result_tags if isinstance(result_tags, list) else [],
                },
            )

    def _handle_memory_stats(self, event: MemoryStatsUpdatedEvent) -> None:
        self._session_stats["total_memories"] = event.total_memories
        self.broadcast("SESSION_STATS", dict(self._session_stats))

    def _handle_primer_loaded(self, event: PrimerLoadedEvent) -> None:
        project_name = event.project_name or "Project Primer"
        self.broadcast(
            "MEMORY_RETRIEVAL",
            {
                "id": f"primer-{event.project_id or 'default'}",
                "content": f"{project_name} ({event.file_count} files tracked)",
                "relevance": 0.95,
                "action": "primer",
                "tags": ["primer"],
            },
        )
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"📚 Project primer loaded: {project_name} ({event.file_count} files)",
                "level": "info",
            },
        )

    def _handle_track_file(self, event: TrackFileRequest) -> None:
        self._record_file_activity(event.path, "tracked")

    def _handle_untrack_file(self, event: UntrackFileRequest) -> None:
        self._tracked_files.pop(event.path, None)
        self._file_details.pop(event.path, None)
        self.broadcast(
            "FILESYSTEM_CHANGE",
            {
                "path": event.path,
                "action": "untracked",
                "files": self._tracked_files,
                "details": self._file_details,
            },
        )

    def _handle_file_write_proposal(self, event: WriteProposalEvent) -> None:
        self._record_file_activity(event.file_path, "proposal", tool_name="write")

    def _handle_file_edit_proposal(self, event: EditProposalEvent) -> None:
        self._record_file_activity(event.file_path, "proposal", diff_text=event.diff, tool_name="edit")

    def _handle_file_created(self, event: FileCreatedEvent) -> None:
        self._record_file_activity(event.file_path, "created", tool_name="write")

    def _handle_file_modified(self, event: FileModifiedEvent) -> None:
        self._record_file_activity(event.file_path, "modified", diff_text=event.diff, tool_name="edit")

    def _handle_model_switched(self, event: ModelSwitchedEvent) -> None:
        self._session_stats["model"] = event.model_id
        if event.context_window:
            self._session_stats["context_limit"] = event.context_window
        self.broadcast("SESSION_STATS", dict(self._session_stats))
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"🔄 Model switched to {event.model_id} (Context: {event.context_window:,} tokens)",
                "level": "info",
            },
        )

    def _handle_message_sent(self, event: MessageSentEvent) -> None:
        message_content = getattr(event, "message_text", "") or getattr(event, "message", "")
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"💬 User: {message_content[:160]}",
                "level": "user",
            },
        )

    def _handle_stats_updated(self, event: StatsUpdatedEvent) -> None:
        self._session_stats["prompt_tokens"] = event.prompt_tokens
        self._session_stats["completion_tokens"] = event.completion_tokens
        self._session_stats["cached_tokens"] = event.cached_tokens
        self._session_stats["total_cost_usd"] = round(float(event.total_cost_usd), 4)
        self._session_stats["tokens_per_sec"] = round(float(event.tokens_per_sec), 1)
        self._session_stats["time_to_first_token"] = round(float(event.time_to_first_token), 2)
        if event.prompt_tokens > 0:
            self._session_stats["cache_hit_pct"] = round((event.cached_tokens / event.prompt_tokens) * 100.0, 1)
        self.broadcast("SESSION_STATS", dict(self._session_stats))

    def _handle_usage_updated(self, event: UsageEvent) -> None:
        self._session_stats["prompt_tokens"] = event.prompt_tokens
        self._session_stats["completion_tokens"] = event.completion_tokens
        self._session_stats["cached_tokens"] = event.cached_tokens
        if event.prompt_tokens > 0:
            self._session_stats["cache_hit_pct"] = round((event.cached_tokens / event.prompt_tokens) * 100.0, 1)
        self.broadcast("SESSION_STATS", dict(self._session_stats))

    def _handle_context_updated(self, event: ContextUpdatedEvent) -> None:
        self._session_stats["context_tokens"] = event.token_count
        self._session_stats["context_pct"] = round(float(event.token_pct) * 100.0, 1)
        self._session_stats["turn_count"] = event.turn_count
        self.broadcast("SESSION_STATS", dict(self._session_stats))

    def _handle_compaction_started(self, event: CompactionStartedEvent) -> None:
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"⚙ Compacting context ({event.original_token_count:,} tokens)...",
                "level": "tool",
                "tool_name": "compact_context",
            },
        )

    def _handle_compaction_completed(self, event: CompactionCompletedEvent) -> None:
        self._session_stats["compactions_count"] = int(self._session_stats.get("compactions_count", 0)) + 1
        self._session_stats["tokens_reclaimed"] = int(self._session_stats.get("tokens_reclaimed", 0)) + int(
            event.tokens_reclaimed
        )
        self._session_stats["context_tokens"] = event.new_token_count
        context_limit = int(self._session_stats.get("context_limit") or 131072)
        if context_limit > 0:
            self._session_stats["context_pct"] = round((event.new_token_count / context_limit) * 100.0, 1)
        self.broadcast("SESSION_STATS", dict(self._session_stats))
        self._tool_executions.append(
            {
                "id": f"compact-{self._session_stats['compactions_count']}",
                "name": "compact_context",
                "kind": "compaction",
                "status": "success",
                "arguments": json.dumps(
                    {
                        "previous_token_count": event.previous_token_count,
                        "new_token_count": event.new_token_count,
                        "tokens_reclaimed": event.tokens_reclaimed,
                    }
                ),
                "description": f"{event.previous_token_count:,} -> {event.new_token_count:,} tokens",
                "output": event.summary,
                "diff": "",
                "duration_seconds": None,
                "started_at": datetime.now(UTC).isoformat(),
            }
        )
        self.broadcast(
            "TOOL_TIMELINE",
            {
                "executions": list(self._tool_executions),
                "action": "update",
            },
        )
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": (
                    f"✓ Context compacted: {event.previous_token_count:,} → {event.new_token_count:,} "
                    f"(-{event.tokens_reclaimed:,} reclaimed)"
                ),
                "level": "success",
                "tool_name": "compact_context",
            },
        )

    def _handle_compaction_failed(self, event: CompactionFailedEvent) -> None:
        self.broadcast(
            "THOUGHT_LOG",
            {
                "text": f"⚠ Compaction skipped: {event.reason}",
                "level": "warning",
                "tool_name": "compact_context",
            },
        )

    def _handle_todo_updated(self, event: TodoUpdatedEvent | TodoChangedEvent) -> None:
        self._session_stats["todos"] = list(event.todos)
        self.broadcast("SESSION_STATS", dict(self._session_stats))

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
