"""Embedding provider abstraction.

Defines the contract for generating text embeddings. Providers are
pluggable — swap DummyEmbeddingProvider for OpenAI, Ollama, Sentence
Transformers, etc., without changing any other component.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import struct
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EmbeddingResult:
    """Result of an embedding operation.

    Attributes:
        vector: The embedding vector (list of floats).
        text_hash: SHA-256 hex digest of the input text (for caching).
        provider: Name of the provider that generated this embedding.
        dimensions: Dimensionality of the vector.
        latency_ms: Time taken to generate the embedding.
        metadata: Optional provider-specific metadata.
    """

    vector: List[float]
    text_hash: str = ""
    provider: str = ""
    dimensions: int = 0
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dimensions and self.vector:
            self.dimensions = len(self.vector)
        if not self.provider:
            self.provider = "unknown"

    @property
    def magnitude(self) -> float:
        """Euclidean norm of the vector."""
        return math.sqrt(sum(x * x for x in self.vector))

    def normalized(self) -> List[float]:
        """Return a unit-length copy of the vector."""
        mag = self.magnitude
        if mag == 0:
            return list(self.vector)
        return [x / mag for x in self.vector]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vector": self.vector,
            "text_hash": self.text_hash,
            "provider": self.provider,
            "dimensions": self.dimensions,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers.

    Every provider must implement:
    - embed_text(): single text -> embedding vector
    - embed_batch(): multiple texts -> embedding vectors
    - dimensions(): return vector dimensionality
    - provider_name(): human-readable provider name
    - health(): check if the provider is operational
    """

    @abstractmethod
    async def embed_text(self, text: str) -> EmbeddingResult:
        """Generate an embedding for a single text string.

        Args:
            text: The input text to embed.

        Returns:
            An EmbeddingResult with the vector and metadata.
        """
        ...

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[EmbeddingResult]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of input text strings.

        Returns:
            List of EmbeddingResult objects, one per input text.
        """
        ...

    @abstractmethod
    def dimensions(self) -> int:
        """Return the dimensionality of vectors produced by this provider."""
        ...

    @abstractmethod
    def provider_name(self) -> str:
        """Return a human-readable name for this provider."""
        ...

    @abstractmethod
    async def health(self) -> Dict[str, Any]:
        """Check provider health. Returns a dict with 'status' and optional details."""
        ...

    def compute_text_hash(self, text: str) -> str:
        """Compute a stable hash of the input text for caching."""
        normalized = text.strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Dummy provider (for testing and development)
# ---------------------------------------------------------------------------


class DummyEmbeddingProvider(EmbeddingProvider):
    """Deterministic pseudo-random embedding provider for testing.

    Produces fixed-dimensional vectors that are deterministic per input
    text (via hashing) but do not carry real semantic meaning.
    Suitable for development, testing, and CI environments.

    The generated vectors are normalized to unit length so that
    cosine similarity works correctly.
    """

    def __init__(
        self,
        dims: int = 128,
        seed: int = 42,
        latency_ms: float = 1.0,
    ) -> None:
        """Initialize the dummy provider.

        Args:
            dims: Dimensionality of generated vectors (default 128).
            seed: Random seed for reproducibility.
            latency_ms: Simulated latency per embedding in milliseconds.
        """
        self._dims = dims
        self._rng = random.Random(seed)
        self._latency_ms = latency_ms
        self._call_count = 0

    async def embed_text(self, text: str) -> EmbeddingResult:
        """Generate a deterministic pseudo-random embedding."""
        t0 = time.monotonic()
        self._call_count += 1

        text_hash = self.compute_text_hash(text)
        vector = self._generate_vector(text_hash)
        # Normalize
        mag = math.sqrt(sum(x * x for x in vector))
        if mag > 0:
            vector = [x / mag for x in vector]

        elapsed = (time.monotonic() - t0) * 1000
        simulated = self._latency_ms

        return EmbeddingResult(
            vector=vector,
            text_hash=text_hash,
            provider=self.provider_name(),
            dimensions=self._dims,
            latency_ms=max(elapsed, simulated),
        )

    async def embed_batch(self, texts: List[str]) -> List[EmbeddingResult]:
        """Generate embeddings for multiple texts."""
        results = []
        for text in texts:
            results.append(await self.embed_text(text))
        return results

    def dimensions(self) -> int:
        return self._dims

    def provider_name(self) -> str:
        return "dummy"

    async def health(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "provider": self.provider_name(),
            "dimensions": self._dims,
            "calls": self._call_count,
        }

    @property
    def call_count(self) -> int:
        return self._call_count

    def _generate_vector(self, text_hash: str) -> List[float]:
        """Generate a deterministic vector from a text hash.

        Uses the hash bytes to seed a per-call RNG, ensuring the
        same text always produces the same vector.
        """
        hash_bytes = bytes.fromhex(text_hash[:16])
        seed_int = int.from_bytes(hash_bytes, byteorder="big")
        rng = random.Random(seed_int)
        return [rng.gauss(0, 1) for _ in range(self._dims)]