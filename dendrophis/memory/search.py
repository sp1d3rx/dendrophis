"""Memory search — spaCy vector similarity + ngram fallback + tag filtering."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np

from dendrophis.memory.memory import MemoryStore, _blob_to_vector
from dendrophis.memory.models import MemorySearchResult

if TYPE_CHECKING:
    from dendrophis.memory.models import MemoryEntry


class MemorySearcher:
    """Search memories using spaCy embeddings, ngram overlap, or hybrid scoring."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Public search API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 5,
        project_id: str | None = None,
        tag: str | None = None,
        source: str | None = None,
        min_score: float = 0.0,
        project_ids: list[str] | None = None,
    ) -> list[MemorySearchResult]:
        """Search memories for a query using hybrid vector + ngram + recency + usage scoring."""
        candidates = self._store.list_memories(
            project_id=project_id,
            source=source,
            tag=tag,
            limit=500,
            project_ids=project_ids,
        )

        if not candidates:
            return []

        query_vec = self._store.compute_embedding(query)
        query_norm = float(np.linalg.norm(query_vec)) if query_vec is not None else 0.0

        query_tokens = re.findall(r"[a-z0-9]+", query.lower())
        ngram_size = 1 if len(query_tokens) < 2 else 2
        query_ngrams = Counter(self._tokens_to_ngrams(query_tokens, ngram_size))
        reference_now = datetime.now()

        scored: list[tuple[float, MemoryEntry, str]] = []
        for entry in candidates:
            vec_score = 0.0
            has_matching_vector = False
            if query_vec is not None and query_norm > 0 and entry.embedding_blob is not None:
                entry_vec = _blob_to_vector(entry.embedding_blob)
                if entry_vec.shape == query_vec.shape:
                    has_matching_vector = True
                    entry_norm = float(np.linalg.norm(entry_vec))
                    if entry_norm > 0:
                        vec_score = float(np.dot(query_vec, entry_vec) / (query_norm * entry_norm))

            ngram_score = self._ngram_similarity_with_counter(query_ngrams, entry.content, ngram_size)
            recency_score = self._recency_score(entry.created_at, now=reference_now)
            usage_score = min(entry.score / 10.0, 1.0)

            if has_matching_vector:
                final_score = 0.70 * vec_score + 0.20 * ngram_score + 0.05 * recency_score + 0.05 * usage_score
                method = "hybrid"
            else:
                final_score = 0.90 * ngram_score + 0.07 * recency_score + 0.03 * usage_score
                method = "ngram"

            if final_score >= min_score:
                scored.append((final_score, entry, method))

        scored.sort(key=lambda scored_tuple: scored_tuple[0], reverse=True)
        return [
            MemorySearchResult(memory=entry, score=round(score, 4), method=method)
            for score, entry, method in scored[:limit]
        ]

    def search_by_tag(
        self,
        tag: str,
        limit: int = 10,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
    ) -> list[MemorySearchResult]:
        """Get memories for a tag, sorted by recency and usage score."""
        candidates = self._store.list_memories(
            project_id=project_id,
            tag=tag,
            limit=200,
            project_ids=project_ids,
        )
        if not candidates:
            return []

        reference_now = datetime.now()
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in candidates:
            recency = self._recency_score(entry.created_at, now=reference_now)
            usage = min(entry.score / 10.0, 1.0)
            final = 0.6 * recency + 0.4 * usage
            scored.append((final, entry))

        scored.sort(key=lambda scored_tuple: scored_tuple[0], reverse=True)
        return [
            MemorySearchResult(memory=entry, score=round(score, 4), method="tag") for score, entry in scored[:limit]
        ]

    def search_by_project(
        self,
        project_id: str,
        query: str | None = None,
        limit: int = 10,
    ) -> list[MemorySearchResult]:
        """Search within a specific project. If query is None, return all memories."""
        if query:
            return self.search(query, limit=limit, project_id=project_id)
        candidates = self._store.list_memories(project_id=project_id, limit=limit)
        return [MemorySearchResult(memory=entry, score=1.0, method="project") for entry in candidates]

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ngram_similarity(query: str, content: str, ngram_size: int = 2) -> float:
        """Compute n-gram overlap similarity between query and content."""
        query_tokens = re.findall(r"[a-z0-9]+", query.lower())
        actual_size = 1 if len(query_tokens) < ngram_size else ngram_size
        query_ngrams = Counter(MemorySearcher._tokens_to_ngrams(query_tokens, actual_size))
        return MemorySearcher._ngram_similarity_with_counter(query_ngrams, content, actual_size)

    @staticmethod
    def _ngram_similarity_with_counter(query_ngrams: Counter[str], content: str, ngram_size: int) -> float:
        if not query_ngrams:
            return 0.0
        content_ngrams = Counter(MemorySearcher._token_ngrams(content, ngram_size))
        if not content_ngrams:
            return 0.0
        intersection = sum((query_ngrams & content_ngrams).values())
        union = sum((query_ngrams | content_ngrams).values())
        if union == 0:
            return 0.0
        return intersection / union

    @staticmethod
    def _tokens_to_ngrams(tokens: list[str], ngram_size: int) -> list[str]:
        if len(tokens) < ngram_size:
            return tokens
        return [
            " ".join(tokens[start_idx : start_idx + ngram_size]) for start_idx in range(len(tokens) - ngram_size + 1)
        ]

    @staticmethod
    def _token_ngrams(text: str, ngram_size: int) -> list[str]:
        """Extract n-grams from text (lowercased, alphanumeric tokens)."""
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        return MemorySearcher._tokens_to_ngrams(tokens, ngram_size)

    @staticmethod
    def _recency_score(created_at: str, now: datetime | None = None) -> float:
        """Compute recency score (0..1) with exponential decay (half-life 30 days)."""
        try:
            created = datetime.fromisoformat(created_at)
            if created.tzinfo is not None:
                created = created.astimezone().replace(tzinfo=None)
            current_time = now if now is not None else datetime.now()
            age_days = (current_time - created).total_seconds() / 86400
            half_life = 30.0
            return 0.5 ** (age_days / half_life)
        except (ValueError, TypeError):
            return 0.5

    # ------------------------------------------------------------------
    # Tag autocomplete
    # ------------------------------------------------------------------

    def autocomplete_tags(self, prefix: str, limit: int = 10) -> list[tuple[str, int]]:
        """Get tag suggestions matching a prefix, sorted by usage count."""
        if not prefix:
            return self._store.get_stats().top_tags[:limit]
        with self._store._connect() as conn:
            cursor = conn.cursor()
            try:
                rows = cursor.execute(
                    "SELECT name, memory_count FROM tags WHERE name LIKE ? ORDER BY memory_count DESC LIMIT ?",
                    (f"{prefix}%", limit),
                ).fetchall()
                return [(row["name"], row["memory_count"]) for row in rows]
            finally:
                cursor.close()
