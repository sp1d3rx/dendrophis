"""Pydantic models for the memory system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryEntry:
    """A single memory record."""

    id: str
    content: str
    summary: str = ""  # One-sentence summary for quick recall
    tags: list[str] = field(default_factory=list)
    source: str = ""  # "manual", "auto", "primer", "session"
    project_id: str = ""
    session_id: str = ""
    created_at: str = ""  # ISO format
    updated_at: str = ""  # ISO format
    score: float = 0.0  # Usage score for recency/frequency weighting
    # Transient: raw BLOB from SQLite (not serialized to dict)
    embedding_blob: bytes | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "summary": self.summary,
            "tags": self.tags,
            "source": self.source,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        return cls(
            id=data["id"],
            content=data["content"],
            summary=data.get("summary", ""),
            tags=data.get("tags", []),
            source=data.get("source", ""),
            project_id=data.get("project_id", ""),
            session_id=data.get("session_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            score=data.get("score", 0.0),
        )


@dataclass
class MemorySearchResult:
    """A memory returned from a search, with relevance score."""

    memory: MemoryEntry
    score: float  # 0..1 relevance score
    method: str = ""  # "vector", "ngram", "hybrid"


@dataclass
class MemoryStats:
    """Aggregate memory statistics."""

    total_memories: int = 0
    total_projects: int = 0
    total_tags: int = 0
    memories_by_source: dict[str, int] = field(default_factory=dict)
    top_tags: list[tuple[str, int]] = field(default_factory=list)
