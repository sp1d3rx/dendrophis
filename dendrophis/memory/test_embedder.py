"""Tests for the embedder module."""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pytest

from dendrophis.memory.embedder import (
    NullEmbedder,
    OpenAIEmbedder,
    SpacyEmbedder,
    create_embedder,
)

# Tests for NullEmbedder


class TestNullEmbedder:
    """Tests for NullEmbedder."""

    def test_embed_returns_none(self):
        """embed always returns None."""
        embedder = NullEmbedder()
        assert embedder.embed("test text") is None

    def test_dimension_is_zero(self):
        """dimension property returns 0."""
        embedder = NullEmbedder()
        assert embedder.dimension == 0

    def test_name(self):
        """name property returns 'none'."""
        embedder = NullEmbedder()
        assert embedder.name == "none"


# Tests for SpacyEmbedder


class TestSpacyEmbedder:
    """Tests for SpacyEmbedder."""

    def test_embed_with_fake_nlp(self):
        """embed returns vector with fake NLP model."""

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
        assert len(result) == 3
        np.testing.assert_array_almost_equal(result, [1.0, 2.0, 3.0])

    def test_dimension(self):
        """dimension returns 300."""

        class FakeNLP:
            meta: ClassVar[dict] = {"name": "fake_model"}

            def __call__(self, text):
                return type("Doc", (), {"has_vector": False})()

        embedder = SpacyEmbedder(FakeNLP())
        assert embedder.dimension == 300

    def test_name(self):
        """name includes model name."""

        class FakeNLP:
            meta: ClassVar[dict] = {"name": "en_core_web_md"}

            def __call__(self, text):
                return type("Doc", (), {"has_vector": False})()

        embedder = SpacyEmbedder(FakeNLP())
        assert embedder.name == "spacy:en_core_web_md"


# Tests for OpenAIEmbedder


class TestOpenAIEmbedder:
    """Tests for OpenAIEmbedder."""

    def test_dimension_text_embedding_3_small(self):
        """dimension returns 1536 for text-embedding-3-small."""

        class FakeClient:
            pass

        embedder = OpenAIEmbedder(FakeClient(), model="text-embedding-3-small")
        assert embedder.dimension == 1536

    def test_dimension_text_embedding_3_large(self):
        """dimension returns 3072 for text-embedding-3-large."""

        class FakeClient:
            pass

        embedder = OpenAIEmbedder(FakeClient(), model="text-embedding-3-large")
        assert embedder.dimension == 3072

    def test_dimension_ada_002(self):
        """dimension returns 1536 for text-embedding-ada-002."""

        class FakeClient:
            pass

        embedder = OpenAIEmbedder(FakeClient(), model="text-embedding-ada-002")
        assert embedder.dimension == 1536

    def test_unknown_model_raises(self):
        """Unknown model raises ValueError."""

        class FakeClient:
            pass

        with pytest.raises(ValueError, match="Unknown OpenAI embedding model"):
            OpenAIEmbedder(FakeClient(), model="unknown_model")

    def test_name(self):
        """name includes model name."""

        class FakeClient:
            pass

        embedder = OpenAIEmbedder(FakeClient(), model="text-embedding-3-small")
        assert embedder.name == "openai:text-embedding-3-small"

    def test_embed_with_fake_client(self):
        """embed returns vector from fake client."""

        class FakeEmbeddingData:
            embedding: ClassVar[list] = [1.0, 2.0, 3.0]

        class FakeResponse:
            data: ClassVar[list] = [FakeEmbeddingData()]

        class FakeEmbeddings:
            def create(self, model, input):
                return FakeResponse()

        class FakeClient:
            embeddings = FakeEmbeddings()

        client = FakeClient()
        embedder = OpenAIEmbedder(client, model="text-embedding-3-small")
        result = embedder.embed("test")
        assert result is not None
        assert len(result) == 3
        np.testing.assert_array_almost_equal(result, [1.0, 2.0, 3.0])

    def test_embed_returns_none_on_error(self):
        """embed returns None when API call fails."""

        class FakeClient:
            def embeddings(self):
                return self

            def create(self, model, input):
                raise Exception("API error")

        embedder = OpenAIEmbedder(FakeClient(), model="text-embedding-3-small")
        assert embedder.embed("test") is None


# Tests for create_embedder factory


class TestCreateEmbedder:
    """Tests for create_embedder factory function."""

    def test_create_null_embedder(self):
        """Create NullEmbedder with type 'none'."""
        embedder = create_embedder("none")
        assert isinstance(embedder, NullEmbedder)

    def test_create_spacy_embedder(self):
        """Create SpacyEmbedder with type 'spacy' and nlp."""

        class FakeNLP:
            meta: ClassVar[dict] = {"name": "test"}

            def __call__(self, text):
                return type("Doc", (), {"has_vector": False})()

        embedder = create_embedder("spacy", nlp=FakeNLP())
        assert isinstance(embedder, SpacyEmbedder)

    def test_create_spacy_embedder_without_nlp_raises(self):
        """Create SpacyEmbedder without nlp raises ValueError."""
        with pytest.raises(ValueError, match="spacy embedder requires nlp"):
            create_embedder("spacy")

    def test_create_openai_embedder(self):
        """Create OpenAIEmbedder with type 'openai' and client."""

        class FakeClient:
            pass

        embedder = create_embedder("openai", openai_client=FakeClient())
        assert isinstance(embedder, OpenAIEmbedder)

    def test_create_openai_embedder_without_client_raises(self):
        """Create OpenAIEmbedder without client raises ValueError."""
        with pytest.raises(ValueError, match="openai embedder requires either openai_client"):
            create_embedder("openai")

    def test_unknown_type_raises(self):
        """Unknown embedder type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown embedder type"):
            create_embedder("unknown")


# Tests for MemoryStore with embedders


class TestMemoryStoreWithEmbedders:
    """Tests for MemoryStore using different embedders."""

    def test_store_with_null_embedder(self, tmp_path):
        """MemoryStore works with NullEmbedder."""
        from dendrophis.memory.memory import MemoryStore

        store = MemoryStore(str(tmp_path / "test.db"), embedder=NullEmbedder())
        entry = store.save_memory(content="test")
        assert entry.embedding_blob is None
        assert store.compute_embedding("test") is None

    def test_store_with_spacy_embedder(self, tmp_path):
        """MemoryStore works with SpacyEmbedder."""
        from dendrophis.memory.memory import MemoryStore

        class FakeDoc:
            def __init__(self, text):
                self.text = text
                self.has_vector = True
                self.vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)

        class FakeNLP:
            meta: ClassVar[dict] = {"name": "fake"}

            def __call__(self, text):
                return FakeDoc(text)

        embedder = SpacyEmbedder(FakeNLP())
        store = MemoryStore(str(tmp_path / "test.db"), embedder=embedder)
        result = store.compute_embedding("test")
        assert result is not None
        np.testing.assert_array_almost_equal(result, [1.0, 2.0, 3.0])

    def test_store_backward_compat_with_nlp(self, tmp_path):
        """MemoryStore still accepts nlp parameter for backward compatibility."""
        from dendrophis.memory.memory import MemoryStore

        class FakeDoc:
            def __init__(self, text):
                self.text = text
                self.has_vector = True
                self.vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)

        class FakeNLP:
            meta: ClassVar[dict] = {"name": "fake"}

            def __call__(self, text):
                return FakeDoc(text)

        # Old API: pass nlp directly
        store = MemoryStore(str(tmp_path / "test.db"), nlp=FakeNLP())
        result = store.compute_embedding("test")
        assert result is not None
        np.testing.assert_array_almost_equal(result, [1.0, 2.0, 3.0])

        # nlp property still works
        assert store.nlp is not None

    def test_store_switch_embedder(self, tmp_path):
        """MemoryStore embedder can be switched at runtime."""
        from dendrophis.memory.memory import MemoryStore

        store = MemoryStore(str(tmp_path / "test.db"), embedder=NullEmbedder())
        assert store.compute_embedding("test") is None

        class FakeDoc:
            def __init__(self, text):
                self.text = text
                self.has_vector = True
                self.vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)

        class FakeNLP:
            meta: ClassVar[dict] = {"name": "fake"}

            def __call__(self, text):
                return FakeDoc(text)

        store.embedder = SpacyEmbedder(FakeNLP())
        result = store.compute_embedding("test")
        assert result is not None
        np.testing.assert_array_almost_equal(result, [1.0, 2.0, 3.0])
