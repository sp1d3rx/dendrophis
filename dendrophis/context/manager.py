"""Context manager — message accumulation, token tracking, compaction trigger."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dendrophis.caching.file_tracker import FileBlockTracker
from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.tokenizer import count_messages_tokens, count_tokens
from dendrophis.llm.models import (
    make_assistant_message,
    make_tool_result_message,
    make_user_message,
)


def _file_fence(path: str, content: str) -> str:
    """Wrap file content in a markdown fenced block with path header."""
    suffix = Path(path).suffix.lstrip(".")
    lang = suffix or "text"
    return f"[File: {path}]\n```{lang}\n{content}\n```"


class ContextManager:
    """Owns the conversation message list and token bookkeeping."""

    MAX_FILE_BYTES = 200_000

    def __init__(self, config: DendrophisConfig) -> None:
        self._config = config
        self.messages: list[dict[str, Any]] = []
        self.token_count: int = 0
        self._system_prompt = config.system_prompt
        self._turn_count: int = 0  # Incremented each time user sends a message
        self._turn_to_index: dict[int, int] = {0: 0}  # Turn 0 is system prompt
        # File tracking for Phase 2 caching
        self._file_tracker = FileBlockTracker(_stable_threshold=config.caching.tier2_file_blocks_stable_turns)
        self._init_system()

    def _init_system(self) -> None:
        if self._system_prompt:
            msg: dict[str, Any] = {"role": "system", "content": self._system_prompt}
            # Cache control will be added after we know if model supports it
            self.messages = [msg]
            self.token_count = count_tokens(self._system_prompt)

    def update_system_prompt_caching(self, caching_enabled: bool) -> None:
        """Update cache_control on system prompt after model is known.

        Called after fetch_models() so we know if the current model supports caching.
        """
        if not self.messages:
            return

        system_msg = self.messages[0]
        if system_msg.get("role") != "system":
            return

        if caching_enabled and self._config.caching.tier1_system_prompt:
            system_msg["cache_control"] = {"type": "ephemeral"}
        else:
            system_msg.pop("cache_control", None)

    # ── Appenders ──────────────────────────────────────────────────────────────

    def append_user(self, text: str) -> None:
        msg = make_user_message(text)
        self.messages.append(msg)
        self.token_count += count_tokens(text) + 4
        self._turn_count += 1
        self._turn_to_index[self._turn_count] = len(self.messages) - 1

    def append_system(self, text: str) -> None:
        """Append a system message (e.g., for memory associations)."""
        msg: dict[str, Any] = {"role": "system", "content": text}
        self.messages.append(msg)
        self.token_count += count_tokens(text) + 4

    def append_assistant(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        msg = make_assistant_message(content, tool_calls, reasoning_content)
        self.messages.append(msg)
        self.token_count += count_tokens(content) + (count_tokens(reasoning_content) if reasoning_content else 0) + 4

    def append_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        # REJECT hashed IDs as tool names - they should never appear
        if len(name) == 9 and all(c in "0123456789abcdef" for c in name):
            raise ValueError(f"Tool name cannot be hashed ID: {name}. This indicates a bug in tool call processing.")

        msg = make_tool_result_message(tool_call_id, name, content)
        self.messages.append(msg)
        self.token_count += count_tokens(content) + 4

    def append_file(self, path: str, content: str) -> None:
        if len(content.encode()) > self.MAX_FILE_BYTES:
            content = content[: self.MAX_FILE_BYTES] + "\n... [truncated]"
        text = _file_fence(path, content)

        # Track file for caching if enabled
        if self._config.caching.enabled and self._config.caching.tier2_file_blocks:
            message_index = len(self.messages)  # Index after append_user
            self._file_tracker.track_file(path, content, self._turn_count, message_index)

        self.append_user(text)

    # ── Sync from API usage ────────────────────────────────────────────────────

    def sync_token_count(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.token_count = prompt_tokens + completion_tokens

    def recalculate_tokens(self) -> None:
        self.token_count = count_messages_tokens(self.messages)

    # ── Compaction check ───────────────────────────────────────────────────────

    @property
    def context_limit(self) -> int:
        return self._config.llm.context_limit

    @property
    def token_pct(self) -> float:
        if self.context_limit == 0:
            return 0.0
        return self.token_count / self.context_limit

    def needs_compaction(self) -> bool:
        return self.token_pct >= self._config.llm.compaction_threshold

    # ── Caching (Phase 2) ──────────────────────────────────────────────────────

    def get_turn_count(self) -> int:
        """Return current turn count (incremented each user message)."""
        return self._turn_count

    def update_file_caches(self) -> None:
        """Mark stable files as cacheable and add cache_control to their messages."""
        if not (self._config.caching.enabled and self._config.caching.tier2_file_blocks):
            return

        cacheable_files = self._file_tracker.get_cacheable_files(self._turn_count)
        for file_path in cacheable_files:
            msg_idx = self._file_tracker.get_file_message_index(file_path)
            if msg_idx >= 0 and msg_idx < len(self.messages):
                # Mark file as cached and add cache_control
                msg = self.messages[msg_idx]
                if "cache_control" not in msg:
                    msg["cache_control"] = {"type": "ephemeral"}
                    self._file_tracker.mark_cacheable(file_path)

    def invalidate_file_cache(self, path: str) -> None:
        """Invalidate cache for a specific file (content changed or user signal)."""
        self._file_tracker.invalidate_file(path)
        # Remove cache_control from corresponding message
        msg_idx = self._file_tracker.get_file_message_index(path)
        if msg_idx >= 0 and msg_idx < len(self.messages):
            self.messages[msg_idx].pop("cache_control", None)

    def invalidate_all_file_caches(self) -> None:
        """Invalidate all file caches (user says 'project changed')."""
        self._file_tracker.invalidate_all()
        # Remove cache_control from all messages
        for msg in self.messages:
            if msg.get("role") == "user" and "cache_control" in msg:
                msg.pop("cache_control", None)

    def update_understanding_cache(self, checkpoint_turn: int) -> None:
        """Mark the understanding block as cacheable."""
        if not (self._config.caching.enabled and self._config.caching.tier2_project_understanding):
            return

        idx = self._turn_to_index.get(checkpoint_turn)
        if idx is not None and idx < len(self.messages):
            msg = self.messages[idx]
            if "cache_control" not in msg:
                msg["cache_control"] = {"type": "ephemeral"}

    def get_file_tracker(self) -> FileBlockTracker:
        """Get the file block tracker (for testing or advanced usage)."""
        return self._file_tracker

    # ── API serialization ──────────────────────────────────────────────────────

    def get_messages_for_api(self) -> list[dict[str, Any]]:
        # Log context messages when debugging
        import os

        if os.environ.get("DENDROPHIS_TOOL_LOG") == "1":
            from dendrophis.session.session import _tool_log

            _tool_log("=== CONTEXT MANAGER - GET MESSAGES FOR API ===")
            _tool_log(f"Total messages in context: {len(self.messages)}")
            for i, msg in enumerate(self.messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls")
                _tool_log(f"Message {i + 1}: role={role}")
                if content:
                    _tool_log(f"  Content: {content[:50]}{'...' if len(content) > 50 else ''}")
                if tool_calls:
                    _tool_log(f"  Tool calls: {len(tool_calls)}")
                    for j, tc in enumerate(tool_calls):
                        func = tc.get("function", {})
                        _tool_log(f"    Tool {j + 1}: {func.get('name')}")

        return list(self.messages)

    def replace_last_assistant(self, content: str) -> bool:
        """Replace the content of the last assistant message. Returns True if found."""
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant":
                msg["content"] = content
                msg.pop("tool_calls", None)
                self.recalculate_tokens()
                return True
        return False

    def reset(self) -> None:
        self.messages = []
        self.token_count = 0
        self._turn_count = 0
        self._turn_to_index = {0: 0}
        self._file_tracker = FileBlockTracker(_stable_threshold=self._config.caching.tier2_file_blocks_stable_turns)
        self._init_system()
