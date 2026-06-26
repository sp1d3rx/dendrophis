"""MemoryViewerScreen — searchable list and details of saved memories."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from rich.table import Table
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

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
        width: 90%;
        height: 90%;
        max-width: 140;
        max-height: 45;
        border: thick $accent;
        background: $panel;
        padding: 1;
        overflow: hidden;
    }
    #memory-list {
        height: 1fr;
        border: solid $panel-lighten-1;
        margin-top: 1;
        background: $surface;
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
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-picker-container"):
            yield Static("Browse & Search Memories", classes="panel-title")
            yield Input(placeholder="Search memories (semantic & keyword search)...", id="memory-search")
            yield Label("", id="memory-picker-status")
            yield OptionList(id="memory-list")

    def on_mount(self) -> None:
        """Populate the memory list and focus search input."""
        self.query_one("#memory-search", Input).focus()
        self._update_list("")

    def _update_list(self, filter_text: str) -> None:
        """Rebuild the memory list based on search/filter criteria."""
        memory_store = self._session.memory_store
        status_label = self.query_one("#memory-picker-status", Label)
        option_list = self.query_one("#memory-list", OptionList)
        option_list.clear_options()

        if not memory_store:
            status_label.update("[red]Memory store is not available.[/red]")
            return

        filter_text = filter_text.strip()
        try:
            if not filter_text:
                # Load recent memories
                memories = memory_store.list_memories(limit=50)
                status_label.update(f"Loaded {len(memories)} recent memories.")

                # Format list items as raw memories
                scored_memories = [(1.0, memory_item, "list") for memory_item in memories]
            else:
                from dendrophis.memory.search import MemorySearcher

                searcher = MemorySearcher(memory_store)
                results = searcher.search(query=filter_text, limit=50)
                status_label.update(f"Found {len(results)} matching memories for query.")
                scored_memories = [
                    (result_item.score, result_item.memory, result_item.method) for result_item in results
                ]

            for score, memory_item, method in scored_memories:
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

                from rich.box import ROUNDED
                from rich.console import Group
                from rich.panel import Panel

                card_elements = []

                # Header grid
                header_table = Table.grid(expand=True)
                header_table.add_column()
                header_table.add_column(justify="right")

                method_suffix = f" ({method})" if filter_text else ""
                score_badge = f"  •  [yellow]Relevance: {score:.4f}{method_suffix}[/]" if filter_text else ""
                id_text = f"[bold cyan]Memory {short_id}[/]  •  [cyan]Source: {source}[/]{score_badge}"
                date_text = f"[cyan]{readable_time}[/]"
                header_table.add_row(id_text, date_text)
                card_elements.append(header_table)

                # Content Preview (or Full if reasonably short, else truncated)
                preview_length = 250
                preview_text = content[:preview_length] + "..." if len(content) > preview_length else content
                card_elements.append(f"\n[white]{preview_text}[/]")

                # Tags line
                if tags:
                    tags_str = ", ".join(f"#{tag_item}" for tag_item in tags)
                    card_elements.append(f"\n[dim]Tags:[/] [italic green]{tags_str}[/]")

                panel = Panel(
                    Group(*card_elements),
                    border_style="cyan",
                    box=ROUNDED,
                    expand=True,
                )

                option_list.add_option(Option(panel, id=memory_id))

        except Exception as error:
            status_label.update(f"[red]Error searching memories: {error}[/red]")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the memory list as the user types."""
        if event.input.id == "memory-search":
            self._update_list(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Open detailed memory content popup on select/press."""
        memory_id = event.option.id
        if not memory_id or not self._session.memory_store:
            return

        memory_item = self._session.memory_store.get_memory(str(memory_id))
        if memory_item:
            # Show the full memory content in a notification
            self.notify(
                f"Memory Details:\n{memory_item.content[:2000]}",
                title=f"Memory {memory_id[:8]}",
                severity="information",
                timeout=15.0,
            )

    def action_dismiss_modal(self) -> None:
        """Close the memory viewer modal."""
        self.dismiss()
