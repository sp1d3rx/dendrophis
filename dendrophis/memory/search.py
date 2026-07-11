"""Memory search — spaCy vector similarity + ngram fallback + tag filtering."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

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
    ) -> list[MemorySearchResult]:
        """Search memories for a query.

        Uses a hybrid approach:
        1. If spaCy model is available: vector similarity (cosine)
        2. Always: ngram token overlap as fallback/complement
        3. Tag filtering applied first to narrow the candidate set
        4. Final score = weighted blend of vector + ngram + recency + usage score

        Returns top-k results sorted by relevance.
        """
        # Step 1: Get candidate memories (filtered by tag/project/source)
        candidates = self._store.list_memories(
            project_id=project_id,
            source=source,
            tag=tag,
            limit=500,  # generous candidate set
        )

        if not candidates:
            return []

        # Step 2: Compute query embedding if available
        query_vec = self._store.compute_embedding(query)

        # Step 3: Score each candidate
        scored: list[tuple[float, MemoryEntry, str]] = []
        for entry in candidates:
            # Vector score
            vec_score = 0.0
            if query_vec is not None and entry.embedding_blob is not None:
                entry_vec = _blob_to_vector(entry.embedding_blob)
                vec_score = self._store.cosine_similarity(query_vec, entry_vec)

            # Ngram score
            ngram_score = self._ngram_similarity(query, entry.content)

            # Recency bonus (exponential decay, half-life ~30 days)
            recency_score = self._recency_score(entry.created_at)

            # Usage score (boost frequently-referenced memories)
            usage_score = min(entry.score / 10.0, 1.0)  # cap at 1.0

            # Hybrid blend
            if query_vec is not None and entry.embedding_blob is not None:
                # Vector-heavy when embeddings available
                final_score = 0.50 * vec_score + 0.20 * ngram_score + 0.15 * recency_score + 0.15 * usage_score
                method = "hybrid"
            else:
                # Ngram-only fallback
                final_score = 0.60 * ngram_score + 0.25 * recency_score + 0.15 * usage_score
                method = "ngram"

            if final_score >= min_score:
                scored.append((final_score, entry, method))

        # Step 4: Sort by score descending, return top-k
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            MemorySearchResult(memory=entry, score=round(score, 4), method=method)
            for score, entry, method in scored[:limit]
        ]

    def search_by_tag(
        self,
        tag: str,
        limit: int = 10,
        project_id: str | None = None,
    ) -> list[MemorySearchResult]:
        """Get memories for a tag, sorted by recency and usage score."""
        candidates = self._store.list_memories(project_id=project_id, tag=tag, limit=200)
        if not candidates:
            return []

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in candidates:
            recency = self._recency_score(entry.created_at)
            usage = min(entry.score / 10.0, 1.0)
            final = 0.6 * recency + 0.4 * usage
            scored.append((final, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
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
    def _ngram_similarity(query: str, content: str, n: int = 2) -> float:
        """Compute n-gram overlap similarity between query and content.

        Uses Jaccard-like normalization: intersection / union of n-gram sets.
        """
        query_ngrams = Counter(MemorySearcher._token_ngrams(query, n))
        content_ngrams = Counter(MemorySearcher._token_ngrams(content, n))

        if not query_ngrams or not content_ngrams:
            return 0.0

        # Intersection over union (Jaccard)
        intersection = sum((query_ngrams & content_ngrams).values())
        union = sum((query_ngrams | content_ngrams).values())
        if union == 0:
            return 0.0

        return intersection / union

    @staticmethod
    def _token_ngrams(text: str, n: int) -> list[str]:
        """Extract n-grams from text (lowercased, alphanumeric tokens)."""
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        if len(tokens) < n:
            return tokens
        return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]

    @staticmethod
    def _recency_score(created_at: str) -> float:
        """Compute recency score (0..1) with exponential decay.

        Half-life of 30 days.
        """
        from datetime import datetime

        try:
            created = datetime.fromisoformat(created_at)
            age_days = (datetime.now() - created).total_seconds() / 86400
            half_life = 30.0
            return 0.5 ** (age_days / half_life)
        except (ValueError, TypeError):
            return 0.5  # default for unknown dates

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
