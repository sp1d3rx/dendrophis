"""SettingsScreen — full-screen structured config editor with dynamic widgets and raw text fallback."""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

from pydantic import ValidationError
from ruamel.yaml import YAML
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
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
    }
    .settings-row {
        height: auto;
        margin-bottom: 1;
    }
    .settings-label {
        width: 25;
        padding-top: 1;
    }
    .settings-input {
        width: 1fr;
    }
    .settings-section {
        margin-top: 1;
        margin-bottom: 1;
        border-top: solid $primary 30%;
        padding-top: 1;
    }
    .panels-grid {
        layout: grid;
        grid-size: 3;
        grid-gutter: 1 2;
        height: auto;
        margin: 1 0;
    }
    .panels-grid Checkbox {
        width: auto;
    }
    .perm-row {
        height: auto;
        margin-bottom: 1;
    }
    .perm-label {
        width: 20;
        padding-top: 1;
    }
    .perm-select {
        width: 22;
    }
    .perm-section-label {
        margin-top: 1;
        margin-bottom: 1;
        color: $accent;
    }
    #error-label {
        color: $error;
        margin-top: 1;
    }
    #actions {
        height: auto;
        dock: bottom;
        margin-top: 1;
    }
    VerticalScroll {
        padding-bottom: 4;
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
        yield Header()
        with Vertical(id="settings-container"):
            yield Label(f"Config: {self._session.config_loader.path}", classes="settings-section")

            with TabbedContent():
                with TabPane("LLM", id="tab-llm"), VerticalScroll():
                    yield self._make_input("llm.base_url", "Base URL", self._cfg.llm.base_url)
                    yield self._make_input("llm.api_key", "API Key", self._cfg.llm.api_key, password=True)
                    yield self._make_input("llm.model", "Model", self._cfg.llm.model)
                    yield self._make_input("llm.max_tokens", "Max Tokens", str(self._cfg.llm.max_tokens))
                    yield self._make_input("llm.temperature", "Temperature", str(self._cfg.llm.temperature))
                    yield self._make_input("llm.context_limit", "Context Limit", str(self._cfg.llm.context_limit))
                    yield self._make_input("llm.timeout", "Timeout", str(self._cfg.llm.timeout))

                    reasoning_opts = [(str(x), str(x)) for x in [None, "low", "medium", "high", "xhigh", "none"]]
                    yield Horizontal(
                        Label("Reasoning Effort", classes="settings-label"),
                        Select(reasoning_opts, value=str(self._cfg.llm.reasoning_effort), id="llm_reasoning_effort"),
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

                with TabPane("UI & Panels", id="tab-ui"), VerticalScroll():
                    yield self._make_input("ui.theme", "Theme", self._cfg.ui.theme)
                    yield Label("Sidebar Panels (Check to enable)", classes="settings-section")

                    active_panels = self._cfg.ui.sidebar.panels
                    with Horizontal(classes="panels-grid"):
                        for p in PanelRegistry.ids():
                            yield Checkbox(p.capitalize(), value=(p in active_panels), id=f"panel_{p}")

                with TabPane("Caching", id="tab-caching"), VerticalScroll():
                    yield self._make_switch("caching.enabled", "Enabled", self._cfg.caching.enabled)
                    yield self._make_switch(
                        "caching.tier1_system_prompt", "Cache System Prompt", self._cfg.caching.tier1_system_prompt
                    )
                    yield self._make_switch(
                        "caching.tier1_tool_definitions", "Cache Tools", self._cfg.caching.tier1_tool_definitions
                    )
                    yield self._make_switch(
                        "caching.tier2_file_blocks", "Cache Files", self._cfg.caching.tier2_file_blocks
                    )
                    yield self._make_switch(
                        "caching.tier2_project_understanding",
                        "Cache Project Context",
                        self._cfg.caching.tier2_project_understanding,
                    )

                with TabPane("Permissions", id="tab-permissions"), VerticalScroll():
                    yield from self._make_permissions_tab()

                with TabPane("Hooks", id="tab-hooks"), VerticalScroll():
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

        yield Label("Tools", classes="perm-section-label settings-section")

        registry = self._session._tool_registry
        tool_names = [
            n for n in (registry.names() if registry else []) if (t := registry.get(n)) and t.permission_controlled
        ]
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

        yield Label("Bash Categories", classes="perm-section-label settings-section")

        bash = perms.bash
        auto_cats = set(bash.auto_approve_categories)
        denied_cats = set(bash.denied_categories)

        for cat in CommandCategory:
            if cat.value in denied_cats:
                val = "deny"
            elif cat.value in auto_cats:
                val = "auto"
            else:
                val = "confirm"
            label = cat.value.replace("_", " ").title()
            yield Horizontal(
                Label(label, classes="perm-label"),
                Select(CAT_PERM_OPTIONS, value=val, id=f"perm_cat_{cat.value}", classes="perm-select"),
                classes="perm-row",
            )

    def _make_input(self, id: str, label: str, value: str, password: bool = False) -> Horizontal:
        return Horizontal(
            Label(label, classes="settings-label"),
            Input(value=value, id=id.replace(".", "_"), password=password, classes="settings-input"),
            classes="settings-row",
        )

    def _make_switch(self, id: str, label: str, value: bool) -> Horizontal:
        return Horizontal(
            Label(label, classes="settings-label"), Switch(value=value, id=id.replace(".", "_")), classes="settings-row"
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
            except Exception as exc:
                error_label.update(f"[red]YAML Error: {exc}[/red]")
                return

        try:
            raw = self._session.config_loader._raw

            def set_nested(d, keys, val):
                for k in keys[:-1]:
                    if k not in d:
                        d[k] = {}
                    d = d[k]
                d[keys[-1]] = val

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

            set_nested(raw, ["ui", "theme"], self._get_val("ui_theme"))

            set_nested(raw, ["caching", "enabled"], self._get_bool("caching_enabled"))
            set_nested(raw, ["caching", "tier1_system_prompt"], self._get_bool("caching_tier1_system_prompt"))
            set_nested(raw, ["caching", "tier1_tool_definitions"], self._get_bool("caching_tier1_tool_definitions"))
            set_nested(raw, ["caching", "tier2_file_blocks"], self._get_bool("caching_tier2_file_blocks"))
            set_nested(
                raw, ["caching", "tier2_project_understanding"], self._get_bool("caching_tier2_project_understanding")
            )

            self._save_permissions(raw)

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
        except (ValueError, ValidationError, Exception) as exc:
            error_label.update(f"[red]Error: {exc}[/red]")

    def _save_permissions(self, raw: dict) -> None:
        from dendrophis.tools.bash_sandbox import CommandCategory

        registry = self._session._tool_registry
        tool_names = [
            n for n in (registry.names() if registry else []) if (t := registry.get(n)) and t.permission_controlled
        ]
        denied_tools: list[str] = []
        require_confirmation: list[str] = []
        for name in tool_names:
            try:
                val = self.query_one(f"#perm_tool_{name}", Select).value
            except Exception:
                continue
            if val == "deny":
                denied_tools.append(name)
            elif val == "confirm":
                require_confirmation.append(name)

        denied_cats: list[str] = []
        auto_cats: list[str] = []
        for cat in CommandCategory:
            try:
                val = self.query_one(f"#perm_cat_{cat.value}", Select).value
            except Exception:
                continue
            if val == "deny":
                denied_cats.append(cat.value)
            elif val == "auto":
                auto_cats.append(cat.value)

        if "permissions" not in raw:
            raw["permissions"] = {}
        p = raw["permissions"]
        p["denied_tools"] = denied_tools
        p["require_confirmation"] = require_confirmation
        if "bash" not in p:
            p["bash"] = {}
        p["bash"]["denied_categories"] = denied_cats
        p["bash"]["auto_approve_categories"] = auto_cats

    def _rewrite_panels_yaml(self, text: str) -> str:
        lines = text.splitlines()
        out_lines = []
        in_panels = False
        panels_indent = ""

        active_set = set()
        for p in PanelRegistry.ids():
            if self.query_one(f"#panel_{p}", Checkbox).value:
                active_set.add(p)

        for line in lines:
            if re.match(r"^(\s*)panels:\s*$", line):
                in_panels = True
                panels_indent = re.match(r"^(\s*)", line).group(1)
                out_lines.append(line)

                for p in PanelRegistry.ids():
                    if p in active_set:
                        out_lines.append(f"{panels_indent}- {p}")
                    else:
                        out_lines.append(f"{panels_indent}# - {p}")
                continue

            if in_panels:
                if re.match(r"^\s*(#\s*)?-\s*\w+\s*$", line):
                    continue
                in_panels = False

            out_lines.append(line)

        return "\n".join(out_lines)

    def action_dismiss_modal(self) -> None:
        self.dismiss()
