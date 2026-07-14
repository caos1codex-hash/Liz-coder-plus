"""Vector store abstraction.

Defines the contract for storing and searching vectors. Implementations
can be in-memory (for testing), FAISS, ChromaDB, Qdrant, Milvus, Weaviate,
pgvector, or any other vector database — all without changing the
semantic retrieval layer.
"""

from __future__ import annotations

import logging
import math
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class VectorSearchResult:
    """A single result from a vector similarity search.

    Attributes:
        id: The record id associated with this vector.
        vector: The stored vector.
        score: Similarity score (0.0 = dissimilar, 1.0 = identical for cosine).
        metadata: Optional metadata associated with the vector.
    """

    id: str
    vector: List[float]
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "score": self.score,
            "metadata": self.metadata,
            "dimensions": len(self.vector),
        }


# ---------------------------------------------------------------------------
# Similarity functions
# ---------------------------------------------------------------------------


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        raise ValueError(
            f"vector dimension mismatch: {len(a)} vs {len(b)}"
        )
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def euclidean_distance(a: List[float], b: List[float]) -> float:
    """Compute Euclidean distance between two vectors."""
    if len(a) != len(b):
        raise ValueError(
            f"vector dimension mismatch: {len(a)} vs {len(b)}"
        )
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def dot_product(a: List[float], b: List[float]) -> float:
    """Compute dot product of two vectors."""
    if len(a) != len(b):
        raise ValueError(
            f"vector dimension mismatch: {len(a)} vs {len(b)}"
        )
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Abstract store
# ---------------------------------------------------------------------------


class VectorStore(ABC):
    """Abstract base class for vector stores.

    Operations:
    - add(): insert one or more vectors
    - update(): update an existing vector
    - remove(): delete a vector by id
    - search(): find the most similar vectors to a query
    - get(): retrieve a specific vector by id
    - clear(): remove all vectors
    - stats(): return store statistics
    """

    @abstractmethod
    async def add(
        self,
        id: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a vector with the given id and optional metadata."""
        ...

    @abstractmethod
    async def add_batch(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadatas: Optional[List[Optional[Dict[str, Any]]]] = None,
    ) -> int:
        """Add multiple vectors. Returns the number of vectors added."""
        ...

    @abstractmethod
    async def update(
        self,
        id: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update an existing vector. Returns True if found and updated."""
        ...

    @abstractmethod
    async def remove(self, id: str) -> bool:
        """Remove a vector by id. Returns True if found and removed."""
        ...

    @abstractmethod
    async def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        min_score: float = 0.0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorSearchResult]:
        """Search for the most similar vectors to the query.

        Args:
            query_vector: The query embedding.
            top_k: Maximum number of results to return.
            min_score: Minimum similarity score threshold.
            filters: Optional metadata filters (key-value pairs, all must match).

        Returns:
            List of VectorSearchResult, sorted by score descending.
        """
        ...

    @abstractmethod
    async def get(self, id: str) -> Optional[Tuple[List[float], Dict[str, Any]]]:
        """Get a vector and its metadata by id. Returns None if not found."""
        ...

    @abstractmethod
    async def exists(self, id: str) -> bool:
        """Check if a vector with the given id exists."""
        ...

    @abstractmethod
    async def clear(self) -> int:
        """Clear all vectors. Returns the number of vectors removed."""
        ...

    @abstractmethod
    async def size(self) -> int:
        """Return the number of vectors in the store."""
        ...

    @abstractmethod
    def stats(self) -> Dict[str, Any]:
        """Return store statistics."""
        ...


# ---------------------------------------------------------------------------
# In-memory vector store
# ---------------------------------------------------------------------------


class InMemoryVectorStore(VectorStore):
    """Dictionary-based vector store for testing and development.

    Uses brute-force cosine similarity search. Thread-safe via
    threading.Lock.

    Suitable for:
    - Unit testing
    - Development environments
    - Small-scale deployments (< 10K vectors)
    """

    def __init__(self, expected_dims: Optional[int] = None) -> None:
        """Initialize the store.

        Args:
            expected_dims: If set, all vectors must match this dimensionality.
                           Violations raise ValueError.
        """
        self._vectors: Dict[str, List[float]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._expected_dims = expected_dims
        self._add_count = 0
        self._search_count = 0

    def _validate_dims(self, vector: List[float]) -> None:
        """Validate vector dimensions if expected_dims is set."""
        if self._expected_dims is not None and len(vector) != self._expected_dims:
            raise ValueError(
                f"expected {self._expected_dims} dimensions, got {len(vector)}"
            )

    async def add(
        self,
        id: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            if id in self._vectors:
                raise ValueError(f"duplicate vector id: {id}")
            self._validate_dims(vector)
            self._vectors[id] = list(vector)
            self._metadata[id] = metadata or {}
            self._add_count += 1

    async def add_batch(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadatas: Optional[List[Optional[Dict[str, Any]]]] = None,
    ) -> int:
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must have the same length")
        if metadatas is None:
            metadatas = [None] * len(ids)
        if len(metadatas) != len(ids):
            raise ValueError("metadatas must have the same length as ids")

        count = 0
        for i, (vid, vec) in enumerate(zip(ids, vectors)):
            try:
                await self.add(vid, vec, metadatas[i])
                count += 1
            except ValueError:
                logger.warning("add_batch skipped duplicate id: %s", vid)
        return count

    async def update(
        self,
        id: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        with self._lock:
            if id not in self._vectors:
                return False
            self._validate_dims(vector)
            self._vectors[id] = list(vector)
            if metadata is not None:
                self._metadata[id] = metadata
            return True

    async def remove(self, id: str) -> bool:
        with self._lock:
            if id in self._vectors:
                del self._vectors[id]
                self._metadata.pop(id, None)
                return True
            return False

    async def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        min_score: float = 0.0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorSearchResult]:
        self._search_count += 1
        with self._lock:
            results: List[VectorSearchResult] = []
            for vid, vec in self._vectors.items():
                # Apply metadata filters
                if filters and not _metadata_matches(self._metadata.get(vid, {}), filters):
                    continue
                score = cosine_similarity(query_vector, vec)
                if score >= min_score:
                    results.append(
                        VectorSearchResult(
                            id=vid,
                            vector=vec,
                            score=score,
                            metadata=dict(self._metadata.get(vid, {})),
                        )
                    )
            results.sort(key=lambda r: r.score, reverse=True)
            return results[:top_k]

    async def get(self, id: str) -> Optional[Tuple[List[float], Dict[str, Any]]]:
        with self._lock:
            if id not in self._vectors:
                return None
            return list(self._vectors[id]), dict(self._metadata.get(id, {}))

    async def exists(self, id: str) -> bool:
        with self._lock:
            return id in self._vectors

    async def clear(self) -> int:
        with self._lock:
            count = len(self._vectors)
            self._vectors.clear()
            self._metadata.clear()
            return count

    async def size(self) -> int:
        with self._lock:
            return len(self._vectors)

    def stats(self) -> Dict[str, Any]:
        return {
            "type": "in_memory",
            "vectors_stored": len(self._vectors),
            "add_count": self._add_count,
            "search_count": self._search_count,
            "expected_dims": self._expected_dims,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metadata_matches(
    record_meta: Dict[str, Any], filter_meta: Dict[str, Any]
) -> bool:
    """Check if all filter key-value pairs exist in the record metadata."""
    for k, v in filter_meta.items():
        if k not in record_meta:
            return False
        if record_meta[k] != v:
            return False
    return True