"""Semantic index — bridges embeddings with the memory system.

The SemanticIndex is responsible for:
- Generating embeddings for memory records via an EmbeddingProvider
- Storing vectors in a VectorStore
- Keeping the vector index in sync with MemoryEngine mutations
- Providing similarity search over memory records
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from ..memctx.engine import MemoryEngine
from ..memctx.events import MemoryEventBus
from ..memctx.models import MemoryRecord, _serialize_value
from .cache import EmbeddingCache
from .embedding import EmbeddingProvider, EmbeddingResult
from .events import SemanticEventType
from .metrics import SemanticMetrics
from .vector_store import VectorSearchResult, VectorStore

logger = logging.getLogger(__name__)


def _extract_text(record: MemoryRecord) -> str:
    """Extract searchable text from a memory record."""
    parts: List[str] = []
    content = record.content
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, dict):
        for v in content.values():
            if isinstance(v, str):
                parts.append(v)
    # Add tags as context
    if record.tags:
        parts.append(" ".join(record.tags))
    return " ".join(parts)


class SemanticIndex:
    """Maintains a semantic vector index over memory records.

    Listens to MemoryEngine events (created/updated/deleted) and
    automatically keeps the vector store in sync.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        vector_store: Optional[VectorStore] = None,
        cache: Optional[EmbeddingCache] = None,
        event_bus: Optional[MemoryEventBus] = None,
        metrics: Optional[SemanticMetrics] = None,
    ) -> None:
        self._provider = embedding_provider
        self._store = vector_store
        self._cache = cache or EmbeddingCache()
        self._event_bus = event_bus
        self._metrics = metrics or SemanticMetrics()
        self._id_set: set[str] = set()
        self._use_count: Dict[str, int] = {}  # memory_id -> access count

    # -- lifecycle ---------------------------------------------------------

    async def start(self, engine: MemoryEngine) -> None:
        """Start the index by indexing all existing memory records.

        Args:
            engine: The MemoryEngine whose records should be indexed.
        """
        records = await engine.list_records(limit=100_000)
        indexed = 0
        for record in records:
            try:
                await self.index_record(record)
                indexed += 1
            except Exception:
                logger.warning("failed to index record %s", record.id)
        logger.info(
            "SemanticIndex started: %d/%d records indexed",
            indexed,
            len(records),
        )

    async def stop(self) -> None:
        """Stop the index and clear runtime state."""
        self._id_set.clear()
        self._use_count.clear()

    # -- index management --------------------------------------------------

    async def index_record(self, record: MemoryRecord) -> None:
        """Generate an embedding and store it for a memory record."""
        text = _extract_text(record)
        if not text.strip():
            logger.debug("skipping empty-text record %s", record.id)
            return

        t0 = time.monotonic()
        embedding = await self._get_or_compute_embedding(text)
        elapsed_ms = (time.monotonic() - t0) * 1000

        if self._store is not None:
            await self._store.add(
                id=record.id,
                vector=embedding.vector,
                metadata={
                    "memory_type": record.memory_type.value,
                    "owner": record.owner,
                    "tags": list(record.tags),
                    "priority": record.priority,
                },
            )

        self._id_set.add(record.id)
        self._use_count[record.id] = 0

        self._metrics.record_embedding_generated(elapsed_ms)

        # Emit event
        if self._event_bus is not None:
            await self._event_bus.emit(
                SemanticEventType.EMBEDDING_CREATED.value,
                source="SemanticIndex",
                payload={
                    "memory_id": record.id,
                    "provider": embedding.provider,
                    "dimensions": embedding.dimensions,
                    "latency_ms": embedding.latency_ms,
                },
            )

    async def update_record(self, record: MemoryRecord) -> None:
        """Re-index a record after it has been updated."""
        # Remove old vector
        await self.remove_record(record.id)
        # Re-index with new content
        await self.index_record(record)

        if self._event_bus is not None:
            await self._event_bus.emit(
                SemanticEventType.EMBEDDING_UPDATED.value,
                source="SemanticIndex",
                payload={"memory_id": record.id},
            )

    async def remove_record(self, memory_id: str) -> bool:
        """Remove a record from the index."""
        removed = False
        if self._store is not None:
            removed = await self._store.remove(memory_id)
        self._id_set.discard(memory_id)
        self._use_count.pop(memory_id, None)
        return removed

    # -- search ------------------------------------------------------------

    async def search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.0,
        memory_type: Optional[str] = None,
        owner: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[VectorSearchResult]:
        """Search the index by semantic similarity.

        Args:
            query: Text query to search for.
            top_k: Maximum number of results.
            min_score: Minimum similarity score.
            memory_type: Optional memory type filter.
            owner: Optional owner filter.
            tags: Optional tags filter.

        Returns:
            List of VectorSearchResult sorted by score descending.
        """
        if self._store is None:
            return []

        t0 = time.monotonic()

        # Build filters
        filters: Dict[str, Any] = {}
        if memory_type is not None:
            filters["memory_type"] = memory_type
        if owner is not None:
            filters["owner"] = owner
        # Note: tag filtering is done post-search (list equality doesn't
        # work well in metadata filters)

        # Embed the query
        embedding = await self._get_or_compute_embedding(query)

        # Search
        results = await self._store.search(
            query_vector=embedding.vector,
            top_k=top_k * 3,  # Over-fetch for post-filtering
            min_score=min_score,
            filters=filters if filters else None,
        )

        # Post-filter by tags (list matching)
        if tags:
            required = set(tags)
            results = [
                r for r in results
                if required.issubset(set(r.metadata.get("tags", [])))
            ]

        elapsed_ms = (time.monotonic() - t0) * 1000

        # Track usage
        for r in results:
            self._use_count[r.id] = self._use_count.get(r.id, 0) + 1

        self._metrics.record_semantic_search(elapsed_ms, len(results))

        if self._event_bus is not None:
            await self._event_bus.emit(
                SemanticEventType.SEMANTIC_SEARCH.value,
                source="SemanticIndex",
                payload={
                    "query_length": len(query),
                    "results_count": len(results),
                    "top_score": results[0].score if results else 0.0,
                    "latency_ms": elapsed_ms,
                },
            )

        return results

    # -- properties --------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._id_set)

    def get_use_count(self, memory_id: str) -> int:
        """Return how many times a record has appeared in search results."""
        return self._use_count.get(memory_id, 0)

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    @property
    def metrics(self) -> SemanticMetrics:
        return self._metrics

    # -- internal ----------------------------------------------------------

    async def _get_or_compute_embedding(self, text: str) -> EmbeddingResult:
        """Get embedding from cache or compute it."""
        text_hash = self._provider.compute_text_hash(text)
        cached = self._cache.get(text_hash)
        if cached is not None:
            self._metrics.record_cache_hit()
            return cached

        self._metrics.record_cache_miss()
        result = await self._provider.embed_text(text)
        self._cache.put(text_hash, result)
        return result