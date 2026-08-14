"""Event handling and EventBus subscription adapter for MainScreen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dendrophis.events import (
    AppendProposalEvent,
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
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    ToolConfirmationRequestEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolResultEvent,
    WriteProposalEvent,
    listen,
)
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

    @listen
    def _on_tool_call_delta(self, event: ToolCallDeltaEvent) -> None:
        """Handle tool call delta events (streaming)."""
        pass

    @listen
    def _on_tool_execution_finished(self, event: ToolExecutionFinishedEvent) -> None:
        """Handle tool execution finished events."""
        pass

    @listen
    def _on_tool_confirmation_request(self, event: ToolConfirmationRequestEvent) -> None:
        """Handle human approval request for sensitive tools."""

        def show_confirmation() -> None:
            from dendrophis.ui.screens.tool_confirmation import ToolConfirmationScreen

            self.app.push_screen(
                ToolConfirmationScreen(event.request_id, event.tool_name, event.arguments, self._event_bus)
            )

        # Schedule on the UI thread to ensure proper app context
        self.call_later(show_confirmation)

    @listen
    def _on_multiple_choice_request(self, event: MultipleChoiceRequestEvent) -> None:
        """Handle human multiple choice question request."""

        def show_mcq() -> None:
            from dendrophis.ui.screens.multiple_choice import MultipleChoiceScreen

            self.app.push_screen(MultipleChoiceScreen(event.request_id, event.question, event.options, self._event_bus))

        # Schedule on the UI thread to ensure proper app context
        self.call_later(show_mcq)

    @listen
    def _on_edit_proposal(self, event: EditProposalEvent) -> None:
        """Handle request for file edit approval with diff."""

        def show_edit_confirmation() -> None:
            from dendrophis.ui.screens.edit_confirmation import EditConfirmationScreen

            self.app.push_screen(EditConfirmationScreen(event, self._event_bus))

        self.call_later(show_edit_confirmation)

    @listen
    def _on_write_proposal(self, event: WriteProposalEvent) -> None:
        """Handle request for new file write approval with content preview."""

        def show_write_confirmation() -> None:
            from dendrophis.ui.screens.write_confirmation import WriteConfirmationScreen

            self.app.push_screen(WriteConfirmationScreen(event, self._event_bus))

        self.call_later(show_write_confirmation)

    @listen
    def _on_append_proposal(self, event: AppendProposalEvent) -> None:
        """Handle request for file append approval with content preview."""

        def show_append_confirmation() -> None:
            from dendrophis.ui.screens.append_confirmation import AppendConfirmationScreen

            self.app.push_screen(AppendConfirmationScreen(event, self._event_bus))

        self.call_later(show_append_confirmation)

    @listen
    def _on_python_exec_proposal(self, event: PythonExecProposalEvent) -> None:
        """Handle request for Python code execution approval with code preview."""

        def show_python_exec_confirmation() -> None:
            from dendrophis.ui.screens.python_exec_confirmation import (
                PythonExecConfirmationScreen,
            )

            self.app.push_screen(PythonExecConfirmationScreen(event, self._event_bus))

        self.call_later(show_python_exec_confirmation)

    @listen
    def _on_primer_screen_request(self, event: PrimerScreenRequest) -> None:
        """Handle request to open the project primer screen."""

        def show_primer_screen() -> None:
            from dendrophis.ui.screens.primer_screen import PrimerScreen

            self.app.push_screen(PrimerScreen(self._session))

        self.call_later(show_primer_screen)

    @listen
    def _on_context_updated(self, event: ContextUpdatedEvent) -> None:
        """Handle context updated events."""
        # Sidebar will refresh via StatsUpdatedEvent
        if getattr(event, "full_chat_restored", False):
            chat = self.query_one(ChatView)
            chat.clear()

            # Track pending tool calls from assistant messages to match with results
            pending_tool_calls: dict[str, dict[str, Any]] = {}

            for message in self._session.context.messages:
                try:
                    role = message.get("role", "unknown")
                    raw_content = message.get("content", "")
                    if isinstance(raw_content, list):
                        content = " ".join(part.get("text", "") for part in raw_content if isinstance(part, dict))
                    else:
                        content = raw_content or ""
                    if role == "user":
                        chat.add_user_message(content)
                    elif role == "assistant":
                        chat.start_assistant_message(loading=False)
                        reasoning = message.get("reasoning_content")
                        if reasoning:
                            chat.append_reasoning_delta(reasoning)
                        if content:
                            chat.append_text_delta(content)
                        # Store tool calls to match with results later and display status lines
                        tool_calls = message.get("tool_calls", [])
                        for call_index, tool_call in enumerate(tool_calls):
                            tool_call_id = tool_call.get("id")
                            function_info = tool_call.get("function", {})
                            tool_name = function_info.get("name", "unknown")
                            tool_arguments = function_info.get("arguments", "")
                            if tool_call_id:
                                pending_tool_calls[tool_call_id] = tool_call
                            chat.add_tool_status(
                                tool_name=tool_name,
                                description="",
                                arguments=tool_arguments,
                                index=call_index,
                                tool_call_id=tool_call_id,
                            )
                        chat.finish_assistant_message()
                    elif role == "tool":
                        # Tool result message - match with pending call
                        tool_call_identifier = message.get("tool_call_id")
                        tool_call_name = message.get("name", "unknown")
                        tool_call_content = message.get("content", "")

                        # Get arguments from pending call if available
                        call_arguments = ""
                        if tool_call_identifier and tool_call_identifier in pending_tool_calls:
                            function_dict = pending_tool_calls[tool_call_identifier].get("function", {})
                            call_arguments = function_dict.get("arguments", "")
                            del pending_tool_calls[tool_call_identifier]

                        chat.add_tool_result(
                            tool_call_name,
                            tool_call_content,
                            description="",
                            arguments=call_arguments,
                            consecutive_failures=0,
                            tool_call_id=tool_call_identifier,
                        )
                    elif role == "system":
                        chat.add_system_message(content)
                except Exception as replay_error:
                    self._debug_widget.write(f"MESSAGE REPLAY ERROR: {type(replay_error).__name__}: {replay_error!s}")
                    import traceback

                    self._debug_widget.write(f"TRACEBACK: {traceback.format_exc()}")

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
