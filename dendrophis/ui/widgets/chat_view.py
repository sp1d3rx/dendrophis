"""ChatView — scrollable streaming message log."""

from __future__ import annotations

import json
import re
import time
from collections import deque
from collections.abc import Callable
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


# Think-tag pairs — order matters: longer/more-specific first.
# Each entry is (open_tag, close_tag).
_THINK_TAG_PAIRS: list[tuple[str, str]] = [
    ("<|channel>thought\n", "<channel|>"),  # Gemma 4
    ("<think>", "</think>"),  # DeepSeek / generic
]
_OPEN_TAGS = [pair[0] for pair in _THINK_TAG_PAIRS]
_CLOSE_TAGS = [pair[1] for pair in _THINK_TAG_PAIRS]


def _clean_latex_shorthand(text: str) -> str:
    """Replace common LaTeX symbols with plain text/unicode equivalents."""
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
    }
    cleaned = text
    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned)
    return cleaned


def _format_tool_args(tool_name: str, arguments: str) -> str:
    """Return a compact Rich-markup string of the key argument for a tool call."""
    try:
        if not arguments:
            return ""
        args = json.loads(arguments)
        if tool_name == "bash":
            cmd = args.get("command", "")
            if len(cmd) > 60:
                cmd = cmd[:57] + "…"
            return f" [cyan]{escape(cmd)}[/cyan]"
        if tool_name in ("glob", "ripgrep"):
            return f" [cyan]{escape(args.get('pattern', ''))}[/cyan]"
        if tool_name == "search_memory":
            query = args.get("query", "")
            if len(query) > 60:
                query = query[:57] + "…"
            tag = args.get("tag")
            tag_suffix = f" [dim]({tag})[/dim]" if tag else ""
            return f" [cyan]{escape(query)}[/cyan]{tag_suffix}"
        if tool_name in ("recall_memory", "delete_memory"):
            memory_id = args.get("memory_id", "")
            return f" [cyan]{escape(memory_id)}[/cyan]"
        if tool_name == "save_memory":
            content = args.get("content", "")
            if len(content) > 60:
                content = content[:57] + "…"
            return f" [cyan]{escape(content)}[/cyan]"
        if tool_name == "ask_multiple_choice":
            question = args.get("question", "")
            if len(question) > 60:
                question = question[:57] + "…"
            return f" [cyan]{escape(question)}[/cyan]"
        if tool_name == "invoke_subagent":
            agent = args.get("agent", "")
            task = args.get("task", "")
            if len(task) > 50:
                task = task[:47] + "…"
            return f" [cyan]{escape(agent)}[/cyan] [dim]{escape(task)}[/dim]"

        if tool_name in ("read", "edit", "write", "analyze_functions", "get_function", "replace_function"):
            path = args.get("file_path", "")
            try:
                rel = str(Path(path).relative_to(Path.cwd()))
            except ValueError:
                rel = path
            if len(rel) > 60:
                rel = "…" + rel[-57:]

            suffix = ""
            if tool_name == "read":
                offset = args.get("offset")
                limit = args.get("limit")

                if offset is None:
                    offset = 1
                if limit is None:
                    limit = 2000

                try:
                    offset_val = int(offset)
                    if str(limit).lower() == "all":
                        line_range = f"[{offset_val}:all]"
                    else:
                        limit_val = int(limit)
                        end_line = offset_val + limit_val - 1
                        line_range = f"[{offset_val}:{end_line}]"
                except (ValueError, TypeError):
                    line_range = f"[{offset}:{limit}]"
                suffix = f"[dim] {line_range}[/dim]"
            elif tool_name in ("get_function", "replace_function"):
                func = args.get("function_name", "")
                suffix = f" [dim]({func})[/dim]"

            return f" [cyan]{escape(rel)}[/cyan]{suffix}"

        # Fallback for any other tools: check common keys
        for key in ("file_path", "path", "command", "pattern", "query", "memory_id", "agent", "question"):
            val = args.get(key)
            if val:
                val_str = str(val)
                if len(val_str) > 60:
                    val_str = val_str[:57] + "…"
                return f" [cyan]{escape(val_str)}[/cyan]"
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
    #thought-text {
        color: $text-muted;
        width: 100%;
        height: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._has_label = False

    def compose(self) -> ComposeResult:
        yield Static("", id="thought-text")

    def append_text(self, text: str) -> None:
        self._parts.append(text)
        joined = "".join(self._parts)
        if not joined.strip():
            return
        if not self._has_label:
            self.query_one("#thought-text", Static).update(f"[bold]Thought:[/bold]\n{escape(joined)}")
            self._has_label = True
        else:
            self.query_one("#thought-text", Static).update(escape(joined))
        self.styles.display = "block"

        # Force a height once we reach the limit to ensure the scrollbar activates
        # in the VerticalScroll container.
        if self.virtual_size.height > 6:
            self.styles.height = 6

        self.scroll_end(animate=False)


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

    def __init__(self) -> None:
        super().__init__()
        self._markdown = CustomMarkdown("")
        # All markdown segments (one per text chunk between tool calls)
        self._markdown_segments: list[CustomMarkdown] = [self._markdown]
        # Text across ALL segments (used for copy-all)
        self._all_parts: list[str] = []
        # Text for the CURRENT segment only
        self._clean_parts: list[str] = []
        self._clean_text = ""
        self._model_id = ""
        self._has_reasoning = False
        self._loading = LoadingIndicator()
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
        self._markdown.update("".join(self._clean_parts))

    def _schedule_render(self) -> None:
        """Schedule a markdown render, throttled to every 250 ms."""
        if not self._render_pending:
            self._render_pending = True
            self.set_timer(0.25, self._do_throttled_render)

    def _do_throttled_render(self) -> None:
        """Perform the actual render, but skip if finalize() already ran."""
        self._render_pending = False
        if not self._finalized:
            self._render_clean()

    def append_delta(self, delta: str) -> None:
        """Route an incoming text delta through the think-tag state machine."""
        self.remove_loading()
        # Clean LaTeX shorthand before processing
        delta = _clean_latex_shorthand(delta)
        self._process_delta(delta)

    def _process_delta(self, delta: str) -> None:
        """State machine: splits delta into reasoning and response text.

        Handles tags that arrive split across multiple streaming chunks by
        buffering the tail of each chunk when it's a prefix of a known tag.
        Supports both  … (DeepSeek) and
        <|channel>thought\\n…<channel|> (Gemma 4).

        Also strips synthesized <tool_call> tags (from local models) from display.
        Backtick-aware: tags inside `code` or ```code``` blocks are treated as text.
        """
        text = self._pending_buf + delta
        self._pending_buf = ""

        # State: None, '`' (in inline code), or '```' (in code block)
        code_state = getattr(self, "_code_state", None)

        i = 0
        while i < len(text):
            # Update code state by scanning from current position
            if code_state is None:
                # Look for backticks
                inline_bt = text.find("`", i)
                block_bt = text.find("```", i)

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
                    self._process_text_segment(text[i:], code_state)
                    break

                # Process text before backtick
                if next_bt > i:
                    self._process_text_segment(text[i:next_bt], code_state)

                # Enter code state
                if next_bt == block_bt:
                    code_state = "```"
                    self._route_text("```")
                    i = next_bt + 3
                else:
                    code_state = "`"
                    self._route_text("`")
                    i = next_bt + 1

            elif code_state == "`":
                # Look for closing inline backtick
                close_bt = text.find("`", i)
                if close_bt == -1:
                    # Still in inline code, emit rest as text
                    self._route_text(text[i:])
                    break
                # Emit code content as text
                self._route_text(text[i : close_bt + 1])
                code_state = None
                i = close_bt + 1

            elif code_state == "```":
                # Look for closing code block
                close_bt = text.find("```", i)
                if close_bt == -1:
                    # Still in code block, emit rest as text
                    self._route_text(text[i:])
                    break
                # Emit code content as text
                self._route_text(text[i : close_bt + 3])
                code_state = None
                i = close_bt + 3

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
            best_pos, best_tag = len(remaining), ""
            for tag in _CLOSE_TAGS:
                pos = remaining.find(tag)
                if 0 <= pos < best_pos:
                    best_pos, best_tag = pos, tag

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
        while remaining:
            # Find earliest think tag or tool_call tag
            best_pos, best_tag, tag_type = len(remaining), "", ""

            for tag in _OPEN_TAGS:
                pos = remaining.find(tag)
                if 0 <= pos < best_pos:
                    best_pos, best_tag = pos, tag
                    tag_type = "think"

            tool_pos = remaining.find("<tool_call>")
            if tool_pos != -1 and tool_pos < best_pos:
                best_pos, best_tag = tool_pos, "<tool_call>"
                tag_type = "tool"

            if not best_tag:
                # No tags found, emit all as text
                buffered = self._try_buffer_partial(remaining, [*_OPEN_TAGS, "<tool_call>"], self._route_text)
                if not buffered:
                    self._route_text(remaining)
                return

            # Emit text before tag
            if best_pos:
                self._route_text(remaining[:best_pos])

            if tag_type == "think":
                self._in_think_tag = True
                remaining = remaining[best_pos + len(best_tag) :]
                # Process remaining text (may contain close tag)
                if remaining:
                    self._process_think_text(remaining)
                return

            # tool_call handling
            tool_end = remaining.find("</tool_call>", best_pos)
            if tool_end != -1:
                remaining = remaining[tool_end + len("</tool_call>") :]
            else:
                self._pending_buf = remaining[best_pos:]
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
        current = "".join(self._clean_parts).strip()
        if current:
            self._markdown.update(current)
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
        if existing_status is not None:
            self.mount(msg, after=existing_status)
        else:
            self.mount(msg, before=self._markdown)

    # ── Finalise ──────────────────────────────────────────────────────────────

    def finalize(self) -> None:
        """Render final markdown and inject copy buttons above each code fence.

        Also flushes any buffered think-tag text (_pending_buf) so it isn't
        silently dropped if the stream ended mid-tag (e.g. on error).
        """
        self._finalized = True
        self.remove_loading()

        # Explicitly end thinking state to prevent cross-session contamination
        if self._in_think_tag:
            self._in_think_tag = False
            self._route_reasoning("</think>")  # Close any open think tag

        # Flush any buffered partial tag text before finalizing
        if self._pending_buf:
            if self._in_think_tag:  # Should be False now, but handle just in case
                self._route_reasoning(self._pending_buf)
            else:
                self._route_text(self._pending_buf)
            self._pending_buf = ""

        if not self._all_parts and not self._has_reasoning:
            self.remove()
            return

        # Full text across all segments (for copy-all via AssistantLabel)
        self._clean_text = "".join(self._all_parts).strip()
        # Remaining text for the last segment
        remaining = "".join(self._clean_parts).strip()

        async def _update_and_inject() -> None:
            if remaining:
                await self._markdown.update(remaining)
            # Inject copy buttons into every markdown segment
            for md in self._markdown_segments:
                for child in list(md.children):
                    if isinstance(child, MarkdownFence) and child.code:
                        await md.mount(CopyCodeButton(child.code), before=child)

        self.run_worker(_update_and_inject())


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
        yield Static("⚠️ Error", classes="label")
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
        super().__init__(f"⚙️ {text}")


class ToolResultMessage(Static):
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

    def on_click(self) -> None:
        """Toggle expanded view on click."""
        self._expanded = not self._expanded
        self.recompose()

    def compose(self) -> ComposeResult:
        """Render tool name with key arguments; show error detail only on repeated failures."""
        display_args = _format_tool_args(self._tool_name, self._arguments)
        label = f"{self._tool_name}{display_args}"
        if self._description:
            label = f"{label} ({self._description})"

        if self._is_error:
            yield Static(f"[error]●[/error] {label}", classes="error")
            if self._show_detail or self._expanded:
                yield Static(self._content, markup=False, classes="content")
        else:
            yield Static(f"[success]●[/success] {label}", classes="success")
            if (self._tool_name == "bash" or self._expanded) and self._content:
                yield Static(self._content, markup=False, classes="content")


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

    def start_assistant_message(self, model_id: str = "") -> None:
        self.remove_retry_status()
        bubble = AssistantMessage()
        bubble._model_id = model_id
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
