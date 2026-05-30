"""SessionPersister — save and load session state.

Responsibilities:
- Save session (messages, stats, model) to compressed JSON
- Load session from compressed JSON
- Handle session file paths
"""

from __future__ import annotations

import json
import lzma
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dendrophis.config.schema import DendrophisConfig
    from dendrophis.context.manager import ContextManager
    from dendrophis.session.session import SessionStats


def _sanitize_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop orphaned tool messages from a message list.

    A tool message is orphaned when the nearest preceding non-tool message is
    not an assistant message with tool_calls — e.g. after compaction cut through
    a tool-call sequence, or a session was saved in a broken state.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "tool":
            preceding = next((m for m in reversed(result) if m.get("role") != "tool"), None)
            if not (preceding and preceding.get("role") == "assistant" and preceding.get("tool_calls")):
                continue
        result.append(msg)
    return result


class SessionPersister:
    """Handles saving and loading session state to/from disk.

    Sessions are saved as compressed JSON files in:
    ~/.config/dendrophis/sessions/session-{id}.{timestamp}.json.xz
    """

    DEFAULT_SESSIONS_DIR = Path.home() / ".config" / "dendrophis" / "sessions"

    def __init__(
        self,
        context: ContextManager,
        stats: SessionStats,
        config: DendrophisConfig,
        debug_logger: Callable[[str], None] | None = None,
    ) -> None:
        self._context = context
        self._stats = stats
        self._config = config
        self._debug_logger = debug_logger

    def _log(self, message: str) -> None:
        """Log a debug message if logger is configured."""
        if self._debug_logger:
            self._debug_logger(message)

    def save(
        self,
        session_id: str,
        session_file: Path | None = None,
    ) -> Path | None:
        """Save the current session to a JSON file.

        Args:
            session_id: The session identifier.
            session_file: Optional path to save to (for resaving existing session).

        Returns:
            The path to the saved file, or None if there are no messages to save.
        """
        if not self._context.messages:
            return None

        # Create sessions directory if it doesn't exist
        sessions_dir = self.DEFAULT_SESSIONS_DIR
        sessions_dir.mkdir(parents=True, exist_ok=True)

        if session_file:
            filepath = session_file
        else:
            short_id = session_id[:8] if session_id else "unknown"
            timestamp = datetime.now().strftime("%Y-%m-%d.%H%M%S")
            filepath = sessions_dir / f"session-{short_id}.{timestamp}.json.xz"

        # Build session data
        session_data = {
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "model": self._config.llm.model,
            "messages": self._context.messages,
            "stats": {
                "prompt_tokens": self._stats.prompt_tokens,
                "completion_tokens": self._stats.completion_tokens,
                "cached_tokens": self._stats.cached_tokens,
                "total_cost_usd": self._stats.total_cost_usd,
            },
        }

        # Save prompt_cache_key if set (for cache continuity across session loads)
        if self._config.llm.prompt_cache_key is not None:
            session_data["prompt_cache_key"] = self._config.llm.prompt_cache_key

        try:
            data = json.dumps(session_data, ensure_ascii=False).encode()
            with lzma.open(filepath, "wb", preset=0) as f:
                f.write(data)
            return filepath
        except Exception:
            return None

    def load(self, path: str) -> tuple[dict[str, Any] | None, str, Path | None]:
        """Load a session from a JSON file.

        Args:
            path: Path to the session JSON file.

        Returns:
            Tuple of (info_dict, session_id, session_file) where info_dict contains
            message_count and model, or None if loading failed.
        """
        filepath = Path(path).expanduser()
        if not filepath.exists():
            self._log(f"load_session: file not found: {filepath}")
            return None, "", None

        try:
            if filepath.suffix == ".xz":
                with lzma.open(filepath, "rb") as f:
                    data = json.loads(f.read().decode())
            else:
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)

            # Extract session ID
            session_id = data.get("session_id", "")

            # Restore messages and sanitize
            loaded_messages = data.get("messages", [])
            for msg in loaded_messages:
                if msg.get("role") == "assistant":
                    msg.pop("reasoning_content", None)
            loaded_messages = _sanitize_tool_messages(loaded_messages)
            self._context.messages = loaded_messages
            self._context.recalculate_tokens()

            # Restore stats
            stats_data = data.get("stats", {})
            self._stats.prompt_tokens = stats_data.get("prompt_tokens", 0)
            self._stats.completion_tokens = stats_data.get("completion_tokens", 0)
            self._stats.cached_tokens = stats_data.get("cached_tokens", 0)
            self._stats.total_cost_usd = stats_data.get("total_cost_usd", 0.0)

            # Optionally restore model if specified
            saved_model = data.get("model")
            if saved_model:
                self._config.llm.model = saved_model

            # Restore prompt_cache_key if it was saved
            saved_prompt_cache_key = data.get("prompt_cache_key")
            if saved_prompt_cache_key:
                self._config.llm.prompt_cache_key = saved_prompt_cache_key

            # Recalculate turn count from messages
            self._context._turn_count = sum(1 for m in self._context.messages if m.get("role") == "user")

            info = {
                "message_count": len([m for m in self._context.messages if m.get("role") != "system"]),
                "model": saved_model,
            }

            return info, session_id, filepath

        except Exception:
            return None, "", None
