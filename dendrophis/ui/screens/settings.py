"""SettingsScreen — full-screen structured config editor with dynamic widgets and raw text fallback."""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Switch,
    TabbedContent,
    TabPane,
    TextArea,
)

from dendrophis.ui.widgets.panels import PanelRegistry

if TYPE_CHECKING:
    from dendrophis.session.session import Session


TOOL_PERM_OPTIONS = [("Allow", "allow"), ("Confirm", "confirm"), ("Deny", "deny")]
CAT_PERM_OPTIONS = [("Auto-approve", "auto"), ("Require confirm", "confirm"), ("Deny", "deny")]


class HookEntryRow(Horizontal):
    """Dynamic row for editing a single hook entry."""

    DEFAULT_CSS = """
    HookEntryRow {
        height: auto;
        margin-bottom: 1;
    }
    .hook-matcher { width: 15; margin-right: 1; }
    .hook-command { width: 1fr; margin-right: 1; }
    .hook-del { width: 5; min-width: 5; }
    """

    def __init__(self, matcher: str, command: str) -> None:
        super().__init__()
        self._initial_matcher = matcher
        self._initial_command = command

    def compose(self) -> ComposeResult:
        yield Input(value=self._initial_matcher, placeholder="Matcher (e.g. bash)", classes="hook-matcher")
        yield Input(value=self._initial_command, placeholder="Command", classes="hook-command")
        yield Button("X", variant="error", classes="hook-del")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("hook-del"):
            self.remove()

    def get_data(self) -> dict:
        inputs = self.query(Input)
        return {"matcher": inputs[0].value, "command": inputs[1].value}


class HookListEditor(Vertical):
    """Editor for a list of hooks."""

    DEFAULT_CSS = """
    HookListEditor {
        height: auto;
        margin-bottom: 1;
        padding: 1;
        border: solid $primary 30%;
    }
    """

    def __init__(self, title: str, initial_data: list[dict], id: str) -> None:
        super().__init__(id=id)
        self._title = title
        self._initial_data = initial_data

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="settings-section")
        with Vertical(id="hook-container"):
            for entry in self._initial_data:
                # Support both dict and Pydantic model formats dynamically
                matcher = entry.matcher if hasattr(entry, "matcher") else entry.get("matcher", "")
                command = entry.command if hasattr(entry, "command") else entry.get("command", "")
                yield HookEntryRow(matcher, command)
        yield Button("+ Add Hook", classes="add-hook", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("add-hook"):
            self.query_one("#hook-container").mount(HookEntryRow("", ""))

    def get_list(self) -> list[dict]:
        return [row.get_data() for row in self.query(HookEntryRow)]


class McpServerEntryRow(Vertical):
    """Dynamic container for editing a single MCP server configuration."""

    DEFAULT_CSS = """
    McpServerEntryRow {
        height: auto;
        margin-bottom: 2;
        padding: 1;
        border: solid $primary 30%;
        background: $surface-darken-1;
    }
    .mcp-header-row {
        height: auto;
        margin-bottom: 1;
        align: left middle;
    }
    .mcp-name-label {
        width: auto;
        margin-right: 1;
        align: left middle;
    }
    .mcp-name-input {
        width: 25;
        margin-right: 2;
        height: 3;
    }
    .mcp-enabled-switch {
        margin-right: 2;
    }
    .mcp-del-btn {
        width: 10;
    }
    .mcp-fields-row {
        height: auto;
        margin-bottom: 1;
    }
    .mcp-field-col {
        width: 1fr;
        padding-right: 2;
        height: auto;
    }
    .mcp-field-col:last-child {
        padding-right: 0;
    }
    .mcp-label {
        color: $text;
        text-style: bold;
        margin-bottom: 0;
    }
    .mcp-input {
        width: 100%;
        height: 3;
    }
    """

    def __init__(
        self,
        server_name: str,
        command: str,
        arguments: list[str],
        env_vars: dict[str, str] | None,
        enabled: bool,
        url: str | None = None,
    ) -> None:
        super().__init__()
        self._initial_name = server_name
        self._initial_command = command or ""
        self._initial_args = arguments
        self._initial_env = env_vars or {}
        self._initial_enabled = enabled
        self._initial_url = url or ""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="mcp-header-row"):
            yield Label("Server Name:", classes="mcp-name-label")
            yield Input(value=self._initial_name, placeholder="e.g. gkeep", classes="mcp-name-input")
            yield Switch(value=self._initial_enabled, classes="mcp-enabled-switch")
            yield Button("Delete", variant="error", classes="mcp-del-btn")

        with Horizontal(classes="mcp-fields-row"):
            with Vertical(classes="mcp-field-col"):
                yield Label("Command", classes="mcp-label")
                yield Input(value=self._initial_command, placeholder="e.g. npx", classes="mcp-input mcp-command")

            with Vertical(classes="mcp-field-col"):
                yield Label("URL (SSE)", classes="mcp-label")
                yield Input(value=self._initial_url, placeholder="e.g. http://...", classes="mcp-input mcp-url")

            with Vertical(classes="mcp-field-col"):
                yield Label("Arguments (comma-separated)", classes="mcp-label")
                arguments_string = ", ".join(self._initial_args)
                yield Input(
                    value=arguments_string,
                    placeholder="e.g. -y, @modelcontextprotocol/server-postgres",
                    classes="mcp-input mcp-args",
                )

            with Vertical(classes="mcp-field-col"):
                yield Label("Env Variables (KEY=VAL, comma-separated)", classes="mcp-label")
                env_string = ", ".join(f"{env_key}={env_value}" for env_key, env_value in self._initial_env.items())
                yield Input(
                    value=env_string,
                    placeholder="e.g. DB_URL=postgresql://localhost",
                    classes="mcp-input mcp-env",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("mcp-del-btn"):
            self.remove()

    def get_data(self) -> dict:
        switch = self.query_one(Switch)

        server_name = self.query_one(".mcp-name-input", Input).value.strip()
        command = self.query_one(".mcp-command", Input).value.strip()
        url = self.query_one(".mcp-url", Input).value.strip()
        args_str = self.query_one(".mcp-args", Input).value
        env_str = self.query_one(".mcp-env", Input).value
        enabled = switch.value

        arguments_list = []
        if args_str.strip():
            arguments_list = [argument.strip() for argument in args_str.split(",") if argument.strip()]

        env_dict = {}
        if env_str.strip():
            for part in env_str.split(","):
                part = part.strip()
                if not part:
                    continue
                if "=" in part:
                    env_key, env_value = part.split("=", 1)
                    env_dict[env_key.strip()] = env_value.strip()

        config = {
            "enabled": enabled,
        }
        if command:
            config["command"] = command
        if arguments_list:
            config["args"] = arguments_list
        if env_dict:
            config["env"] = env_dict
        if url:
            config["url"] = url

        return {
            "name": server_name,
            "config": config,
        }


class McpServerListEditor(Vertical):
    """Editor for a list of MCP servers."""

    DEFAULT_CSS = """
    McpServerListEditor {
        height: auto;
        margin-bottom: 1;
        padding: 1;
    }
    .add-mcp-btn {
        margin-top: 1;
        width: 25;
    }
    """

    def __init__(self, title: str, initial_servers: dict[str, Any], id: str) -> None:
        super().__init__(id=id)
        self._title = title
        self._initial_servers = initial_servers

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="settings-section")
        with Vertical(id="mcp-container"):
            for server_name, server_config in self._initial_servers.items():
                command = (
                    server_config.command if hasattr(server_config, "command") else server_config.get("command", "")
                )
                args = server_config.args if hasattr(server_config, "args") else server_config.get("args", [])
                env = server_config.env if hasattr(server_config, "env") else server_config.get("env", {})
                enabled = (
                    server_config.enabled if hasattr(server_config, "enabled") else server_config.get("enabled", True)
                )
                url = server_config.url if hasattr(server_config, "url") else server_config.get("url", "")
                yield McpServerEntryRow(server_name, command, args, env, enabled, url)
        yield Button("+ Add MCP Server", classes="add-mcp-btn", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("add-mcp-btn"):
            self.query_one("#mcp-container").mount(McpServerEntryRow("", "", [], {}, True, ""))

    def get_servers_dict(self) -> dict[str, dict]:
        servers_dictionary = {}
        for row in self.query(McpServerEntryRow):
            data = row.get_data()
            name = data["name"]
            if name:
                servers_dictionary[name] = data["config"]
        return servers_dictionary


class Slider(Widget, can_focus=True):
    """A custom slider widget for numerical values, snapping to 32k detents."""

    DEFAULT_CSS = """
    Slider {
        width: 100%;
        height: 3;
        background: $surface;
        border: tall transparent;
        padding: 0 1;
        align: left middle;
    }
    Slider:focus {
        border: tall $accent;
    }
    """

    value = reactive(0)

    class Changed(Message):
        """Emitted when the slider value changes."""

        def __init__(self, value: int) -> None:
            super().__init__()
            self.value = value

    def __init__(
        self,
        value: int,
        min_value: int,
        max_value: int,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.min_value = min_value
        self.max_value = max_value

        # Generate detents every 32k (32768) starting from min_value
        detents_list = [self.min_value]
        step_size = 32768
        current_step = step_size
        while current_step <= self.max_value:
            if current_step > self.min_value:
                detents_list.append(current_step)
            current_step += step_size

        if self.max_value not in detents_list:
            detents_list.append(self.max_value)

        self.detents = sorted(set(detents_list))

        # Ensure starting value is snapped to the nearest detent
        self.value = min(self.detents, key=lambda detent: abs(detent - value))

    def render(self) -> str:
        # Calculate visual progress based on detent index
        total_detents = len(self.detents)
        current_index = self.detents.index(self.value)

        track_width = self.size.width - 20  # Reserve space for the value label
        if track_width <= 0:
            track_width = 20

        progress_ratio = current_index / (total_detents - 1) if total_detents > 1 else 0.0

        pointer_position = int(progress_ratio * track_width)
        pointer_position = max(0, min(pointer_position, track_width))

        completed_track = "█" * pointer_position
        slider_pointer = "●"
        remaining_track = "░" * max(0, track_width - pointer_position - 1)

        # Format label (e.g. 32k, 64k)
        display_label = f"{self.value // 1024}k" if self.value >= 1024 else str(self.value)

        return f"[{completed_track}{slider_pointer}{remaining_track}] {display_label}"

    def key_left(self) -> None:
        current_index = self.detents.index(self.value)
        self.value = self.detents[max(0, current_index - 1)]
        self.post_message(self.Changed(self.value))

    def key_right(self) -> None:
        current_index = self.detents.index(self.value)
        self.value = self.detents[min(len(self.detents) - 1, current_index + 1)]
        self.post_message(self.Changed(self.value))

    def on_click(self, click_event: events.Click) -> None:
        self.focus()
        track_width = self.size.width - 20
        if track_width <= 0:
            return

        # Estimate the detent target from click position
        relative_click_x = click_event.x - 2  # Account for border/padding
        progress_ratio = relative_click_x / track_width
        progress_ratio = max(0.0, min(progress_ratio, 1.0))

        target_value = self.min_value + progress_ratio * (self.max_value - self.min_value)
        self.value = min(self.detents, key=lambda detent: abs(detent - target_value))
        self.post_message(self.Changed(self.value))


class SettingsScreen(Screen):
    """Full-screen config editor."""

    from typing import ClassVar

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+s", "save", "Save"),
        ("escape", "dismiss_modal", "Cancel"),
    ]

    DEFAULT_CSS = """
    SettingsScreen {
        width: 100%;
        height: 100%;
        background: $surface;
    }
    #settings-container {
        width: 100%;
        height: 1fr;
        padding: 1 2;
        align-horizontal: center;
    }
    TabbedContent {
        max-width: 120;
        width: 100%;
        height: 1fr;
    }

    .settings-group {
        border: solid $primary 20%;
        background: $surface-darken-1;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
    }
    .settings-group-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
        width: 100%;
    }
    .column-layout {
        height: auto;
        width: 100%;
    }
    .column-layout > Vertical {
        width: 1fr;
        height: auto;
        padding-right: 4;
    }
    .column-layout > Vertical:last-child {
        padding-right: 0;
    }
    .settings-row {
        layout: vertical;
        height: auto;
        margin-bottom: 1;
        width: 100%;
    }
    .settings-label {
        color: $text;
        text-style: bold;
        padding: 0;
        margin-bottom: 0;
        width: 100%;
    }
    .settings-input {
        width: 100%;
        height: 3;
    }
    .switch-row {
        height: auto;
        margin-bottom: 1;
        width: 100%;
        align: left middle;
    }
    .switch-label {
        width: 1fr;
        color: $text;
        text-style: bold;
    }
    Select {
        width: 100%;
        height: 3;
    }
    .panels-grid {
        layout: grid;
        grid-size: 3;
        grid-gutter: 1 2;
        height: auto;
        margin: 1 0;
    }
    .perm-row {
        height: auto;
        margin-bottom: 1;
        align: left middle;
    }
    .perm-label {
        width: 1fr;
        color: $text;
        text-style: bold;
    }
    .perm-select {
        width: 18;
    }
    .settings-section {
        margin-top: 1;
        margin-bottom: 1;
        border-top: solid $primary 30%;
        padding-top: 1;
        width: 100%;
        text-style: bold;
    }
    #settings-footer {
        dock: bottom;
        height: auto;
        width: 100%;
        align-horizontal: center;
        background: $surface;
    }
    #error-label {
        max-width: 120;
        width: 100%;
        color: $error;
        text-style: bold;
        text-align: center;
    }
    #actions {
        max-width: 120;
        margin-top: 1;
        width: 100%;
        height: auto;
        align-horizontal: center;
    }
    #actions Button {
        margin: 0 1;
    }
    #system_prompt {
        height: 10;
    }
    VerticalScroll {
        padding-bottom: 2;
    }
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._raw = session.config_loader._raw
        self._cfg = session.config
        self._last_valid_yaml = session.config_loader.raw_yaml
        self._last_tab = "tab-llm"

    def compose(self) -> ComposeResult:
        # Determine maximum context window from model list or fallback to 128000
        active_model = self._cfg.llm.model
        max_context = max(self._cfg.llm.context_limit, 128000)
        if hasattr(self._session, "models") and self._session.models:
            for model_info in self._session.models:
                if model_info.id == active_model:
                    if model_info.context_window > 0:
                        max_context = model_info.context_window
                    break

        yield Header()
        with Vertical(id="settings-container"):
            yield Label(f"Config: {self._session.config_loader.path}", classes="settings-section")

            with TabbedContent():
                with TabPane("LLM", id="tab-llm"), VerticalScroll():
                    with Vertical(classes="settings-group"):
                        yield Label("API Connection & Model", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                yield self._make_input("llm.base_url", "Base URL", self._cfg.llm.base_url)
                                yield self._make_input("llm.model", "Model", self._cfg.llm.model)
                            with Vertical():
                                yield self._make_input("llm.api_key", "API Key", self._cfg.llm.api_key, password=True)
                                yield self._make_input(
                                    "llm.code_writer_model",
                                    "Code Writer Model Override",
                                    self._cfg.llm.code_writer_model or "",
                                )

                    with Vertical(classes="settings-group"):
                        yield Label("Reasoning & Tooling", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                reasoning_opts = [
                                    ("Default", "None"),
                                    ("low", "low"),
                                    ("medium", "medium"),
                                    ("high", "high"),
                                    ("xhigh", "xhigh"),
                                    ("none (off)", "none"),
                                ]
                                yield Horizontal(
                                    Label("Reasoning Effort", classes="settings-label"),
                                    Select(
                                        reasoning_opts,
                                        value=str(self._cfg.llm.reasoning_effort),
                                        id="llm_reasoning_effort",
                                    ),
                                    classes="settings-row",
                                )

                                thinking_start_opts = [
                                    ("Auto-detect", "None"),
                                    ("Text", "text"),
                                    ("Thinking", "thinking"),
                                ]
                                yield Horizontal(
                                    Label("Thinking Start Mode", classes="settings-label"),
                                    Select(
                                        thinking_start_opts,
                                        value=str(self._cfg.llm.thinking_start_mode),
                                        id="llm_thinking_start_mode",
                                    ),
                                    classes="settings-row",
                                )
                            with Vertical():
                                tool_mode_options = [
                                    ("Auto-detect", "auto"),
                                    ("Native", "native"),
                                    ("XML injection", "xml"),
                                ]
                                yield Horizontal(
                                    Label("Tool Format Mode", classes="settings-label"),
                                    Select(tool_mode_options, value=self._cfg.llm.tool_mode, id="llm_tool_mode"),
                                    classes="settings-row",
                                )

                                use_responses_options = [
                                    ("Default (None)", "None"),
                                    ("True", "True"),
                                    ("False", "False"),
                                ]
                                yield Horizontal(
                                    Label("Use Responses API", classes="settings-label"),
                                    Select(
                                        use_responses_options,
                                        value=str(self._cfg.llm.use_responses_api),
                                        id="llm_use_responses_api",
                                    ),
                                    classes="settings-row",
                                )

                    with Vertical(classes="settings-group"):
                        yield Label("Generation Parameters", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                yield self._make_input("llm.max_tokens", "Max Tokens", str(self._cfg.llm.max_tokens))
                                yield self._make_input("llm.temperature", "Temperature", str(self._cfg.llm.temperature))
                                yield self._make_input("llm.top_k", "Top K (optional)", str(self._cfg.llm.top_k or ""))
                                yield self._make_input("llm.min_p", "Min P (optional)", str(self._cfg.llm.min_p or ""))
                            with Vertical():
                                yield self._make_context_slider(
                                    "llm.context_limit", "Context Limit", self._cfg.llm.context_limit, max_context
                                )
                                yield self._make_input("llm.timeout", "Timeout", str(self._cfg.llm.timeout))
                                yield self._make_input(
                                    "llm.repetition_penalty",
                                    "Repetition Penalty (optional)",
                                    str(self._cfg.llm.repetition_penalty or ""),
                                )
                                yield self._make_input(
                                    "llm.compaction_threshold",
                                    "Compaction Threshold",
                                    str(self._cfg.llm.compaction_threshold),
                                )

                    with Vertical(classes="settings-group"):
                        yield Label("Penalties & Sequences", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                yield self._make_input(
                                    "llm.presence_penalty", "Presence Penalty", str(self._cfg.llm.presence_penalty)
                                )
                                yield self._make_input(
                                    "llm.frequency_penalty", "Frequency Penalty", str(self._cfg.llm.frequency_penalty)
                                )
                            with Vertical():
                                yield self._make_input(
                                    "llm.stop", "Stop Sequences (comma-separated)", ", ".join(self._cfg.llm.stop or [])
                                )
                                yield self._make_input(
                                    "llm.prompt_cache_key",
                                    "Prompt Cache Key Override",
                                    self._cfg.llm.prompt_cache_key or "",
                                )

                with TabPane("UI & Panels", id="tab-ui"), VerticalScroll():
                    with Vertical(classes="settings-group"):
                        yield Label("General UI Layout", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                yield self._make_input("ui.theme", "Theme", self._cfg.ui.theme)
                                yield self._make_input(
                                    "ui.scrollback_limit", "Scrollback Limit", str(self._cfg.ui.scrollback_limit)
                                )
                            with Vertical():
                                sidebar_position_options = [("Left", "left"), ("Right", "right")]
                                yield Horizontal(
                                    Label("Sidebar Position", classes="settings-label"),
                                    Select(
                                        sidebar_position_options,
                                        value=self._cfg.ui.sidebar.position,
                                        id="ui_sidebar_position",
                                    ),
                                    classes="settings-row",
                                )
                                yield self._make_input(
                                    "ui.sidebar.width", "Sidebar Width", str(self._cfg.ui.sidebar.width)
                                )

                    with Vertical(classes="settings-group"):
                        yield Label("Sidebar Panels (Check to enable)", classes="settings-group-title")
                        active_panels = self._cfg.ui.sidebar.panels
                        with Horizontal(classes="panels-grid"):
                            for panel_id in PanelRegistry.ids():
                                yield Checkbox(
                                    panel_id.capitalize(), value=(panel_id in active_panels), id=f"panel_{panel_id}"
                                )

                    with Vertical(classes="settings-group"):
                        yield Label("UI Custom Colors", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                yield self._make_input(
                                    "ui.colors.primary", "Primary Color", self._cfg.ui.colors.primary
                                )
                                yield self._make_input(
                                    "ui.colors.secondary", "Secondary Color", self._cfg.ui.colors.secondary
                                )
                                yield self._make_input(
                                    "ui.colors.success", "Success Color", self._cfg.ui.colors.success
                                )
                                yield self._make_input(
                                    "ui.colors.warning", "Warning Color", self._cfg.ui.colors.warning
                                )
                            with Vertical():
                                yield self._make_input("ui.colors.danger", "Danger Color", self._cfg.ui.colors.danger)
                                yield self._make_input(
                                    "ui.colors.surface", "Surface Color", self._cfg.ui.colors.surface
                                )
                                yield self._make_input("ui.colors.text", "Text Color", self._cfg.ui.colors.text)
                                yield self._make_input(
                                    "ui.colors.neutral", "Neutral Color", self._cfg.ui.colors.neutral
                                )

                with TabPane("Caching", id="tab-caching"), VerticalScroll():
                    with Vertical(classes="settings-group"):
                        yield Label("Cache Control Toggles", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                yield self._make_switch("caching.enabled", "Enabled", self._cfg.caching.enabled)
                                yield self._make_switch(
                                    "caching.tier1_system_prompt",
                                    "Cache System Prompt",
                                    self._cfg.caching.tier1_system_prompt,
                                )
                                yield self._make_switch(
                                    "caching.tier1_tool_definitions",
                                    "Cache Tools",
                                    self._cfg.caching.tier1_tool_definitions,
                                )
                                yield self._make_switch(
                                    "caching.tier2_file_blocks", "Cache Files", self._cfg.caching.tier2_file_blocks
                                )
                            with Vertical():
                                yield self._make_switch(
                                    "caching.tier2_project_understanding",
                                    "Cache Project Context",
                                    self._cfg.caching.tier2_project_understanding,
                                )
                                yield self._make_switch(
                                    "caching.tier3_on_compaction",
                                    "Cache on Compaction Checkpoint",
                                    self._cfg.caching.tier3_on_compaction,
                                )
                                yield self._make_switch(
                                    "caching.pr_enabled",
                                    "Primer Feature Enabled",
                                    self._cfg.caching.pr_enabled,
                                )

                    with Vertical(classes="settings-group"):
                        yield Label("Stable Thresholds", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                yield self._make_input(
                                    "caching.tier2_file_blocks_stable_turns",
                                    "File Caching Stable Turns",
                                    str(self._cfg.caching.tier2_file_blocks_stable_turns),
                                )
                            with Vertical():
                                yield self._make_input(
                                    "caching.tier2_project_understanding_min_turns",
                                    "Project Context Min Turns",
                                    str(self._cfg.caching.tier2_project_understanding_min_turns),
                                )

                with TabPane("General & Tools", id="tab-general-tools"), VerticalScroll():
                    with Vertical(classes="settings-group"):
                        yield Label("Root Config Paths", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                yield self._make_input("memory_db", "Memory DB Path", self._cfg.memory_db)
                            with Vertical():
                                yield self._make_input("debug_log", "Debug Log Path", self._cfg.debug_log)

                    with Vertical(classes="settings-group"):
                        yield Label("Tools Execution Limits", classes="settings-group-title")
                        with Horizontal(classes="column-layout"):
                            with Vertical():
                                yield self._make_input(
                                    "tools.extra_paths",
                                    "Extra PATH Directories (comma-separated)",
                                    ", ".join(self._cfg.tools.extra_paths),
                                )
                            with Vertical():
                                yield self._make_input(
                                    "tools.max_calls", "Max Consecutive Calls", str(self._cfg.tools.max_calls)
                                )
                                yield self._make_switch(
                                    "tools.parallel_tools", "Allow Parallel Execution", self._cfg.tools.parallel_tools
                                )

                    with Vertical(classes="settings-group"):
                        yield Label("System Prompt (Instructions to Agent)", classes="settings-group-title")
                        yield TextArea(self._cfg.system_prompt, id="system_prompt")

                with TabPane("MCP Servers", id="tab-mcp"), VerticalScroll():
                    yield McpServerListEditor("MCP Servers Config", self._cfg.mcp_servers, id="editor-mcp-servers")

                with TabPane("Permissions", id="tab-permissions"), VerticalScroll():
                    yield from self._make_permissions_tab()

                with TabPane("Hooks", id="tab-hooks"), VerticalScroll(), Horizontal(classes="column-layout"):
                    yield HookListEditor("Pre-Tool Use Hooks", self._cfg.hooks.pre_tool_use, id="editor-pre-hooks")
                    yield HookListEditor("Post-Tool Use Hooks", self._cfg.hooks.post_tool_use, id="editor-post-hooks")

                with TabPane("Advanced (YAML)", id="tab-yaml"):
                    with Horizontal(classes="settings-row"):
                        yield Button("Revert YAML", id="revert-yaml-btn", variant="warning")
                        yield Label(
                            " Edit raw YAML directly. Syntax must be valid to leave this tab.", classes="settings-label"
                        )
                    yield TextArea(
                        self._last_valid_yaml,
                        language="yaml",
                        id="config-editor",
                    )

            with Vertical(id="settings-footer"):
                yield Label("", id="error-label")
                with Horizontal(id="actions"):
                    yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                    yield Button("Cancel (Esc)", id="cancel-btn")

        yield Footer()

    def _make_permissions_tab(self):
        from dendrophis.tools.bash_sandbox import CommandCategory

        perms = self._cfg.permissions
        denied = set(perms.denied_tools)
        confirm = set(perms.require_confirmation)

        registry = self._session._tool_registry
        tool_names = []
        if registry:
            for tool_name in registry.names():
                tool_instance = registry.get(tool_name)
                if tool_instance and tool_instance.permission_controlled:
                    tool_names.append(tool_name)

        bash = perms.bash
        auto_cats = set(bash.auto_approve_categories)
        denied_cats = set(bash.denied_categories)

        with Horizontal(classes="column-layout"):
            with Vertical():
                yield Label("Tool Permissions", classes="settings-group-title")
                for name in tool_names:
                    if name in denied:
                        val = "deny"
                    elif name in confirm:
                        val = "confirm"
                    else:
                        val = "allow"
                    yield Horizontal(
                        Label(name, classes="perm-label"),
                        Select(TOOL_PERM_OPTIONS, value=val, id=f"perm_tool_{name}", classes="perm-select"),
                        classes="perm-row",
                    )

            with Vertical():
                yield Label("Bash Category Permissions", classes="settings-group-title")
                for category in CommandCategory:
                    if category.value in denied_cats:
                        val = "deny"
                    elif category.value in auto_cats:
                        val = "auto"
                    else:
                        val = "confirm"
                    label = category.value.replace("_", " ").title()
                    yield Horizontal(
                        Label(label, classes="perm-label"),
                        Select(CAT_PERM_OPTIONS, value=val, id=f"perm_cat_{category.value}", classes="perm-select"),
                        classes="perm-row",
                    )

        yield Label("Global Permission Limits", classes="settings-section")
        with Horizontal(classes="column-layout"):
            with Vertical():
                yield self._make_input(
                    "permissions.allowed_tools", "Allowed Tools", ", ".join(perms.allowed_tools or [])
                )
            with Vertical():
                yield self._make_input(
                    "permissions.bash.allowed_categories",
                    "Allowed Bash Categories",
                    ", ".join(perms.bash.allowed_categories or []),
                )

    def _make_input(self, id: str, label: str, value: str, password: bool = False) -> Horizontal:
        return Horizontal(
            Label(label, classes="settings-label"),
            Input(value=value, id=id.replace(".", "_"), password=password, classes="settings-input"),
            classes="settings-row",
        )

    def _make_context_slider(self, id: str, label: str, value: int, max_context: int) -> Horizontal:
        slider = Slider(
            value=value,
            min_value=4096,
            max_value=max_context,
            id=id.replace(".", "_"),
        )
        return Horizontal(
            Label(label, classes="settings-label"),
            slider,
            classes="settings-row",
        )

    def _make_switch(self, id: str, label: str, value: bool) -> Horizontal:
        return Horizontal(
            Label(label, classes="switch-label"), Switch(value=value, id=id.replace(".", "_")), classes="switch-row"
        )

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if self._last_tab == "tab-yaml" and event.pane.id != "tab-yaml":
            # Validate YAML before leaving tab
            editor = self.query_one("#config-editor", TextArea)
            yaml = YAML()
            try:
                parsed = yaml.load(editor.text)
                if not isinstance(parsed, dict):
                    raise ValueError("Root element must be a dictionary")
                self._last_valid_yaml = editor.text
                self.query_one("#error-label", Label).update("")
            except Exception as e:
                # Invalid YAML, block switch
                self.query_one("#error-label", Label).update(f"[red]YAML Syntax Error: {e}[/red]")
                self.query_one(TabbedContent).active = "tab-yaml"
                return

        self._last_tab = event.pane.id

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.action_save()
        elif event.button.id == "cancel-btn":
            self.dismiss()
        elif event.button.id == "revert-yaml-btn":
            editor = self.query_one("#config-editor", TextArea)
            editor.text = self._last_valid_yaml
            self.query_one("#error-label", Label).update("[green]Reverted to last valid YAML.[/green]")

    def _get_val(self, id_str: str, default: str = "") -> str:
        try:
            val = self.query_one(f"#{id_str}").value
            return str(val) if val is not None else default
        except Exception:
            return default

    def _get_bool(self, id_str: str) -> bool:
        try:
            return bool(self.query_one(f"#{id_str}").value)
        except Exception:
            return False

    def action_save(self) -> None:
        error_label = self.query_one("#error-label", Label)

        active_tab = self.query_one(TabbedContent).active
        if active_tab == "tab-yaml":
            try:
                editor = self.query_one("#config-editor", TextArea)
                self._session.config_loader.save(editor.text)
                self._session.reload_config()
                self.dismiss()
                return
            except Exception as save_exception:
                from rich.markup import escape

                error_label.update(f"[red]YAML Error: {escape(str(save_exception))}[/red]")
                return

        try:
            raw = self._session.config_loader._raw

            def set_nested(data_dict: dict, keys: list[str], val: Any) -> None:
                current_dict = data_dict
                for key in keys[:-1]:
                    if key not in current_dict:
                        current_dict[key] = {}
                    current_dict = current_dict[key]
                current_dict[keys[-1]] = val

            def get_nullable_str(id_str: str) -> str | None:
                val_str = self._get_val(id_str).strip()
                return val_str if val_str else None

            def get_nullable_int(id_str: str, default_val: int | None = None) -> int | None:
                val_str = self._get_val(id_str).strip()
                try:
                    return int(val_str) if val_str else default_val
                except Exception:
                    return default_val

            def get_nullable_float(id_str: str, default_val: float | None = None) -> float | None:
                val_str = self._get_val(id_str).strip()
                try:
                    return float(val_str) if val_str else default_val
                except Exception:
                    return default_val

            set_nested(raw, ["llm", "base_url"], self._get_val("llm_base_url"))
            set_nested(raw, ["llm", "api_key"], self._get_val("llm_api_key"))
            set_nested(raw, ["llm", "model"], self._get_val("llm_model"))
            set_nested(raw, ["llm", "max_tokens"], int(self._get_val("llm_max_tokens", "4096")))
            set_nested(raw, ["llm", "temperature"], float(self._get_val("llm_temperature", "0.2")))
            set_nested(raw, ["llm", "context_limit"], int(self._get_val("llm_context_limit", "128000")))
            set_nested(raw, ["llm", "timeout"], float(self._get_val("llm_timeout", "120.0")))

            reasoning = self._get_val("llm_reasoning_effort")
            set_nested(raw, ["llm", "reasoning_effort"], None if reasoning == "None" else reasoning)

            thinking_start_mode = self._get_val("llm_thinking_start_mode")
            set_nested(
                raw,
                ["llm", "thinking_start_mode"],
                None if thinking_start_mode == "None" else thinking_start_mode,
            )

            # Save the new LLM settings
            set_nested(raw, ["llm", "code_writer_model"], get_nullable_str("llm_code_writer_model"))
            set_nested(raw, ["llm", "top_k"], get_nullable_int("llm_top_k"))
            set_nested(raw, ["llm", "min_p"], get_nullable_float("llm_min_p"))
            set_nested(raw, ["llm", "repetition_penalty"], get_nullable_float("llm_repetition_penalty"))
            set_nested(raw, ["llm", "presence_penalty"], float(self._get_val("llm_presence_penalty", "0.0")))
            set_nested(raw, ["llm", "frequency_penalty"], float(self._get_val("llm_frequency_penalty", "0.0")))
            set_nested(raw, ["llm", "compaction_threshold"], float(self._get_val("llm_compaction_threshold", "0.85")))

            stop_val = self._get_val("llm_stop").strip()
            stop_list = [item.strip() for item in stop_val.split(",") if item.strip()] if stop_val else None
            set_nested(raw, ["llm", "stop"], stop_list)

            set_nested(raw, ["llm", "prompt_cache_key"], get_nullable_str("llm_prompt_cache_key"))
            set_nested(raw, ["llm", "tool_mode"], self._get_val("llm_tool_mode", "auto"))

            responses_val = self._get_val("llm_use_responses_api")
            if responses_val == "True":
                set_nested(raw, ["llm", "use_responses_api"], True)
            elif responses_val == "False":
                set_nested(raw, ["llm", "use_responses_api"], False)
            else:
                set_nested(raw, ["llm", "use_responses_api"], None)

            # Save UI Config Settings
            set_nested(raw, ["ui", "theme"], self._get_val("ui_theme"))
            set_nested(raw, ["ui", "scrollback_limit"], int(self._get_val("ui_scrollback_limit", "100")))
            set_nested(raw, ["ui", "sidebar", "position"], self._get_val("ui_sidebar_position", "right"))
            set_nested(raw, ["ui", "sidebar", "width"], int(self._get_val("ui_sidebar_width", "28")))

            # Save Colors
            set_nested(raw, ["ui", "colors", "primary"], self._get_val("ui_colors_primary"))
            set_nested(raw, ["ui", "colors", "secondary"], self._get_val("ui_colors_secondary"))
            set_nested(raw, ["ui", "colors", "success"], self._get_val("ui_colors_success"))
            set_nested(raw, ["ui", "colors", "warning"], self._get_val("ui_colors_warning"))
            set_nested(raw, ["ui", "colors", "danger"], self._get_val("ui_colors_danger"))
            set_nested(raw, ["ui", "colors", "surface"], self._get_val("ui_colors_surface"))
            set_nested(raw, ["ui", "colors", "text"], self._get_val("ui_colors_text"))
            set_nested(raw, ["ui", "colors", "neutral"], self._get_val("ui_colors_neutral"))

            # Save Caching Settings
            set_nested(raw, ["caching", "enabled"], self._get_bool("caching_enabled"))
            set_nested(raw, ["caching", "tier1_system_prompt"], self._get_bool("caching_tier1_system_prompt"))
            set_nested(raw, ["caching", "tier1_tool_definitions"], self._get_bool("caching_tier1_tool_definitions"))
            set_nested(raw, ["caching", "tier2_file_blocks"], self._get_bool("caching_tier2_file_blocks"))
            set_nested(
                raw,
                ["caching", "tier2_file_blocks_stable_turns"],
                int(self._get_val("caching_tier2_file_blocks_stable_turns", "3")),
            )
            set_nested(
                raw, ["caching", "tier2_project_understanding"], self._get_bool("caching_tier2_project_understanding")
            )
            set_nested(
                raw,
                ["caching", "tier2_project_understanding_min_turns"],
                int(self._get_val("caching_tier2_project_understanding_min_turns", "5")),
            )
            set_nested(raw, ["caching", "tier3_on_compaction"], self._get_bool("caching_tier3_on_compaction"))
            set_nested(raw, ["caching", "pr_enabled"], self._get_bool("caching_pr_enabled"))

            # Save General & Tools Settings
            set_nested(raw, ["memory_db"], self._get_val("memory_db"))
            set_nested(raw, ["debug_log"], self._get_val("debug_log"))

            paths_val = self._get_val("tools_extra_paths").strip()
            paths_list = [item.strip() for item in paths_val.split(",") if item.strip()]
            set_nested(raw, ["tools", "extra_paths"], paths_list)
            set_nested(raw, ["tools", "max_calls"], int(self._get_val("tools_max_calls", "3")))
            set_nested(raw, ["tools", "parallel_tools"], self._get_bool("tools_parallel_tools"))

            set_nested(raw, ["system_prompt"], self.query_one("#system_prompt", TextArea).text)

            mcp_servers = self.query_one("#editor-mcp-servers", McpServerListEditor).get_servers_dict()
            raw["mcp_servers"] = mcp_servers

            # Save Permissions additional settings
            self._save_permissions(raw)
            allowed_tools_val = self._get_val("permissions_allowed_tools").strip()
            allowed_tools_list = [item.strip() for item in allowed_tools_val.split(",") if item.strip()]
            set_nested(raw, ["permissions", "allowed_tools"], allowed_tools_list)

            allowed_cats_val = self._get_val("permissions_bash_allowed_categories").strip()
            allowed_cats_list = [item.strip() for item in allowed_cats_val.split(",") if item.strip()]
            set_nested(raw, ["permissions", "bash", "allowed_categories"], allowed_cats_list)

            pre_hooks = self.query_one("#editor-pre-hooks", HookListEditor).get_list()
            post_hooks = self.query_one("#editor-post-hooks", HookListEditor).get_list()
            if "hooks" not in raw:
                raw["hooks"] = {}
            raw["hooks"]["pre_tool_use"] = pre_hooks
            raw["hooks"]["post_tool_use"] = post_hooks

            yaml = YAML()
            yaml.preserve_quotes = True
            buf = io.StringIO()
            yaml.dump(raw, buf)
            base_yaml = buf.getvalue()

            base_yaml = self._rewrite_panels_yaml(base_yaml)

            self._session.config_loader.save(base_yaml)
            self._session.reload_config()
            error_label.update("")
            self.dismiss()
        except (ValueError, ValidationError, Exception) as save_exception:
            from rich.markup import escape

            error_label.update(f"[red]Error: {escape(str(save_exception))}[/red]")

    def _save_permissions(self, raw: dict) -> None:
        from dendrophis.tools.bash_sandbox import CommandCategory

        registry = self._session._tool_registry
        tool_names = []
        if registry:
            for tool_name in registry.names():
                tool_instance = registry.get(tool_name)
                if tool_instance and tool_instance.permission_controlled:
                    tool_names.append(tool_name)

        denied_tools: list[str] = []
        require_confirmation: list[str] = []
        for name in tool_names:
            try:
                val = self.query_one(f"#perm_tool_{name}", Select).value
            except Exception:
                continue
            if val == "deny":
                denied_tools.append(str(name))
            elif val == "confirm":
                require_confirmation.append(str(name))

        denied_cats: list[str] = []
        auto_cats: list[str] = []
        for category in CommandCategory:
            try:
                val = self.query_one(f"#perm_cat_{category.value}", Select).value
            except Exception:
                continue
            if val == "deny":
                denied_cats.append(category.value)
            elif val == "auto":
                auto_cats.append(category.value)

        if "permissions" not in raw:
            raw["permissions"] = {}
        permissions_dict = raw["permissions"]
        permissions_dict["denied_tools"] = denied_tools
        permissions_dict["require_confirmation"] = require_confirmation
        if "bash" not in permissions_dict:
            permissions_dict["bash"] = {}
        permissions_dict["bash"]["denied_categories"] = denied_cats
        permissions_dict["bash"]["auto_approve_categories"] = auto_cats

    def _rewrite_panels_yaml(self, text_content: str) -> str:
        lines = text_content.splitlines()
        out_lines = []
        in_panels = False
        panels_indent = ""

        active_set = set()
        for panel_id in PanelRegistry.ids():
            if self.query_one(f"#panel_{panel_id}", Checkbox).value:
                active_set.add(panel_id)

        for line in lines:
            if re.match(r"^(\s*)panels:\s*$", line):
                in_panels = True
                panels_indent = re.match(r"^(\s*)", line).group(1)
                out_lines.append(line)

                for panel_id in PanelRegistry.ids():
                    if panel_id in active_set:
                        out_lines.append(f"{panels_indent}- {panel_id}")
                    else:
                        out_lines.append(f"{panels_indent}# - {panel_id}")
                continue

            if in_panels:
                if re.match(r"^\s*(#\s*)?-\s*\w+\s*$", line):
                    continue
                in_panels = False

            out_lines.append(line)

        return "\n".join(out_lines)

    def action_dismiss_modal(self) -> None:
        self.dismiss()
