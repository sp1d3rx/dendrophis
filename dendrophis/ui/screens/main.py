"""MainScreen — chat column + sidebar layout with event bus integration."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header

from dendrophis.events import (
    ConfigReloadedEvent,
    ContextUpdatedEvent,
    EditProposalEvent,
    ErrorEvent,
    EventBus,
    ModelSwitchedEvent,
    ModelSwitchRequest,
    MultipleChoiceRequestEvent,
    PrimerLoadedEvent,
    PrimerScreenRequest,
    PythonExecProposalEvent,
    ReasoningDeltaEvent,
    RetryEvent,
    StatsUpdatedEvent,
    StreamingFinishedEvent,
    StreamingStartedEvent,
    TextDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    ToolConfirmationRequestEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolResultEvent,
    WaitingForInputEvent,
    WriteProposalEvent,
)
from dendrophis.ui.widgets.chat_view import ChatView
from dendrophis.ui.widgets.debug_log import DebugLogWidget
from dendrophis.ui.widgets.input_bar import InputBar
from dendrophis.ui.widgets.panels.model_panel import ModelPanel
from dendrophis.ui.widgets.sidebar import Sidebar

if TYPE_CHECKING:
    from dendrophis.session.session import Session

from dendrophis.ui.widgets.input_bar import FileAutocomplete


class MainScreen(Screen):
    """Primary screen: streaming chat + configurable sidebar with event bus."""

    from typing import ClassVar

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+l", "clear_chat", "Clear"),
        ("ctrl+o", "open_session_picker", "Resume"),
        ("ctrl+t", "open_settings", "Settings"),
        ("ctrl+m", "open_memory_viewer", "Memory"),
        ("ctrl+shift+d", "toggle_debug", "Debug"),
        ("ctrl+e", "export_session", "Export"),
        ("escape", "interrupt", "Interrupt"),
        ("ctrl+q", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    #main-layout {
        height: 1fr;
    }
    #chat-column {
        height: 1fr;
        width: 1fr;
    }
    ChatView {
        height: 1fr;
        width: 100%;
        scrollbar-gutter: stable;
    }
    FileAutocomplete {
        dock: bottom;
        layer: top;
        offset: 2 -5;
    }
    """

    def __init__(self, session: Session, event_bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._event_bus = event_bus
        self._streaming = False
        self._input_queue: deque[InputBar.Submitted] = deque(maxlen=10)
        self._debug_widget = DebugLogWidget()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield self._debug_widget
        with Horizontal(id="main-layout"):
            with Vertical(id="chat-column"):
                yield ChatView(max_scrollback=self._session.config.ui.scrollback_limit)
                yield InputBar(language="markdown", soft_wrap=True)
            if self._session.config.ui.sidebar.panels:
                yield Sidebar(
                    session=self._session,
                    event_bus=self._event_bus,
                )
        yield FileAutocomplete()
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(InputBar).focus()
        # Subscribe to events
        self._setup_event_handlers()
        # Auto-load project primer if available (deferred so UI is ready)
        self.call_later(self._auto_load_primer)

    def _auto_load_primer(self) -> None:
        """Automatically load project primer on session start."""
        chat = self.query_one(ChatView)
        info = self._session.load_project_primer()
        if info:
            # Inject primer files into context so the LLM has project knowledge
            inj_result = self._session.inject_primer_files()
            parts = [f"Project primer loaded: [bold]{info['project_name']}[/bold] ({info['file_count']} files)"]
            if inj_result["injected"]:
                parts.append(f"[green]  {inj_result['injected']} file(s) injected into context[/green]")
                injected_files = inj_result.get("injected_files", [])
                if injected_files:
                    parts.append(f"  [dim]Files: {', '.join(injected_files)}[/dim]")
            if info["understanding"]:
                parts.append(f"  {info['understanding']}")

            chat.add_system_message("\n".join(parts))
            self._debug_widget.write(
                f"[NOTIFY] Auto-loaded primer: {info['project_name']} ({info['file_count']} files)"
            )
        else:
            # No primer — show welcome help so user knows what's available
            # Only show welcome help automatically if primer feature is enabled
            if self._session.config.caching.pr_enabled:
                self._show_help()

    def _setup_event_handlers(self) -> None:
        """Subscribe this screen to relevant events."""
        self._handlers = [
            (TextDeltaEvent, self._on_text_delta),
            (ReasoningDeltaEvent, self._on_reasoning_delta),
            (ErrorEvent, self._on_error),
            (RetryEvent, self._on_retry),
            (ToolResultEvent, self._on_tool_result),
            (StreamingStartedEvent, self._on_streaming_started),
            (StreamingFinishedEvent, self._on_streaming_finished),
            (ToolCallStartEvent, self._on_tool_call_start),
            (ToolCallDeltaEvent, self._on_tool_call_delta),
            (ToolExecutionStartedEvent, self._on_tool_execution_started),
            (ToolExecutionFinishedEvent, self._on_tool_execution_finished),
            (ToolConfirmationRequestEvent, self._on_tool_confirmation_request),
            (MultipleChoiceRequestEvent, self._on_multiple_choice_request),
            (ContextUpdatedEvent, self._on_context_updated),
            (ModelSwitchedEvent, self._on_model_switched),
            (ConfigReloadedEvent, self._on_config_reloaded),
            (EditProposalEvent, self._on_edit_proposal),
            (WriteProposalEvent, self._on_write_proposal),
            (PythonExecProposalEvent, self._on_python_exec_proposal),
            (PrimerScreenRequest, self._on_primer_screen_request),
        ]

        for event_type, handler in self._handlers:
            self._event_bus.subscribe(event_type, handler)

    def on_unmount(self) -> None:
        """Unsubscribe all event handlers to prevent memory leaks."""
        for event_type, handler in self._handlers:
            self._event_bus.unsubscribe(event_type, handler)
        self._handlers.clear()

    def _on_text_delta(self, event: TextDeltaEvent) -> None:
        """Handle text delta events."""
        self.query_one(ChatView).append_text_delta(event.delta)

    def _on_reasoning_delta(self, event: ReasoningDeltaEvent) -> None:
        """Handle reasoning delta events."""
        self.query_one(ChatView).append_reasoning_delta(event.delta)

    def _on_error(self, event: ErrorEvent) -> None:
        """Handle error events."""
        self.query_one(ChatView).add_error(event.message)

    def _on_retry(self, event: RetryEvent) -> None:
        """Handle retry events."""
        self.query_one(ChatView).show_retry_status(event.message, event.delay)

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

    def _on_streaming_started(self, event: StreamingStartedEvent) -> None:
        """Handle streaming started events."""
        self._streaming = True
        model_id = self._session.config.llm.model
        self.query_one(ChatView).start_assistant_message(model_id=model_id)

    def _on_streaming_finished(self, event: StreamingFinishedEvent) -> None:
        """Handle streaming finished events."""
        self._streaming = False
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
            remaining = f" ({len(self._input_queue)} remaining)" if self._input_queue else ""
            msg = f"Processing queued prompt...{remaining}"
            self._debug_widget.write(f"[NOTIFY] {msg}")
            self.notify(msg, severity="information")
            self._process_input(next_event)

    def _on_tool_execution_started(self, event: ToolExecutionStartedEvent) -> None:
        self.query_one(ChatView).add_tool_status(
            event.tool_name, event.description, event.arguments, index=event.tool_call_index
        )

    def _on_tool_call_start(self, event: ToolCallStartEvent) -> None:
        self.query_one(ChatView).add_tool_placeholder(event.index, event.name, tool_call_id=event.id)

    def _on_tool_call_delta(self, event: ToolCallDeltaEvent) -> None:
        """Handle tool call delta events (streaming)."""
        pass

    def _on_tool_execution_finished(self, event: ToolExecutionFinishedEvent) -> None:
        """Handle tool execution finished events."""
        pass

    def _on_tool_confirmation_request(self, event: ToolConfirmationRequestEvent) -> None:
        """Handle human approval request for sensitive tools."""

        def show_confirmation() -> None:
            from dendrophis.ui.screens.tool_confirmation import ToolConfirmationScreen

            self.app.push_screen(
                ToolConfirmationScreen(event.request_id, event.tool_name, event.arguments, self._event_bus)
            )

        # Schedule on the UI thread to ensure proper app context
        self.call_later(show_confirmation)

    def _on_multiple_choice_request(self, event: MultipleChoiceRequestEvent) -> None:
        """Handle human multiple choice question request."""

        def show_mcq() -> None:
            from dendrophis.ui.screens.multiple_choice import MultipleChoiceScreen

            self.app.push_screen(MultipleChoiceScreen(event.request_id, event.question, event.options, self._event_bus))

        # Schedule on the UI thread to ensure proper app context
        self.call_later(show_mcq)

    def _on_edit_proposal(self, event: EditProposalEvent) -> None:
        """Handle request for file edit approval with diff."""

        def show_edit_confirmation() -> None:
            from dendrophis.ui.screens.edit_confirmation import EditConfirmationScreen

            self.app.push_screen(EditConfirmationScreen(event, self._event_bus))

        self.call_later(show_edit_confirmation)

    def _on_write_proposal(self, event: WriteProposalEvent) -> None:
        """Handle request for new file write approval with content preview."""

        def show_write_confirmation() -> None:
            from dendrophis.ui.screens.write_confirmation import WriteConfirmationScreen

            self.app.push_screen(WriteConfirmationScreen(event, self._event_bus))

        self.call_later(show_write_confirmation)

    def _on_python_exec_proposal(self, event: PythonExecProposalEvent) -> None:
        """Handle request for Python code execution approval with code preview."""

        def show_python_exec_confirmation() -> None:
            from dendrophis.ui.screens.python_exec_confirmation import (
                PythonExecConfirmationScreen,
            )

            self.app.push_screen(PythonExecConfirmationScreen(event, self._event_bus))

        self.call_later(show_python_exec_confirmation)

    def _on_primer_screen_request(self, event: PrimerScreenRequest) -> None:
        """Handle request to open the project primer screen."""

        def show_primer_screen() -> None:
            from dendrophis.ui.screens.primer_screen import PrimerScreen

            self.app.push_screen(PrimerScreen(self._session))

        self.call_later(show_primer_screen)

    def _on_context_updated(self, event: ContextUpdatedEvent) -> None:
        """Handle context updated events."""
        # Sidebar will refresh via StatsUpdatedEvent
        if getattr(event, "full_chat_restored", False):
            chat = self.query_one(ChatView)
            chat.clear()

            # Track pending tool calls from assistant messages to match with results
            pending_tool_calls: dict[str, dict] = {}

            for msg in self._session.context.messages:
                try:
                    role = msg.get("role", "unknown")
                    raw_content = msg.get("content", "")
                    if isinstance(raw_content, list):
                        content = " ".join(part.get("text", "") for part in raw_content if isinstance(part, dict))
                    else:
                        content = raw_content or ""
                    if role == "user":
                        chat.add_user_message(content)
                    elif role == "assistant":
                        chat.start_assistant_message()
                        if content:
                            chat.append_text_delta(content)
                        # Store tool calls to match with results later
                        for tc in msg.get("tool_calls", []):
                            tc_id = tc.get("id")
                            if tc_id:
                                pending_tool_calls[tc_id] = tc
                        chat.finish_assistant_message()
                    elif role == "tool":
                        # Tool result message - match with pending call
                        tc_id = msg.get("tool_call_id")
                        tc_name = msg.get("name", "unknown")
                        tc_content = msg.get("content", "")

                        # Get arguments from pending call if available
                        arguments = ""
                        if tc_id and tc_id in pending_tool_calls:
                            fn = pending_tool_calls[tc_id].get("function", {})
                            arguments = fn.get("arguments", "")
                            del pending_tool_calls[tc_id]

                        chat.add_tool_result(
                            tc_name,
                            tc_content,
                            description="",
                            arguments=arguments,
                            consecutive_failures=0,
                            tool_call_id=tc_id,
                        )
                    elif role == "system":
                        chat.add_system_message(content)
                except Exception as e:
                    self._debug_widget.write(f"MESSAGE REPLAY ERROR: {type(e).__name__}: {e!s}")
                    import traceback

                    self._debug_widget.write(f"TRACEBACK: {traceback.format_exc()}")
                # Add handling for other roles if needed

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

    def _on_config_reloaded(self, event: ConfigReloadedEvent) -> None:
        """Rebuild the sidebar whenever the config is saved."""
        # Already on the event loop thread (event bus uses call_soon_threadsafe).
        # call_later defers DOM mutations to the next tick so any modal dismiss
        # animation finishes first.
        self.call_later(self._rebuild_sidebar)

    def _rebuild_sidebar(self) -> None:
        """Remove the existing sidebar and mount a fresh one from updated config."""
        layout = self.query_one("#main-layout")
        # Remove old sidebar if present
        for old in self.query(Sidebar):
            old.remove()
        # Mount a new one if any panels are configured
        if self._session.config.ui.sidebar.panels:
            new_sidebar = Sidebar(
                session=self._session,
                event_bus=self._event_bus,
            )
            layout.mount(new_sidebar)

    # ── Input handling ─────────────────────────────────────────────────────────────

    def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        if self._streaming:
            if len(self._input_queue) == self._input_queue.maxlen:
                msg = f"Queue full ({self._input_queue.maxlen} max). Input dropped."
                self._debug_widget.write(f"[NOTIFY] {msg}")
                self.notify(msg, severity="warning", timeout=3.0)
                return
            self._input_queue.append(event)
            msg = f"Prompt queued. ({len(self._input_queue)}/{self._input_queue.maxlen} pending)"
            self._debug_widget.write(f"[NOTIFY] {msg}")
            self.notify(msg, severity="information", timeout=4.0)
            return

        self._process_input(event)

    def action_export_session(self) -> None:
        """Handle ctrl+e to export session."""
        self._export_session()

    def _export_session(self) -> None:
        """Export the full conversation to a markdown file."""
        from datetime import datetime

        # Generate filename: session-SHORTID.YYYY-MM-DD.HHMMSS.md
        session_id = self._session.session_id[:8]
        timestamp = datetime.now().strftime("%Y-%m-%d.%H%M%S")
        filename = f"session-{session_id}.{timestamp}.md"

        try:
            # Build markdown content
            md_parts = [f"# Dendrophis Session Export - {session_id}\n"]
            md_parts.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            md_parts.append(f"**Model:** {self._session.config.llm.model}\n")
            md_parts.append("---\n")

            for msg in self._session.context.messages:
                role = msg.get("role", "unknown").capitalize()
                content = msg.get("content", "")

                md_parts.append(f"### {role}\n")
                if content:
                    md_parts.append(f"{content}\n")

                # Handle tool calls
                if "tool_calls" in msg:
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        md_parts.append(f"> **Tool Call:** `{fn.get('name')}`\n")
                        md_parts.append(f"> ```json\n> {fn.get('arguments')}\n> ```\n")

                md_parts.append("\n")

            # Save to current directory
            with open(filename, "w", encoding="utf-8") as f:
                f.write("\n".join(md_parts))

            msg = f"Session exported to {filename}"
            self._debug_widget.write(f"[NOTIFY] {msg}")
            self.notify(f"Session exported to [bold]{filename}[/bold]", severity="information")
        except Exception as e:
            msg = f"Export failed: {e!s}"
            self._debug_widget.write(f"[NOTIFY ERROR] {msg}")
            self.notify(msg, severity="error")

    def _save_primer(self) -> None:
        """Save a project primer from current session understanding."""
        chat = self.query_one(ChatView)
        result = self._session.save_project_primer()
        if result:
            msg = f"Project primer saved for [bold]{result}[/bold]"
            self._debug_widget.write(f"[NOTIFY] {msg}")
            chat.add_system_message(f"Project primer saved: {result}")
            self.notify(msg, severity="information")
            # Emit event to update primer panel
            info = self._session.load_project_primer()
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
            msg = "Failed to save project primer"
            self._debug_widget.write(f"[NOTIFY ERROR] {msg}")
            self.notify(msg, severity="error")

    def _load_primer(self) -> None:
        """Load the project primer and inject into context."""
        chat = self.query_one(ChatView)
        info = self._session.load_project_primer()
        if info:
            parts = [f"Loaded project primer: [bold]{info['project_name']}[/bold]"]
            parts.append(f"  Files tracked: {info['file_count']}")
            if info["understanding"]:
                parts.append(f"  Understanding: {info['understanding']}")
            msg = "\n".join(parts)
            self._debug_widget.write(f"[NOTIFY] Primer loaded: {info['project_name']}")
            chat.add_system_message(msg)
            self.notify(f"Primer loaded: {info['file_count']} files", severity="information")
            # Emit event to update primer panel
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
            msg = "No project primer found for this directory"
            self._debug_widget.write(f"[NOTIFY] {msg}")
            chat.add_system_message(msg)
            self.notify(msg, severity="information")

    def _show_help(self) -> None:
        """Show available slash commands in the chat as a single compact message."""
        chat = self.query_one(ChatView)
        pr_enabled = self._session.config.caching.pr_enabled

        commands = [
            ("  /hello       ", "Greeting from Dendrophis"),
            ("  /help        ", "Show this help message"),
        ]

        if pr_enabled:
            commands.append(("  /clear       ", "Clear chat and reset context (primer re-injected)"))
            commands.append(("  /fresh       ", "Clear chat without primer (truly fresh start)"))
        else:
            commands.append(("  /clear       ", "Clear chat and reset context"))

        commands.extend(
            [
                ("  /compact     ", "Manually compact context to reduce token usage"),
                ("  /export      ", "Export conversation to markdown file"),
            ]
        )

        if pr_enabled:
            commands.extend(
                [
                    ("  /save-primer ", "Save project primer for future sessions"),
                    ("  /load-primer ", "Load the project primer and inject it into context"),
                    ("  /track       ", "Add a file to the project primer"),
                    ("  /untrack     ", "Remove a file from the project primer"),
                ]
            )

        commands.append(("  /set         ", "Override the last assistant response"))

        parts = ["[bold]Slash Commands[/bold]"]
        for cmd_name, cmd_desc in commands:
            parts.append(f"{cmd_name}— {cmd_desc}")

        parts.extend(
            [
                "",
                "[bold]Key Bindings[/bold]",
                "  Ctrl+L  — Clear chat (same as /clear)",
                "  Ctrl+S  — Open session picker",
                "  Ctrl+T  — Open settings",
                "  Ctrl+E  — Export session (same as /export)",
                "  Esc     — Interrupt streaming",
                "  Ctrl+Q  — Quit",
            ]
        )

        if pr_enabled:
            parts.extend(
                [
                    "",
                    "[bold]Project Primer[/bold]",
                    "On a new session, any saved primer is loaded automatically — tracked\n"
                    "files are re-read from disk and injected into context so the LLM\n"
                    "already knows your project. Changed files are detected via content\n"
                    "hashing and re-read fresh. Use /save-primer after exploring a project\n"
                    "to capture it for next time. Use /fresh to start without the primer.",
                ]
            )

        chat.add_system_message("\n".join(parts))

    def _process_input(self, event: InputBar.Submitted) -> None:
        """Handle user input from the chat bar."""
        cmd = event.text.strip().lower()
        if cmd == "/export":
            self._export_session()
            return
        if cmd == "/clear":
            self.action_clear_chat()
            return
        if cmd == "/fresh":
            self._fresh_chat()
            return
        if cmd == "/compact":
            self._compact_context()
            return
        if cmd == "/help":
            self._show_help()
            return
        if cmd == "/hello":
            chat = self.query_one(ChatView)
            chat.add_system_message("Hello! 👋 I'm Dendrophis, your coding assistant.")
            return
        if cmd == "/save-primer":
            if not self._session.config.caching.pr_enabled:
                chat = self.query_one(ChatView)
                chat.add_system_message(
                    "[warning]Project primer is disabled in config "
                    "(caching.pr_enabled). Enable it in Settings.[/warning]"
                )
                return
            self._save_primer()
            return
        if cmd == "/load-primer":
            if not self._session.config.caching.pr_enabled:
                chat = self.query_one(ChatView)
                chat.add_system_message(
                    "[warning]Project primer is disabled in config "
                    "(caching.pr_enabled). Enable it in Settings.[/warning]"
                )
                return
            self._load_primer()
            return
        if event.text.strip().startswith("/track "):
            if not self._session.config.caching.pr_enabled:
                self.notify("Primer feature is disabled in settings.", severity="warning")
                return
            path = event.text.strip()[7:].strip()
            if self._session.track_file(path):
                chat = self.query_one(ChatView)
                chat.add_system_message(f"Tracking file: [bold]{path}[/bold]")
                self.notify(f"Now tracking: {path}", severity="information")
            else:
                self.notify(f"Failed to track: {path}", severity="error")
            return
        if event.text.strip().startswith("/untrack "):
            if not self._session.config.caching.pr_enabled:
                self.notify("Primer feature is disabled in settings.", severity="warning")
                return
            path = event.text.strip()[9:].strip()
            if self._session.untrack_file(path):
                chat = self.query_one(ChatView)
                chat.add_system_message(f"Stopped tracking: [bold]{path}[/bold]")
                self.notify(f"Stopped tracking: {path}", severity="information")
            else:
                self.notify(f"Failed to untrack: {path}", severity="error")
            return
        if event.text.strip().startswith("/set "):
            fake_text = event.text.strip()[5:]
            chat = self.query_one(ChatView)
            if self._session.context.replace_last_assistant(fake_text):
                chat.add_system_message("Last response overridden.")
            else:
                chat.add_system_message("No assistant message to override.")
            return

        chat = self.query_one(ChatView)

        # Inject @file contents into context
        for path in event.file_paths:
            self._session.context.append_file(str(path), path.read_text(errors="replace"))

        if not event.text:
            return

        chat.add_user_message(event.text)
        # Start streaming in background - events will update UI
        self.run_worker(self._session.send_message(event.text), exclusive=True, exit_on_error=False)

    def on_worker_state_changed(self, event) -> None:
        from textual.worker import WorkerState

        if event.state == WorkerState.ERROR and event.worker.error:
            chat = self.query_one(ChatView)
            chat.add_error(f"Worker error: {event.worker.error}")

    def on_model_panel_switched(self, event: ModelPanel.Switched) -> None:
        """Handle click on model panel by opening the switcher."""
        from dendrophis.ui.screens.model_switcher import ModelSwitcherScreen

        def handle_model_selected(result: tuple[str, bool] | None) -> None:
            if result:
                model_id, clear = result
                self._event_bus.publish(ModelSwitchRequest(model_id=model_id))
                if clear:
                    self.action_clear_chat()

        self.app.push_screen(ModelSwitcherScreen(self._session), handle_model_selected)

    # ── Actions ─────────────────────────────────────────────────────────────────────

    def action_clear_chat(self) -> None:
        self.query_one(ChatView).clear()
        # Synchronously reset session state
        self._session.reset()
        # Re-inject primer files so the fresh context has project knowledge
        inj_result = self._session.inject_primer_files()

        # Publish events to update panels/stats
        from dendrophis.events import ContextUpdatedEvent

        self._event_bus.publish(
            ContextUpdatedEvent(
                token_count=self._session.context.token_count,
                token_pct=self._session.context.token_pct,
                turn_count=self._session.context.get_turn_count(),
                full_chat_restored=False,
            )
        )
        self._event_bus.publish(
            StatsUpdatedEvent(
                prompt_tokens=self._session.stats.prompt_tokens,
                completion_tokens=self._session.stats.completion_tokens,
                total_cost_usd=self._session.stats.total_cost_usd,
                tokens_per_sec=0.0,
                time_to_first_token=0.0,
            )
        )

        chat = self.query_one(ChatView)
        if inj_result["injected"]:
            injected_files = inj_result.get("injected_files", [])
            files_str = f" ({', '.join(injected_files)})" if injected_files else ""
            chat.add_system_message(f"Project primer re-loaded: {inj_result['injected']} file(s) injected{files_str}")
        # Always show help so available commands are visible
        self._show_help()

    def _fresh_chat(self) -> None:
        """Clear chat without injecting primer — truly fresh start."""
        self.query_one(ChatView).clear()
        self._session.reset()
        from dendrophis.events import ContextUpdatedEvent

        self._event_bus.publish(
            ContextUpdatedEvent(
                token_count=self._session.context.token_count,
                token_pct=self._session.context.token_pct,
                turn_count=self._session.context.get_turn_count(),
                full_chat_restored=False,
            )
        )
        self._event_bus.publish(
            StatsUpdatedEvent(
                prompt_tokens=0,
                completion_tokens=0,
                total_cost_usd=0.0,
                tokens_per_sec=0.0,
                time_to_first_token=0.0,
            )
        )
        self._show_help()

    def _compact_context(self) -> None:
        """Manually trigger context compaction to reduce token usage."""
        chat = self.query_one(ChatView)

        # Capture before state
        before_tokens = self._session.context.token_count
        before_pct = self._session.context.token_pct * 100
        before_msg_count = len(self._session.context.messages)

        chat.add_system_message(
            f"[dim]Compacting context... ({before_tokens:,} tokens, {before_pct:.1f}%,"
            f" {before_msg_count} messages)[/dim]"
        )
        self._debug_widget.write(
            f"[NOTIFY] Starting context compaction ({before_tokens:,} tokens, {before_msg_count} messages)"
        )

        async def do_compact() -> None:
            try:
                result = await self._session.compact()

                if not result.get("compacted"):
                    chat.add_system_message(
                        f"[dim]Compaction skipped: {result.get('reason', 'No messages to compact')}[/dim]"
                    )
                    return

                # Show results
                after_tokens = self._session.context.token_count
                after_pct = self._session.context.token_pct * 100
                after_msg_count = len(self._session.context.messages)
                saved = before_tokens - after_tokens
                compacted = result.get("messages_compacted", 0)
                kept = result.get("kept_recent", 0)
                summary = result.get("summary", "")

                # Build detailed message
                lines = [
                    "[bold]Context Compacted[/bold]",
                    "",
                    f"[dim]Messages:[/dim] {before_msg_count} → {after_msg_count}"
                    f" ([green]-{compacted}[/] compacted, {kept} kept)",
                    f"[dim]Tokens:[/dim]   {before_tokens:,} → {after_tokens:,}"
                    f" ([green]-{saved:,}[/], {after_pct:.1f}%)",
                ]

                # Add summary preview (truncated if long)
                if summary:
                    lines.append("")
                    lines.append("[dim]Summary:[/dim]")
                    # Truncate summary to ~300 chars for display
                    preview = summary[:300] + "..." if len(summary) > 300 else summary
                    lines.append(f"[italic]{preview}[/italic]")

                chat.add_system_message("\n".join(lines))
                self._debug_widget.write(f"[NOTIFY] Compacted {compacted} messages, saved {saved:,} tokens")

                # Emit context update event so panels refresh
                self._event_bus.publish(
                    ContextUpdatedEvent(
                        token_count=after_tokens,
                        token_pct=after_pct / 100,
                    )
                )
            except Exception as e:
                chat.add_system_message(f"[red]Compaction failed: {e}[/red]")
                self._debug_widget.write(f"[NOTIFY ERROR] Context compaction failed: {e}")

        self.run_worker(do_compact(), exclusive=True, exit_on_error=False)

    def action_open_session_picker(self) -> None:
        """Open the session picker to load a previous session."""
        from dendrophis.ui.screens.session_picker import SessionPickerScreen

        def handle_session_selected(selected_path: str | None) -> None:
            if not selected_path:
                return

            # Save current session to avoid data loss
            if self._session.context.messages:
                saved_path = self._session.save_session()
                if saved_path:
                    self._debug_widget.write(f"[NOTIFY] Session autosaved to: {saved_path}")

            loaded_info = self._session.load_session(selected_path)
            if loaded_info:
                self.app._update_title()
                message_count = loaded_info.get("message_count", 0)
                self.notify(f"Resumed session with {message_count} messages", severity="information")
                self._debug_widget.write(f"[NOTIFY] Session loaded: {selected_path}")
            else:
                self.notify("Failed to load session", severity="error")
                self._debug_widget.write(f"[NOTIFY ERROR] Failed to load session: {selected_path}")

        self.app.push_screen(SessionPickerScreen(self._session), handle_session_selected)

    def action_open_settings(self) -> None:
        from dendrophis.ui.screens.settings import SettingsScreen

        self.app.push_screen(SettingsScreen(self._session))

    def action_open_memory_viewer(self) -> None:
        """Open the memory viewer."""
        from dendrophis.ui.screens.memory_viewer import MemoryViewerScreen

        self.app.push_screen(MemoryViewerScreen(self._session))

    def action_interrupt(self) -> None:
        if self._streaming:
            self._session.cancel_streaming()
            self._streaming = False
            chat = self.query_one(ChatView)
            chat.remove_retry_status()
            chat.finish_assistant_message()
            # Reset status panel to "Ready" — the background worker may not
            # emit WaitingForInputEvent if it gets cancelled before reaching
            # the post-stream code.
            self._event_bus.publish(WaitingForInputEvent())

    def action_toggle_debug(self) -> None:
        """Toggle debug log visibility."""
        self._debug_widget.toggle()

    # ── Autocomplete Handlers ────────────────────────────────────────────────

    def on_input_bar_request_autocomplete(self, event: InputBar.RequestAutocomplete) -> None:
        """Find matching files or commands and update the suggestion list."""
        auto = self.query_one(FileAutocomplete)
        if event.prefix is None:
            auto.set_suggestions([])
            return

        if event.kind == "command":
            self._complete_commands(auto, event.prefix)
        else:
            self._complete_files(auto, event.prefix)

    def _complete_commands(self, auto: FileAutocomplete, prefix: str) -> None:
        """Filter available slash commands by prefix."""
        pr_enabled = self._session.config.caching.pr_enabled
        commands = [
            ("/hello", "Greeting from Dendrophis"),
            ("/help", "Show this help message"),
        ]
        if pr_enabled:
            commands.append(("/clear", "Clear chat and reset context (primer re-injected)"))
            commands.append(("/fresh", "Clear chat without primer (truly fresh start)"))
        else:
            commands.append(("/clear", "Clear chat and reset context"))

        commands.extend(
            [
                ("/compact", "Manually compact context to reduce token usage"),
                ("/export", "Export conversation to markdown file"),
            ]
        )

        if pr_enabled:
            commands.extend(
                [
                    ("/save-primer", "Save project primer for future sessions"),
                    ("/load-primer", "Load the project primer and inject it into context"),
                    ("/track", "Add a file to the project primer"),
                    ("/untrack", "Remove a file from the project primer"),
                ]
            )

        commands.append(("/set", "Override the last assistant response"))

        for name, skill in self._session._skill_manager._all_skills.items():
            short_desc = skill.description.splitlines()[0][:60]
            commands.append((f"/{name}", short_desc))
        matched = [(cmd, desc) for cmd, desc in commands if cmd.startswith("/" + prefix)]
        suggestions = [f"{cmd}  \u2014 {desc}" for cmd, desc in matched]
        auto.set_suggestions(suggestions, kind="command")

    def _complete_files(self, auto: FileAutocomplete, prefix: str) -> None:
        """Find matching files recursively."""
        import glob

        pattern = f"**/{prefix}*"
        try:
            matches = glob.glob(pattern, recursive=True)
            matches.sort(key=len)
            files = matches[:15]
            auto.set_suggestions(files, kind="file")
        except Exception:
            pass

    def on_input_bar_navigate_autocomplete(self, event: InputBar.NavigateAutocomplete) -> None:
        """Navigate up/down in the suggestion list."""
        auto = self.query_one(FileAutocomplete)
        if auto.option_count > 0:
            if event.delta > 0:
                auto.action_cursor_down()
            else:
                auto.action_cursor_up()

    def on_input_bar_select_autocomplete(self, event: InputBar.SelectAutocomplete) -> None:
        """Apply the selected suggestion to the input bar."""
        auto = self.query_one(FileAutocomplete)
        input_bar = self.query_one(InputBar)
        selected = auto.selected

        # Hide immediately
        auto.set_suggestions([])

        if selected:
            import re

            row, col = input_bar.cursor_location
            lines = input_bar.text.splitlines()
            if not lines:
                lines = [""]
            current_line = lines[row]

            # Check if this is a command selection (contains em dash)
            if " — " in selected:
                # Extract just the command name before the description
                cmd_name = selected.split(" — ")[0].strip()

                # Check if this is a slash command (starts with /)
                if cmd_name.startswith("/"):
                    # Execute slash command immediately
                    self._execute_slash_command(cmd_name)
                    # Clear the input bar completely since command was executed
                    input_bar.text = ""
                    input_bar._draft = ""
                    input_bar._history_index = -1
                    # Keep focus on input bar for next command
                    input_bar.focus()
                    # Prevent any further processing of the original text
                    return
                # Regular file completion
                new_line = cmd_name + " "
                lines[row] = new_line
                input_bar.text = "\n".join(lines)
                new_col = len(cmd_name) + 1
                input_bar.move_cursor((row, new_col))
            else:
                # File selection: replace the @prefix with @selected
                match = re.search(r"@(\S*)$", current_line[:col])
                if match:
                    start_idx = match.start()
                    new_line = current_line[:start_idx] + f"@{selected} " + current_line[col:]
                    lines[row] = new_line
                    input_bar.text = "\n".join(lines)
                    new_col = start_idx + len(selected) + 2
                    input_bar.move_cursor((row, new_col))

        # Always restore focus to the input bar
        input_bar.focus()

    def _execute_slash_command(self, command: str) -> None:
        """Execute a slash command immediately and provide feedback."""

        # Remove leading slash
        cmd_name = command[1:]  # Remove the /

        # Check if it's a built-in command
        builtin_commands = {
            "hello": lambda: self.query_one(ChatView).add_system_message(
                "Hello! 👋 I'm Dendrophis, your coding assistant."
            ),
            "help": self._show_help,
            "clear": lambda: self._process_input(InputBar.Submitted("/clear", [])),
            "fresh": lambda: self._process_input(InputBar.Submitted("/fresh", [])),
            "compact": lambda: self._process_input(InputBar.Submitted("/compact", [])),
            "export": lambda: self._process_input(InputBar.Submitted("/export", [])),
            "save-primer": lambda: self._process_input(InputBar.Submitted("/save-primer", [])),
            "load-primer": lambda: self._process_input(InputBar.Submitted("/load-primer", [])),
            "track": lambda: self._process_input(InputBar.Submitted("/track", [])),
            "untrack": lambda: self._process_input(InputBar.Submitted("/untrack", [])),
            "set": lambda: self._process_input(InputBar.Submitted("/set", [])),
        }

        if cmd_name in builtin_commands:
            # Execute built-in command
            builtin_commands[cmd_name]()
            feedback = f"[bold]✅[/bold] Command [code]/{cmd_name}[/code] executed"
        else:
            # Check if it's a skill command
            if hasattr(self, "_session") and self._session and hasattr(self._session, "_skill_manager"):
                if cmd_name in self._session._skill_manager._all_skills:
                    skill = self._session._skill_manager._all_skills[cmd_name]
                    # Add skill context to chat
                    skill_message = (
                        f"[bold]📚 Skill Activated: {skill.name}[/bold]\n\n[italic]{skill.description}[/italic]"
                    )

                    # Add as system message
                    chat = self.query_one(ChatView)
                    chat.add_system_message(skill_message)

                    feedback = (
                        f"[bold][green]✓[/green][/bold] Skill [code]/{cmd_name}[/code] loaded. "
                        f"The skill documentation has been added to your context. "
                        f"You can now use its capabilities."
                    )
                else:
                    feedback = f"[bold][red]✗[/red][/bold] Unknown skill: [code]/{cmd_name}[/code]"
            else:
                feedback = (
                    f"[bold][yellow]⚠[/yellow][/bold] Skills not available yet. "
                    f"Command [code]/{cmd_name}[/code] queued."
                )

        # Add feedback to chat
        chat = self.query_one(ChatView)
        chat.add_system_message(feedback)

        # Note: No auto-submit needed since we cleared the input bar
        # and the system message will be visible to the LLM
