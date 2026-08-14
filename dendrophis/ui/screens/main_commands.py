"""Slash command handling and session action dispatcher for MainScreen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendrophis.events import (
    ContextUpdatedEvent,
    PrimerLoadedEvent,
    StatsUpdatedEvent,
)
from dendrophis.ui.widgets.chat_view import ChatView
from dendrophis.ui.widgets.input_bar import InputBar

if TYPE_CHECKING:
    from dendrophis.events import EventBus
    from dendrophis.session.session import Session


class MainScreenCommands:
    """Mixin containing slash command processing and session actions for MainScreen."""

    _session: Session
    _event_bus: EventBus

    def action_export_session(self) -> None:
        """Handle ctrl+e to export session."""
        self._export_session()

    def _export_session(self) -> None:
        """Export the full conversation to a markdown file."""
        from datetime import datetime

        session_identifier = self._session.session_id[:8]
        timestamp_str = datetime.now().strftime("%Y-%m-%d.%H%M%S")
        export_filename = f"session-{session_identifier}.{timestamp_str}.md"

        try:
            markdown_parts = [f"# Dendrophis Session Export - {session_identifier}\n"]
            markdown_parts.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            markdown_parts.append(f"**Model:** {self._session.config.llm.model}\n")
            markdown_parts.append("---\n")

            for message in self._session.context.messages:
                role_name = message.get("role", "unknown").capitalize()
                message_content = message.get("content", "")

                markdown_parts.append(f"### {role_name}\n")
                if message_content:
                    markdown_parts.append(f"{message_content}\n")

                if "tool_calls" in message:
                    for tool_call in message["tool_calls"]:
                        function_dict = tool_call.get("function", {})
                        markdown_parts.append(f"> **Tool Call:** `{function_dict.get('name')}`\n")
                        markdown_parts.append(f"> ```json\n> {function_dict.get('arguments')}\n> ```\n")

                markdown_parts.append("\n")

            with open(export_filename, "w", encoding="utf-8") as file_handle:
                file_handle.write("\n".join(markdown_parts))

            notification_message = f"Session exported to {export_filename}"
            self._debug_widget.write(f"[NOTIFY] {notification_message}")
            self.notify(f"Session exported to [bold]{export_filename}[/bold]", severity="information")
        except Exception as export_error:
            error_message = f"Export failed: {export_error!s}"
            self._debug_widget.write(f"[NOTIFY ERROR] {error_message}")
            self.notify(error_message, severity="error")

    def _save_primer(self) -> None:
        """Save a project primer from current session understanding."""
        chat = self.query_one(ChatView)
        save_result = self._session.save_project_primer()
        if save_result:
            notification_message = f"Project primer saved for [bold]{save_result}[/bold]"
            self._debug_widget.write(f"[NOTIFY] {notification_message}")
            chat.add_system_message(f"Project primer saved: {save_result}")
            self.notify(notification_message, severity="information")
            primer_info = self._session.load_project_primer()
            if primer_info:
                self._event_bus.publish(
                    PrimerLoadedEvent(
                        project_id=primer_info["project_id"],
                        project_name=primer_info["project_name"],
                        file_count=primer_info["file_count"],
                        turn_count=primer_info.get("turn_count", 0),
                        understanding=primer_info.get("understanding", ""),
                    )
                )
        else:
            error_message = "Failed to save project primer"
            self._debug_widget.write(f"[NOTIFY ERROR] {error_message}")
            self.notify(error_message, severity="error")

    def _load_primer(self) -> None:
        """Load the project primer and inject into context."""
        chat = self.query_one(ChatView)
        primer_info = self._session.load_project_primer()
        if primer_info:
            display_parts = [f"Loaded project primer: [bold]{primer_info['project_name']}[/bold]"]
            display_parts.append(f"  Files tracked: {primer_info['file_count']}")
            if primer_info["understanding"]:
                display_parts.append(f"  Understanding: {primer_info['understanding']}")
            summary_message = "\n".join(display_parts)
            self._debug_widget.write(f"[NOTIFY] Primer loaded: {primer_info['project_name']}")
            chat.add_system_message(summary_message)
            self.notify(f"Primer loaded: {primer_info['file_count']} files", severity="information")
            self._event_bus.publish(
                PrimerLoadedEvent(
                    project_id=primer_info["project_id"],
                    project_name=primer_info["project_name"],
                    file_count=primer_info["file_count"],
                    turn_count=primer_info.get("turn_count", 0),
                    understanding=primer_info.get("understanding", ""),
                )
            )
        else:
            status_message = "No project primer found for this directory"
            self._debug_widget.write(f"[NOTIFY] {status_message}")
            chat.add_system_message(status_message)
            self.notify(status_message, severity="information")

    def _show_help(self) -> None:
        """Show available slash commands in the chat as a single compact message."""
        chat = self.query_one(ChatView)
        primer_enabled = self._session.config.caching.pr_enabled

        command_list = [
            ("  /hello       ", "Greeting from Dendrophis"),
            ("  /help        ", "Show this help message"),
        ]

        if primer_enabled:
            command_list.append(("  /clear       ", "Clear chat and reset context (primer re-injected)"))
            command_list.append(("  /fresh       ", "Clear chat without primer (truly fresh start)"))
        else:
            command_list.append(("  /clear       ", "Clear chat and reset context"))

        command_list.extend(
            [
                ("  /compact     ", "Manually compact context to reduce token usage"),
                ("  /fork        ", "Fork current context into a new session branch"),
                ("  /export      ", "Export conversation to markdown file"),
            ]
        )

        if primer_enabled:
            command_list.extend(
                [
                    ("  /save-primer ", "Save project primer for future sessions"),
                    ("  /load-primer ", "Load the project primer and inject it into context"),
                    ("  /track       ", "Add a file to the project primer"),
                    ("  /untrack     ", "Remove a file from the project primer"),
                ]
            )

        command_list.append(("  /set         ", "Override the last assistant response"))

        message_sections = ["[bold]Slash Commands[/bold]"]
        for command_name, command_description in command_list:
            message_sections.append(f"{command_name}— {command_description}")

        message_sections.extend(
            [
                "",
                "[bold]Key Bindings[/bold]",
                "  Ctrl+L  — Clear chat (same as /clear)",
                "  Ctrl+S  — Open session picker",
                "  Ctrl+T  — Open settings",
                "  Ctrl+E  — Export session (same as /export)",
                "  Esc     — Interrupt streaming",
                "  Ctrl+Q  — Quit",
            ]
        )

        if primer_enabled:
            message_sections.extend(
                [
                    "",
                    "[bold]Project Primer[/bold]",
                    "On a new session, any saved primer is loaded automatically — tracked\n"
                    "files are re-read from disk and injected into context so the LLM\n"
                    "already knows your project. Changed files are detected via content\n"
                    "hashing and re-read fresh. Use /save-primer after exploring a project\n"
                    "to capture it for next time. Use /fresh to start without the primer.",
                ]
            )

        chat.add_system_message("\n".join(message_sections))

    def _process_input(self, event: InputBar.Submitted) -> None:
        """Handle user input from the chat bar."""
        command_string = event.text.strip().lower()
        if command_string == "/export":
            self._export_session()
            return
        if command_string == "/clear":
            self.action_clear_chat()
            return
        if command_string == "/fresh":
            self._fresh_chat()
            return
        if command_string == "/compact":
            self._compact_context()
            return
        if command_string == "/help":
            self._show_help()
            return
        if command_string == "/hello":
            chat = self.query_one(ChatView)
            chat.add_system_message("Hello! 👋 I'm Dendrophis, your coding assistant.")
            return
        if command_string.startswith("/fork") and (len(command_string) == 5 or command_string[5] == " "):
            chat = self.query_one(ChatView)
            if not self._session.context.messages:
                chat.add_system_message(
                    "[warning]Context is empty — write some messages before forking context.[/warning]"
                )
                return

            fork_name_argument = event.text.strip()[5:].strip() or None
            new_session_id = self._session.fork(name=fork_name_argument)
            self.app._update_title()

            name_label = f" named '[bold cyan]{fork_name_argument}[/bold cyan]'" if fork_name_argument else ""
            chat.add_system_message(
                f"🍴 Context forked into new session branch [bold green]{new_session_id[:8]}[/bold green]{name_label}! "
                "Subsequent messages will be saved to this new session branch."
            )
            return
        if command_string == "/save-primer":
            if not self._session.config.caching.pr_enabled:
                chat = self.query_one(ChatView)
                chat.add_system_message(
                    "[warning]Project primer is disabled in config "
                    "(caching.pr_enabled). Enable it in Settings.[/warning]"
                )
                return
            self._save_primer()
            return
        if command_string == "/load-primer":
            if not self._session.config.caching.pr_enabled:
                chat = self.query_one(ChatView)
                chat.add_system_message(
                    "[warning]Project primer is disabled in config "
                    "(caching.pr_enabled). Enable it in Settings.[/warning]"
                )
                return
            self._load_primer()
            return
        if event.text.strip().startswith("/track "):
            if not self._session.config.caching.pr_enabled:
                self.notify("Primer feature is disabled in settings.", severity="warning")
                return
            track_file_path = event.text.strip()[7:].strip()
            if self._session.track_file(track_file_path):
                chat = self.query_one(ChatView)
                chat.add_system_message(f"Tracking file: [bold]{track_file_path}[/bold]")
                self.notify(f"Now tracking: {track_file_path}", severity="information")
            else:
                self.notify(f"Failed to track: {track_file_path}", severity="error")
            return
        if event.text.strip().startswith("/untrack "):
            if not self._session.config.caching.pr_enabled:
                self.notify("Primer feature is disabled in settings.", severity="warning")
                return
            untrack_file_path = event.text.strip()[9:].strip()
            if self._session.untrack_file(untrack_file_path):
                chat = self.query_one(ChatView)
                chat.add_system_message(f"Stopped tracking: [bold]{untrack_file_path}[/bold]")
                self.notify(f"Stopped tracking: {untrack_file_path}", severity="information")
            else:
                self.notify(f"Failed to untrack: {untrack_file_path}", severity="error")
            return
        if event.text.strip().startswith("/set "):
            override_text = event.text.strip()[5:]
            chat = self.query_one(ChatView)
            if self._session.context.replace_last_assistant(override_text):
                chat.add_system_message("Last response overridden.")
            else:
                chat.add_system_message("No assistant message to override.")
            return

        chat = self.query_one(ChatView)

        # Inject @file contents into context
        for file_path in event.file_paths:
            self._session.context.append_file(str(file_path), file_path.read_text(errors="replace"))

        if not event.text:
            return

        chat.add_user_message(event.text)
        # Start streaming in background - events will update UI
        self.run_worker(self._session.send_message(event.text), exclusive=True, exit_on_error=False)

    def action_clear_chat(self) -> None:
        self.query_one(ChatView).clear()
        self._session.reset()
        injection_result = self._session.inject_primer_files()

        self._event_bus.publish(
            ContextUpdatedEvent(
                token_count=self._session.context.token_count,
                token_pct=self._session.context.token_pct,
                turn_count=self._session.context.get_turn_count(),
                full_chat_restored=False,
            )
        )
        self._event_bus.publish(
            StatsUpdatedEvent(
                prompt_tokens=self._session.stats.prompt_tokens,
                completion_tokens=self._session.stats.completion_tokens,
                total_cost_usd=self._session.stats.total_cost_usd,
                tokens_per_sec=0.0,
                time_to_first_token=0.0,
                cached_tokens=self._session.stats.cached_tokens,
            )
        )

        chat = self.query_one(ChatView)
        if injection_result["injected"]:
            injected_files = injection_result.get("injected_files", [])
            injected_summary = f" ({', '.join(injected_files)})" if injected_files else ""
            chat.add_system_message(
                f"Project primer re-loaded: {injection_result['injected']} file(s) injected{injected_summary}"
            )
        self._show_help()

    def _fresh_chat(self) -> None:
        """Clear chat without injecting primer — truly fresh start."""
        self.query_one(ChatView).clear()
        self._session.reset()

        self._event_bus.publish(
            ContextUpdatedEvent(
                token_count=self._session.context.token_count,
                token_pct=self._session.context.token_pct,
                turn_count=self._session.context.get_turn_count(),
                full_chat_restored=False,
            )
        )
        self._event_bus.publish(
            StatsUpdatedEvent(
                prompt_tokens=0,
                completion_tokens=0,
                total_cost_usd=0.0,
                tokens_per_sec=0.0,
                time_to_first_token=0.0,
            )
        )
        self._show_help()

    def _compact_context(self) -> None:
        """Manually trigger context compaction to reduce token usage."""
        chat = self.query_one(ChatView)

        before_token_count = self._session.context.token_count
        before_token_percentage = self._session.context.token_pct * 100
        before_message_count = len(self._session.context.messages)

        chat.add_system_message(
            f"[dim]Compacting context... ({before_token_count:,} tokens, {before_token_percentage:.1f}%,"
            f" {before_message_count} messages)[/dim]"
        )
        self._debug_widget.write(
            f"[NOTIFY] Starting context compaction ({before_token_count:,} tokens, {before_message_count} messages)"
        )

        async def do_compact() -> None:
            try:
                compaction_result = await self._session.compact()

                if not compaction_result.get("compacted"):
                    chat.add_system_message(
                        f"[dim]Compaction skipped: {compaction_result.get('reason', 'No messages to compact')}[/dim]"
                    )
                    return

                after_token_count = self._session.context.token_count
                after_token_percentage = self._session.context.token_pct * 100
                after_message_count = len(self._session.context.messages)
                saved_tokens = before_token_count - after_token_count
                compacted_count = compaction_result.get("messages_compacted", 0)
                kept_count = compaction_result.get("kept_recent", 0)
                summary_text = compaction_result.get("summary", "")

                output_lines = [
                    "[bold]Context Compacted[/bold]",
                    "",
                    f"[dim]Messages:[/dim] {before_message_count} → {after_message_count}"
                    f" ([green]-{compacted_count}[/] compacted, {kept_count} kept)",
                    f"[dim]Tokens:[/dim]   {before_token_count:,} → {after_token_count:,}"
                    f" ([green]-{saved_tokens:,}[/], {after_token_percentage:.1f}%)",
                ]

                if summary_text:
                    output_lines.append("")
                    output_lines.append("[dim]Summary:[/dim]")
                    preview_text = summary_text[:300] + "..." if len(summary_text) > 300 else summary_text
                    output_lines.append(f"[italic]{preview_text}[/italic]")

                chat.add_system_message("\n".join(output_lines))
                self._debug_widget.write(
                    f"[NOTIFY] Compacted {compacted_count} messages, saved {saved_tokens:,} tokens"
                )

                self._event_bus.publish(
                    ContextUpdatedEvent(
                        token_count=after_token_count,
                        token_pct=after_token_percentage / 100,
                    )
                )
            except Exception as compaction_error:
                chat.add_system_message(f"[red]Compaction failed: {compaction_error}[/red]")
                self._debug_widget.write(f"[NOTIFY ERROR] Context compaction failed: {compaction_error}")

        self.run_worker(do_compact(), exclusive=True, exit_on_error=False)

    def _execute_slash_command(self, command: str) -> None:
        """Execute a slash command immediately and provide feedback."""
        command_parts = command[1:].split(maxsplit=1)
        command_name = command_parts[0].lower() if command_parts else ""

        builtin_commands = {
            "hello": lambda: self.query_one(ChatView).add_system_message(
                "Hello! 👋 I'm Dendrophis, your coding assistant."
            ),
            "help": self._show_help,
            "clear": lambda: self._process_input(InputBar.Submitted("/clear", [])),
            "fresh": lambda: self._process_input(InputBar.Submitted("/fresh", [])),
            "compact": lambda: self._process_input(InputBar.Submitted("/compact", [])),
            "fork": lambda: self._process_input(InputBar.Submitted(command, [])),
            "export": lambda: self._process_input(InputBar.Submitted("/export", [])),
            "save-primer": lambda: self._process_input(InputBar.Submitted("/save-primer", [])),
            "load-primer": lambda: self._process_input(InputBar.Submitted("/load-primer", [])),
            "track": lambda: self._process_input(InputBar.Submitted(command, [])),
            "untrack": lambda: self._process_input(InputBar.Submitted(command, [])),
            "set": lambda: self._process_input(InputBar.Submitted(command, [])),
        }

        if command_name in builtin_commands:
            builtin_commands[command_name]()
            feedback_message = f"[bold]✅[/bold] Command [code]/{command_name}[/code] executed"
        else:
            if hasattr(self, "_session") and self._session and hasattr(self._session, "_skill_manager"):
                if command_name in self._session._skill_manager._all_skills:
                    skill_instance = self._session._skill_manager._all_skills[command_name]
                    skill_message = (
                        f"[bold]📚 Skill Activated: {skill_instance.name}[/bold]\n\n"
                        f"[italic]{skill_instance.description}[/italic]"
                    )

                    chat = self.query_one(ChatView)
                    chat.add_system_message(skill_message)

                    feedback_message = (
                        f"[bold][green]✓[/green][/bold] Skill [code]/{command_name}[/code] loaded. "
                        f"The skill documentation has been added to your context. "
                        f"You can now use its capabilities."
                    )
                else:
                    feedback_message = f"[bold][red]✗[/red][/bold] Unknown skill: [code]/{command_name}[/code]"
            else:
                feedback_message = (
                    f"[bold][yellow]⚠[/yellow][/bold] Skills not available yet. "
                    f"Command [code]/{command_name}[/code] queued."
                )

        chat = self.query_one(ChatView)
        chat.add_system_message(feedback_message)
