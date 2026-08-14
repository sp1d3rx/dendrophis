"""MainScreen — chat column + sidebar layout with event bus integration."""

from __future__ import annotations

import os
from collections import deque
from typing import TYPE_CHECKING, Any, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header

from dendrophis.events import ModelSwitchRequest, WaitingForInputEvent
from dendrophis.ui.screens.main_autocomplete import MainScreenAutocomplete
from dendrophis.ui.screens.main_commands import MainScreenCommands
from dendrophis.ui.screens.main_events import MainScreenEventHandler
from dendrophis.ui.widgets.chat_view import ChatView
from dendrophis.ui.widgets.debug_log import DebugLogWidget
from dendrophis.ui.widgets.input_bar import FileAutocomplete, InputBar
from dendrophis.ui.widgets.panels.model_panel import ModelPanel
from dendrophis.ui.widgets.sidebar import Sidebar

if TYPE_CHECKING:
    from dendrophis.events import EventBus
    from dendrophis.session.session import Session


class MainScreen(MainScreenEventHandler, MainScreenCommands, MainScreenAutocomplete, Screen):
    """Primary screen: streaming chat + configurable sidebar with event bus."""

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
    #streaming-indicator {
        display: none;
        width: 1;
        padding: 0 1;
        color: $primary;
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
        target_settings_tab = os.environ.get("DENDROPHIS_OPEN_SETTINGS")
        if target_settings_tab:
            self.call_later(lambda: self.action_open_settings(initial_tab=target_settings_tab))

    def _auto_load_primer(self) -> None:
        """Automatically load project primer on session start."""
        chat = self.query_one(ChatView)
        primer_info = self._session.load_project_primer()
        if primer_info:
            # Inject primer files into context so the LLM has project knowledge
            injection_result = self._session.inject_primer_files()
            output_parts = [
                f"Project primer loaded: [bold]{primer_info['project_name']}[/bold] ({primer_info['file_count']} files)"
            ]
            if injection_result["injected"]:
                output_parts.append(f"[green]  {injection_result['injected']} file(s) injected into context[/green]")
                injected_files = injection_result.get("injected_files", [])
                if injected_files:
                    output_parts.append(f"  [dim]Files: {', '.join(injected_files)}[/dim]")
            if primer_info["understanding"]:
                output_parts.append(f"  {primer_info['understanding']}")

            chat.add_system_message("\n".join(output_parts))
            self._debug_widget.write(
                f"[NOTIFY] Auto-loaded primer: {primer_info['project_name']} ({primer_info['file_count']} files)"
            )
        else:
            # No primer — show welcome help so user knows what's available
            if self._session.config.caching.pr_enabled:
                self._show_help()

    def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        if self._streaming:
            if len(self._input_queue) == self._input_queue.maxlen:
                warning_message = f"Queue full ({self._input_queue.maxlen} max). Input dropped."
                self._debug_widget.write(f"[NOTIFY] {warning_message}")
                self.notify(warning_message, severity="warning", timeout=3.0)
                return
            self._input_queue.append(event)
            queue_message = f"Prompt queued. ({len(self._input_queue)}/{self._input_queue.maxlen} pending)"
            self._debug_widget.write(f"[NOTIFY] {queue_message}")
            self.notify(queue_message, severity="information", timeout=4.0)
            return

        self._process_input(event)

    def on_worker_state_changed(self, event: Any) -> None:
        from textual.worker import WorkerState

        if event.state == WorkerState.ERROR and event.worker.error:
            chat_view = self.query_one(ChatView)
            chat_view.add_error(f"Worker error: {event.worker.error}")
            self.app.debug_log(f"[ERROR] Worker error: {event.worker.error}")

    def on_model_panel_switched(self, event: ModelPanel.Switched) -> None:
        """Handle click on model panel by opening the switcher."""
        from dendrophis.ui.screens.model_switcher import ModelSwitcherScreen

        def handle_model_selected(selected_result: tuple[str, bool] | None) -> None:
            if selected_result:
                model_identifier, should_clear_chat = selected_result
                self._event_bus.publish(ModelSwitchRequest(model_id=model_identifier))
                if should_clear_chat:
                    self.action_clear_chat()

        self.app.push_screen(ModelSwitcherScreen(self._session), handle_model_selected)

    def action_open_session_picker(self) -> None:
        """Open the session picker to load a previous session."""
        from dendrophis.ui.screens.session_picker import SessionPickerScreen

        def handle_session_selected(selection_result: tuple[str, str] | str | None) -> None:
            if not selection_result:
                return

            if isinstance(selection_result, tuple):
                action_type, selected_path = selection_result
                as_fork = action_type == "fork"
            else:
                selected_path = selection_result
                as_fork = False

            # Save current session to avoid data loss
            if self._session.context.messages:
                saved_path = self._session.save_session()
                if saved_path:
                    self._debug_widget.write(f"[NOTIFY] Session autosaved to: {saved_path}")

            loaded_info = self._session.load_session(selected_path, fork=as_fork)
            if loaded_info:
                self.app._update_title()
                message_count = loaded_info.get("message_count", 0)
                if as_fork:
                    self.notify(
                        f"Forked session [{self._session.session_id[:8]}] with {message_count} messages",
                        severity="information",
                    )
                else:
                    self.notify(f"Resumed session with {message_count} messages", severity="information")
                self._debug_widget.write(f"[NOTIFY] Session loaded (fork={as_fork}): {selected_path}")
            else:
                self.notify("Failed to load session", severity="error")
                self._debug_widget.write(f"[NOTIFY ERROR] Failed to load session: {selected_path}")

        self.app.push_screen(SessionPickerScreen(self._session), handle_session_selected)

    def action_open_settings(self, initial_tab: str = "tab-llm") -> None:
        from dendrophis.ui.screens.settings import SettingsScreen

        self.app.push_screen(SettingsScreen(self._session, initial_tab=initial_tab))

    def action_open_memory_viewer(self) -> None:
        """Open the memory viewer."""
        from dendrophis.ui.screens.memory_viewer import MemoryViewerScreen

        self.app.push_screen(MemoryViewerScreen(self._session))

    def action_interrupt(self) -> None:
        if self._streaming:
            self._session.cancel_streaming()
            self._streaming = False
            self.query_one(InputBar).set_streaming(False)
            chat = self.query_one(ChatView)
            chat.remove_retry_status()
            chat.finish_assistant_message()
            # Reset status panel to "Ready"
            self._event_bus.publish(WaitingForInputEvent())

    def action_toggle_debug(self) -> None:
        """Toggle debug log visibility."""
        self._debug_widget.toggle()
