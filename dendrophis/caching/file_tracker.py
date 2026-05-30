"""File block tracking for caching stability and invalidation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dendrophis.utils import _hash_content


@dataclass
class FileBlock:
    """Metadata for a cached file block."""

    path: str
    content_hash: str
    turn_added: int
    turns_stable: int = 0
    cached: bool = False
    message_index: int = -1

    def is_stable_for(self, current_turn: int, threshold: int = 3) -> bool:
        """Check if file has been stable long enough to cache."""
        turns_since_added = current_turn - self.turn_added
        return turns_since_added >= threshold

    def validate_hash(self, current_content: str) -> bool:
        """Check if current file content matches cached hash."""
        current_hash = _hash_content(current_content)
        return current_hash == self.content_hash


@dataclass
class FileBlockTracker:
    """Tracks file reads, hashing, and cache state."""

    _files: dict[str, FileBlock] = None  # path -> FileBlock
    _stable_threshold: int = 3  # Mark cacheable after N turns

    def __post_init__(self) -> None:
        if self._files is None:
            self._files = {}

    def track_file(self, path: str, content: str, turn: int, message_index: int) -> None:
        """Register a file read at current turn."""
        content_hash = _hash_content(content)

        # If same file, update stability counter; else reset
        if path in self._files:
            self._files[path].turns_stable += 1
            self._files[path].message_index = message_index
        else:
            self._files[path] = FileBlock(
                path=path,
                content_hash=content_hash,
                turn_added=turn,
                message_index=message_index,
            )

    def mark_cacheable(self, path: str) -> None:
        """Mark a file block as cacheable."""
        if path in self._files:
            self._files[path].cached = True

    def get_cacheable_files(self, current_turn: int) -> list[str]:
        """Return list of files ready to cache (stable for N+ turns)."""
        cacheable = []
        for path, block in self._files.items():
            if not block.cached and block.is_stable_for(current_turn, self._stable_threshold):
                cacheable.append(path)
        return cacheable

    def invalidate_file(self, path: str) -> None:
        """Invalidate cache for a file (content changed or user signal)."""
        if path in self._files:
            self._files[path].cached = False
            self._files[path].turns_stable = 0

    def invalidate_all(self) -> None:
        """Invalidate all file caches (user says 'project changed')."""
        for block in self._files.values():
            block.cached = False
            block.turns_stable = 0

    def check_file_changed(self, path: str, current_content: str) -> bool:
        """Check if a file's content has changed since last read."""
        if path not in self._files:
            return False
        return not self._files[path].validate_hash(current_content)

    def get_file_message_index(self, path: str) -> int:
        """Get the message index for a cached file block."""
        if path in self._files:
            return self._files[path].message_index
        return -1

    def list_cached_files(self) -> list[str]:
        """Return list of currently cached files."""
        return [path for path, block in self._files.items() if block.cached]

    def get_stats(self) -> dict[str, Any]:
        """Return caching statistics."""
        total = len(self._files)
        cached = len([block for block in self._files.values() if block.cached])
        return {
            "total_files_tracked": total,
            "files_cached": cached,
            "files_pending": total - cached,
            "cache_threshold": self._stable_threshold,
        }
