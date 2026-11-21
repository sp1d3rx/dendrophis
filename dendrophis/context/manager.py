"""Context manager — message accumulation, token tracking, compaction trigger."""

from __future__ import annotations

import hashlib
import json
import os
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
        # Dedup: track SHA256 hashes of read contents (skip identical read results per file path)
        self._read_file_hashes: dict[str, str] = {}
        self._raw_read_hashes: set[str] = set()
        self._init_system()

    def _init_system(self) -> None:
        if self._system_prompt:
            msg: dict[str, Any] = {"role": "system", "content": self._system_prompt}
            self.messages = [msg]
            if getattr(self._config.llm, "visual_system_prompt", False):
                self.token_count = 310
            else:
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
        assistant_message = make_assistant_message(content, tool_calls, reasoning_content)
        self.messages.append(assistant_message)
        tool_calls_tokens = 0
        if tool_calls:
            for call_entry in tool_calls:
                function_spec = call_entry.get("function", {}) if isinstance(call_entry, dict) else {}
                tool_calls_tokens += count_tokens(str(function_spec.get("name", "")))
                tool_calls_tokens += count_tokens(str(function_spec.get("arguments", "")))
        self.token_count += (
            count_tokens(content)
            + (count_tokens(reasoning_content) if reasoning_content else 0)
            + tool_calls_tokens
            + 4
        )

    def append_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        # REJECT hashed IDs as tool names - they should never appear
        if len(name) == 9 and all(character in "0123456789abcdef" for character in name):
            raise ValueError(f"Tool name cannot be hashed ID: {name}. This indicates a bug in tool call processing.")

        # Invalidate cached read hash when a file-mutating tool modifies a path so
        # reverting a file to an earlier state still returns full content on re-read.
        if name in ("edit", "patch", "write", "write_file", "append", "edit_function", "replace_function"):
            try:
                mutation_data = json.loads(content)
                if isinstance(mutation_data, dict) and "error" not in mutation_data:
                    mutated_path = (
                        mutation_data.get("path") or mutation_data.get("file_path") or mutation_data.get("file")
                    )
                    if isinstance(mutated_path, str) and mutated_path:
                        stale_keys = [
                            tracked_key
                            for tracked_key in self._read_file_hashes
                            if tracked_key == mutated_path or tracked_key.startswith(f"{mutated_path}#")
                        ]
                        for stale_key in stale_keys:
                            self._read_file_hashes.pop(stale_key, None)
            except (json.JSONDecodeError, TypeError):
                pass
        elif name in ("bash", "execute_code", "invoke_subagent"):
            self._read_file_hashes.clear()
            self._raw_read_hashes.clear()

        # Deduplicate unchanged read results by replacing the body with a compact stub
        # rather than dropping the message, keeping assistant.tool_calls strictly paired.
        if name in ("read", "read_file"):
            dedup_file_path: str | None = None
            is_duplicate_read = False
            try:
                result_data = json.loads(content)
                if isinstance(result_data, dict) and "error" in result_data:
                    pass
                elif isinstance(result_data, dict) and (result_data.get("type") == "file" or "content" in result_data):
                    file_path = result_data.get("path") or result_data.get("file")
                    file_content = result_data.get("content", "")
                    if file_path and isinstance(file_content, str):
                        dedup_file_path = file_path
                        showing_lines = result_data.get("showing_lines")
                        total_lines = result_data.get("total_lines")
                        is_partial_slice = bool(showing_lines and total_lines and showing_lines != f"1-{total_lines}")
                        cache_key = f"{file_path}#{showing_lines}" if is_partial_slice else file_path
                        content_hash = hashlib.sha256(file_content.encode(errors="replace")).hexdigest()
                        if self._read_file_hashes.get(cache_key) == content_hash:
                            is_duplicate_read = True
                        else:
                            self._read_file_hashes[cache_key] = content_hash
                    else:
                        content_hash = hashlib.sha256(content.encode(errors="replace")).hexdigest()
                        if content_hash in self._raw_read_hashes:
                            is_duplicate_read = True
                        else:
                            self._raw_read_hashes.add(content_hash)
                else:
                    content_hash = hashlib.sha256(content.encode(errors="replace")).hexdigest()
                    if content_hash in self._raw_read_hashes:
                        is_duplicate_read = True
                    else:
                        self._raw_read_hashes.add(content_hash)
            except (json.JSONDecodeError, TypeError):
                content_hash = hashlib.sha256(content.encode(errors="replace")).hexdigest()
                if content_hash in self._raw_read_hashes:
                    is_duplicate_read = True
                else:
                    self._raw_read_hashes.add(content_hash)

            # Cap dictionary size to 500 to prevent unbounded growth
            if len(self._read_file_hashes) > 500:
                self._read_file_hashes.pop(next(iter(self._read_file_hashes)), None)
            if len(self._raw_read_hashes) > 500:
                self._raw_read_hashes.pop()

            if is_duplicate_read:
                stub_payload: dict[str, Any] = {"status": "unchanged_since_last_read"}
                if dedup_file_path:
                    stub_payload["path"] = dedup_file_path
                content = json.dumps(stub_payload)

        tool_message = make_tool_result_message(tool_call_id, name, content)
        self.messages.append(tool_message)
        self.token_count += count_tokens(content) + 4

    def append_file(self, path: str, content: str) -> None:
        encoded_bytes = content.encode("utf-8")
        if len(encoded_bytes) > self.MAX_FILE_BYTES:
            content = encoded_bytes[: self.MAX_FILE_BYTES].decode("utf-8", errors="ignore") + "\n... [truncated]"
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

    def clear_read_hashes(self) -> None:
        """Clear the read dedup set and reset message-index caches after compaction or reload."""
        self._read_file_hashes.clear()
        self._raw_read_hashes.clear()
        self._file_tracker = FileBlockTracker(_stable_threshold=self._config.caching.tier2_file_blocks_stable_turns)
        turn_index_map: dict[int, int] = {0: 0}
        turn_counter = 0
        for message_index, message in enumerate(self.messages):
            if message.get("role") == "user":
                turn_counter += 1
                turn_index_map[turn_counter] = message_index
        self._turn_to_index = turn_index_map

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
        if os.environ.get("DENDROPHIS_TOOL_LOG") == "1":
            from dendrophis.utils import _tool_log

            _tool_log("=== CONTEXT MANAGER - GET MESSAGES FOR API ===")
            _tool_log(f"Total messages in context: {len(self.messages)}")
            for message_index, message in enumerate(self.messages):
                role = message.get("role", "unknown")
                content = message.get("content", "")
                tool_calls = message.get("tool_calls")
                _tool_log(f"Message {message_index + 1}: role={role}")
                if content:
                    _tool_log(f"  Content: {content[:50]}{'...' if len(content) > 50 else ''}")
                if tool_calls:
                    _tool_log(f"  Tool calls: {len(tool_calls)}")
                    for call_index, tool_call in enumerate(tool_calls):
                        function_spec = tool_call.get("function", {})
                        _tool_log(f"    Tool {call_index + 1}: {function_spec.get('name')}")

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

    def merge_last_turns(self) -> None:
        """Collapse a continuation-nudge exchange.

        The nudge is a user message sandwiched between two assistant turns.
        Remove it and merge the two assistant turns into one, so the nudge never
        pollutes the persisted transcript. Safe to call repeatedly for
        back-to-back nudges (each call collapses one nudge).
        """
        if len(self.messages) < 3:
            return
        last = self.messages[-1]
        nudge = self.messages[-2]
        before = self.messages[-3]
        if last.get("role") != "assistant" or nudge.get("role") != "user" or before.get("role") != "assistant":
            return

        before_content = before.get("content") or ""
        last_content = last.get("content") or ""
        merged_content = (before_content + "\n\n" + last_content).strip() or None

        merged: dict[str, Any] = {"role": "assistant", "content": merged_content}
        # The pre-nudge turn has no tool calls (that's what triggered the nudge),
        # so the post-nudge turn's tool_calls/reasoning are the ones to keep.
        if last.get("tool_calls"):
            merged["tool_calls"] = last["tool_calls"]
        if last.get("reasoning_content"):
            merged["reasoning_content"] = last["reasoning_content"]
        elif before.get("reasoning_content"):
            merged["reasoning_content"] = before["reasoning_content"]

        self.messages[-3:] = [merged]

        # The nudge's append_user incremented the turn count; undo it since the
        # user message is now gone.
        if self._turn_count > 0:
            self._turn_to_index.pop(self._turn_count, None)
            self._turn_count -= 1

        # Recompute token count from scratch for correctness.
        self.recalculate_tokens()

    def reset(self) -> None:
        self.messages = []
        self.token_count = 0
        self._turn_count = 0
        self._turn_to_index = {0: 0}
        self._file_tracker = FileBlockTracker(_stable_threshold=self._config.caching.tier2_file_blocks_stable_turns)
        self._read_file_hashes = {}
        self._raw_read_hashes = set()
        self._init_system()
