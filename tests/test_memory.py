"""Tests for the memory system."""

from __future__ import annotations

import numpy as np
import pytest

from dendrophis.memory.memory import MemoryStore, _blob_to_vector, _vector_to_blob
from dendrophis.memory.models import MemoryEntry
from dendrophis.memory.search import MemorySearcher

# Fixtures


@pytest.fixture
def store(tmp_path):
    """Create a MemoryStore instance without spaCy model."""
    db_file = tmp_path / "test_memory.db"
    return MemoryStore(str(db_file), nlp=None)


@pytest.fixture
def store_with_fake_nlp(tmp_path):
    """Create a MemoryStore with a fake NLP model for embedding tests."""

    class FakeToken:
        def __init__(self, text):
            self.text = text
            self.has_vector = True
            self.vector = np.random.randn(300).astype(np.float32)

    class FakeDoc:
        def __init__(self, text):
            self.text = text
            self.has_vector = True
            self.vector = np.random.randn(300).astype(np.float32)

        def __iter__(self):
            return iter([FakeToken(t) for t in self.text.split()])

    class FakeNLP:
        def __call__(self, text):
            return FakeDoc(text)

    db_file = tmp_path / "test_memory_with_nlp.db"
    return MemoryStore(str(db_file), nlp=FakeNLP())


# Tests for vector/BLOB helpers


class TestVectorBlobHelpers:
    """Tests for vector serialization helpers."""

    def test_vector_to_blob_and_back(self):
        """Round-trip vector serialization preserves data."""
        original = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        blob = _vector_to_blob(original)
        restored = _blob_to_vector(blob)
        np.testing.assert_array_almost_equal(original, restored)

    def test_blob_to_vector_empty(self):
        """Empty blob returns empty array."""
        result = _blob_to_vector(b"")
        assert len(result) == 0


# Tests for MemoryEntry


class TestMemoryEntry:
    """Tests for MemoryEntry dataclass."""

    def test_to_dict(self):
        """Serialize entry to dict excludes embedding_blob."""
        entry = MemoryEntry(
            id="test123",
            content="test content",
            tags=["tag1", "tag2"],
            source="manual",
            project_id="proj1",
            session_id="sess1",
            created_at="2024-01-01",
            updated_at="2024-01-02",
            score=1.5,
            embedding_blob=b"some_blob",
        )
        d = entry.to_dict()
        assert d["id"] == "test123"
        assert d["content"] == "test content"
        assert d["tags"] == ["tag1", "tag2"]
        assert "embedding_blob" not in d

    def test_from_dict(self):
        """Deserialize entry from dict."""
        data = {
            "id": "test456",
            "content": "hello",
            "tags": ["a", "b"],
            "source": "auto",
            "project_id": "p1",
            "session_id": "s1",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-02",
            "score": 2.0,
        }
        entry = MemoryEntry.from_dict(data)
        assert entry.id == "test456"
        assert entry.content == "hello"
        assert entry.tags == ["a", "b"]
        assert entry.score == 2.0


# Tests for MemoryStore CRUD


class TestMemoryStoreCRUD:
    """Tests for MemoryStore CRUD operations."""

    def test_save_and_get(self, store):
        """Save and retrieve a memory."""
        entry = store.save_memory(
            content="test content",
            tags=["test", "memory"],
            source="manual",
            project_id="proj1",
        )
        assert entry.id is not None
        assert len(entry.id) == 16  # uuid hex[:16]

        retrieved = store.get_memory(entry.id)
        assert retrieved is not None
        assert retrieved.content == "test content"
        assert retrieved.tags == ["test", "memory"]
        assert retrieved.source == "manual"
        assert retrieved.project_id == "proj1"

    def test_get_nonexistent(self, store):
        """Get returns None for nonexistent ID."""
        assert store.get_memory("nonexistent") is None

    def test_update_memory(self, store):
        """Update memory fields."""
        entry = store.save_memory(content="original")
        updated = store.update_memory(entry.id, content="updated")
        assert updated is not None
        assert updated.content == "updated"

    def test_update_tags(self, store):
        """Update memory tags."""
        entry = store.save_memory(content="test", tags=["old"])
        updated = store.update_memory(entry.id, tags=["new", "tags"])
        assert updated is not None
        assert updated.tags == ["new", "tags"]

    def test_delete_memory(self, store):
        """Delete a memory."""
        entry = store.save_memory(content="to delete")
        assert store.get_memory(entry.id) is not None
        result = store.delete_memory(entry.id)
        assert result is True
        assert store.get_memory(entry.id) is None

    def test_delete_nonexistent(self, store):
        """Delete returns False for nonexistent ID."""
        assert store.delete_memory("nonexistent") is False


# Tests for MemoryStore listing and filtering


class TestMemoryStoreListing:
    """Tests for MemoryStore list and filter operations."""

    def test_list_memories_empty(self, store):
        """List returns empty for new store."""
        assert store.list_memories() == []

    def test_list_memories_all(self, store):
        """List returns all memories."""
        store.save_memory(content="a", project_id="p1")
        store.save_memory(content="b", project_id="p2")
        store.save_memory(content="c", project_id="p1")
        results = store.list_memories()
        assert len(results) == 3

    def test_list_memories_by_project(self, store):
        """Filter memories by project_id."""
        store.save_memory(content="a", project_id="p1")
        store.save_memory(content="b", project_id="p2")
        store.save_memory(content="c", project_id="p1")
        results = store.list_memories(project_id="p1")
        assert len(results) == 2
        assert all(r.project_id == "p1" for r in results)

    def test_list_memories_by_source(self, store):
        """Filter memories by source."""
        store.save_memory(content="a", source="manual")
        store.save_memory(content="b", source="auto")
        store.save_memory(content="c", source="manual")
        results = store.list_memories(source="manual")
        assert len(results) == 2
        assert all(r.source == "manual" for r in results)

    def test_list_memories_by_tag(self, store):
        """Filter memories by tag."""
        store.save_memory(content="a", tags=["python", "code"])
        store.save_memory(content="b", tags=["rust", "code"])
        store.save_memory(content="c", tags=["python", "data"])
        results = store.list_memories(tag="python")
        assert len(results) == 2

    def test_list_memories_limit_offset(self, store):
        """Limit and offset work correctly."""
        for i in range(10):
            store.save_memory(content=f"entry{i}")
        results = store.list_memories(limit=3, offset=2)
        assert len(results) == 3
        # Ordered by created_at DESC, so offset 2 skips first 2


# Tests for MemoryStore stats


class TestMemoryStoreStats:
    """Tests for MemoryStore statistics."""

    def test_stats_empty(self, store):
        """Stats for empty store."""
        stats = store.get_stats()
        assert stats.total_memories == 0
        assert stats.total_projects == 0
        assert stats.total_tags == 0

    def test_stats_with_data(self, store):
        """Stats reflect saved data."""
        store.save_memory(content="a", project_id="p1", tags=["tag1", "tag2"])
        store.save_memory(content="b", project_id="p1", tags=["tag1"])
        store.save_memory(content="c", project_id="p2", tags=["tag3"])
        stats = store.get_stats()
        assert stats.total_memories == 3
        assert stats.total_projects == 2
        assert stats.total_tags == 3
        assert stats.memories_by_source["auto"] == 3


# Tests for MemoryStore tag management


class TestMemoryStoreTags:
    """Tests for MemoryStore tag operations."""

    def test_tag_count_increment(self, store):
        """Tag counts are tracked."""
        store.save_memory(content="a", tags=["tag1"])
        store.save_memory(content="b", tags=["tag1", "tag2"])
        stats = store.get_stats()
        tag_counts = dict(stats.top_tags)
        assert tag_counts.get("tag1", 0) == 2
        assert tag_counts.get("tag2", 0) == 1

    def test_tag_count_decrement_on_update(self, store):
        """Tag counts decrement when tags are removed."""
        entry = store.save_memory(content="a", tags=["tag1", "tag2"])
        store.update_memory(entry.id, tags=["tag1"])
        stats = store.get_stats()
        tag_counts = dict(stats.top_tags)
        assert tag_counts.get("tag1", 0) == 1
        assert tag_counts.get("tag2", 0) == 0  # or not present


# Tests for MemoryStore scoring


class TestMemoryStoreScoring:
    """Tests for MemoryStore scoring system."""

    def test_increment_score(self, store):
        """Score increments correctly."""
        entry = store.save_memory(content="test")
        # save_memory calls increment_score with amount=1.0, so initial score is 1.0
        assert entry.score == 1.0
        store.increment_score(entry.id, amount=1.0)
        updated = store.get_memory(entry.id)
        assert updated is not None
        assert updated.score == 2.0
        store.increment_score(entry.id, amount=2.5)
        updated = store.get_memory(entry.id)
        assert updated is not None
        assert updated.score == 4.5


# Tests for MemoryStore embeddings


class TestMemoryStoreEmbeddings:
    """Tests for MemoryStore embedding operations."""

    def test_save_with_embedding(self, store):
        """Save memory with embedding blob."""
        embedding = np.random.randn(300).astype(np.float32)
        entry = store.save_memory(content="test", embedding=embedding)
        retrieved = store.get_memory(entry.id)
        assert retrieved is not None
        assert retrieved.embedding_blob is not None
        restored = _blob_to_vector(retrieved.embedding_blob)
        np.testing.assert_array_almost_equal(embedding, restored)

    def test_compute_embedding_without_nlp(self, store):
        """compute_embedding returns None without NLP model."""
        assert store.nlp is None
        assert store.compute_embedding("test text") is None

    def test_compute_embedding_with_nlp(self, store_with_fake_nlp):
        """compute_embedding returns vector with NLP model."""
        result = store_with_fake_nlp.compute_embedding("test text")
        assert result is not None
        assert len(result) == 300

    def test_cosine_similarity(self, store):
        """Cosine similarity computed correctly."""
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([1.0, 0.0, 0.0])
        assert store.cosine_similarity(a, b) == 1.0

        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        assert store.cosine_similarity(a, b) == 0.0

        a = np.array([1.0, 1.0, 0.0])
        b = np.array([1.0, 0.0, 0.0])
        # cos_sim = (1*1 + 1*0 + 0*0) / (sqrt(2) * 1) = 1/sqrt(2)
        expected = 1.0 / (2**0.5)
        assert abs(store.cosine_similarity(a, b) - expected) < 0.001

    def test_cosine_similarity_zero_vectors(self, store):
        """Cosine similarity returns 0 for zero vectors."""
        a = np.zeros(3)
        b = np.array([1.0, 0.0, 0.0])
        assert store.cosine_similarity(a, b) == 0.0


# Tests for MemorySearcher


class TestMemorySearcher:
    """Tests for MemorySearcher."""

    def test_search_empty(self, store):
        """Search on empty store returns no results."""
        searcher = MemorySearcher(store)
        results = searcher.search("test query")
        assert results == []

    def test_search_ngram_only(self, store):
        """Search works with ngram fallback when no embeddings."""
        store.save_memory(content="python programming language")
        store.save_memory(content="rust programming language")
        store.save_memory(content="cooking recipes")
        searcher = MemorySearcher(store)
        results = searcher.search("python")
        assert len(results) >= 1
        assert any("python" in r.memory.content.lower() for r in results)

    def test_search_exact_match(self, store):
        """Exact match scores highest for ngram."""
        store.save_memory(content="exact match")
        store.save_memory(content="partial match here")
        searcher = MemorySearcher(store)
        results = searcher.search("exact match")
        assert len(results) >= 1
        assert results[0].memory.content == "exact match"

    def test_search_by_tag(self, store):
        """Search by tag returns correct memories."""
        store.save_memory(content="a", tags=["python"])
        store.save_memory(content="b", tags=["rust"])
        store.save_memory(content="c", tags=["python"])
        searcher = MemorySearcher(store)
        results = searcher.search("test", tag="python")
        assert len(results) == 2
        assert all("python" in r.memory.tags for r in results)

    def test_search_by_project(self, store):
        """Search by project filters correctly."""
        store.save_memory(content="a", project_id="p1")
        store.save_memory(content="b", project_id="p2")
        store.save_memory(content="c", project_id="p1")
        searcher = MemorySearcher(store)
        results = searcher.search("test", project_id="p1")
        assert len(results) == 2
        assert all(r.memory.project_id == "p1" for r in results)

    def test_search_min_score(self, store):
        """min_score filters low-scoring results."""
        store.save_memory(content="xyz abc def")
        searcher = MemorySearcher(store)
        # With no matching content, ngram score is 0 but recency/usage contribute
        # Set min_score high enough to filter out non-matching results
        results = searcher.search("match", min_score=0.5)
        assert len(results) == 0

    def test_search_limit(self, store):
        """Limit restricts number of results."""
        for i in range(10):
            store.save_memory(content=f"similar content {i}")
        searcher = MemorySearcher(store)
        results = searcher.search("similar", limit=3)
        assert len(results) <= 3

    def test_search_by_tag_dedicated(self, store):
        """search_by_tag works correctly."""
        store.save_memory(content="a", tags=["tag1"])
        store.save_memory(content="b", tags=["tag2"])
        searcher = MemorySearcher(store)
        results = searcher.search_by_tag("tag1")
        assert len(results) == 1
        assert results[0].memory.tags == ["tag1"]

    def test_search_by_project_dedicated(self, store):
        """search_by_project works correctly."""
        store.save_memory(content="a", project_id="p1")
        store.save_memory(content="b", project_id="p1")
        searcher = MemorySearcher(store)
        results = searcher.search_by_project("p1")
        assert len(results) == 2

    def test_autocomplete_tags(self, store):
        """Tag autocomplete returns matching tags."""
        store.save_memory(content="a", tags=["python-ai"])
        store.save_memory(content="b", tags=["python-web"])
        store.save_memory(content="c", tags=["rust-compiler"])
        searcher = MemorySearcher(store)
        results = searcher.autocomplete_tags("python")
        assert len(results) >= 2
        assert all(tag.startswith("python") for tag, _ in results)


# Tests for ngram similarity


class TestNgramSimilarity:
    """Tests for ngram similarity scoring."""

    def test_ngram_similarity_identical(self):
        """Identical text has ngram similarity of 1.0."""
        score = MemorySearcher._ngram_similarity("hello world", "hello world")
        assert score == 1.0

    def test_ngram_similarity_disjoint(self):
        """Completely different text has low similarity."""
        score = MemorySearcher._ngram_similarity("hello world", "goodbye moon")
        assert score < 0.5

    def test_ngram_similarity_partial(self):
        """Partial overlap has medium similarity."""
        score = MemorySearcher._ngram_similarity("the cat sat", "the cat")
        assert 0.0 < score < 1.0

    def test_ngram_similarity_empty(self):
        """Empty text has 0 similarity."""
        score = MemorySearcher._ngram_similarity("", "hello")
        assert score == 0.0

    def test_token_ngrams(self):
        """Token ngrams extracted correctly."""
        ngrams = MemorySearcher._token_ngrams("hello world", n=2)
        assert ngrams == ["hello world"]

        ngrams = MemorySearcher._token_ngrams("a b c d", n=2)
        assert ngrams == ["a b", "b c", "c d"]


# Tests for recency scoring


class TestRecencyScoring:
    """Tests for recency scoring."""

    def test_recency_score_recent(self):
        """Recent dates score close to 1.0."""
        from datetime import datetime, timedelta

        recent = (datetime.now() - timedelta(days=1)).isoformat()
        score = MemorySearcher._recency_score(recent)
        assert score > 0.9  # Should be close to 1 for 1-day-old

    def test_recency_score_old(self):
        """Old dates score close to 0.0."""
        from datetime import datetime, timedelta

        old = (datetime.now() - timedelta(days=365)).isoformat()
        score = MemorySearcher._recency_score(old)
        assert score < 0.1  # Should be near 0 for 1-year-old

    def test_recency_score_invalid(self):
        """Invalid dates return default score."""
        score = MemorySearcher._recency_score("not a date")
        assert score == 0.5  # default


# Tests for hybrid search with embeddings


class TestHybridSearch:
    """Tests for hybrid vector + ngram search."""

    def test_hybrid_search_with_embeddings(self, store_with_fake_nlp):
        """Hybrid search uses vector similarity when embeddings available."""
        # Save with embedding
        store_with_fake_nlp.save_memory(
            content="machine learning python",
            embedding=np.random.randn(300).astype(np.float32),
        )
        searcher = MemorySearcher(store_with_fake_nlp)
        results = searcher.search("machine learning")
        assert len(results) >= 1
        assert results[0].method == "hybrid"

    def test_hybrid_score_components(self, store_with_fake_nlp):
        """Hybrid score includes all components."""
        store_with_fake_nlp.save_memory(
            content="test content",
            embedding=np.random.randn(300).astype(np.float32),
        )
        searcher = MemorySearcher(store_with_fake_nlp)
        results = searcher.search("test")
        assert len(results) >= 1
        # Score should be between 0 and 1
        assert 0 <= results[0].score <= 1


@pytest.mark.anyio
async def test_search_memory_tool_query_optional(store):
    from dendrophis.tools.builtins.memory import SearchMemoryTool

    # Save some memories
    store.save_memory(content="memory with python tag", tags=["python"])
    store.save_memory(content="memory with rust tag", tags=["rust"])

    tool = SearchMemoryTool(store)

    # 1. Search with only tag (no query)
    result = await tool.execute(query=None, tag="python")
    assert result["success"] is True
    assert len(result["results"]) == 1
    assert "python" in result["results"][0]["tags"]

    # 2. Search with neither query nor tag
    all_results = await tool.execute(query=None)
    assert all_results["success"] is True
    assert len(all_results["results"]) == 2
