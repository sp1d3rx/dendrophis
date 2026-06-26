"""Event types for the event bus system.

Event Hierarchy:
    StreamEvent           - All LLM streaming-related events (content, lifecycle)
    ToolExecutionEvent    - Tool execution lifecycle (post-LLM tool calls)
    SessionEvent          - User/session interaction events
    ConfirmationEvent     - Human approval request/response events
    ConfigEvent           - Configuration change events
    RequestEvent          - UI-to-session command events
    PrimerEvent           - Project primer management events
    UnderstandingEvent     - Project understanding phase events
    MemoryEvent           - Long-term memory system events
    ContextEvent          - Context state management events
    ModelEvent            - Model switching events
    StatsEvent            - Token/usage statistics events
    TrackFileEvent        - File tracking for primer
    MultipleChoiceEvent    - Multiple choice UI interaction events
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# =============================================================================
# Data Classes (not events, but used by events)
# =============================================================================


@dataclass(frozen=True, slots=True)
class ToolCallChunk:
    """Partial tool-call delta received in a single SSE chunk."""

    index: int
    id: str
    name: str
    arguments_delta: str


@dataclass
class ToolCall:
    """Accumulated tool call built from streaming deltas."""

    index: int
    id: str
    name: str
    arguments: str = ""

    def finish(self) -> ToolCall:
        """Return self once arguments are fully accumulated."""
        return self


# =============================================================================
# Base Event Classes
# =============================================================================


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """Base class for all LLM streaming-related events.

    Includes both content events (text, reasoning, tool calls) and
    lifecycle events (stream start/finish).
    """

    pass


@dataclass(frozen=True, slots=True)
class ToolExecutionEvent:
    """Base class for tool execution lifecycle events.

    These events fire after the LLM has returned a complete tool call
    and the system is executing it.
    """

    pass


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """Base class for user/session interaction events."""

    pass


@dataclass(frozen=True, slots=True)
class ConfirmationEvent:
    """Base class for human approval request/response events."""

    pass


@dataclass(frozen=True, slots=True)
class ConfigEvent:
    """Base class for configuration change events."""

    pass


@dataclass(frozen=True, slots=True)
class RequestEvent:
    """Base class for UI-to-session command/request events."""

    pass


@dataclass(frozen=True, slots=True)
class PrimerEvent:
    """Base class for project primer management events."""

    pass


@dataclass(frozen=True, slots=True)
class UnderstandingEvent:
    """Base class for project understanding phase detection events."""

    pass


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    """Base class for long-term memory system events."""

    pass


@dataclass(frozen=True, slots=True)
class ContextEvent:
    """Base class for context state management events."""

    pass


@dataclass(frozen=True, slots=True)
class ModelEvent:
    """Base class for model-related events."""

    pass


@dataclass(frozen=True, slots=True)
class StatsEvent:
    """Base class for token usage and statistics events."""

    pass


@dataclass(frozen=True, slots=True)
class TrackFileEvent:
    """Base class for file tracking events (primer)."""

    pass


@dataclass(frozen=True, slots=True)
class MultipleChoiceEvent:
    """Base class for multiple choice UI interaction events."""

    pass


# =============================================================================
# Stream Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class StreamingStartedEvent(StreamEvent):
    """Streaming has started for a new message."""

    user_message: str


@dataclass(frozen=True, slots=True)
class StreamingFinishedEvent(StreamEvent):
    """Streaming has finished for the current message."""

    pass


@dataclass(frozen=True, slots=True)
class TextDeltaEvent(StreamEvent):
    """A chunk of text from the LLM stream."""

    delta: str


@dataclass(frozen=True, slots=True)
class ReasoningDeltaEvent(StreamEvent):
    """A chunk of reasoning/thought tokens from the LLM."""

    delta: str


@dataclass(frozen=True, slots=True)
class ToolCallStartEvent(StreamEvent):
    """Signals the beginning of a new tool call in the stream."""

    index: int
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ToolCallDeltaEvent(StreamEvent):
    """Partial arguments delta for an in-progress tool call."""

    index: int
    arguments_delta: str


@dataclass(frozen=True, slots=True)
class ToolCallDoneEvent(StreamEvent):
    """Signals a fully accumulated tool call ready for execution."""

    tool_call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolResultEvent(StreamEvent):
    """A tool has completed execution with a result."""

    tool_call_id: str
    name: str
    content: str
    description: str = ""
    arguments: str = ""
    consecutive_failures: int = 0


@dataclass(frozen=True, slots=True)
class UsageEvent(StreamEvent):
    """Token usage statistics from the LLM."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int = 0
    cached_tokens: int = 0
    is_estimated: bool = False

    def __post_init__(self) -> None:
        if self.total_tokens == 0:
            object.__setattr__(self, "total_tokens", self.prompt_tokens + self.completion_tokens)


@dataclass(frozen=True, slots=True)
class DoneEvent(StreamEvent):
    """The LLM stream has completed."""

    finish_reason: str


@dataclass(frozen=True, slots=True)
class TurnResult(StreamEvent):
    """Accumulated result of one complete LLM turn, yielded as the final stream event.

    Carries resolved text, reasoning, and tool calls after the client has handled
    all provider quirks (text-based tool-call parsing, MLC false-positive retry).
    Session uses this to write to context and decide whether to loop.
    """

    text: str
    reasoning: str
    tool_calls: list[ToolCall]
    finish_reason: str


@dataclass(frozen=True, slots=True)
class ErrorEvent(StreamEvent):
    """An error occurred during streaming or tool execution."""

    message: str


@dataclass(frozen=True, slots=True)
class RetryEvent(StreamEvent):
    """A retry is being attempted with a delay."""

    message: str
    attempt: int
    delay: float


@dataclass(frozen=True, slots=True)
class AuthFailedEvent(StreamEvent):
    """HTTP 401 — API key is missing, expired, or invalid."""

    url: str


# =============================================================================
# Tool Execution Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class ToolExecutionStartedEvent(ToolExecutionEvent):
    """A tool is about to be executed."""

    tool_name: str
    description: str = ""
    arguments: str = ""
    tool_call_index: int = -1


@dataclass(frozen=True, slots=True)
class ToolExecutionFinishedEvent(ToolExecutionEvent):
    """A tool has finished execution."""

    tool_name: str
    success: bool


# =============================================================================
# Session Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class MessageSentEvent(SessionEvent):
    """A message has been sent to the LLM."""

    message_text: str


@dataclass(frozen=True, slots=True)
class WaitingForInputEvent(SessionEvent):
    """System is waiting for user input."""

    pass


# =============================================================================
# Confirmation Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class ToolConfirmationRequestEvent(ConfirmationEvent):
    """Request human approval before executing a tool."""

    request_id: str
    tool_name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolConfirmationResponseEvent(ConfirmationEvent):
    """Response to a tool confirmation request."""

    request_id: str
    approved: bool


@dataclass(frozen=True, slots=True)
class EditProposalEvent(ConfirmationEvent):
    """Request human approval for a file edit, providing a diff for review."""

    request_id: str
    file_path: str
    diff: str
    new_content: str


@dataclass(frozen=True, slots=True)
class EditApprovalEvent(ConfirmationEvent):
    """Response to an EditProposalEvent."""

    request_id: str
    approved: bool


@dataclass(frozen=True, slots=True)
class WriteProposalEvent(ConfirmationEvent):
    """Request human approval for a file write, providing the content for review."""

    request_id: str
    file_path: str
    content: str


@dataclass(frozen=True, slots=True)
class WriteApprovalEvent(ConfirmationEvent):
    """Response to a WriteProposalEvent."""

    request_id: str
    approved: bool


# =============================================================================
# Python Exec Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class PythonExecProposalEvent(ConfirmationEvent):
    """Request human approval for Python code execution, showing the code for review."""

    request_id: str
    code: str


@dataclass(frozen=True, slots=True)
class PythonExecApprovalEvent(ConfirmationEvent):
    """Response to a PythonExecProposalEvent."""

    request_id: str
    approved: bool


# =============================================================================
# Config Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class ConfigReloadedEvent(ConfigEvent):
    """Configuration has been reloaded from disk."""

    pass


@dataclass(frozen=True, slots=True)
class ConfigChangeRequest(ConfigEvent):
    """Generic request to change a config value."""

    key: str
    value: Any


# =============================================================================
# Request Events (UI -> Session commands)
# =============================================================================


@dataclass(frozen=True, slots=True)
class ModelSwitchRequest(RequestEvent):
    """Request to switch the active model."""

    model_id: str


@dataclass(frozen=True, slots=True)
class ReasoningEffortChangedEvent(ConfigEvent):
    """Reasoning effort setting has changed."""

    reasoning_effort: str | None


@dataclass(frozen=True, slots=True)
class ReasoningEffortChangeRequest(RequestEvent):
    """Request to change reasoning_effort setting."""

    reasoning_effort: str | None


@dataclass(frozen=True, slots=True)
class TemperatureChangedEvent(ConfigEvent):
    """Temperature setting has changed."""

    temperature: float


@dataclass(frozen=True, slots=True)
class TemperatureChangeRequest(RequestEvent):
    """Request to change temperature setting."""

    temperature: float


@dataclass(frozen=True, slots=True)
class SessionResetRequest(RequestEvent):
    """Request to reset the current session (clear context)."""

    pass


@dataclass(frozen=True, slots=True)
class SendMessageRequest(RequestEvent):
    """Request to send a user message."""

    text: str


@dataclass(frozen=True, slots=True)
class SessionSaveRequest(RequestEvent):
    """Request to save the current session."""

    path: str | None = None


@dataclass(frozen=True, slots=True)
class SessionLoadRequest(RequestEvent):
    """Request to load a session from a file."""

    path: str


@dataclass(frozen=True, slots=True)
class CompactRequest(RequestEvent):
    """Request to manually compact context."""

    pass


@dataclass(frozen=True, slots=True)
class CancelStreamingRequest(RequestEvent):
    """Request to cancel current streaming."""

    pass


# =============================================================================
# Primer Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class PrimerSaveRequest(PrimerEvent):
    """Request to save project primer."""

    pass


@dataclass(frozen=True, slots=True)
class PrimerLoadRequest(PrimerEvent):
    """Request to load project primer."""

    pass


@dataclass(frozen=True, slots=True)
class PrimerInjectRequest(PrimerEvent):
    """Request to inject primer files into context."""

    pass


@dataclass(frozen=True, slots=True)
class PrimerLoadedEvent(PrimerEvent):
    """Project primer has been loaded."""

    project_id: str | None
    project_name: str | None
    file_count: int
    turn_count: int
    understanding: str | None


# =============================================================================
# Understanding Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class UnderstandingStatsUpdatedEvent(UnderstandingEvent):
    """Understanding phase detection stats have changed."""

    established: bool
    checkpoint_turn: int
    min_turns_required: int
    current_turn: int


# =============================================================================
# Primer Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class PrimerScreenRequest(PrimerEvent):
    """Request to open the primer file management screen."""

    pass


# =============================================================================
# Memory Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class MemorySavedEvent(MemoryEvent):
    """A memory was saved (auto or manual)."""

    memory_id: str
    content: str
    tags: list[str]
    source: str


@dataclass(frozen=True, slots=True)
class MemorySearchRequest(MemoryEvent):
    """Request to search memories (UI -> Session)."""

    query: str
    limit: int = 5
    project_id: str | None = None
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class MemorySearchResponse(MemoryEvent):
    """Response to a memory search request."""

    query: str
    results: list[dict]  # serialized MemorySearchResult
    count: int


@dataclass(frozen=True, slots=True)
class MemoryStatsUpdatedEvent(MemoryEvent):
    """Memory system statistics have changed."""

    total_memories: int
    total_projects: int
    total_tags: int
    top_tags: list[tuple[str, int]]


@dataclass(frozen=True, slots=True)
class MemoryAssociationEvent(MemoryEvent):
    """A memory surfaced spontaneously - "this makes me think of..."

    Not a search result, but an association. May be relevant, may be random.
    The confidence and framing tell the story of how it came to mind.
    """

    trigger: str  # What the user said that sparked this
    memory_content: str  # The full memory (for retrieval)
    memory_summary: str  # One-sentence teaser (for display)
    memory_id: str  # For deep-linking: "tell me more about [id]"
    relevance_score: float  # 0.0 to 1.0, but we don't show this raw
    confidence: str  # "strong", "weak", "random"
    when: str  # "Tuesday", "last month", "a while back" - human time
    source: str  # "session", "project", "auto"


# =============================================================================
# Context Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class ContextUpdatedEvent(ContextEvent):
    """Context manager has been updated (e.g., compaction)."""

    token_count: int
    token_pct: float
    turn_count: int = 0
    full_chat_restored: bool = False


# =============================================================================
# Model Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class ModelSwitchedEvent(ModelEvent):
    """The active model has been switched."""

    model_id: str
    context_window: int


# =============================================================================
# Stats Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class StatsUpdatedEvent(StatsEvent):
    """Session statistics have been updated."""

    prompt_tokens: int
    completion_tokens: int
    total_cost_usd: float
    tokens_per_sec: float
    time_to_first_token: float


# =============================================================================
# Track File Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class TrackFileRequest(TrackFileEvent):
    """Request to track a file in primer."""

    path: str


@dataclass(frozen=True, slots=True)
class UntrackFileRequest(TrackFileEvent):
    """Request to untrack a file from primer."""

    path: str


# =============================================================================
# Multiple Choice Events
# =============================================================================


@dataclass(frozen=True, slots=True)
class MultipleChoiceRequestEvent(MultipleChoiceEvent):
    """Request user to answer a multiple choice question."""

    request_id: str
    question: str
    options: list[str]


@dataclass(frozen=True, slots=True)
class MultipleChoiceResponseEvent(MultipleChoiceEvent):
    """Response to a multiple choice question."""

    request_id: str
    selected_option: str | None


# =============================================================================
# Union Type
# =============================================================================


AnyEvent = (
    # Stream events
    TextDeltaEvent
    | ReasoningDeltaEvent
    | TurnResult
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallDoneEvent
    | ToolResultEvent
    | UsageEvent
    | DoneEvent
    | ErrorEvent
    | RetryEvent
    | AuthFailedEvent
    | StreamingStartedEvent
    | StreamingFinishedEvent
    # Tool execution events
    | ToolExecutionStartedEvent
    | ToolExecutionFinishedEvent
    # Session events
    | MessageSentEvent
    | WaitingForInputEvent
    # Confirmation events
    | ToolConfirmationRequestEvent
    | ToolConfirmationResponseEvent
    | EditProposalEvent
    | EditApprovalEvent
    | WriteProposalEvent
    | WriteApprovalEvent
    | PythonExecProposalEvent
    | PythonExecApprovalEvent
    # Config events
    | ConfigReloadedEvent
    # Request events
    | ModelSwitchRequest
    | ReasoningEffortChangedEvent
    | ReasoningEffortChangeRequest
    | TemperatureChangedEvent
    | TemperatureChangeRequest
    | SessionResetRequest
    | SendMessageRequest
    | SessionSaveRequest
    | SessionLoadRequest
    | CompactRequest
    | ConfigChangeRequest
    | CancelStreamingRequest
    # Primer events
    | PrimerSaveRequest
    | PrimerLoadRequest
    | PrimerInjectRequest
    | PrimerLoadedEvent
    | UnderstandingStatsUpdatedEvent
    | PrimerScreenRequest
    # Memory events
    | MemorySavedEvent
    | MemorySearchRequest
    | MemorySearchResponse
    | MemoryStatsUpdatedEvent
    | MemoryAssociationEvent
    # Context events
    | ContextUpdatedEvent
    # Model events
    | ModelSwitchedEvent
    # Stats events
    | StatsUpdatedEvent
    # Track file events
    | TrackFileRequest
    | UntrackFileRequest
    # Multiple choice events
    | MultipleChoiceRequestEvent
    | MultipleChoiceResponseEvent
)
