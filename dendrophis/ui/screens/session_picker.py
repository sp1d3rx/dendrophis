"""SessionPickerScreen — searchable list of available sessions."""

from __future__ import annotations

import json
import lzma
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from rich.table import Table
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class SessionPickerScreen(ModalScreen[str]):
    """Modal for switching to a previous saved session."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss_modal", "Cancel"),
        ("delete,d", "delete_session", "Delete"),
    ]

    DEFAULT_CSS = """
    SessionPickerScreen {
        align: center middle;
    }
    #picker-container {
        width: 90%;
        height: 90%;
        max-width: 140;
        max-height: 45;
        border: thick $accent;
        background: $panel;
        padding: 1;
        overflow: hidden;
    }
    #session-list {
        height: 1fr;
        border: solid $panel-lighten-1;
        margin-top: 1;
        background: $surface;
    }
    #picker-status {
        color: $text-muted;
        height: 1;
        margin: 0 1;
    }
    .panel-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._all_sessions: list[dict] = []
        self._is_loading = False

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-container"):
            yield Static("Select Saved Session to Resume", classes="panel-title")
            yield Input(placeholder="Search sessions (by ID, model, date, or prompt)...", id="session-search")
            yield Label("", id="picker-status")
            yield OptionList(id="session-list")

    def on_mount(self) -> None:
        """Populate the session list and focus search input."""
        self.query_one("#session-search", Input).focus()
        self.run_worker(self._load_sessions())

    async def _load_sessions(self) -> None:
        """Fetch sessions in the background and update the UI."""
        if self._is_loading:
            return
        self._is_loading = True
        status_label = self.query_one("#picker-status", Label)
        status_label.update("Scanning session files...")

        try:
            sessions_directory = Path.home() / ".config" / "dendrophis" / "sessions"
            if not sessions_directory.exists():
                status_label.update("No saved sessions directory found.")
                return

            session_files = sorted(
                sessions_directory.glob("session-*.json*"),
                key=lambda session_file: session_file.stat().st_mtime,
                reverse=True,
            )

            # Limit to the last 50 sessions for performance
            recent_files = session_files[:50]
            loaded_sessions: list[dict] = []

            for session_file in recent_files:
                try:
                    if session_file.suffix == ".xz":
                        with lzma.open(session_file, "rb") as file_handle:
                            session_data = json.loads(file_handle.read().decode())
                    else:
                        session_data = json.loads(session_file.read_text())

                    session_id = session_data.get("session_id", "unknown")
                    timestamp = session_data.get("timestamp", "")
                    model = session_data.get("model", "unknown")
                    messages = session_data.get("messages", [])

                    # Find first user message preview
                    first_user_prompt = ""
                    for message in messages:
                        if message.get("role") == "user":
                            content = message.get("content", "")
                            if isinstance(content, list):
                                content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
                            first_user_prompt = content.strip().replace("\n", " ")
                            break

                    message_count = len([message for message in messages if message.get("role") != "system"])

                    loaded_sessions.append(
                        {
                            "path": str(session_file),
                            "session_id": session_id,
                            "timestamp": timestamp,
                            "model": model,
                            "message_count": message_count,
                            "preview": first_user_prompt,
                        }
                    )
                except Exception:
                    # Skip corrupt files silently
                    continue

            self._all_sessions = loaded_sessions
            if self._all_sessions:
                status_label.update(f"Loaded {len(self._all_sessions)} recent sessions.")
            else:
                status_label.update("No saved sessions found.")
            self._update_list("")

        except Exception as loading_error:
            status_label.update(f"Error loading sessions: {loading_error}")
        finally:
            self._is_loading = False

    def _update_list(self, filter_text: str) -> None:
        """Rebuild the session list based on filter_text using card-like Panels."""
        option_list = self.query_one("#session-list", OptionList)
        option_list.clear_options()

        filter_text = filter_text.lower()

        for session_item in self._all_sessions:
            session_id = session_item["session_id"]
            short_id = session_id[:8]
            model = session_item["model"]
            preview = session_item["preview"]
            timestamp = session_item["timestamp"]
            message_count = session_item["message_count"]

            # Parse ISO timestamp to a readable date/time
            readable_time = ""
            if timestamp:
                try:
                    parsed_time = datetime.fromisoformat(timestamp)
                    readable_time = parsed_time.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    readable_time = timestamp[:19].replace("T", " ")

            match_text = f"{short_id} {model} {preview} {readable_time}".lower()

            if not filter_text or filter_text in match_text:
                from rich.box import ROUNDED
                from rich.console import Group
                from rich.panel import Panel

                card_elements = []

                # Header: short ID, message count (left) and Date/Time (right)
                header_table = Table.grid(expand=True)
                header_table.add_column()
                header_table.add_column(justify="right")

                is_current = session_id == self._session.session_id
                current_badge = " [green bold](Current)[/]" if is_current else ""

                plural_suffix = "s" if message_count != 1 else ""
                messages_count_text = f"{message_count} message{plural_suffix}"

                id_text = f"[bold cyan]Session {short_id}[/]{current_badge}  •  [yellow]{messages_count_text}[/]"
                date_text = f"[cyan]{readable_time}[/]"
                header_table.add_row(id_text, date_text)
                card_elements.append(header_table)

                # Line 2: Model
                model_display = model.split("/")[-1]
                card_elements.append(f"[dim]Model:[/] [bold white]{model_display}[/]")

                # Line 3: Prompt preview
                if preview:
                    preview_text = preview[:100] + "..." if len(preview) > 100 else preview
                    card_elements.append(f"[dim]Prompt:[/] [italic]{preview_text}[/]")
                else:
                    card_elements.append("[dim italic](No messages)[/]")

                # Wrap card elements in a Panel
                border_color = "green" if is_current else "blue"
                panel = Panel(
                    Group(*card_elements),
                    border_style=border_color,
                    box=ROUNDED,
                    expand=True,
                )

                option_list.add_option(Option(panel, id=session_item["path"]))

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the session list as the user types."""
        if event.input.id == "session-search":
            self._update_list(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the selected session's path."""
        if event.option.id:
            self.dismiss(str(event.option.id))

    def action_dismiss_modal(self) -> None:
        """Close the picker without changing the session."""
        self.dismiss()

    def action_delete_session(self) -> None:
        """Delete the highlighted session if it has 0 messages."""
        option_list = self.query_one("#session-list", OptionList)
        highlighted_index = option_list.highlighted
        if highlighted_index is None or highlighted_index < 0:
            return

        option = option_list.get_option_at_index(highlighted_index)
        session_path = option.id
        if not session_path:
            return

        session_item = next((session for session in self._all_sessions if session["path"] == session_path), None)
        if not session_item:
            return

        status_label = self.query_one("#picker-status", Label)

        if session_item["message_count"] == 0:
            try:
                # Delete from disk
                path = Path(session_path)
                if path.exists():
                    path.unlink()

                # Remove from self._all_sessions
                self._all_sessions = [session for session in self._all_sessions if session["path"] != session_path]

                # Update status
                status_label.update(f"Deleted session {session_item['session_id'][:8]}.")

                # Refresh list
                search_input = self.query_one("#session-search", Input)
                self._update_list(search_input.value)

            except Exception as delete_error:
                status_label.update(f"Error deleting session: {delete_error}")
        else:
            self.notify("Only sessions with 0 messages can be deleted", severity="warning")
