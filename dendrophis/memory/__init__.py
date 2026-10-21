"""Memory system — persistent, searchable memory for Dendrophis.

Provides:
- MemoryStore: SQLite-backed CRUD with pluggable embedding storage
- MemorySearcher: hybrid vector + ngram search with tag filtering
- Auto-save: memories captured from tool results, edits, and session context
- Embedders: pluggable embedding backends (spaCy, OpenAI, or none)
"""

from __future__ import annotations

from dendrophis.memory.embedder import (
    BaseEmbedder,
    NullEmbedder,
    OpenAIEmbedder,
    SpacyEmbedder,
    create_embedder,
)
from dendrophis.memory.memory import MemoryStore
from dendrophis.memory.models import MemoryEntry, MemorySearchResult, MemoryStats
from dendrophis.memory.scope import GLOBAL_PROJECT_ID, current_project_id, default_scope, scope_for_project
from dendrophis.memory.search import MemorySearcher

__all__ = [
    "GLOBAL_PROJECT_ID",
    "BaseEmbedder",
    "MemoryEntry",
    "MemorySearchResult",
    "MemorySearcher",
    "MemoryStats",
    "MemoryStore",
    "NullEmbedder",
    "OpenAIEmbedder",
    "SpacyEmbedder",
    "create_embedder",
    "current_project_id",
    "default_scope",
    "scope_for_project",
]
