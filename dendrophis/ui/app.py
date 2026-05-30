"""DendrophisApp — Textual application root with event bus integration."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App

from dendrophis.config.loader import ConfigLoader
from dendrophis.events import (
    AuthFailedEvent,
    EventBus,
    ModelSwitchedEvent,
    set_event_bus,
)
from dendrophis.session.factory import SessionFactory
from dendrophis.ui.screens.debug_log import DebugLogScreen
from dendrophis.ui.screens.main import MainScreen


class DendrophisApp(App):
    """Root Textual application with event bus integration."""

    CSS_PATH = Path(__file__).parent / "styles" / "dendrophis.tcss"
    TITLE = "Dendrophis"
    SUB_TITLE = "Python coding agent"

    from typing import ClassVar

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+shift+d", "toggle_debug_log", "Debug Log"),
    ]

    def __init__(self, config_loader: ConfigLoader, session_path: str | None = None) -> None:
        super().__init__()
        self._config_loader = config_loader
        self._session_path = session_path

        # Initialize the event bus
        self._event_bus = EventBus(max_workers=8)
        set_event_bus(self._event_bus)

        # Use the SessionFactory for DI
        self._session = SessionFactory.create_session(
            config_loader=self._config_loader,
            event_bus=self._event_bus,
            debug_logger=self._on_debug_log,
        )

        self._debug_log_screen: DebugLogScreen | None = None
        self._main_screen: MainScreen | None = None
        self._debug_widget = None

    def _on_debug_log(self, message: str) -> None:
        """Callback for debug log messages."""
        # Write to docked debug widget if available
        if self._debug_widget:
            self._debug_widget.write(message)
        # Also write to screen debug log if it exists (for backward compatibility)
        elif self._debug_log_screen:
            self._debug_log_screen.write(message)

    def _update_title(self) -> None:
        """Update the title bar with session ID and model name."""
        short_id = self._session.session_id[:8]
        model = self._session.config.llm.model.split("/")[-1][:40]
        self.title = f"Dendrophis [{short_id}]"
        self.sub_title = model

    async def on_mount(self) -> None:
        # Set event loop for async handlers
        self._event_bus.set_event_loop(asyncio.get_event_loop())

        # Apply custom colors from config
        self._apply_colors()

        # Subscribe to events
        self._setup_event_handlers()

        # Update title with session ID and model
        self._update_title()

        # Fetch models in background
        self.run_worker(self._session.fetch_models())

        if not self._session.config.llm.api_key:
            from dendrophis.ui.screens.api_key_prompt import ApiKeyPromptScreen

            self.push_screen(ApiKeyPromptScreen(self._session), self._on_key_provided)
        else:
            self._push_main_screen()

        # Load session AFTER main screen is mounted so its event handlers
        # (e.g. ContextUpdatedEvent with full_chat_restored) are active.
        if self._session_path:
            self.call_later(self._load_session_deferred)

    def _load_session_deferred(self) -> None:
        loaded = self._session.load_session(self._session_path)
        if loaded:
            self._update_title()
            self.debug_log(f"[NOTIFY] SESSION LOADED: {loaded}")
            self.notify(f"Loaded session with {loaded['message_count']} messages", severity="information")
        else:
            self.debug_log(f"[NOTIFY ERROR] SESSION LOAD ERROR: Failed to load {self._session_path}")
            self.notify(f"Failed to load session: {self._session_path}", severity="error")

    def _setup_event_handlers(self) -> None:
        """Subscribe UI components to event bus events."""
        self._event_bus.subscribe(ModelSwitchedEvent, self._on_model_switched)
        self._event_bus.subscribe(AuthFailedEvent, self._on_auth_failed)

    def _on_model_switched(self, event: ModelSwitchedEvent) -> None:
        """Update title bar when model changes."""
        self._update_title()

    def _on_auth_failed(self, event: AuthFailedEvent) -> None:
        from dendrophis.ui.screens.api_key_prompt import ApiKeyPromptScreen

        def _on_key_updated(_key: str) -> None:
            self.run_worker(self._session.fetch_models())

        self.call_later(self.push_screen, ApiKeyPromptScreen(self._session), _on_key_updated)

    def _on_key_provided(self, _key: str) -> None:
        self.run_worker(self._session.fetch_models())
        self._push_main_screen()

    def _push_main_screen(self) -> None:
        """Push the main screen and connect event handlers."""
        self._main_screen = MainScreen(self._session, self._event_bus)
        self.push_screen(self._main_screen)
        # Get reference to debug widget for logging
        self._debug_widget = self._main_screen._debug_widget

    async def on_unmount(self) -> None:
        await self._session.aclose()
        self._event_bus.shutdown(wait=False)

    def action_toggle_debug_log(self) -> None:
        """Toggle the debug log window."""
        if self._debug_log_screen is None:
            self._debug_log_screen = DebugLogScreen()
            self.push_screen(self._debug_log_screen)
        else:
            self._debug_log_screen = None
            # Close the top screen if it's the debug log
            if isinstance(self.screen, DebugLogScreen):
                self.pop_screen()

    def _apply_colors(self) -> None:
        """Apply custom colors from configuration to the application's CSS variables."""
        try:
            colors = self._session.config.ui.colors
            variables = {
                "primary": colors.primary,
                "secondary": colors.secondary,
                "success": colors.success,
                "warning": colors.warning,
                "error": colors.danger,
                "surface": colors.surface,
                "background": colors.surface,
                "accent": colors.primary,
                "panel": colors.surface,
                "text": colors.text,
                "neutral": colors.neutral,
            }
            # Add derived variables for the stylesheet
            variables["panel-darken-2"] = f"{colors.surface} darken(10%)"
            variables["panel-lighten-1"] = f"{colors.surface} lighten(5%)"
            variables["text-muted"] = f"{colors.text} 60%"

            self.stylesheet.update_variables(variables)
        except Exception as error:
            # Fallback if colors are missing or invalid
            self.debug_log(f"Failed to apply custom colors: {error}")

    def debug_log(self, message: str) -> None:
        """Write a message to the debug log if it's open."""
        # Write to docked debug widget if available
        if self._debug_widget:
            self._debug_widget.write(message)
        # Also write to screen debug log if it exists
        if self._debug_log_screen:
            self._debug_log_screen.write(message)
