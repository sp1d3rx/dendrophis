from __future__ import annotations

from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

import numpy as np
import pytest

from dendrophis.memory.embedder import (
    NullEmbedder,
    OpenAIEmbedder,
    SpacyEmbedder,
    create_embedder,
)
from dendrophis.memory.memory import MemoryStore, _blob_to_vector, _vector_to_blob
from dendrophis.memory.models import MemoryEntry
from dendrophis.memory.project import (
    _EXTENSION_MAP,
    FileEntry,
    ProjectPrimer,
    _hash_file,
    _project_id,
    delete_primer,
    detect_project_root,
    list_primers,
    load_primer,
    save_primer,
)
from dendrophis.memory.search import MemorySearcher
from dendrophis.ui.screens.main import MainScreen
from dendrophis.ui.screens.memory_viewer import MemoryViewerScreen


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
            return iter([FakeToken(token_text) for token_text in self.text.split()])

    class FakeNLP:
        def __call__(self, text):
            return FakeDoc(text)

    db_file = tmp_path / "test_memory_with_nlp.db"
    return MemoryStore(str(db_file), nlp=FakeNLP())


class TestVectorBlobHelpersAndEntry:
    """Tests for vector/blob serialization and MemoryEntry model."""

    def test_vector_to_blob_and_back(self):
        """Round-trip vector serialization preserves data, and empty blob handles safely."""
        original = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        blob = _vector_to_blob(original)
        restored = _blob_to_vector(blob)
        np.testing.assert_array_almost_equal(original, restored)

        result_empty = _blob_to_vector(b"")
        assert len(result_empty) == 0

    def test_entry_serialization_roundtrip(self):
        """Serialization to/from dictionary works and strips raw embedding blob."""
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
        serialized_dict = entry.to_dict()
        assert serialized_dict["id"] == "test123"
        assert serialized_dict["content"] == "test content"
        assert serialized_dict["tags"] == ["tag1", "tag2"]
        assert "embedding_blob" not in serialized_dict

        loaded_entry = MemoryEntry.from_dict(serialized_dict)
        assert loaded_entry.id == "test123"
        assert loaded_entry.content == "test content"
        assert loaded_entry.tags == ["tag1", "tag2"]
        assert loaded_entry.score == 1.5


class TestMemoryStoreCRUDAndListing:
    """Consolidated CRUD and listing/filtering tests for MemoryStore."""

    def test_memory_store_lifecycle_and_crud(self, store):
        """Verify saving, retrieving, updating, and deleting memories."""
        # 1. Save and retrieve
        entry = store.save_memory(
            content="test content",
            tags=["test", "memory"],
            source="manual",
            project_id="proj1",
        )
        assert entry.id is not None
        assert len(entry.id) == 16

        retrieved = store.get_memory(entry.id)
        assert retrieved is not None
        assert retrieved.content == "test content"
        assert retrieved.tags == ["test", "memory"]

        # Get nonexistent
        assert store.get_memory("nonexistent") is None

        # 2. Update content and tags
        updated = store.update_memory(entry.id, content="updated content", tags=["new_tag"])
        assert updated is not None
        assert updated.content == "updated content"
        assert updated.tags == ["new_tag"]

        # 3. Delete memory
        assert store.delete_memory(entry.id) is True
        assert store.get_memory(entry.id) is None
        assert store.delete_memory("nonexistent") is False

    def test_list_memories_and_filtering(self, store):
        """Verify list_memories and its filter parameters."""
        assert store.list_memories() == []

        # Populate memory store
        store.save_memory(content="python code", project_id="project_one", source="manual", tags=["python", "code"])
        store.save_memory(content="rust code", project_id="project_two", source="auto", tags=["rust", "code"])
        store.save_memory(content="python data", project_id="project_one", source="manual", tags=["python", "data"])

        # Filter by project
        results_project = store.list_memories(project_id="project_one")
        assert len(results_project) == 2
        assert all(result.project_id == "project_one" for result in results_project)

        # Filter by source
        results_source = store.list_memories(source="auto")
        assert len(results_source) == 1
        assert results_source[0].content == "rust code"

        # Filter by tag
        results_tag = store.list_memories(tag="python")
        assert len(results_tag) == 2

        # Test limit and offset
        results_limit = store.list_memories(limit=1, offset=1)
        assert len(results_limit) == 1


class TestMemoryStoreStatsTagsAndScoring:
    """Consolidated stats, tags, and scoring tests for MemoryStore."""

    def test_stats_and_tag_lifecycle(self, store):
        """Verify store stats and tags reflect addition, update, and deletion of memories."""
        # Empty stats
        empty_stats = store.get_stats()
        assert empty_stats.total_memories == 0
        assert empty_stats.total_projects == 0

        # Save memories with tags
        entry_one = store.save_memory(content="first", project_id="project_one", tags=["tag1", "tag2"])
        store.save_memory(content="second", project_id="project_one", tags=["tag1"])
        store.save_memory(content="third", project_id="project_two", tags=["tag3"])

        stats = store.get_stats()
        assert stats.total_memories == 3
        assert stats.total_projects == 2
        assert stats.total_tags == 3

        tag_counts = dict(stats.top_tags)
        assert tag_counts.get("tag1") == 2
        assert tag_counts.get("tag2") == 1

        # Update to remove a tag
        store.update_memory(entry_one.id, tags=["tag1"])
        stats_updated = store.get_stats()
        tag_counts_updated = dict(stats_updated.top_tags)
        assert tag_counts_updated.get("tag1") == 2
        assert tag_counts_updated.get("tag2", 0) == 0

    def test_scoring_increments(self, store):
        """Verify entry score increments correctly."""
        entry = store.save_memory(content="score test")
        assert entry.score == 1.0
        store.increment_score(entry.id, amount=1.5)
        updated = store.get_memory(entry.id)
        assert updated is not None
        assert updated.score == 2.5


class TestMemoryStoreEmbeddingsAndSearch:
    """Consolidated tests for embeddings, similarity, and search functionality."""

    def test_embeddings_and_similarity(self, store, store_with_fake_nlp):
        """Verify embedding saving, computation, and similarity metrics."""
        # Save with embedding
        embedding_vector = np.random.randn(300).astype(np.float32)
        entry = store.save_memory(content="embedded", embedding=embedding_vector)
        retrieved = store.get_memory(entry.id)
        assert retrieved is not None
        np.testing.assert_array_almost_equal(embedding_vector, _blob_to_vector(retrieved.embedding_blob))

        # NLP embeddings
        assert store.compute_embedding("test") is None
        nlp_embedding = store_with_fake_nlp.compute_embedding("test")
        assert nlp_embedding is not None
        assert len(nlp_embedding) == 300

        # Cosine similarity
        vector_one = np.array([1.0, 0.0, 0.0])
        vector_two = np.array([1.0, 0.0, 0.0])
        assert store.cosine_similarity(vector_one, vector_two) == 1.0

        vector_three = np.array([0.0, 1.0, 0.0])
        assert store.cosine_similarity(vector_one, vector_three) == 0.0

        vector_zero = np.zeros(3)
        assert store.cosine_similarity(vector_zero, vector_one) == 0.0

    def test_searcher_operations(self, store):
        """Verify MemorySearcher results and filters."""
        searcher = MemorySearcher(store)
        assert searcher.search("query") == []

        # Populate and search
        store.save_memory(content="python programming language", project_id="project_one", tags=["python"])
        store.save_memory(content="rust programming language", project_id="project_two", tags=["rust"])

        results = searcher.search("python")
        assert len(results) >= 1
        assert "python" in results[0].memory.content

        results_tag = searcher.search("programming", tag="rust")
        assert len(results_tag) == 1
        assert "rust" in results_tag[0].memory.tags

        results_project = searcher.search("programming", project_id="project_one")
        assert len(results_project) == 1
        assert results_project[0].memory.project_id == "project_one"


class TestMemoryEmbedders:
    """Consolidated tests for NullEmbedder, SpacyEmbedder, OpenAIEmbedder, and factory."""

    def test_null_embedder(self):
        """Verify NullEmbedder properties and return value."""
        embedder = NullEmbedder()
        assert embedder.embed("test text") is None
        assert embedder.dimension == 0
        assert embedder.name == "none"

    def test_spacy_embedder(self):
        """Verify SpacyEmbedder embeds using spaCy NLP and exposes properties."""

        class FakeDoc:
            def __init__(self, text):
                self.text = text
                self.has_vector = True
                self.vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)

        class FakeNLP:
            meta: ClassVar[dict[str, str]] = {"name": "fake_model"}

            def __call__(self, text):
                return FakeDoc(text)

        embedder = SpacyEmbedder(FakeNLP())
        result = embedder.embed("test")
        assert result is not None
        np.testing.assert_array_almost_equal(result, [1.0, 2.0, 3.0])
        assert embedder.dimension == 300
        assert embedder.name == "spacy:fake_model"

    def test_openai_embedder(self):
        """Verify OpenAIEmbedder client interactions, models, and dimensions."""

        class FakeClient:
            pass

        embedder_small = OpenAIEmbedder(FakeClient(), model="text-embedding-3-small")
        assert embedder_small.dimension == 1536
        assert embedder_small.name == "openai:text-embedding-3-small"

        embedder_large = OpenAIEmbedder(FakeClient(), model="text-embedding-3-large")
        assert embedder_large.dimension == 3072

        with pytest.raises(ValueError, match="Unknown OpenAI embedding model"):
            OpenAIEmbedder(FakeClient(), model="unknown_model")

        # Fake embedding client calls
        class FakeEmbeddingData:
            embedding: ClassVar[list] = [1.0, 2.0, 3.0]

        class FakeResponse:
            data: ClassVar[list] = [FakeEmbeddingData()]

        class FakeEmbeddings:
            def create(self, model, input):
                return FakeResponse()

        class FakeEmbeddingClient:
            embeddings = FakeEmbeddings()

        embedder_client = OpenAIEmbedder(FakeEmbeddingClient(), model="text-embedding-3-small")
        result = embedder_client.embed("test")
        np.testing.assert_array_almost_equal(result, [1.0, 2.0, 3.0])

        # Exception handling
        class FakeFailingClient:
            def embeddings(self):
                return self

            def create(self, model, input):
                raise Exception("API error")

        embedder_failing = OpenAIEmbedder(FakeFailingClient(), model="text-embedding-3-small")
        assert embedder_failing.embed("test") is None

    def test_create_embedder_factory(self):
        """Verify create_embedder instantiates correct classes."""
        assert isinstance(create_embedder("none"), NullEmbedder)

        class FakeNLP:
            meta: ClassVar[dict] = {"name": "test"}

            def __call__(self, text):
                return type("Doc", (), {"has_vector": False})()

        assert isinstance(create_embedder("spacy", nlp=FakeNLP()), SpacyEmbedder)


class TestMemoryViewerScreenIntegration:
    """Consolidated test cases for MemoryViewerScreen."""

    def test_memory_viewer_screen_lifecycle_and_bindings(self):
        """Verify screen instantiation, hotkey bindings, and push screen actions."""
        session_mock = MagicMock()
        screen = MemoryViewerScreen(session=session_mock)
        assert screen._session == session_mock

        # MainScreen binding for ctrl+m
        has_binding = False
        for binding_tuple in MainScreen.BINDINGS:
            if binding_tuple[0] == "ctrl+m" and binding_tuple[1] == "open_memory_viewer":
                has_binding = True
                break
        assert has_binding is True

        # Open memory viewer action
        event_bus_mock = MagicMock()
        app_mock = MagicMock()

        from textual._context import active_app

        main_screen = MainScreen(session=session_mock, event_bus=event_bus_mock)

        reset_token = active_app.set(app_mock)
        try:
            main_screen.action_open_memory_viewer()
        finally:
            active_app.reset(reset_token)

        app_mock.push_screen.assert_called_once()
        arguments, _unused_kwargs = app_mock.push_screen.call_args
        assert isinstance(arguments[0], MemoryViewerScreen)
        assert arguments[0]._session == session_mock


class TestProjectMemorySystem:
    """Consolidated tests for project ID, hashing, FileEntry, ProjectPrimer, change detection, and IO."""

    def test_project_id_generation(self, tmp_path):
        """Verify project ID generation for git and non-git projects."""
        # Case 1: Git repo HTTPS
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        config_path = git_dir / "config"
        config_path.write_text('[remote "origin"]\n    url = https://github.com/user/repo.git\n')
        head = git_dir / "HEAD"
        head.write_text("ref: refs/heads/main\n")

        project_id = _project_id(tmp_path)
        assert project_id == "github.com/user/repo"

        # Case 2: Non-git project uses hashed path
        non_git_path = tmp_path / "nongit"
        non_git_path.mkdir()
        project_id_nongit = _project_id(non_git_path)
        assert project_id_nongit.startswith("anon:")
        assert len(project_id_nongit) == 17

    def test_file_hashing_and_entry(self, tmp_path):
        """Verify file hashing and FileEntry initialization."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")
        hash_one = _hash_file(test_file)
        hash_two = _hash_file(test_file)
        assert hash_one == hash_two
        assert len(hash_one) == 64
        assert _hash_file(Path("/nonexistent/file.txt")) is None

        # FileEntry defaults
        entry_default = FileEntry(path="test.py", content_hash="abc123")
        assert entry_default.path == "test.py"
        assert entry_default.language == ""

        # FileEntry fields
        entry_fields = FileEntry(
            path="src/main.py",
            content_hash="def456",
            language="python",
            summary="Main entry",
            size_bytes=1024,
        )
        assert entry_fields.language == "python"

    def test_project_primer_operations(self, tmp_path):
        """Verify ProjectPrimer field serialization and change detection."""
        primer = ProjectPrimer(project_id="test_project", project_root=str(tmp_path))
        assert primer.project_id == "test_project"
        assert primer.turn_count == 0

        # Serialization
        primer.add_file("main.py", "print('hello')", summary="main script")
        serialized_dict = primer.to_dict()
        assert serialized_dict["project_id"] == "test_project"
        assert len(serialized_dict["key_files"]) == 1

        deserialized_primer = ProjectPrimer.from_dict(serialized_dict)
        assert deserialized_primer.project_id == "test_project"
        assert len(deserialized_primer.key_files) == 1
        assert deserialized_primer.key_files[0].path == "main.py"

        # Change detection
        test_file = tmp_path / "main.py"
        test_file.write_text("print('hello')")
        assert primer.verify_files(tmp_path) == []

        test_file.write_text("print('changed')")
        changed_files = primer.verify_files(tmp_path)
        assert "main.py" in changed_files
        assert primer.has_stale_files()

        primer.mark_fresh("main.py")
        assert not primer.has_stale_files()

        # Language detection and extension map
        primer.add_file("main.rs", "fn main() {}")
        assert primer.key_files[1].language == "rust"
        assert _EXTENSION_MAP["py"] == "python"

        # Remove file
        primer.remove_file("main.py")
        assert len(primer.key_files) == 1

    def test_project_root_detection(self, tmp_path, monkeypatch):
        """Verify project root detection logic."""
        git_dir = tmp_path / "subdir" / ".git"
        git_dir.mkdir(parents=True)
        assert detect_project_root(str(tmp_path / "subdir" / "file.txt")) == tmp_path / "subdir"

        monkeypatch.chdir(tmp_path)
        assert detect_project_root() == tmp_path

    def test_primer_io_lifecycle(self, tmp_path):
        """Verify saving, loading, listing, and deleting primers."""
        import dendrophis.memory.project as project_module

        original = project_module._PRIMER_DIR
        project_module._PRIMER_DIR = tmp_path
        try:
            primer_one = ProjectPrimer(project_id="proj_one", project_root="/path/one", project_name="Project One")
            primer_one.add_file("main.py", "print('one')")
            save_primer(primer_one)

            primer_two = ProjectPrimer(project_id="proj_two", project_root="/path/two", project_name="Project Two")
            save_primer(primer_two)

            # Load
            loaded = load_primer("proj_one")
            assert loaded is not None
            assert loaded.project_name == "Project One"

            # List
            primer_list = list_primers()
            assert len(primer_list) == 2

            # Delete
            assert delete_primer("proj_one") is True
            assert load_primer("proj_one") is None
        finally:
            project_module._PRIMER_DIR = original
