"""Project understanding phase detection and caching."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class UnderstandingPhaseDetector:
    """Detects when initial project understanding phase is complete."""

    start_turn: int = 0
    established: bool = False
    checkpoint_turn: int = -1
    min_turns_before_established: int = 5
    _last_user_message_types: list[str] = None

    def __post_init__(self) -> None:
        if self._last_user_message_types is None:
            self._last_user_message_types = []

    def record_user_message(self, content: str, turn: int) -> None:
        """Record a user message and check if understanding is established."""
        # Detect task request patterns: "add", "fix", "implement", "create", "modify", "debug", "test"
        task_keywords = (
            "add ",
            "fix ",
            "implement",
            "create",
            "modify",
            "debug",
            "test",
            "write ",
            "build",
            "refactor",
            "update",
            "change",
            "remove",
            "delete",
            "rename",
            "migrate",
            "optimize",
            "improve",
            "upgrade",
            "configure",
            "setup",
            "install",
            "generate",
            "convert",
            "migrate",
            "port",
            "integrate",
            "make ",
            "run ",
            "check ",
            "validate",
            "review",
            "document",
            "cleanup",
            "restructure",
            "extract",
        )
        is_task_request = any(kw in content.lower() for kw in task_keywords)

        if is_task_request:
            self._last_user_message_types.append("task")
        else:
            self._last_user_message_types.append("clarification")

        # Check if understanding is established
        if not self.established and turn >= self.min_turns_before_established:
            # Simple heuristic: if last few messages include a task request, understanding is probably established
            recent = self._last_user_message_types[-3:]  # Last 3 user messages
            if "task" in recent:
                self.established = True
                self.checkpoint_turn = turn

    def is_established(self) -> bool:
        """Check if project understanding phase is complete."""
        return self.established

    def get_understanding_checkpoint_turn(self) -> int:
        """Get the turn at which understanding was established."""
        return self.checkpoint_turn

    def reset(self) -> None:
        """Reset the detector (e.g., if user says 'project changed')."""
        self.established = False
        self.checkpoint_turn = -1
        self._last_user_message_types = []

    def get_stats(self) -> dict[str, Any]:
        """Return understanding phase statistics."""
        return {
            "established": self.established,
            "checkpoint_turn": self.checkpoint_turn,
            "min_turns_required": self.min_turns_before_established,
        }


def should_cache_understanding_block(messages: list[dict[str, Any]], checkpoint_turn: int, message_count: int) -> bool:
    """Determine if understanding block should be marked cacheable.

    Arguments:
        messages: Full message list
        checkpoint_turn: Turn at which understanding was established
        message_count: Total messages in conversation

    Returns:
        True if block should be cached (enough turns have passed since checkpoint)
    """
    # (we want to keep recent context mutable)
    return message_count >= checkpoint_turn + 3
