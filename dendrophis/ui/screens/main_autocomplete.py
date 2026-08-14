"""Autocomplete and suggestion handling for MainScreen input."""

from __future__ import annotations

import glob
import re
from typing import TYPE_CHECKING

from dendrophis.ui.widgets.input_bar import FileAutocomplete, InputBar

if TYPE_CHECKING:
    from dendrophis.session.session import Session


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

        for name, skill in self._session._skill_manager._all_skills.items():
            short_description = skill.description.splitlines()[0][:60]
            commands.append((f"/{name}", short_description))
        matched = [(cmd, desc) for cmd, desc in commands if cmd.startswith("/" + prefix)]
        suggestions = [f"{cmd}  \u2014 {desc}" for cmd, desc in matched]
        autocomplete_widget.set_suggestions(suggestions, kind="command")

    def _complete_files(self, autocomplete_widget: FileAutocomplete, prefix: str) -> None:
        """Find matching files recursively."""
        search_pattern = f"**/{prefix}*"
        try:
            matches = glob.glob(search_pattern, recursive=True)
            matches.sort(key=len)
            files = matches[:15]
            autocomplete_widget.set_suggestions(files, kind="file")
        except Exception:
            pass

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
                # Extract just the command name before the description
                command_name = selected_item.split(" — ")[0].strip()

                # Check if this is a slash command (starts with /)
                if command_name.startswith("/"):
                    # Execute slash command immediately
                    self._execute_slash_command(command_name)
                    # Clear the input bar completely since command was executed
                    input_bar.text = ""
                    input_bar._draft = ""
                    input_bar._history_index = -1
                    # Keep focus on input bar for next command
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
