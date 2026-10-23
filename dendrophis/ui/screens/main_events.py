"""Event handling and EventBus subscription adapter for MainScreen."""

from __future__ import annotations

import json
import traceback
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from dendrophis.events import (
    AppendProposalEvent,
    CompactionCompletedEvent,
    CompactionFailedEvent,
    CompactionStartedEvent,
    ConfigReloadedEvent,
    ContextUpdatedEvent,
    EditProposalEvent,
    ErrorEvent,
    ModelSwitchedEvent,
    MultipleChoiceRequestEvent,
    PrimerScreenRequest,
    PythonExecProposalEvent,
    ReasoningDeltaEvent,
    RetryEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
    TextDeltaEvent,
    ToolCallStartEvent,
    ToolConfirmationRequestEvent,
    ToolExecutionStartedEvent,
    ToolResultEvent,
    WriteProposalEvent,
    listen,
)

# Screen classes are imported at module level (no import cycles: the screens
# only depend on dendrophis.events / textual widgets). They are still
# CONSTRUCTED lazily at push time via _push_screen_later so construction
# happens in the app's UI context.
from dendrophis.ui.screens.append_confirmation import AppendConfirmationScreen
from dendrophis.ui.screens.edit_confirmation import EditConfirmationScreen
from dendrophis.ui.screens.multiple_choice import MultipleChoiceScreen
from dendrophis.ui.screens.primer_screen import PrimerScreen
from dendrophis.ui.screens.python_exec_confirmation import PythonExecConfirmationScreen
from dendrophis.ui.screens.tool_confirmation import ToolConfirmationScreen
from dendrophis.ui.screens.write_confirmation import WriteConfirmationScreen
from dendrophis.ui.widgets.chat_view import ChatView
from dendrophis.ui.widgets.input_bar import InputBar
from dendrophis.ui.widgets.sidebar import Sidebar

if TYPE_CHECKING:
    from dendrophis.events import EventBus
    from dendrophis.session.session import Session


class MainScreenEventHandler:
    """Mixin containing all EventBus listeners for MainScreen."""

    _session: Session
    _event_bus: EventBus
    _streaming: bool

    def _setup_event_handlers(self) -> None:
        """Subscribe this screen to relevant events."""
        self._events = self._event_bus.bind(self)

    def on_unmount(self) -> None:
        """Unsubscribe all event handlers to prevent memory leaks."""
        self._events.unsubscribe_all()

    @listen
    def _on_text_delta(self, event: TextDeltaEvent) -> None:
        """Handle text delta events."""
        self.query_one(ChatView).append_text_delta(event.delta)

    @listen
    def _on_reasoning_delta(self, event: ReasoningDeltaEvent) -> None:
        """Handle reasoning delta events."""
        self.query_one(ChatView).append_reasoning_delta(event.delta)

    @listen
    def _on_error(self, event: ErrorEvent) -> None:
        """Handle error events."""
        self.query_one(ChatView).add_error(event.message)
        self.app.debug_log(f"[ERROR] {event.message}")

    @listen
    def _on_retry(self, event: RetryEvent) -> None:
        """Handle retry events."""
        self.query_one(ChatView).show_retry_status(event.message, event.delay)

    @listen
    def _on_tool_result(self, event: ToolResultEvent) -> None:
        """Handle tool result events."""
        self.query_one(ChatView).add_tool_result(
            event.name,
            event.content,
            event.description,
            event.arguments,
            event.consecutive_failures,
            tool_call_id=event.tool_call_id,
        )

    @listen
    def _on_streaming_started(self, event: StreamingStartedEvent) -> None:
        """Handle streaming started events."""
        self._streaming = True
        self.query_one(InputBar).set_streaming(True)
        model_identifier = self._session.config.llm.model
        self.query_one(ChatView).start_assistant_message(model_id=model_identifier)

    @listen
    def _on_streaming_finished(self, event: StreamingFinishedEvent) -> None:
        """Handle streaming finished events."""
        self._streaming = False
        self.query_one(InputBar).set_streaming(False)
        chat = self.query_one(ChatView)
        chat.remove_retry_status()
        chat.finish_assistant_message()
        # Refresh sidebar
        sidebar_list = self.query(Sidebar)
        if sidebar_list:
            sidebar_list.first().refresh_all()

        # Process queued inputs
        if self._input_queue:
            next_event = self._input_queue.popleft()
            remaining_text = f" ({len(self._input_queue)} remaining)" if self._input_queue else ""
            notification_message = f"Processing queued prompt...{remaining_text}"
            self._debug_widget.write(f"[NOTIFY] {notification_message}")
            self.notify(notification_message, severity="information")
            self._process_input(next_event)

    @listen
    def _on_tool_execution_started(self, event: ToolExecutionStartedEvent) -> None:
        self.query_one(ChatView).add_tool_status(
            event.tool_name, event.description, event.arguments, index=event.tool_call_index
        )

    @listen
    def _on_tool_call_start(self, event: ToolCallStartEvent) -> None:
        self.query_one(ChatView).add_tool_placeholder(event.index, event.name, tool_call_id=event.id)

    def _push_screen_later(self, screen_factory: Callable[[], Any]) -> None:
        """Schedule a screen push on the UI thread.

        ``screen_factory`` is invoked at push time (inside ``call_later``), so
        the screen is constructed in the app's UI context rather than on
        whatever thread published the event.
        """
        self.call_later(lambda: self.app.push_screen(screen_factory()))

    @listen
    def _on_tool_confirmation_request(self, event: ToolConfirmationRequestEvent) -> None:
        """Handle human approval request for sensitive tools."""
        self._push_screen_later(
            lambda: ToolConfirmationScreen(event.request_id, event.tool_name, event.arguments, self._event_bus)
        )

    @listen
    def _on_multiple_choice_request(self, event: MultipleChoiceRequestEvent) -> None:
        """Handle human multiple choice question request."""
        self._push_screen_later(
            lambda: MultipleChoiceScreen(event.request_id, event.question, event.options, self._event_bus)
        )

    @listen
    def _on_edit_proposal(self, event: EditProposalEvent) -> None:
        """Handle request for file edit approval with diff."""
        self._push_screen_later(lambda: EditConfirmationScreen(event, self._event_bus))

    @listen
    def _on_write_proposal(self, event: WriteProposalEvent) -> None:
        """Handle request for new file write approval with content preview."""
        self._push_screen_later(lambda: WriteConfirmationScreen(event, self._event_bus))

    @listen
    def _on_append_proposal(self, event: AppendProposalEvent) -> None:
        """Handle request for file append approval with content preview."""
        self._push_screen_later(lambda: AppendConfirmationScreen(event, self._event_bus))

    @listen
    def _on_python_exec_proposal(self, event: PythonExecProposalEvent) -> None:
        """Handle request for Python code execution approval with code preview."""
        self._push_screen_later(lambda: PythonExecConfirmationScreen(event, self._event_bus))

    @listen
    def _on_primer_screen_request(self, event: PrimerScreenRequest) -> None:
        """Handle request to open the project primer screen."""
        self._push_screen_later(lambda: PrimerScreen(self._session))

    @listen
    def _on_context_updated(self, event: ContextUpdatedEvent) -> None:
        """Handle context updated events."""
        # Sidebar will refresh via StatsUpdatedEvent
        if getattr(event, "full_chat_restored", False):
            chat = self.query_one(ChatView)
            chat.clear()
            self._replay_context(chat)

    @staticmethod
    def _message_text(message: dict[str, Any]) -> str:
        """Extract displayable text from a message (handles list content parts)."""
        raw_content = message.get("content", "")
        if isinstance(raw_content, list):
            return " ".join(part.get("text", "") for part in raw_content if isinstance(part, dict))
        return raw_content or ""

    def _replay_context(self, chat: ChatView) -> None:
        """Replay the persisted conversation into the chat view.

        Assistant tool calls are tracked by tool_call_id so tool-result
        messages can be matched back to their arguments.
        """
        pending_tool_calls: dict[str, dict[str, Any]] = {}
        for message in self._session.context.messages:
            try:
                role = message.get("role", "unknown")
                content = self._message_text(message)
                if role == "user":
                    chat.add_user_message(content)
                elif role == "assistant":
                    self._replay_assistant_message(chat, message, content, pending_tool_calls)
                elif role == "tool":
                    self._replay_tool_message(chat, message, pending_tool_calls)
                elif role == "system":
                    chat.add_system_message(content)
            except Exception as replay_error:
                self._debug_widget.write(f"MESSAGE REPLAY ERROR: {type(replay_error).__name__}: {replay_error!s}")
                self._debug_widget.write(f"TRACEBACK: {traceback.format_exc()}")

    def _replay_assistant_message(
        self,
        chat: ChatView,
        message: dict[str, Any],
        content: str,
        pending_tool_calls: dict[str, dict[str, Any]],
    ) -> None:
        """Replay one assistant message: reasoning, text, and tool-call status lines."""
        chat.start_assistant_message(loading=False)
        reasoning = message.get("reasoning_content")
        if reasoning:
            chat.append_reasoning_delta(reasoning)
        if content:
            chat.append_text_delta(content)
        # Store tool calls to match with results later and display status lines
        for call_index, tool_call in enumerate(message.get("tool_calls", [])):
            tool_call_id = tool_call.get("id")
            function_info = tool_call.get("function", {})
            if tool_call_id:
                pending_tool_calls[tool_call_id] = tool_call
            chat.add_tool_status(
                tool_name=function_info.get("name", "unknown"),
                description="",
                arguments=function_info.get("arguments", ""),
                index=call_index,
                tool_call_id=tool_call_id,
            )
        chat.finish_assistant_message()

    def _replay_tool_message(
        self,
        chat: ChatView,
        message: dict[str, Any],
        pending_tool_calls: dict[str, dict[str, Any]],
    ) -> None:
        """Replay one tool-result message, matched to its pending call's arguments."""
        tool_call_identifier = message.get("tool_call_id")
        call_arguments = ""
        if tool_call_identifier and tool_call_identifier in pending_tool_calls:
            function_dict = pending_tool_calls[tool_call_identifier].get("function", {})
            call_arguments = function_dict.get("arguments", "")
            del pending_tool_calls[tool_call_identifier]
        chat.add_tool_result(
            message.get("name", "unknown"),
            message.get("content", ""),
            description="",
            arguments=call_arguments,
            consecutive_failures=0,
            tool_call_id=tool_call_identifier,
        )

    @listen
    def _on_model_switched(self, event: ModelSwitchedEvent) -> None:
        """Handle model switched events."""
        self.query_one(ChatView).add_system_message(f"Model switched to {event.model_id}")

        # Return focus to input bar after switching models
        def focus_input() -> None:
            try:
                from dendrophis.ui.widgets.input_bar import InputBar

                self.query_one(InputBar).focus()
            except Exception:
                pass

        self.call_later(focus_input)

    @listen
    def _on_compaction_started(self, event: CompactionStartedEvent) -> None:
        """Render context compaction start inline as a tool call."""
        compaction_call_id = f"compact_{uuid.uuid4().hex[:8]}"
        self._compaction_call_id = compaction_call_id
        self.query_one(ChatView).add_tool_status(
            "compact_context",
            description="",
            arguments=json.dumps({"tokens": event.original_token_count}),
            index=-1,
            tool_call_id=compaction_call_id,
        )

    @listen
    def _on_compaction_completed(self, event: CompactionCompletedEvent) -> None:
        """Render context compaction completion inline as a tool result."""
        compaction_call_id = getattr(self, "_compaction_call_id", None)
        self._compaction_call_id = None
        self.query_one(ChatView).add_tool_result(
            "compact_context",
            event.summary,
            description="",
            arguments=json.dumps(
                {
                    "previous_token_count": event.previous_token_count,
                    "new_token_count": event.new_token_count,
                    "tokens_reclaimed": event.tokens_reclaimed,
                }
            ),
            consecutive_failures=0,
            tool_call_id=compaction_call_id,
        )

    @listen
    def _on_compaction_failed(self, event: CompactionFailedEvent) -> None:
        """Render context compaction failure or skip inline as a tool result."""
        compaction_call_id = getattr(self, "_compaction_call_id", None)
        self._compaction_call_id = None
        self.query_one(ChatView).add_tool_result(
            "compact_context",
            event.reason,
            description="",
            arguments=json.dumps({"reason": event.reason}),
            consecutive_failures=1,
            tool_call_id=compaction_call_id,
        )

    @listen
    def _on_config_reloaded(self, event: ConfigReloadedEvent) -> None:
        """Rebuild the sidebar whenever the config is saved."""
        self.call_later(self._rebuild_sidebar)

    def _rebuild_sidebar(self) -> None:
        """Remove the existing sidebar and mount a fresh one from updated config."""
        layout = self.query_one("#main-layout")
        # Remove old sidebar if present
        for old_sidebar in self.query(Sidebar):
            old_sidebar.remove()
        # Mount a new one if any panels are configured
        if self._session.config.ui.sidebar.panels:
            new_sidebar = Sidebar(
                session=self._session,
                event_bus=self._event_bus,
            )
            layout.mount(new_sidebar)
