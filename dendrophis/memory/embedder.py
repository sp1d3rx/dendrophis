"""Embedding providers for the memory system.

Provides a pluggable abstraction for computing text embeddings.
Supports multiple backends: spaCy (local), OpenAI (cloud), or none (ngram-only).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
import numpy as np

if TYPE_CHECKING:
    from spacy.language import Language


class BaseEmbedder(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> np.ndarray | None:
        """Compute embedding for text. Returns None if embedding unavailable."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the embedding dimension."""

    @property
    def name(self) -> str:
        """Return a human-readable name for this embedder."""
        return self.__class__.__name__


class NullEmbedder(BaseEmbedder):
    """No-op embedder that always returns None. Useful for ngram-only mode."""

    def embed(self, text: str) -> None:
        return None

    @property
    def dimension(self) -> int:
        return 0

    @property
    def name(self) -> str:
        return "none"


class SpacyEmbedder(BaseEmbedder):
    """spaCy-based embedder using local language models.

    Requires a spaCy model with word vectors, e.g., en_core_web_md.
    Embedding dimension is 300 for most spaCy models.
    """

    def __init__(self, nlp: Language) -> None:
        self._nlp = nlp

    def embed(self, text: str) -> np.ndarray | None:
        doc = self._nlp(text)
        if doc.has_vector:
            return doc.vector.astype(np.float32)
        # Fallback: average of word vectors
        vectors = [w.vector for w in doc if w.has_vector and w.vector.any()]
        if vectors:
            return np.mean(vectors, axis=0).astype(np.float32)
        return None

    @property
    def dimension(self) -> int:
        return 300  # Standard for en_core_web_md

    @property
    def name(self) -> str:
        return f"spacy:{self._nlp.meta.get('name', 'unknown')}"


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI embedding API client.

    Uses the /v1/embeddings endpoint. Supports text-embedding-3-small (1536-dim),
    text-embedding-3-large (3072-dim), and text-embedding-ada-002 (1536-dim).

    Can be initialized with either:
    - An official OpenAI client (from `openai` package)
    - A base URL and API key (uses httpx internally)
    """

    MODEL_DIMENSIONS: ClassVar[dict[str, int]] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        client: Any | None = None,
        model: str = "text-embedding-3-small",
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._base_url = base_url
        self._api_key = api_key

        if model not in self.MODEL_DIMENSIONS:
            known = ", ".join(self.MODEL_DIMENSIONS.keys())
            raise ValueError(f"Unknown OpenAI embedding model: {model}. Known models: {known}")

    def embed(self, text: str) -> np.ndarray | None:
        try:
            if self._client is not None:
                # Use official OpenAI client
                response = self._client.embeddings.create(
                    model=self._model,
                    input=text,
                )
                embedding = response.data[0].embedding
            else:
                # Use httpx to call the API directly
                embedding = self._fetch_embedding_httpx(text)

            if embedding is None:
                return None
            return np.array(embedding, dtype=np.float32)
        except Exception:
            return None

    def _fetch_embedding_httpx(self, text: str) -> list[float] | None:
        """Fetch embedding using httpx directly."""
        if self._base_url is None or self._api_key is None:
            return None

        url = f"{self._base_url.rstrip('/')}/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": text,
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    @property
    def dimension(self) -> int:
        return self.MODEL_DIMENSIONS[self._model]

    @property
    def name(self) -> str:
        return f"openai:{self._model}"


def create_embedder(
    embedder_type: str,
    nlp: Language | None = None,
    openai_client: Any | None = None,
    openai_model: str = "text-embedding-3-small",
    base_url: str | None = None,
    api_key: str | None = None,
) -> BaseEmbedder:
    """Factory function to create embedders by name.

    Args:
        embedder_type: "none", "spacy", or "openai"
        nlp: spaCy language model (required for "spacy")
        openai_client: OpenAI client (required for "openai" if not using base_url/api_key)
        openai_model: OpenAI embedding model name
        base_url: Base URL for OpenAI-compatible API (e.g., "https://api.deepinfra.com/v1")
        api_key: API key for OpenAI-compatible API

    Returns:
        Configured embedder instance
    """
    if embedder_type == "none":
        return NullEmbedder()
    if embedder_type == "spacy":
        if nlp is None:
            raise ValueError("spacy embedder requires nlp parameter")
        return SpacyEmbedder(nlp)
    if embedder_type == "openai":
        if openai_client is not None:
            return OpenAIEmbedder(openai_client, openai_model)
        if base_url is not None and api_key is not None:
            return OpenAIEmbedder(None, openai_model, base_url=base_url, api_key=api_key)
        raise ValueError("openai embedder requires either openai_client parameter or both base_url and api_key")
    raise ValueError(f"Unknown embedder type: {embedder_type}. Use 'none', 'spacy', or 'openai'.")
