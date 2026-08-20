"""ChatView — scrollable streaming message log."""

from __future__ import annotations

import json
import re
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownFence

if TYPE_CHECKING:
    from textual.widgets import Static as TextualStatic


# Tag configuration — order matters: longer/more-specific first.
# Each entry is (open_tag, close_tag, tag_type).
_TAG_CONFIG: list[tuple[str, str, str]] = [
    ("<|channel>thought\n", "<channel|>", "think"),  # Gemma 4
    ("<think>", "</think>", "think"),  # DeepSeek / generic
    ("<tool_call>", "</tool_call>", "tool"),
    ("<tool_call|>", "</tool_call|>", "tool"),
]
_THINK_TAG_PAIRS: list[tuple[str, str]] = [
    (open_tag, close_tag) for open_tag, close_tag, tag_type in _TAG_CONFIG if tag_type == "think"
]
_TOOL_TAG_PAIRS: list[tuple[str, str]] = [
    (open_tag, close_tag) for open_tag, close_tag, tag_type in _TAG_CONFIG if tag_type == "tool"
]
_OPEN_TAGS = [pair[0] for pair in _THINK_TAG_PAIRS]
_CLOSE_TAGS = [pair[1] for pair in _THINK_TAG_PAIRS]
_ALL_OPEN_TAGS = [open_tag for open_tag, _, _ in _TAG_CONFIG]
_ALL_CLOSE_TAGS = [close_tag for _, close_tag, _ in _TAG_CONFIG]
_ALL_KNOWN_TAGS = _ALL_OPEN_TAGS + _ALL_CLOSE_TAGS


def _clean_latex_shorthand(text: str) -> str:
    """Replace common LaTeX symbols and non-standard dashes with plain text equivalents."""
    # Pattern to match optional dollar signs and whitespace around common symbols
    replacements = {
        r"\$?\s*\\rightarrow\s*\$?": "→",
        r"\$?\s*\\to\s*\$?": "→",
        r"\$?\s*\\Rightarrow\s*\$?": "⇒",
        r"\$?\s*\\leftarrow\s*\$?": "←",
        r"\$?\s*\\Leftarrow\s*\$?": "⇐",
        r"\$?\s*\\leftrightarrow\s*\$?": "↔",
        r"\$?\s*\\Leftrightarrow\s*\$?": "⇔",
        r"\$?\s*\\times\s*\$?": "\u00d7",
        r"\$?\s*\\dots\s*\$?": "...",
        r"\$?\s*\\quad\s*\$?": "  ",
        r"\$?\s*\\qquad\s*\$?": "    ",
        r"\\text\{([^}]*)\}": r"\1",
        r"\\xrightarrow(?:\[([^\]]*)\])?\{([^}]*)\}": lambda match: (
            f" → ({match.group(2)} / {match.group(1)}) "
            if match.group(1)
            else (f" → ({match.group(2)}) " if match.group(2) else " → ")
        ),
        r"\$\$": "",
        "\u2014": "-",
        "\u2013": "-",
    }
    cleaned = text
    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned)
    return cleaned


def _format_tool_args(tool_name: str, arguments: str, result_content: str = "") -> str:
    """Return a compact Rich-markup string of key arguments for a tool call."""
    try:
        if not arguments:
            return ""
        arguments_data = json.loads(arguments)
        if not isinstance(arguments_data, dict) or not arguments_data:
            return ""

        if tool_name == "bash":
            command_string = arguments_data.get("command", "")
            if len(command_string) > 60:
                command_string = command_string[:57] + "…"
            if command_string:
                return f" [cyan]{escape(command_string)}[/cyan]"

        if tool_name in ("glob", "ripgrep"):
            pattern_string = arguments_data.get("pattern", "")
            path_string = (
                arguments_data.get("path")
                or arguments_data.get("directory")
                or arguments_data.get("file_path")
                or arguments_data.get("search_path")
            )
            path_suffix = f" [dim]in {escape(str(path_string))}[/dim]" if path_string else ""
            if pattern_string:
                return f" [cyan]{escape(str(pattern_string))}[/cyan]{path_suffix}"

        if tool_name == "search_memory":
            query_input = arguments_data.get("query")
            tags_input = arguments_data.get("tags") or arguments_data.get("tag")
            project_identifier = arguments_data.get("project_id")
            limit_number = arguments_data.get("limit")

            tags_string = ""
            if isinstance(tags_input, list):
                tags_string = ", ".join(str(item) for item in tags_input if item)
            elif tags_input:
                tags_string = str(tags_input)

            parts_list = []
            if query_input:
                query_string = str(query_input)
                if len(query_string) > 60:
                    query_string = query_string[:57] + "…"
                parts_list.append(f"[cyan]{escape(query_string)}[/cyan]")

            if tags_string:
                parts_list.append(f"[dim]({escape(tags_string)})[/dim]")

            if not query_input and not tags_string:
                if project_identifier:
                    parts_list.append(f"[dim]project: {escape(str(project_identifier))}[/dim]")
                if limit_number is not None:
                    parts_list.append(f"[dim]limit: {limit_number}[/dim]")

            if parts_list:
                return " " + " ".join(parts_list)

        if tool_name in ("recall_memory", "delete_memory"):
            memory_identifier = (
                arguments_data.get("memory_id") or arguments_data.get("id") or arguments_data.get("query")
            )
            if memory_identifier:
                memory_string = str(memory_identifier)
                if len(memory_string) > 60:
                    memory_string = memory_string[:57] + "…"
                return f" [cyan]{escape(memory_string)}[/cyan]"

        if tool_name == "save_memory":
            content_input = arguments_data.get("content") or arguments_data.get("memory") or arguments_data.get("text")
            tags_input = arguments_data.get("tags") or arguments_data.get("tag")
            if content_input:
                content_string = str(content_input)
                if len(content_string) > 60:
                    content_string = content_string[:57] + "…"
                tags_suffix = ""
                if tags_input:
                    if isinstance(tags_input, list):
                        tags_string = ", ".join(str(item) for item in tags_input)
                    else:
                        tags_string = str(tags_input)
                    tags_suffix = f" [dim]({escape(tags_string)})[/dim]"
                return f" [cyan]{escape(content_string)}[/cyan]{tags_suffix}"

        if tool_name == "ask_multiple_choice":
            question_input = arguments_data.get("question") or arguments_data.get("prompt")
            if question_input:
                question_string = str(question_input)
                if len(question_string) > 60:
                    question_string = question_string[:57] + "…"
                return f" [cyan]{escape(question_string)}[/cyan]"

        if tool_name == "invoke_subagent":
            agent_name = (
                arguments_data.get("agent")
                or arguments_data.get("subagent")
                or arguments_data.get("role")
                or arguments_data.get("name", "")
            )
            task_description = (
                arguments_data.get("task") or arguments_data.get("prompt") or arguments_data.get("description", "")
            )
            task_string = str(task_description)
            if len(task_string) > 50:
                task_string = task_string[:47] + "…"
            return f" [cyan]{escape(str(agent_name))}[/cyan] [dim]{escape(task_string)}[/dim]"

        if tool_name in ("execute_code", "python_exec"):
            code_snippet = arguments_data.get("code") or arguments_data.get("script") or arguments_data.get("command")
            if code_snippet:
                code_string = str(code_snippet)
                if len(code_string) > 60:
                    code_string = code_string[:57] + "…"
                return f" [cyan]{escape(code_string)}[/cyan]"

        if tool_name == "todo":
            action_type = arguments_data.get("action", "")
            item_text = arguments_data.get("text") or arguments_data.get("todo_id", "")
            return f" [cyan]{escape(str(action_type))}[/cyan] [dim]{escape(str(item_text))}[/dim]"

        if tool_name in (
            "read",
            "edit",
            "write",
            "analyze_functions",
            "get_function",
            "replace_function",
            "read_file",
            "write_file",
            "edit_function",
            "list_dir",
            "patch",
        ):
            file_path_input = (
                arguments_data.get("file_path")
                or arguments_data.get("path")
                or arguments_data.get("directory")
                or arguments_data.get("dir_path")
                or arguments_data.get("target_file")
            )
            if file_path_input is not None:
                file_path_string = str(file_path_input)
                try:
                    relative_path = str(Path(file_path_string).relative_to(Path.cwd()))
                except (ValueError, TypeError):
                    relative_path = file_path_string
                if len(relative_path) > 60:
                    relative_path = "…" + relative_path[-57:]

                suffix_text = ""
                if tool_name in ("read", "read_file"):
                    showing_lines = None
                    total_lines = None
                    if result_content:
                        try:
                            result_data = json.loads(result_content)
                            showing_lines = result_data.get("showing_lines")
                            total_lines = result_data.get("total_lines")
                        except Exception:
                            pass

                    if showing_lines and total_lines is not None:
                        line_range = f"[{showing_lines}] ({total_lines} lines total)"
                    else:
                        offset_input = arguments_data.get("offset")
                        limit_input = arguments_data.get("limit")

                        if offset_input is None:
                            offset_input = 1
                        if limit_input is None:
                            limit_input = 2000

                        try:
                            offset_number = int(offset_input)
                            if str(limit_input).lower() == "all":
                                line_range = f"[{offset_number}:all]"
                            else:
                                limit_number = int(limit_input)
                                end_line_number = offset_number + limit_number - 1
                                line_range = f"[{offset_number}:{end_line_number}]"
                        except (ValueError, TypeError):
                            line_range = f"[{offset_input}:{limit_input}]"
                    suffix_text = f"[dim] {line_range}[/dim]"
                elif tool_name in ("edit", "edit_function"):
                    if result_content:
                        try:
                            result_data = json.loads(result_content)
                            changes_summary = result_data.get("changes", "")
                            if changes_summary:
                                changes_parts = changes_summary.split("/")
                                if len(changes_parts) == 2:
                                    suffix_text = f" [green]{changes_parts[0]}[/green]/[red]{changes_parts[1]}[/red]"
                                else:
                                    suffix_text = f" [green]{changes_summary}[/green]"
                        except Exception:
                            pass
                elif tool_name in ("get_function", "replace_function"):
                    function_name = arguments_data.get("function_name", "")
                    if function_name:
                        suffix_text = f" [dim]({function_name})[/dim]"

                return f" [cyan]{escape(relative_path)}[/cyan]{suffix_text}"

        # Fallback 1: check common primary keys
        common_keys = (
            "file_path",
            "path",
            "command",
            "pattern",
            "query",
            "memory_id",
            "agent",
            "question",
            "prompt",
            "code",
            "content",
            "text",
            "directory",
            "target_file",
        )
        for key_name in common_keys:
            value_data = arguments_data.get(key_name)
            if value_data is not None and value_data != "":
                value_string = str(value_data)
                if len(value_string) > 60:
                    value_string = value_string[:57] + "…"
                return f" [cyan]{escape(value_string)}[/cyan]"

        # Fallback 2: key-value summary for any other tool with arguments
        fallback_items = []
        for argument_key, argument_value in arguments_data.items():
            if argument_value is None or argument_value == "":
                continue
            if isinstance(argument_value, (dict, list)):
                value_string = json.dumps(argument_value)
            else:
                value_string = str(argument_value)
            if len(value_string) > 40:
                value_string = value_string[:37] + "…"
            fallback_items.append(f"[dim]{escape(str(argument_key))}=[/dim][cyan]{escape(value_string)}[/cyan]")
            if len(fallback_items) >= 3:
                break

        if fallback_items:
            return " " + ", ".join(fallback_items)
    except Exception:
        pass
    return ""


class UserMessage(Static):
    """Chat bubble displaying a user's message."""

    DEFAULT_CSS = """
    UserMessage {
        height: auto;
        margin: 1 0;
        padding: 0 1;
        color: $accent;
    }
    UserMessage .label { text-style: bold; }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        yield Static("👤 You", classes="label")
        yield Static(self._text, markup=False)


class LoadingIndicator(Static):
    """Subtle pulsing loading indicator with TTFT timer."""

    DEFAULT_CSS = """
    LoadingIndicator {
        color: $primary;
        margin-top: 0;
        padding-left: 1;
    }
    """

    def on_mount(self) -> None:
        self._frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._index = 0
        self._start_time = time.monotonic()
        self.set_interval(0.1, self._update_frame)

    def _update_frame(self) -> None:
        elapsed = time.monotonic() - self._start_time
        frame = self._frames[self._index]
        self.update(f"{frame} waiting... {elapsed:.1f}s")
        self._index = (self._index + 1) % len(self._frames)


class AssistantLabel(Static):
    """Clickable label above an assistant bubble; click copies the response."""

    DEFAULT_CSS = """
    AssistantLabel {
        text-style: bold;
        color: $primary;
        width: auto;
        padding: 0 1;
    }
    AssistantLabel:hover {
        background: $accent;
        color: $text;
    }
    """

    def on_click(self) -> None:
        parent = self.parent
        if hasattr(parent, "_clean_text") and parent._clean_text:
            self.app.copy_to_clipboard(parent._clean_text.strip())
            self.app.notify("Copied response to clipboard!", severity="information", timeout=2)


class ThoughtBubble(VerticalScroll):
    """Collapsible area for reasoning/thought tokens."""

    DEFAULT_CSS = """
    ThoughtBubble {
        background: $panel;
        border-left: solid $accent;
        margin: 1 2;
        width: 100%;
        height: auto;
        max-height: 6;
        scrollbar-gutter: stable;
        scrollbar-size-vertical: 1;
        display: none;
    }
    #thought-header {
        color: $accent;
        width: 100%;
        height: 1;
    }
    #thought-text {
        color: $text-muted;
        width: 100%;
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._collapsed = False

    def compose(self) -> ComposeResult:
        yield Static("🧠 [bold]Thought[/bold]", id="thought-header", markup=True)
        yield Static("", id="thought-text", markup=False)

    def on_mount(self) -> None:
        if self._parts:
            joined = "".join(self._parts)
            self.query_one("#thought-header", Static).update("🧠 [bold]Thinking...[/bold]")
            body = self.query_one("#thought-text", Static)
            body.styles.display = "block"
            body.update(joined)
            self.styles.display = "block"
        self._update_display_state()

    def append_text(self, text: str) -> None:
        self._parts.append(text)
        joined = "".join(self._parts)
        if not joined.strip():
            return

        self.styles.display = "block"
        self._collapsed = False

        if not self.is_mounted:
            return

        self.query_one("#thought-header", Static).update("🧠 [bold]Thinking...[/bold]")
        body = self.query_one("#thought-text", Static)
        body.styles.display = "block"
        body.update(joined)

        # Force a height once we reach the limit to ensure the scrollbar activates
        # in the VerticalScroll container.
        if self.virtual_size.height > 6:
            self.styles.height = 6
        else:
            self.styles.height = "auto"

        self.scroll_end(animate=False)

    def collapse(self) -> None:
        """Collapse the thought bubble after thinking finishes."""
        self._collapsed = True
        self._update_display_state()

    def on_click(self) -> None:
        """Toggle collapsed/expanded state on click."""
        self._collapsed = not self._collapsed
        self._update_display_state()

    def _update_display_state(self) -> None:
        """Update display heights and text based on collapsed state."""
        if not self.is_mounted:
            return
        header = self.query_one("#thought-header", Static)
        body = self.query_one("#thought-text", Static)

        if self._collapsed:
            body.styles.display = "none"
            self.styles.height = 1
            header.update("🧠 [bold]Thought[/bold] [dim](click to expand)[/dim]")
        else:
            body.styles.display = "block"
            header.update("🧠 [bold]Thought[/bold] [dim](click to collapse)[/dim]")
            if self.virtual_size.height > 6:
                self.styles.height = 6
            else:
                self.styles.height = "auto"


class CopyCodeButton(Static):
    """Slim button that sits flush on top of a code block."""

    DEFAULT_CSS = """
    CopyCodeButton {
        width: 100%;
        height: 1;
        background: $panel-lighten-1;
        color: $text-muted;
        text-align: right;
        padding: 0 1;
        margin-top: 1;
        margin-bottom: 0;
    }
    CopyCodeButton:hover {
        background: $accent;
        color: $background;
        text-style: bold;
    }
    """

    def __init__(self, code: str) -> None:
        super().__init__("📋 copy")
        self._code = code

    def on_click(self) -> None:
        self.app.copy_to_clipboard(self._code.strip())
        self.app.notify("Code block copied to clipboard", severity="information", timeout=2)


class CustomMarkdown(Markdown):
    """Markdown subclass — keeps open_links off so custom links don't open a browser."""

    DEFAULT_CSS = """
    CustomMarkdown MarkdownFence {
        margin-top: 0;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("open_links", False)
        super().__init__(*args, **kwargs)


class InlineToolStatus(Static):
    """Compact tool-call status line rendered inline inside an assistant bubble."""

    DEFAULT_CSS = """
    InlineToolStatus {
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        margin: 0;
    }
    """


def _find_earliest_tag(text: str, tags: list[str]) -> tuple[int, str]:
    """Find the earliest occurrence of any tag in text. Returns (position, tag) or (len(text), "")."""
    best_pos, best_tag = len(text), ""
    for tag in tags:
        pos = text.find(tag)
        if 0 <= pos < best_pos:
            best_pos, best_tag = pos, tag
    return best_pos, best_tag


class AssistantMessage(Vertical):
    """Streaming assistant response bubble with markdown rendering.

    Text segments are separated by inline tool-call widgets so that the full
    conversation flow (text → tool → result → text) is visible without
    scrolling away from the response.
    """

    DEFAULT_CSS = """
    AssistantMessage {
        margin: 1 0 0 0;
        padding: 0 1;
        height: auto;
    }
    AssistantMessage CustomMarkdown {
        height: auto;
        margin-top: 0;
        padding: 0;
    }
    AssistantMessage ToolResultMessage {
        margin: 0 0 0 1;
        border-left: solid $panel-darken-2;
    }
    AssistantMessage InlineToolStatus {
        margin-top: 1;
    }
    """

    def __init__(self, model_id: str = "", loading: bool = True) -> None:
        super().__init__()
        self._markdown = CustomMarkdown("")
        # All markdown segments (one per text chunk between tool calls)
        self._markdown_segments: list[CustomMarkdown] = [self._markdown]
        # Text across ALL segments (used for copy-all)
        self._all_parts: list[str] = []
        # Text for the CURRENT segment only
        self._clean_parts: list[str] = []
        self._clean_text = ""
        self._model_id = model_id
        self._has_reasoning = False
        self._loading = LoadingIndicator() if loading else None
        self._thought_bubble = ThoughtBubble()
        self._active_thought_bubble: ThoughtBubble = self._thought_bubble
        self._text_since_last_thought: bool = False  # True once text arrives after a thought
        self._in_think_tag: bool = False  # True while inside a think block
        self._pending_buf: str = ""  # chars buffered while detecting a tag boundary
        self._code_state: str | None = None  # None, '`', or '```' - tracks if we're in code
        self._token_count = 0
        self._render_pending: bool = False
        self._last_render_time: float = 0
        self._last_status_widget: InlineToolStatus | None = None
        # Maps tool call index → placeholder status widget, for updating once args arrive
        self._tool_status_by_index: dict[int, InlineToolStatus] = {}
        # Maps tool call ID → placeholder status widget, for placing results inline under tool calls
        self._tool_status_by_id: dict[str, InlineToolStatus] = {}
        self._finalized: bool = False

    def compose(self) -> ComposeResult:
        label_text = "🤖 Dendrophis"
        if self._model_id:
            # Shorten common model names
            model_short = self._model_id.split("/")[-1]
            label_text += f" [dim]({model_short})[/dim]"
        yield AssistantLabel(label_text)
        if self._loading:
            yield self._loading
        yield self._thought_bubble
        yield self._markdown

    def remove_loading(self) -> None:
        if self._loading:
            self._loading.remove()
            self._loading = None

    # ── Text rendering ────────────────────────────────────────────────────────

    def _render_clean(self) -> None:
        """Render accumulated text for the current segment to markdown."""
        current_text = _clean_latex_shorthand("".join(self._clean_parts))
        if current_text:
            self.run_worker(self._markdown.update(current_text))

    def _schedule_render(self) -> None:
        """Schedule a markdown render, throttled to every 50 ms."""
        if not self._render_pending:
            self._render_pending = True
            self.set_timer(0.05, self._do_throttled_render)

    def _do_throttled_render(self) -> None:
        """Perform the actual render, but skip if finalize() already ran."""
        self._render_pending = False
        if not self._finalized:
            self._render_clean()

    def append_delta(self, delta: str) -> None:
        """Route an incoming text delta through the think-tag state machine."""
        self.remove_loading()
        self._process_delta(delta)

    def _process_delta(self, delta: str) -> None:
        """State machine: splits delta into reasoning and response text.

        Handles tags that arrive split across multiple streaming chunks by
        buffering the tail of each chunk when it's a prefix of a known tag.
        Supports both <think> … </think> (DeepSeek) and
        <|channel>thought\n…<channel|> (Gemma 4).

        Also strips synthesized <tool_call> tags (from local models) from display.
        Backtick-aware: tags inside `code` or ```code``` blocks are treated as text.
        """
        text = self._pending_buf + delta
        self._pending_buf = ""

        # State: None, '`' (in inline code), or '```' (in code block)
        code_state = getattr(self, "_code_state", None)

        char_index = 0
        while char_index < len(text):
            # Update code state by scanning from current position
            if code_state is None:
                # Look for backticks
                inline_bt = text.find("`", char_index)
                block_bt = text.find("```", char_index)

                # Find which comes first
                next_bt = -1
                if inline_bt != -1 and block_bt != -1:
                    next_bt = min(inline_bt, block_bt)
                elif inline_bt != -1:
                    next_bt = inline_bt
                elif block_bt != -1:
                    next_bt = block_bt

                if next_bt == -1:
                    # No more backticks, process rest of text normally
                    self._process_text_segment(text[char_index:], code_state)
                    break

                # Process text before backtick
                if next_bt > char_index:
                    self._process_text_segment(text[char_index:next_bt], code_state)

                # Enter code state
                if next_bt == block_bt:
                    code_state = "```"
                    self._route_text("```")
                    char_index = next_bt + 3
                else:
                    code_state = "`"
                    self._route_text("`")
                    char_index = next_bt + 1

            elif code_state == "`":
                # Look for closing inline backtick
                close_bt = text.find("`", char_index)
                if close_bt == -1:
                    # Still in inline code, emit rest as text
                    self._route_text(text[char_index:])
                    break
                # Emit code content as text
                self._route_text(text[char_index : close_bt + 1])
                code_state = None
                char_index = close_bt + 1

            elif code_state == "```":
                # Look for closing code block
                close_bt = text.find("```", char_index)
                if close_bt == -1:
                    # Still in code block, emit rest as text
                    self._route_text(text[char_index:])
                    break
                # Emit code content as text
                self._route_text(text[char_index : close_bt + 3])
                code_state = None
                char_index = close_bt + 3

        self._code_state = code_state

    def _process_text_segment(self, text: str, code_state: str | None) -> None:
        """Process a segment of text that is outside code blocks.
        Handles think tags and tool_call tags."""
        if not text:
            return

        # If we're in a think tag, look for close tags
        if self._in_think_tag:
            self._process_think_text(text)
        else:
            self._process_normal_text(text)

    def _process_think_text(self, text: str) -> None:
        """Process text while inside a think tag, looking for close tags."""
        remaining = text
        while remaining:
            best_pos, best_tag = _find_earliest_tag(remaining, _CLOSE_TAGS)

            if best_tag:
                if best_pos:
                    self._route_reasoning(remaining[:best_pos])
                self._in_think_tag = False
                remaining = remaining[best_pos + len(best_tag) :]
            else:
                # Check for partial close tag at end
                buffered = self._try_buffer_partial(remaining, _CLOSE_TAGS, self._route_reasoning)
                if not buffered:
                    self._route_reasoning(remaining)
                return

    def _process_normal_text(self, text: str) -> None:
        """Process normal text, looking for think tags and tool_call tags."""
        remaining = text
        # Strip leading "thought" artifact (common with Gemma models) at the start of the response
        if not self._all_parts and not self._has_reasoning:
            lower_remaining = remaining.lower()
            if "thought".startswith(lower_remaining) and len(lower_remaining) < 7:
                self._pending_buf = remaining
                return
            if lower_remaining.startswith("thought"):
                length_to_strip = 7
                while length_to_strip < len(remaining) and remaining[length_to_strip].isspace():
                    length_to_strip += 1
                remaining = remaining[length_to_strip:]

        while remaining:
            # Find earliest think tag or tool_call tag
            think_pos, think_tag = _find_earliest_tag(remaining, _OPEN_TAGS)
            tool_pos, tool_tag = _find_earliest_tag(remaining, _ALL_OPEN_TAGS)

            # Determine which tag comes first
            best_position = len(remaining)
            best_tag = ""
            tag_type = ""
            associated_close_tag = ""

            if think_tag and think_pos < best_position:
                best_position = think_pos
                best_tag = think_tag
                tag_type = "think"

            if tool_tag and tool_pos < best_position:
                best_position = tool_pos
                best_tag = tool_tag
                tag_type = "tool"
                # Look up the close tag for this tool tag
                for open_tag, close_tag, _ in _TAG_CONFIG:
                    if open_tag == tool_tag:
                        associated_close_tag = close_tag
                        break

            if not best_tag:
                # No tags found, emit all as text
                buffered = self._try_buffer_partial(remaining, _ALL_OPEN_TAGS, self._route_text)
                if not buffered:
                    self._route_text(remaining)
                return

            # Emit text before tag
            if best_position:
                self._route_text(remaining[:best_position])

            if tag_type == "think":
                self._in_think_tag = True
                remaining = remaining[best_position + len(best_tag) :]
                # Process remaining text (may contain close tag)
                if remaining:
                    self._process_think_text(remaining)
                return

            # tool_call handling
            tool_end_position = remaining.find(associated_close_tag, best_position)
            if tool_end_position != -1:
                remaining = remaining[tool_end_position + len(associated_close_tag) :]
            else:
                self._pending_buf = remaining[best_position:]
                return

    def _try_buffer_partial(
        self,
        text: str,
        tags: list[str],
        emit_fn: Callable[[str], None],
    ) -> bool:
        """If *text* ends with a prefix of any tag, buffer that prefix and
        emit the rest via *emit_fn*. Returns True if buffering occurred."""
        for tag in tags:
            max_prefix = min(len(tag) - 1, len(text))
            for prefix_len in range(max_prefix, 0, -1):
                if text.endswith(tag[:prefix_len]):
                    if len(text) > prefix_len:
                        emit_fn(text[:-prefix_len])
                    self._pending_buf = text[-prefix_len:]
                    return True
        return False

    def _route_text(self, text: str) -> None:
        """Append text to the current visible markdown segment."""
        if not text:
            return
        if self._has_reasoning and self._active_thought_bubble and not self._active_thought_bubble._collapsed:
            self._active_thought_bubble.collapse()
        self._text_since_last_thought = True
        self._clean_parts.append(text)
        self._all_parts.append(text)
        self._token_count += 1
        self._schedule_render()

    def _route_reasoning(self, text: str) -> None:
        """Append text to the active thought bubble.

        If text was already output since the last thought, mount a fresh bubble
        so each thought block gets its own collapsible rather than merging all
        reasoning into a single one.
        """
        if not text:
            return
        if not text.strip() and not self._has_reasoning:
            return
        if self._text_since_last_thought:
            # Freeze current markdown and create new segment for post-thought text
            self._freeze_current_segment()
            new_bubble = ThoughtBubble()
            new_md = CustomMarkdown("")
            self._markdown_segments.append(new_md)
            self._markdown = new_md
            # Mount thought bubble then new markdown, both after current content
            self.mount(new_bubble, new_md)
            self._active_thought_bubble = new_bubble
            self._text_since_last_thought = False
        self._has_reasoning = True
        self._active_thought_bubble.append_text(text)

    def append_reasoning(self, delta: str) -> None:
        """Public API: route reasoning_content field (DeepInfra) to thought bubble."""
        self.remove_loading()
        self._route_reasoning(delta)

    # ── Inline tool widgets ───────────────────────────────────────────────────

    def _freeze_current_segment(self) -> None:
        """Flush pending text into the active markdown and reset the part buffer."""
        current_text = "".join(self._clean_parts).strip()
        if current_text:
            self.run_worker(self._markdown.update(_clean_latex_shorthand(current_text)))
        self._clean_parts = []

    def add_tool_placeholder(self, index: int, tool_name: str, tool_call_id: str | None = None) -> None:
        """Mount a placeholder status line for a tool call that is still streaming arguments."""
        self.remove_loading()
        self._freeze_current_segment()

        status = InlineToolStatus(f"⚙ {tool_name} [dim]…[/dim]")
        self._tool_status_by_index[index] = status
        if tool_call_id:
            self._tool_status_by_id[tool_call_id] = status
        self._last_status_widget = status

        new_md = CustomMarkdown("")
        self._markdown_segments.append(new_md)
        self._markdown = new_md

        self.mount(status, new_md)

    def add_inline_status(
        self, tool_name: str, description: str, arguments: str, index: int = -1, tool_call_id: str | None = None
    ) -> None:
        """Update an existing placeholder (by index or ID) or mount a new status line."""
        self.remove_loading()

        display_args = _format_tool_args(tool_name, arguments)
        label = f"⚙ {tool_name}{display_args}"
        if description:
            label += f" — {description}"

        # Update the placeholder created by add_tool_placeholder if we have one
        existing = None
        if index >= 0:
            existing = self._tool_status_by_index.pop(index, None)
        if existing is None and tool_call_id:
            existing = self._tool_status_by_id.get(tool_call_id)

        if existing is not None:
            existing.update(label)
            if tool_call_id:
                self._tool_status_by_id[tool_call_id] = existing
            self._last_status_widget = existing
            return

        # No placeholder — create the widget now (e.g. execution started without prior streaming)
        self._freeze_current_segment()
        status = InlineToolStatus(label)
        if index >= 0:
            self._tool_status_by_index[index] = status
        if tool_call_id:
            self._tool_status_by_id[tool_call_id] = status
        self._last_status_widget = status

        new_md = CustomMarkdown("")
        self._markdown_segments.append(new_md)
        self._markdown = new_md

        self.mount(status, new_md)

    def add_inline_result(
        self,
        tool_name: str,
        content: str,
        description: str,
        arguments: str,
        consecutive_failures: int,
        tool_call_id: str | None = None,
    ) -> None:
        """Mount a tool-result widget inline directly below the corresponding tool call status if found."""
        self._last_status_widget = None  # Prevent further updates to the previous status
        msg = ToolResultMessage(tool_name, content, description, arguments, consecutive_failures)

        existing_status = self._tool_status_by_id.pop(tool_call_id, None) if tool_call_id else None
        if existing_status is not None and existing_status.parent is not None:
            self.mount(msg, after=existing_status)
        elif self._markdown.parent is not None:
            self.mount(msg, before=self._markdown)
        else:
            self.mount(msg)

    def on_mount(self) -> None:
        """Ensure markdown content and copy buttons are rendered when mounted."""
        if self._finalized or self._clean_parts or self._all_parts:
            self._schedule_finalized_render()

    def _schedule_finalized_render(self) -> None:
        remaining_text = _clean_latex_shorthand("".join(self._clean_parts)).strip()

        async def _update_and_inject() -> None:
            if remaining_text:
                await self._markdown.update(remaining_text)
            # Inject copy buttons into every markdown segment
            for markdown_widget in self._markdown_segments:
                for child_widget in list(markdown_widget.walk_children()):
                    if isinstance(child_widget, MarkdownFence) and child_widget.code:
                        parent_widget = child_widget.parent
                        if parent_widget is not None:
                            with suppress(Exception):
                                await parent_widget.mount(CopyCodeButton(child_widget.code), before=child_widget)

        self.run_worker(_update_and_inject())

    # ── Finalise ──────────────────────────────────────────────────────────────

    def finalize(self) -> None:
        """Render final markdown and inject copy buttons above each code fence.

        Also flushes any buffered think-tag text (_pending_buf) so it isn't
        silently dropped if the stream ended mid-tag (e.g. on error).
        """
        self._finalized = True
        self.remove_loading()

        was_thinking = self._in_think_tag
        # Explicitly end thinking state to prevent cross-session contamination
        if self._in_think_tag:
            self._in_think_tag = False
            self._route_reasoning("</think>")  # Close any open think tag

        # Flush any buffered partial tag text before finalizing
        if self._pending_buf:
            is_tag_content = (
                self._pending_buf.startswith("<tool_call>")
                or self._pending_buf.startswith("<tool_call|>")
                or any(known_tag.startswith(self._pending_buf) for known_tag in _ALL_KNOWN_TAGS)
            )
            if not is_tag_content:
                if was_thinking:
                    self._route_reasoning(self._pending_buf)
                else:
                    self._route_text(self._pending_buf)
            self._pending_buf = ""

        # Collapse the active thought bubble if thinking has finished
        if self._active_thought_bubble and not self._active_thought_bubble._collapsed:
            self._active_thought_bubble.collapse()

        has_tools_or_status = any(isinstance(child, (InlineToolStatus, ToolResultMessage)) for child in self.children)
        if not self._all_parts and not self._has_reasoning and not has_tools_or_status:
            self.remove()
            return

        # Full text across all segments (for copy-all via AssistantLabel)
        self._clean_text = _clean_latex_shorthand("".join(self._all_parts)).strip()
        self._schedule_finalized_render()


class ErrorMessage(Static):
    """Chat bubble displaying a stream or tool error."""

    DEFAULT_CSS = """
    ErrorMessage {
        color: $error;
        margin: 1 0 0 0;
        padding: 0 1;
    }
    ErrorMessage .label { text-style: bold; }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        yield Static("⚠ Error", classes="label")
        yield Static(self._text, markup=False)


class SystemMessage(Static):
    """Italicised status line for internal events (e.g. model switches)."""

    DEFAULT_CSS = """
    SystemMessage {
        color: $text-muted;
        text-style: italic;
        margin: 1 0 0 0;
        padding: 0 1;
        border-left: double $panel;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(f"⚙ {text}")


class ToolResultMessage(Vertical):
    """Displays tool call results with success/failure indicator."""

    DEFAULT_CSS = """
    ToolResultMessage {
        margin: 0;
        padding: 0 1;
        height: auto;
    }
    ToolResultMessage .success {
        color: $success;
    }
    ToolResultMessage .error {
        color: $error;
    }
    ToolResultMessage .content {
        color: $text;
        background: $surface-darken-1;
        padding: 0 1;
        margin-top: 1;
    }
    ToolResultMessage .expansion-hint {
        color: $text-muted;
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    def __init__(
        self, tool_name: str, content: str, description: str = "", arguments: str = "", consecutive_failures: int = 0
    ) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._content = content
        self._description = description
        self._arguments = arguments
        self._is_error = consecutive_failures > 0
        self._show_detail = consecutive_failures >= 2
        self._expanded = False

        self._parsed_bash = None
        if self._tool_name == "bash" and self._content:
            try:
                self._parsed_bash = json.loads(self._content)
                if isinstance(self._parsed_bash, dict):
                    returncode = self._parsed_bash.get("returncode", 0)
                    if returncode != 0:
                        self._is_error = True
            except Exception:
                pass

        # Compute self._full_output_content
        if self._tool_name == "bash":
            stdout = self._parsed_bash.get("stdout", "").strip() if self._parsed_bash else ""
            stderr = self._parsed_bash.get("stderr", "").strip() if self._parsed_bash else ""
            combined = []
            if stdout:
                combined.append(stdout)
            if stderr:
                combined.append(stderr)
            self._full_output_content = "\n".join(combined) if combined else (self._content or "")
        elif self._tool_name in ("edit", "edit_function") and self._content:
            try:
                parsed_result = json.loads(self._content)
                if isinstance(parsed_result, dict) and "diff" in parsed_result:
                    self._full_output_content = parsed_result["diff"]
                else:
                    self._full_output_content = self._content
            except Exception:
                self._full_output_content = self._content
        else:
            self._full_output_content = self._content or ""

    def on_mount(self) -> None:
        """Initialize widget states once mounted."""
        self._update_display_state()

    def on_click(self) -> None:
        """Toggle expanded view on click."""
        self._expanded = not self._expanded
        self._update_display_state()

    def _update_display_state(self) -> None:
        """Update visibility and text of child widgets based on expansion state."""
        lines_count = len(self._full_output_content.splitlines())
        is_bash = self._tool_name == "bash"

        # 1. Update content widget
        if is_bash:
            self._content_static.styles.display = "block" if self._full_output_content else "none"
            if lines_count > 10 and not self._expanded:
                truncated_content = "\n".join(self._full_output_content.splitlines()[:10])
                self._content_static.update(truncated_content)
            else:
                self._content_static.update(self._full_output_content)
        else:
            show_content = self._expanded or (self._is_error and self._show_detail)
            self._content_static.styles.display = "block" if (show_content and self._full_output_content) else "none"
            self._content_static.update(self._full_output_content)

        # 2. Update hint widget
        if is_bash and lines_count > 10:
            self._hint_static.styles.display = "block"
            if self._expanded:
                self._hint_static.update("(click to collapse)")
            else:
                self._hint_static.update(f"... ({lines_count - 10} more lines, click to expand)")
        else:
            self._hint_static.styles.display = "none"

    def compose(self) -> ComposeResult:
        """Render tool name with key arguments; show error detail only on repeated failures."""
        display_args = _format_tool_args(self._tool_name, self._arguments, self._content)

        exit_str = ""
        if self._parsed_bash is not None:
            returncode = self._parsed_bash.get("returncode", 0)
            exit_str = " [green](exit: 0)[/green]" if returncode == 0 else f" [red](exit: {returncode})[/red]"

        escaped_description = escape(self._description) if self._description else ""

        label = f"{self._tool_name}{display_args}{exit_str}"
        if escaped_description:
            label = f"{label} ({escaped_description})"

        header_classes = "error" if self._is_error else "success"
        yield Static(f"[{header_classes}]●[/{header_classes}] {label}", classes=header_classes)

        self._content_static = Static("", markup=False, classes="content")
        yield self._content_static

        self._hint_static = Static("", markup=False, classes="expansion-hint")
        yield self._hint_static


class RetryStatus(Static):
    """Animated countdown shown while waiting for a retry."""

    DEFAULT_CSS = """
    RetryStatus {
        color: $warning;
        text-style: bold italic;
        margin: 0 0 0 1;
        padding: 0 1;
    }
    """

    def __init__(self, message: str, delay: float) -> None:
        super().__init__()
        self._message = message
        self._remaining = delay

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        if self._remaining <= 0:
            self.update(f"🔄 {self._message}. Retrying now...")
        else:
            self.update(f"🔄 {self._message}. Retrying in {self._remaining:.1f}s...")
            self._remaining -= 0.1


class ChatView(VerticalScroll):
    """Scrollable message log that streams assistant responses in real time.

    Maintains a deque of message widgets with a configurable max length.
    Oldest messages are evicted when the limit is reached.
    """

    class StreamingStarted(Message):
        pass

    class StreamingFinished(Message):
        pass

    MAX_SCROLLBACK = 100  # Maximum number of message widgets to keep

    def __init__(self, max_scrollback: int = MAX_SCROLLBACK) -> None:
        super().__init__()
        self._active_bubble: AssistantMessage | None = None
        self._retry_status: RetryStatus | None = None
        self._max_scrollback = max_scrollback
        # Deque of all mounted message widgets (excluding loading/retry/status)
        # Note: Do NOT use maxlen here, as we need to manually .remove() widgets from DOM
        self._message_widgets: deque[TextualStatic] = deque()
        self._scroll_pending = False

    def _throttled_scroll_end(self) -> None:
        """Throttled scroll_end to prevent UI lag during high-speed streaming."""
        if not self._scroll_pending:
            self._scroll_pending = True
            self.set_timer(0.1, self._do_scroll)

    def _do_scroll(self) -> None:
        """Perform the actual scroll and reset pending flag."""
        self._scroll_pending = False
        self.scroll_end(animate=False)

    def _evict_if_needed(self) -> None:
        """Remove oldest messages if we've exceeded the scrollback limit."""
        while len(self._message_widgets) >= self._max_scrollback:
            oldest = self._message_widgets.popleft()
            oldest.remove()

    def add_user_message(self, text: str) -> None:
        self._evict_if_needed()
        msg = UserMessage(text)
        self.mount(msg)
        self._message_widgets.append(msg)
        self.scroll_end(animate=False)

    def start_assistant_message(self, model_id: str = "", loading: bool = True) -> None:
        self.remove_retry_status()
        bubble = AssistantMessage(model_id=model_id, loading=loading)
        self._active_bubble = bubble
        self.mount(bubble)
        self._message_widgets.append(bubble)
        self.post_message(self.StreamingStarted())

    def append_text_delta(self, delta: str) -> None:
        if self._active_bubble is not None:
            self._active_bubble.append_delta(delta)
            self._throttled_scroll_end()

    def append_reasoning_delta(self, delta: str) -> None:
        if self._active_bubble is not None:
            self._active_bubble.append_reasoning(delta)
            self._throttled_scroll_end()

    def append_reasoning(self, delta: str) -> None:
        self.append_reasoning_delta(delta)

    def finish_assistant_message(self) -> None:
        if self._active_bubble is not None:
            self._active_bubble.finalize()
            self._active_bubble = None
        self.post_message(self.StreamingFinished())
        self.set_timer(0.1, lambda: self.scroll_end(animate=False))

    def add_system_message(self, text: str) -> None:
        self._evict_if_needed()
        msg = SystemMessage(text)
        self.mount(msg)
        self._message_widgets.append(msg)
        self.scroll_end(animate=False)

    def add_tool_result(
        self,
        tool_name: str,
        content: str,
        description: str = "",
        arguments: str = "",
        consecutive_failures: int = 0,
        tool_call_id: str | None = None,
    ) -> None:
        """Add a tool result. If streaming, inject inline inside the active bubble."""
        if self._active_bubble:
            self._active_bubble.add_inline_result(
                tool_name, content, description, arguments, consecutive_failures, tool_call_id
            )
            self._throttled_scroll_end()
            return
        # Fallback: no active bubble — mount as a standalone sibling
        self._evict_if_needed()
        msg = ToolResultMessage(tool_name, content, description, arguments, consecutive_failures)
        self.mount(msg)
        self._message_widgets.append(msg)
        self.scroll_end(animate=False)

    def add_tool_placeholder(self, index: int, tool_name: str, tool_call_id: str | None = None) -> None:
        """Add a placeholder status line for a streaming tool call."""
        if self._active_bubble:
            self._active_bubble.add_tool_placeholder(index, tool_name, tool_call_id)
            self._throttled_scroll_end()

    def add_tool_status(
        self,
        tool_name: str,
        description: str = "",
        arguments: str = "",
        index: int = -1,
        tool_call_id: str | None = None,
    ) -> None:
        """Add or update a tool-status line. If streaming, inject inline inside the active bubble."""
        if self._active_bubble:
            self._active_bubble.add_inline_status(tool_name, description, arguments, index, tool_call_id)
            self._throttled_scroll_end()
            return
        # Fallback: no active bubble — mount as a standalone sibling
        display_args = _format_tool_args(tool_name, arguments)
        label = f"Calling tool: {tool_name}{display_args}"
        if description:
            label += f" ({description})"
        msg = SystemMessage(label)
        self.mount(msg)
        self._message_widgets.append(msg)
        self.scroll_end(animate=False)

    def show_retry_status(self, message: str, delay: float) -> None:
        self.remove_retry_status()
        if self._active_bubble:
            self._active_bubble.remove_loading()
        self._retry_status = RetryStatus(message, delay)
        self.mount(self._retry_status)
        self.scroll_end(animate=False)

    def remove_retry_status(self) -> None:
        if self._retry_status:
            self._retry_status.remove()
            self._retry_status = None

    def add_error(self, message: str) -> None:
        self.remove_retry_status()
        if self._active_bubble:
            self._active_bubble.finalize()
            self._active_bubble = None
        self._evict_if_needed()
        msg = ErrorMessage(message)
        self.mount(msg)
        self._message_widgets.append(msg)
        self.scroll_end(animate=False)

    def clear(self) -> None:
        self.remove_children()
        self._active_bubble = None
        self._retry_status = None
        self._message_widgets.clear()
