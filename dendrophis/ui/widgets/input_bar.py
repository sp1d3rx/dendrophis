"""InputBar — multi-line prompt input with @file and /command completion."""

from __future__ import annotations

import re
from pathlib import Path

from textual.message import Message
from textual.widgets import OptionList, TextArea

_AT_PATTERN = re.compile(r"@(\S+)")
_SLASH_PATTERN = re.compile(r"^/(\w*)$")  # /command at start of line


def _resolve_file_refs(text: str) -> tuple[str, list[Path]]:
    """Extract @file references from text, return (cleaned_text, paths)."""
    paths: list[Path] = []
    clean = text
    for match in _AT_PATTERN.finditer(text):
        raw_path = match.group(1)
        path_obj = Path(raw_path).expanduser()
        if path_obj.exists() and path_obj.is_file():
            paths.append(path_obj)
            clean = clean.replace(match.group(0), "", 1)
    return clean.strip(), paths


class FileAutocomplete(OptionList):
    """Floating list of file suggestions using OptionList."""

    DEFAULT_CSS = """
    FileAutocomplete {
        width: 44;
        height: auto;
        max-height: 14;
        background: $surface;
        border: solid $primary;
        border-title-align: left;
        display: none;
        layer: top;
        dock: bottom;
        offset: 2 -6;
        /* Custom scrollbar for a sleeker look */
        scrollbar-size-vertical: 1;
    }
    FileAutocomplete > .option-list--option-highlighted {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    """
    can_focus = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.border_title = "Files (@)"

    def set_suggestions(self, suggestions: list[str], kind: str = "file") -> None:
        self.clear_options()
        if not suggestions:
            self.styles.display = "none"
            return

        self.border_title = "Commands (/)" if kind == "command" else "Files (@)"
        for item in suggestions:
            self.add_option(item)

        self.styles.display = "block"
        self.highlighted = 0

    @property
    def selected(self) -> str | None:
        if self.highlighted is not None and 0 <= self.highlighted < self.option_count:
            return str(self.get_option_at_index(self.highlighted).prompt)
        return None


class InputBar(TextArea):
    """Multi-line input; Enter sends, Shift+Enter inserts newline."""

    DEFAULT_CSS = """
    InputBar {
        height: 5;
        border-top: solid $primary 50%;
        background: $panel;
        padding: 0 1;
    }
    InputBar:focus {
        border-top: solid $primary;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._history_index: int = -1
        self._draft: str = ""
        self._completing: bool = False

    class Submitted(Message):
        """Posted when the user submits a prompt."""

        def __init__(self, text: str, file_paths: list[Path]) -> None:
            super().__init__()
            self.text = text
            self.file_paths = file_paths

    async def _on_key(self, event: object) -> None:
        from textual.events import Key

        if not isinstance(event, Key):
            return

        # Autocomplete interaction takes priority when active
        if self._completing:
            if event.key == "tab":
                event.prevent_default()
                self._apply_suggestion()
                return
            if event.key == "enter":
                event.prevent_default()
                self._apply_suggestion()
                return
            if event.key in ("up", "down"):
                event.prevent_default()
                delta = -1 if event.key == "up" else 1
                self.post_message(self.NavigateAutocomplete(delta))
                return
            if event.key == "escape":
                event.prevent_default()
                self._close_autocomplete()
                return

        # Handle normal key events — track whether InputBar consumed the event
        handled = True

        if event.key == "enter":
            # Enter sends, Shift+Enter inserts newline
            if event.name == "enter":
                event.prevent_default()
                self._submit()
            else:
                handled = False  # Shift+Enter → let base class insert newline

        elif event.key == "up":
            # Cycle history if at the top of the buffer
            if self.cursor_location[0] == 0:
                if self._history_index == -1:
                    self._draft = self.text
                if self._history_index < len(self._history) - 1:
                    event.prevent_default()
                    self._history_index += 1
                    self.text = self._history[-(self._history_index + 1)]
                    self.move_cursor((len(self.text.splitlines()), 0))
                else:
                    handled = False  # No more history to cycle
            else:
                handled = False  # Not at top → let base class move cursor up

        elif event.key == "down":
            # Cycle history if at the bottom of the buffer
            if self.cursor_location[0] >= len(self.text.splitlines()) - 1:
                if self._history_index > 0:
                    event.prevent_default()
                    self._history_index -= 1
                    self.text = self._history[-(self._history_index + 1)]
                    self.move_cursor((len(self.text.splitlines()), 0))
                elif self._history_index == 0:
                    event.prevent_default()
                    self._history_index = -1
                    self.text = self._draft
                    self.move_cursor((len(self.text.splitlines()), 0))
                else:
                    handled = False  # No history to cycle through
            else:
                handled = False  # Not at bottom → let base class move cursor down

        else:
            handled = False  # All other keys → let base class handle

        # Only delegate to base class for keys InputBar did NOT handle
        if not handled:
            await super()._on_key(event)

    def on_descendant_blur(self) -> None:
        self._close_autocomplete()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Detect @ or / prefix and trigger autocomplete."""
        # Get text before cursor
        row, col = self.cursor_location
        lines = self.text.splitlines()
        if not lines:
            lines = [""]

        if row >= len(lines):
            self._close_autocomplete()
            return

        current_line = lines[row][:col]

        # Check for /command at start of line
        slash_match = _SLASH_PATTERN.search(current_line)
        if slash_match and row == 0 and col <= len(current_line):
            prefix = slash_match.group(1)
            self._completing = True
            self.post_message(self.RequestAutocomplete(prefix, kind="command"))
            return

        # Check for @file reference
        at_match = re.search(r"@(\S*)$", current_line)
        if at_match:
            prefix = at_match.group(1)
            self._completing = True
            self.post_message(self.RequestAutocomplete(prefix, kind="file"))
            return

        self._close_autocomplete()

    def _apply_suggestion(self) -> None:
        """Apply the currently selected suggestion."""
        self.post_message(self.SelectAutocomplete())

    def _close_autocomplete(self) -> None:
        self._completing = False
        self.post_message(self.RequestAutocomplete(None))

    class RequestAutocomplete(Message):
        def __init__(self, prefix: str | None, kind: str = "file") -> None:
            super().__init__()
            self.prefix = prefix
            self.kind = kind  # "file" or "command"

    class NavigateAutocomplete(Message):
        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

    class SelectAutocomplete(Message):
        pass

    def _submit(self) -> None:
        raw = self.text.strip()
        if not raw:
            return

        # Save to history if different from last entry
        if not self._history or raw != self._history[-1]:
            self._history.append(raw)
        self._history_index = -1
        self._draft = ""

        _, file_paths = _resolve_file_refs(raw)
        self.clear()
        # Post original raw text so @references are visible in chat UI
        self.post_message(self.Submitted(text=raw, file_paths=file_paths))
