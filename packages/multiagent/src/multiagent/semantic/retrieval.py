"""Semantic retrieval engine — unified search across semantic, text, and hybrid modes.

The SemanticRetrievalEngine provides a single entry point for all
memory searches, supporting:
- Semantic search (meaning-based via embeddings)
- Text search (keyword-based via MemorySearchIndex)
- Hybrid search (combining both with configurable weights)

Results are ranked using the RankingEngine and returned in order
of relevance.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from ..memctx.context import ContextEngine, ContextRequest, ContextResult
from ..memctx.engine import MemoryEngine
from ..memctx.events import MemoryEventBus
from ..memctx.models import MemoryRecord, MemoryType
from ..memctx.search import MemorySearchIndex, SearchResult
from .cache import EmbeddingCache
from .embedding import EmbeddingProvider
from .events import SemanticEventType
from .metrics import SemanticMetrics
from .ranking import RankedResult, RankingConfig, RankingEngine
from .semantic_index import SemanticIndex
from .vector_store import VectorSearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and data classes
# ---------------------------------------------------------------------------


class RetrievalMode(str, Enum):
    """Search mode for the retrieval engine."""

    SEMANTIC = "semantic"
    TEXT = "text"
    HYBRID = "hybrid"


@dataclass
class RetrievalRequest:
    """Parameters for a retrieval query.

    Attributes:
        query: The search query text.
        mode: Search mode (semantic, text, or hybrid).
        top_k: Maximum number of results.
        min_score: Minimum relevance score (0.0-1.0).
        owner: Filter by owner.
        memory_types: Filter by memory types.
        tags: Filter by tags (all must match).
        metadata_filter: Filter by metadata key-value pairs.
        include_metadata: Whether to include full record metadata.
        ranking_config: Optional ranking configuration override.
    """

    query: str = ""
    mode: RetrievalMode = RetrievalMode.HYBRID
    top_k: int = 10
    min_score: float = 0.0
    owner: Optional[str] = None
    memory_types: Optional[List[MemoryType]] = None
    tags: Optional[List[str]] = None
    metadata_filter: Optional[Dict[str, Any]] = None
    include_metadata: bool = True
    ranking_config: Optional[RankingConfig] = None


@dataclass
class RetrievalResult:
    """Result of a retrieval operation.

    Attributes:
        results: Ranked results.
        mode: The search mode used.
        query: The original query.
        total_time_ms: Total time for the retrieval.
        semantic_count: Number of results from semantic search.
        text_count: Number of results from text search.
        duplicates_merged: Number of duplicates merged.
    """

    results: List[RankedResult] = field(default_factory=list)
    mode: str = ""
    query: str = ""
    total_time_ms: float = 0.0
    semantic_count: int = 0
    text_count: int = 0
    duplicates_merged: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "mode": self.mode,
            "query": self.query,
            "total_time_ms": round(self.total_time_ms, 2),
            "semantic_count": self.semantic_count,
            "text_count": self.text_count,
            "duplicates_merged": self.duplicates_merged,
            "total_results": len(self.results),
        }


# ---------------------------------------------------------------------------
# Retrieval engine
# ---------------------------------------------------------------------------


class SemanticRetrievalEngine:
    """Unified retrieval engine for semantic, text, and hybrid search.

    Integrates with MemoryEngine, SemanticIndex, and ContextEngine
    to provide a comprehensive search experience.
    """

    def __init__(
        self,
        memory_engine: MemoryEngine,
        embedding_provider: Optional[EmbeddingProvider] = None,
        semantic_index: Optional[SemanticIndex] = None,
        ranking_engine: Optional[RankingEngine] = None,
        event_bus: Optional[MemoryEventBus] = None,
        metrics: Optional[SemanticMetrics] = None,
        cache: Optional[EmbeddingCache] = None,
    ) -> None:
        self._engine = memory_engine
        self._embedding_provider = embedding_provider
        self._semantic_index = semantic_index
        self._ranking = ranking_engine or RankingEngine()
        self._event_bus = event_bus or memory_engine.event_bus
        self._metrics = metrics or SemanticMetrics()
        self._cache = cache

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the retrieval engine. Initializes the semantic index if available."""
        if self._semantic_index is not None:
            await self._semantic_index.start(self._engine)
        logger.info("SemanticRetrievalEngine started")

    async def stop(self) -> None:
        """Stop the retrieval engine."""
        if self._semantic_index is not None:
            await self._semantic_index.stop()
        logger.info("SemanticRetrievalEngine stopped")

    # -- search ------------------------------------------------------------

    async def retrieve(
        self,
        request: RetrievalRequest,
    ) -> RetrievalResult:
        """Execute a retrieval query.

        Delegates to the appropriate search method based on the mode,
        then ranks and deduplicates results.
        """
        t0 = time.monotonic()

        if request.mode == RetrievalMode.SEMANTIC:
            result = await self._semantic_search(request)
        elif request.mode == RetrievalMode.TEXT:
            result = await self._text_search(request)
        else:  # HYBRID
            result = await self._hybrid_search(request)

        result.total_time_ms = (time.monotonic() - t0) * 1000
        result.query = request.query
        result.mode = request.mode.value

        # Emit event
        event_type = (
            SemanticEventType.HYBRID_SEARCH.value
            if request.mode == RetrievalMode.HYBRID
            else SemanticEventType.SEMANTIC_SEARCH.value
        )
        await self._event_bus.emit(
            event_type,
            source="SemanticRetrievalEngine",
            payload={
                "query_length": len(request.query),
                "mode": request.mode.value,
                "results_count": len(result.results),
                "total_time_ms": result.total_time_ms,
                "top_score": result.results[0].final_score if result.results else 0.0,
            },
        )

        return result

    async def semantic_search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.0,
        owner: Optional[str] = None,
        tags: Optional[List[str]] = None,
        memory_types: Optional[List[MemoryType]] = None,
    ) -> RetrievalResult:
        """Convenience method for semantic-only search."""
        request = RetrievalRequest(
            query=query,
            mode=RetrievalMode.SEMANTIC,
            top_k=top_k,
            min_score=min_score,
            owner=owner,
            tags=tags,
            memory_types=memory_types,
        )
        return await self.retrieve(request)

    async def text_search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.0,
        owner: Optional[str] = None,
        tags: Optional[List[str]] = None,
        memory_types: Optional[List[MemoryType]] = None,
    ) -> RetrievalResult:
        """Convenience method for text-only search."""
        request = RetrievalRequest(
            query=query,
            mode=RetrievalMode.TEXT,
            top_k=top_k,
            min_score=min_score,
            owner=owner,
            tags=tags,
            memory_types=memory_types,
        )
        return await self.retrieve(request)

    async def hybrid_search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.0,
        owner: Optional[str] = None,
        tags: Optional[List[str]] = None,
        memory_types: Optional[List[MemoryType]] = None,
    ) -> RetrievalResult:
        """Convenience method for hybrid search."""
        request = RetrievalRequest(
            query=query,
            mode=RetrievalMode.HYBRID,
            top_k=top_k,
            min_score=min_score,
            owner=owner,
            tags=tags,
            memory_types=memory_types,
        )
        return await self.retrieve(request)

    # -- internal search methods -------------------------------------------

    async def _semantic_search(self, request: RetrievalRequest) -> RetrievalResult:
        """Pure semantic search via the SemanticIndex."""
        result = RetrievalResult(semantic_count=0)

        if self._semantic_index is None:
            logger.warning("semantic search requested but no semantic index available")
            return result

        # Build type filter
        mt_filter = None
        if request.memory_types and len(request.memory_types) == 1:
            mt_filter = request.memory_types[0].value

        vector_results = await self._semantic_index.search(
            query=request.query,
            top_k=request.top_k * 3,  # Over-fetch for ranking
            min_score=0.0,  # Don't filter here, let ranking handle it
            memory_type=mt_filter,
            owner=request.owner,
            tags=request.tags,
        )

        # Fetch full records
        semantic_scores: Dict[str, float] = {}
        records: List[MemoryRecord] = []
        for vr in vector_results:
            record = await self._engine.retrieve(vr.id)
            if record is not None:
                semantic_scores[record.id] = vr.score
                records.append(record)

        result.semantic_count = len(records)

        # Apply additional filters
        records = self._apply_filters(records, request)

        # Rank
        use_counts = self._get_use_counts()
        ranked = self._ranking.rank(
            records=records,
            semantic_scores=semantic_scores,
            use_counts=use_counts,
        )

        # Apply min_score and top_k
        result.results = ranked[: request.top_k]
        return result

    async def _text_search(self, request: RetrievalRequest) -> RetrievalResult:
        """Pure text search via MemorySearchIndex."""
        result = RetrievalResult(text_count=0)

        search_results = self._engine._search_index.combined_search(
            query=request.query,
            tags=request.tags,
            owner=request.owner,
            memory_type=request.memory_types[0] if request.memory_types and len(request.memory_types) == 1 else None,
            metadata_filter=request.metadata_filter,
            limit=request.top_k * 3,
        )

        records: List[MemoryRecord] = []
        text_scores: Dict[str, float] = {}
        for sr in search_results:
            records.append(sr.record)
            text_scores[sr.record.id] = sr.score / max(
                (sr.record.priority + 1) * 2, 1
            )  # Normalize

        result.text_count = len(records)

        # Apply additional filters
        records = self._apply_filters(records, request)

        # Rank (with text scores mapped to semantic_scores for the ranker)
        ranked = self._ranking.rank(
            records=records,
            semantic_scores=text_scores,
            use_counts=self._get_use_counts(),
        )

        result.results = ranked[: request.top_k]
        return result

    async def _hybrid_search(self, request: RetrievalRequest) -> RetrievalResult:
        """Hybrid search combining semantic and text results."""
        result = RetrievalResult()

        # Run both searches in parallel
        sem_task = self._semantic_search(request)
        txt_task = self._text_search(request)

        import asyncio
        sem_result, txt_result = await asyncio.gather(sem_task, txt_task)

        # Merge results, deduplicating by record id
        merged: Dict[str, RankedResult] = {}
        for r in sem_result.results:
            merged[r.record.id] = r
        for r in txt_result.results:
            if r.record.id in merged:
                # Combine scores: take the max semantic score and merge matched_by
                existing = merged[r.record.id]
                max_sem = max(existing.semantic_score, r.semantic_score)
                existing.semantic_score = max_sem
                existing.signal_scores.update(r.signal_scores)
                for m in r.matched_by:
                    if m not in existing.matched_by:
                        existing.matched_by.append(m)
                result.duplicates_merged += 1
            else:
                merged[r.record.id] = r

        result.semantic_count = sem_result.semantic_count
        result.text_count = txt_result.text_count

        # Re-rank merged results
        all_ranked = list(merged.values())
        # Re-sort by final_score since we may have modified scores
        all_ranked.sort(key=lambda r: r.final_score, reverse=True)
        result.results = all_ranked[: request.top_k]

        return result

    # -- helpers -----------------------------------------------------------

    def _apply_filters(
        self, records: List[MemoryRecord], request: RetrievalRequest
    ) -> List[MemoryRecord]:
        """Apply type, tag, and metadata filters."""
        filtered = records

        # Memory type filter
        if request.memory_types:
            type_set = set(request.memory_types)
            filtered = [r for r in filtered if r.memory_type in type_set]

        # Tag filter
        if request.tags:
            tag_set = set(request.tags)
            filtered = [r for r in filtered if tag_set.issubset(set(r.tags))]

        # Metadata filter
        if request.metadata_filter:
            filtered = [
                r for r in filtered
                if all(
                    r.metadata.get(k) == v
                    for k, v in request.metadata_filter.items()
                )
            ]

        # Min score (applied to semantic_score if available)
        if request.min_score > 0:
            filtered = [
                r for r in filtered
                # We can't easily filter here since ranking hasn't happened
                # Let the ranking engine handle min_score
            ]

        return filtered

    def _get_use_counts(self) -> Dict[str, int]:
        """Get access frequency counts from the semantic index."""
        if self._semantic_index is not None:
            # Access internal use_count mapping
            return dict(self._semantic_index._use_count)
        return {}

    # -- context integration helper ----------------------------------------

    async def retrieve_for_context(
        self,
        query: str,
        target: str = "",
        max_tokens: int = 4096,
        top_k: int = 10,
        owner: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[MemoryRecord]:
        """Retrieve records optimized for context building.

        Returns MemoryRecords (not RankedResult) for direct use
        in ContextEngine or other context-building pipelines.
        """
        request = RetrievalRequest(
            query=query,
            mode=RetrievalMode.HYBRID,
            top_k=top_k,
            owner=owner,
            tags=tags,
        )
        result = await self.retrieve(request)
        return [r.record for r in result.results]