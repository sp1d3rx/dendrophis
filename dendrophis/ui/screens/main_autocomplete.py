"""Autocomplete and suggestion handling for MainScreen input."""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING

from dendrophis.ui.widgets.input_bar import FileAutocomplete, InputBar

if TYPE_CHECKING:
    from dendrophis.session.session import Session

_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)
_ARGUMENT_COMMANDS = frozenset({"/track", "/untrack", "/set", "/fork"})


def _find_matching_files(prefix: str, limit: int = 15) -> list[str]:
    """Walk the workspace pruning ignored directories and return matching relative paths."""
    normalized_prefix = prefix.lstrip("./")
    lower_prefix = normalized_prefix.lower()
    has_path_sep = "/" in normalized_prefix or os.sep in normalized_prefix

    candidates: list[str] = []
    max_candidates = limit * 6
    try:
        for root_dir, dir_names, file_names in os.walk("."):
            dir_names[:] = [
                directory_name
                for directory_name in dir_names
                if directory_name not in _EXCLUDED_DIRS and not directory_name.startswith(".git")
            ]
            rel_root = "" if root_dir == "." else root_dir.removeprefix("./").removeprefix(".\\")
            for file_name in file_names:
                rel_path = f"{rel_root}/{file_name}" if rel_root else file_name
                target_to_match = rel_path.lower() if has_path_sep else file_name.lower()
                if target_to_match.startswith(lower_prefix) or (lower_prefix and lower_prefix in rel_path.lower()):
                    candidates.append(rel_path)
                    if len(candidates) >= max_candidates:
                        break
            if len(candidates) >= max_candidates:
                break
    except OSError:
        return []

    candidates.sort(
        key=lambda candidate_path: (not candidate_path.lower().startswith(lower_prefix), len(candidate_path))
    )
    return candidates[:limit]


class MainScreenAutocomplete:
    """Mixin handling file and slash command autocompletions for MainScreen."""

    _session: Session

    def on_input_bar_request_autocomplete(self, event: InputBar.RequestAutocomplete) -> None:
        """Find matching files or commands and update the suggestion list."""
        autocomplete_widget = self.query_one(FileAutocomplete)
        if event.prefix is None:
            autocomplete_widget.set_suggestions([])
            return

        if event.kind == "command":
            self._complete_commands(autocomplete_widget, event.prefix)
        else:
            self._complete_files(autocomplete_widget, event.prefix)

    def _complete_commands(self, autocomplete_widget: FileAutocomplete, prefix: str) -> None:
        """Filter available slash commands by prefix."""
        primer_enabled = self._session.config.caching.pr_enabled
        commands = [
            ("/hello", "Greeting from Dendrophis"),
            ("/help", "Show this help message"),
        ]
        if primer_enabled:
            commands.append(("/clear", "Clear chat and reset context (primer re-injected)"))
            commands.append(("/fresh", "Clear chat without primer (truly fresh start)"))
        else:
            commands.append(("/clear", "Clear chat and reset context"))

        commands.extend(
            [
                ("/compact", "Manually compact context to reduce token usage"),
                ("/fork", "Fork current context into a new session branch"),
                ("/export", "Export conversation to markdown file"),
            ]
        )

        if primer_enabled:
            commands.extend(
                [
                    ("/save-primer", "Save project primer for future sessions"),
                    ("/load-primer", "Load the project primer and inject it into context"),
                    ("/track", "Add a file to the project primer"),
                    ("/untrack", "Remove a file from the project primer"),
                ]
            )

        commands.append(("/set", "Override the last assistant response"))

        if hasattr(self._session, "_skill_manager") and self._session._skill_manager:
            for skill_name, skill_instance in self._session._skill_manager._all_skills.items():
                description_lines = skill_instance.description.splitlines()
                first_line = description_lines[0] if description_lines else ""
                short_description = " ".join(first_line.split())[:60]
                commands.append((f"/{skill_name}", short_description))
        matched_commands = [
            (command_name, command_description)
            for command_name, command_description in commands
            if command_name.startswith("/" + prefix)
        ]
        suggestions = [
            f"{command_name}  \u2014 {command_description}" for command_name, command_description in matched_commands
        ]
        autocomplete_widget.set_suggestions(suggestions, kind="command")

    def _complete_files(self, autocomplete_widget: FileAutocomplete, prefix: str) -> None:
        """Find matching files asynchronously off the UI thread."""
        if not getattr(self, "is_running", False):
            autocomplete_widget.set_suggestions(_find_matching_files(prefix, 15), kind="file")
            return
        self.run_worker(
            self._complete_files_async(autocomplete_widget, prefix),
            group="file_autocomplete",
            exclusive=True,
        )

    async def _complete_files_async(self, autocomplete_widget: FileAutocomplete, prefix: str) -> None:
        """Run workspace file walk in a worker thread and apply suggestions."""
        matched_files = await asyncio.to_thread(_find_matching_files, prefix, 15)
        input_bar = self.query_one(InputBar)
        if input_bar._completing:
            autocomplete_widget.set_suggestions(matched_files, kind="file")

    def on_input_bar_navigate_autocomplete(self, event: InputBar.NavigateAutocomplete) -> None:
        """Navigate up/down in the suggestion list."""
        autocomplete_widget = self.query_one(FileAutocomplete)
        if autocomplete_widget.option_count > 0:
            if event.delta > 0:
                autocomplete_widget.action_cursor_down()
            else:
                autocomplete_widget.action_cursor_up()

    def on_input_bar_select_autocomplete(self, event: InputBar.SelectAutocomplete) -> None:
        """Apply the selected suggestion to the input bar."""
        autocomplete_widget = self.query_one(FileAutocomplete)
        input_bar = self.query_one(InputBar)
        selected_item = autocomplete_widget.selected

        # Hide immediately
        autocomplete_widget.set_suggestions([])

        if selected_item:
            row_index, column_index = input_bar.cursor_location
            line_collection = input_bar.text.splitlines()
            if not line_collection:
                line_collection = [""]
            current_line = line_collection[row_index]

            # Check if this is a command selection (contains em dash)
            if " — " in selected_item:
                command_name = selected_item.split(" — ")[0].strip()

                # Commands that take arguments are inserted with a trailing space
                if command_name.startswith("/") and command_name not in _ARGUMENT_COMMANDS:
                    self._execute_slash_command(command_name)
                    input_bar.text = ""
                    input_bar._draft = ""
                    input_bar._history_index = -1
                    input_bar.focus()
                    return

                new_line = command_name + " "
                line_collection[row_index] = new_line
                input_bar.text = "\n".join(line_collection)
                new_column = len(command_name) + 1
                input_bar.move_cursor((row_index, new_column))
            else:
                # File selection: replace the @prefix with @selected
                match_result = re.search(r"@(\S*)$", current_line[:column_index])
                if match_result:
                    start_index = match_result.start()
                    new_line = current_line[:start_index] + f"@{selected_item} " + current_line[column_index:]
                    line_collection[row_index] = new_line
                    input_bar.text = "\n".join(line_collection)
                    new_column = start_index + len(selected_item) + 2
                    input_bar.move_cursor((row_index, new_column))

        # Always restore focus to the input bar
        input_bar.focus()
