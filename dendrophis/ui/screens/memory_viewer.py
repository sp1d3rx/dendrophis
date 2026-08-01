"""MemoryViewerScreen — searchable list and details of saved memories."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea

if TYPE_CHECKING:
    from dendrophis.session.session import Session


class MemoryViewerScreen(ModalScreen[None]):
    """Modal for browsing, searching, and viewing saved memories."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss_modal", "Close"),
    ]

    DEFAULT_CSS = """
    MemoryViewerScreen {
        align: center middle;
    }
    #memory-picker-container {
        width: 95%;
        height: 90%;
        max-width: 160;
        max-height: 50;
        border: thick $accent;
        background: $panel;
        padding: 1;
        overflow: hidden;
    }
    #memory-list-container {
        width: 50%;
        height: 1fr;
        border-right: solid $panel-lighten-1;
        margin-top: 1;
        margin-right: 1;
        background: $surface;
        overflow-y: auto;
    }
    #memory-detail-container {
        width: 50%;
        height: 1fr;
        margin-top: 1;
        background: $surface;
        overflow-y: auto;
    }
    #memory-detail-container.hidden {
        display: none;
    }
    .detail-panel {
        width: 100%;
        height: 1fr;
    }
    .detail-panel.hidden {
        display: none;
    }
    #memory-picker-status {
        color: $text-muted;
        height: 1;
        margin: 0 1;
    }
    .panel-title {
        text-style: bold;
        margin-bottom: 1;
    }
    .memory-card {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0;
        border-bottom: solid $panel-lighten-1;
        background: $surface;
    }
    .memory-card:hover {
        background: $boost;
    }
    .memory-card.selected {
        background: $boost;
        border-bottom: solid $accent;
    }
    .detail-content {
        width: 100%;
        height: auto;
        padding: 1;
        border: solid $panel-lighten-1;
        background: $surface;
        margin-top: 1;
        overflow-y: auto;
    }
    .action-buttons {
        width: 100%;
        height: auto;
        margin-top: 1;
    }
    .action-buttons Button {
        width: 48%;
    }
    #edit-textarea {
        width: 100%;
        height: 1fr;
    }
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._selected_memory_id: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-picker-container"):
            yield Static("Browse & Search Memories", classes="panel-title")
            yield Input(placeholder="Search memories (semantic & keyword search)...", id="memory-search")
            yield Label("", id="memory-picker-status")
            with Horizontal(id="memory-picker-body"):
                with Vertical(id="memory-list-container"):
                    yield Label("Memories", id="memory-list-title")
                with Vertical(id="memory-detail-container", classes="hidden"):
                    # View panel — always in DOM
                    with Vertical(id="detail-view-panel", classes="detail-panel"):
                        yield Label("Memory Details", id="detail-title", classes="panel-title")
                        yield Static("", id="detail-content", classes="detail-content")
                        with Horizontal(id="view-actions", classes="action-buttons"):
                            yield Button("Edit", id="detail-edit")
                            yield Button("Delete", id="detail-delete")
                    # Edit panel — always in DOM
                    with Vertical(id="detail-edit-panel", classes="detail-panel hidden"):
                        yield TextArea(text="", id="edit-textarea")
                        yield Input(
                            placeholder="Tags (comma-separated), e.g. python, debugging",
                            id="edit-tags",
                        )
                        with Horizontal(id="edit-actions", classes="action-buttons"):
                            yield Button("Save Changes", id="edit-save")
                            yield Button("Cancel", id="edit-cancel")

    def on_mount(self) -> None:
        """Populate the memory list and focus search input."""
        self.query_one("#memory-search", Input).focus()
        self._update_list("")

    def _update_list(self, filter_text: str) -> None:
        """Rebuild the memory list based on search/filter criteria."""
        memory_store = self._session.memory_store
        status_label = self.query_one("#memory-picker-status", Label)
        list_container = self.query_one("#memory-list-container", Vertical)

        # Remove old card buttons only, keep title label
        list_container.remove_children(Button)

        if not memory_store:
            status_label.update("[red]Memory store is not available.[/red]")
            return

        filter_text = filter_text.strip()
        try:
            if not filter_text:
                # Load recent memories
                memories = memory_store.list_memories(limit=50)
                status_label.update(f"Loaded {len(memories)} recent memories.")
                scored_memories = [(1.0, memory_item, "saved") for memory_item in memories]
            else:
                from dendrophis.memory.search import MemorySearcher

                searcher = MemorySearcher(memory_store)
                results = searcher.search(query=filter_text, limit=50)
                status_label.update(f"Found {len(results)} matching memories for query.")
                scored_memories = [
                    (result_item.score, result_item.memory, result_item.method) for result_item in results
                ]

            for score, memory_item, _method in scored_memories:
                memory_id = memory_item.id
                short_id = memory_id[:8]
                content = memory_item.content
                tags = memory_item.tags
                created_at = memory_item.created_at
                source = memory_item.source

                readable_time = ""
                if created_at:
                    try:
                        parsed_time = datetime.fromisoformat(created_at)
                        readable_time = parsed_time.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        readable_time = created_at[:19].replace("T", " ")

                # Build compact card content
                card_parts = []

                # Header line: ID/Source | Time
                header = f"[bold cyan]Memory {short_id}[/] [dim]•[/] [cyan]{escape(source)}[/]"
                if filter_text:
                    header += f" [dim]•[/] [yellow]score:{score:.4f}[/]"
                if readable_time:
                    header += f" [dim]•[/] [dim]{readable_time}[/]"
                card_parts.append(header)

                # Content preview (truncated)
                preview_length = 180
                preview_text = content[:preview_length] + "..." if len(content) > preview_length else content
                card_parts.append(f"[white]{escape(preview_text)}[/white]")

                # Tags line
                if tags:
                    tags_str = ", ".join(f"#{escape(tag_item)}" for tag_item in tags)
                    card_parts.append(f"[dim]Tags:[/] [italic green]{tags_str}[/]")

                card_text = "\n".join(card_parts)

                # Create button as a card (no ID - store memory_id as attribute)
                card_button = Button(card_text)
                card_button._memory_id = memory_id
                card_button.add_class("memory-card")
                if self._selected_memory_id == memory_id:
                    card_button.add_class("selected")
                list_container.mount(card_button)

        except Exception as error:
            status_label.update(f"[red]Error searching memories: {error}[/red]")

    def _show_detail(self, memory_item) -> None:
        """Display the full detail panel for a memory."""
        if not memory_item:
            return

        self._selected_memory_id = memory_item.id

        # Show the detail container
        detail_container = self.query_one("#memory-detail-container", Vertical)
        detail_container.remove_class("hidden")

        # Show view panel, hide edit panel
        self.query_one("#detail-view-panel", Vertical).remove_class("hidden")
        self.query_one("#detail-edit-panel", Vertical).add_class("hidden")

        # Build detail content
        detail_parts = []

        # Header info
        detail_parts.append(f"[bold cyan]Memory ID:[/bold cyan] {memory_item.id}")
        detail_parts.append(f"[bold cyan]Source:[/bold cyan] {memory_item.source}")
        if memory_item.project_id:
            detail_parts.append(f"[bold cyan]Project:[/bold cyan] {memory_item.project_id}")
        if memory_item.session_id:
            detail_parts.append(f"[bold cyan]Session:[/bold cyan] {memory_item.session_id}")
        if memory_item.created_at:
            detail_parts.append(f"[bold cyan]Created:[/bold cyan] {memory_item.created_at}")
        if memory_item.updated_at:
            detail_parts.append(f"[bold cyan]Updated:[/bold cyan] {memory_item.updated_at}")
        if memory_item.score:
            detail_parts.append(f"[bold cyan]Score:[/bold cyan] {memory_item.score}")

        # Tags
        if memory_item.tags:
            tags_str = ", ".join(f"#{escape(tag_item)}" for tag_item in memory_item.tags)
            detail_parts.append(f"[bold cyan]Tags:[/bold cyan] {tags_str}")

        # Separator and content
        detail_parts.append("")
        detail_parts.append("[bold]Content:[/bold]")
        detail_parts.append(escape(memory_item.content))

        detail_text = "\n".join(detail_parts)
        detail_static = self.query_one("#detail-content", Static)
        detail_static.update(detail_text)

        # Update card selection state
        list_container = self.query_one("#memory-list-container", Vertical)
        for widget in list_container.query("Button"):
            widget.remove_class("selected")
            if getattr(widget, "_memory_id", None) == memory_item.id:
                widget.add_class("selected")

    def _clear_detail(self) -> None:
        """Clear the detail panel and selection."""
        self._selected_memory_id = None

        # Clear detail content in place
        detail_static = self.query_one("#detail-content", Static)
        detail_static.update("")

        # Hide the detail container entirely
        detail_container = self.query_one("#memory-detail-container", Vertical)
        detail_container.add_class("hidden")

        list_container = self.query_one("#memory-list-container", Vertical)
        for widget in list_container.query("Button"):
            widget.remove_class("selected")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the memory list as the user types."""
        if event.input.id == "memory-search":
            self._update_list(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle card selection, edit, and delete actions."""
        button_id = event.button.id

        # Card selection (cards store memory_id as custom attribute)
        memory_id = getattr(event.button, "_memory_id", None)
        if memory_id:
            if self._session.memory_store:
                memory_item = self._session.memory_store.get_memory(memory_id)
                if memory_item:
                    self._show_detail(memory_item)
            return

        # Edit button (view panel)
        if button_id == "detail-edit":
            self._start_edit()
            return

        # Delete button (view panel)
        if button_id == "detail-delete":
            self._delete_memory()
            return

        # Save button (edit panel)
        if button_id == "edit-save":
            self._save_edit()
            return

        # Cancel button (edit panel)
        if button_id == "edit-cancel":
            self._cancel_edit()
            return

    def _start_edit(self) -> None:
        """Switch the detail panel to edit mode."""
        if not self._selected_memory_id or not self._session.memory_store:
            return

        memory_item = self._session.memory_store.get_memory(self._selected_memory_id)
        if not memory_item:
            return

        # Hide view panel, show edit panel
        self.query_one("#detail-view-panel", Vertical).add_class("hidden")
        self.query_one("#detail-edit-panel", Vertical).remove_class("hidden")

        # Populate edit fields
        edit_textarea = self.query_one("#edit-textarea", TextArea)
        edit_textarea.text = memory_item.content

        tags_input = self.query_one("#edit-tags", Input)
        tags_input.value = ", ".join(memory_item.tags) if memory_item.tags else ""

    def _save_edit(self) -> None:
        """Save edited memory content and tags."""
        if not self._selected_memory_id or not self._session.memory_store:
            return

        edit_textarea = self.query_one("#edit-textarea", TextArea)
        new_content = edit_textarea.text

        # Parse tags from comma-separated input
        tags_input = self.query_one("#edit-tags", Input)
        new_tags = [tag.strip().lstrip("#") for tag in tags_input.value.split(",") if tag.strip()]

        # Update the memory
        self._session.memory_store.update_memory(self._selected_memory_id, content=new_content, tags=new_tags)

        # Refresh the list and show detail again
        self._update_list(self.query_one("#memory-search", Input).value)
        self._show_detail(self._session.memory_store.get_memory(self._selected_memory_id))
        self.query_one("#memory-search", Input).focus()

    def _cancel_edit(self) -> None:
        """Cancel editing and restore detail view."""
        if not self._selected_memory_id or not self._session.memory_store:
            return

        memory_item = self._session.memory_store.get_memory(self._selected_memory_id)
        if not memory_item:
            return

        # Switch back to view panel
        self.query_one("#detail-view-panel", Vertical).remove_class("hidden")
        self.query_one("#detail-edit-panel", Vertical).add_class("hidden")

        # Rebuild detail content
        self._update_list(self.query_one("#memory-search", Input).value)
        self._show_detail(memory_item)

    def _delete_memory(self) -> None:
        """Delete the currently selected memory."""
        if not self._selected_memory_id or not self._session.memory_store:
            return

        memory_id = self._selected_memory_id
        self._session.memory_store.delete_memory(memory_id)

        # Refresh the list and clear detail
        self._update_list("")
        self._clear_detail()
        self.query_one("#memory-search", Input).focus()

    def action_dismiss_modal(self) -> None:
        """Close the memory viewer modal."""
        self.dismiss()
