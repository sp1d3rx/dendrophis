"""Memory association generator - "this makes me think of..."

Surfaces memories with narrative framing, not search results.
Sometimes relevant, sometimes random, always contemplative.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from dendrophis.events.types import MemoryAssociationEvent
from dendrophis.memory.search import MemorySearcher

if TYPE_CHECKING:
    from dendrophis.memory.memory import MemoryStore

logger = logging.getLogger(__name__)


class MemoryAssociationGenerator:
    """Generates memory associations - moments of "hmm, this reminds me of..."

    Not deterministic search. Sometimes finds relevant connections,
    sometimes surfaces random memories when nothing fits.
    Always frames them with uncertainty and narrative voice.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._searcher = MemorySearcher(store)
        self._turns_since_last = 0
        self._next_trigger = self._random_trigger_interval()

    def _random_trigger_interval(self) -> int:
        """Random interval between associations - feels organic."""
        return random.randint(8, 15)

    def on_turn(self, user_message: str) -> MemoryAssociationEvent | None:
        """Called each turn. May return an association, may not."""
        self._turns_since_last += 1

        if self._turns_since_last < self._next_trigger:
            return None

        # Time to surface something
        self._turns_since_last = 0
        self._next_trigger = self._random_trigger_interval()

        return self._generate_association(user_message)

    def _generate_association(self, trigger: str) -> MemoryAssociationEvent | None:
        """Find a memory and frame it with narrative voice."""
        # Try to find something relevant first
        results = self._searcher.search(trigger, limit=3, min_score=0.3)

        if results and results[0].score > 0.6:
            # Strong match - confident but not certain
            memory = results[0].memory
            return MemoryAssociationEvent(
                trigger=trigger[:100],  # Keep it brief
                memory_content=memory.content,
                memory_summary=memory.summary or self._truncate(memory.content, 120),
                memory_id=memory.id,
                relevance_score=results[0].score,
                confidence="strong",
                when=self._humanize_time(memory.created_at),
                source=memory.source,
            )
        if results and results[0].score > 0.4:
            # Weak match - uncertain framing
            memory = results[0].memory
            return MemoryAssociationEvent(
                trigger=trigger[:100],
                memory_content=memory.content,
                memory_summary=memory.summary or self._truncate(memory.content, 120),
                memory_id=memory.id,
                relevance_score=results[0].score,
                confidence="weak",
                when=self._humanize_time(memory.created_at),
                source=memory.source,
            )
        # No good match - random memory with "my mind wandered" framing
        return self._random_association(trigger)

    def _random_association(self, trigger: str) -> MemoryAssociationEvent | None:
        """Pick a random memory when nothing seems relevant."""
        # Get recent memories, pick one randomly
        memories = self._store.list_memories(limit=20)
        if not memories:
            return None

        memory = random.choice(memories)
        return MemoryAssociationEvent(
            trigger=trigger[:100],
            memory_content=memory.content,
            memory_summary=memory.summary or self._truncate(memory.content, 120),
            memory_id=memory.id,
            relevance_score=0.0,
            confidence="random",
            when=self._humanize_time(memory.created_at),
            source=memory.source,
        )

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """Truncate text with ellipsis if too long."""
        if len(text) > max_len:
            return text[: max_len - 3] + "..."
        return text

    @staticmethod
    def _humanize_time(iso_timestamp: str) -> str:
        """Convert ISO timestamp to human-friendly relative time.

        "Tuesday" for recent, "last month" for older, "a while back" for ancient.
        """
        try:
            dt = datetime.fromisoformat(iso_timestamp)
            now = datetime.now()
            age = now - dt

            if age < timedelta(days=1):
                return "earlier today"
            if age < timedelta(days=2):
                return "yesterday"
            if age < timedelta(days=7):
                return dt.strftime("%A")  # Day name like "Tuesday"
            if age < timedelta(days=14):
                return "last week"
            if age < timedelta(days=30):
                return "a few weeks ago"
            if age < timedelta(days=60):
                return "last month"
            if age < timedelta(days=365):
                return dt.strftime("%B")  # Month name like "March"
            return "a while back"
        except (ValueError, TypeError):
            return "some time ago"

    @staticmethod
    def format_association(event: MemoryAssociationEvent) -> str:
        """Format an association with narrative voice based on confidence."""

        if event.confidence == "strong":
            openers = [
                "This reminds me of",
                "This brings to mind",
                "I'm remembering",
            ]
        elif event.confidence == "weak":
            openers = [
                "Not sure why, but this makes me think of",
                "My mind keeps drifting to",
                "There's something about this that connects to",
                "This feels related to",
            ]
        else:  # random
            openers = [
                "My mind wandered to something from",
                "Unrelated, but I was just thinking about",
                "This probably doesn't matter, but",
                "Random thought - I was remembering",
            ]

        opener = random.choice(openers)

        # Use summary (already truncated if needed)
        summary = event.memory_summary or MemoryAssociationGenerator._truncate(event.memory_content, 120)

        if event.confidence == "random":
            return f"{opener} {event.when}... {summary} (ID: {event.memory_id[:8]})"
        return f"{opener} {event.when}: {summary} (ID: {event.memory_id[:8]})"
