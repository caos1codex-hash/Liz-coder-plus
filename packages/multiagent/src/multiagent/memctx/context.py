"""Context Engine — assembles context from memory for agents and workflows.

The ContextEngine queries the MemoryEngine, selects relevant records,
deduplicates, trims to size limits, and produces a ContextSnapshot
ready for consumption.

Sprint 2.7 extension: optionally integrates with SemanticRetrievalEngine
to include semantically relevant records in context building. The semantic
layer is completely optional — the engine works identically without it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .engine import MemoryEngine
from .events import MemoryEventBus, MemoryEventType
from .models import (
    ContextSnapshot,
    MemoryRecord,
    MemoryType,
    _serialize_value,
    _utcnow,
)

if TYPE_CHECKING:
    from ..semantic.retrieval import SemanticRetrievalEngine

logger = logging.getLogger(__name__)


@dataclass
class ContextRequest:
    """Parameters for building a context snapshot.

    Attributes:
        target: Who the context is for (agent name, workflow id, etc.).
        owner: Filter memories by owner.
        tags: Required tags (all must match).
        memory_types: Which memory types to include.
        max_tokens: Maximum estimated token budget.
        query: Optional text query for relevance filtering.
        include_metadata: Whether to include full metadata in the snapshot.
        priority_threshold: Minimum priority to include.
        max_records: Maximum number of records (before trimming).
        use_semantic: Whether to use semantic search for candidate fetching
            (requires a SemanticRetrievalEngine to be set).
        semantic_top_k: How many semantic results to include.
    """

    target: str = ""
    owner: Optional[str] = None
    tags: Optional[List[str]] = None
    memory_types: Optional[List[MemoryType]] = None
    max_tokens: int = 4096
    query: Optional[str] = None
    include_metadata: bool = True
    priority_threshold: int = 0
    max_records: int = 200
    use_semantic: bool = False
    semantic_top_k: int = 20


@dataclass
class ContextResult:
    """Result of context building."""

    snapshot: ContextSnapshot
    records_scanned: int = 0
    duplicates_removed: int = 0
    build_time_ms: float = 0.0


# Rough token estimation: ~4 chars per token
_CHARS_PER_TOKEN = 4


class ContextEngine:
    """Builds context snapshots from memory for agent/workflow consumption.

    The engine is independent of storage — it reads from a MemoryEngine
    and assembles relevant, deduplicated, trimmed context.

    Sprint 2.7: Optionally uses SemanticRetrievalEngine for semantic
    candidate fetching when use_semantic=True in the request.
    """

    def __init__(
        self,
        memory_engine: MemoryEngine,
        event_bus: Optional[MemoryEventBus] = None,
        semantic_retrieval: Optional["SemanticRetrievalEngine"] = None,
    ) -> None:
        self._engine = memory_engine
        self._event_bus = event_bus or memory_engine.event_bus
        self._semantic_retrieval = semantic_retrieval
        self._build_count = 0
        self._trim_count = 0
        self._semantic_contexts = 0

    def set_semantic_retrieval(
        self, retrieval: Optional["SemanticRetrievalEngine"]
    ) -> None:
        """Set or replace the semantic retrieval engine."""
        self._semantic_retrieval = retrieval

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "contexts_built": self._build_count,
            "contexts_trimmed": self._trim_count,
            "semantic_contexts": self._semantic_contexts,
        }

    async def build_context(
        self, request: ContextRequest
    ) -> ContextResult:
        """Build a context snapshot based on the request parameters.

        Process:
        1. Fetch candidate records (optionally via semantic search)
        2. Deduplicate by content hash
        3. Sort by priority (desc) and recency (desc)
        4. Trim to fit within token budget
        5. Produce ContextSnapshot
        """
        t0 = time.monotonic()
        self._build_count += 1

        # Step 1: Fetch candidates
        candidates = await self._fetch_candidates(request)
        records_scanned = len(candidates)

        # Step 2: Deduplicate
        candidates, dup_count = self._deduplicate(candidates)

        # Step 3: Filter by priority
        candidates = [
            r
            for r in candidates
            if r.priority >= request.priority_threshold
        ]

        # Step 4: Sort by priority desc, then updated_at desc
        candidates.sort(
            key=lambda r: (r.priority, r.updated_at), reverse=True
        )

        # Step 5: Trim to token budget
        trimmed, trim_count = self._trim_to_budget(
            candidates, request.max_tokens
        )
        if trim_count > 0:
            self._trim_count += 1

        # Step 6: Build snapshot
        records_data = [
            r.to_dict() if request.include_metadata else {"id": r.id, "content": _serialize_value(r.content)}
            for r in trimmed
        ]
        token_estimate = sum(self._estimate_tokens(r) for r in trimmed)

        snapshot = ContextSnapshot(
            target=request.target,
            records=records_data,
            summary=self._build_summary(trimmed),
            total_tokens_estimate=token_estimate,
            max_tokens=request.max_tokens,
            was_trimmed=trim_count > 0,
            trimmed_count=trim_count,
            sources=list({r.owner for r in trimmed}),
        )

        elapsed_ms = (time.monotonic() - t0) * 1000

        # Emit event
        await self._event_bus.emit(
            MemoryEventType.CONTEXT_CREATED,
            source="ContextEngine",
            payload={
                "target": request.target,
                "records_included": len(trimmed),
                "records_scanned": records_scanned,
                "duplicates_removed": dup_count,
                "tokens_estimate": token_estimate,
                "was_trimmed": trim_count > 0,
                "build_time_ms": elapsed_ms,
                "used_semantic": request.use_semantic,
            },
        )

        return ContextResult(
            snapshot=snapshot,
            records_scanned=records_scanned,
            duplicates_removed=dup_count,
            build_time_ms=elapsed_ms,
        )

    async def build_agent_context(
        self,
        agent_name: str,
        task: Optional[Dict[str, Any]] = None,
        max_tokens: int = 4096,
        tags: Optional[List[str]] = None,
    ) -> ContextSnapshot:
        """Convenience: build context for a specific agent."""
        req_tags = tags or []
        if task and "tags" in task:
            req_tags = list(set(req_tags + task["tags"]))

        request = ContextRequest(
            target=agent_name,
            tags=req_tags if req_tags else None,
            max_tokens=max_tokens,
            memory_types=[
                MemoryType.CONVERSATION,
                MemoryType.PROJECT,
                MemoryType.KNOWLEDGE,
            ],
        )
        result = await self.build_context(request)
        return result.snapshot

    async def build_workflow_context(
        self,
        workflow_id: str,
        step_id: Optional[str] = None,
        max_tokens: int = 8192,
    ) -> ContextSnapshot:
        """Convenience: build context for a workflow step."""
        tags = ["workflow", workflow_id]
        if step_id:
            tags.append(step_id)

        request = ContextRequest(
            target=workflow_id,
            tags=tags,
            max_tokens=max_tokens,
            memory_types=[
                MemoryType.WORKFLOW,
                MemoryType.CONVERSATION,
                MemoryType.PROJECT,
                MemoryType.KNOWLEDGE,
            ],
        )
        result = await self.build_context(request)
        return result.snapshot

    async def load_context(self, context_id: str) -> Optional[ContextSnapshot]:
        """Load a previously created context by id (retrieves from memory)."""
        record = await self._engine.retrieve(context_id)
        if record is None or record.memory_type != MemoryType.CONTEXT:
            return None
        try:
            return ContextSnapshot.from_dict(record.content)
        except (KeyError, TypeError, ValueError):
            return None

    async def save_context(self, snapshot: ContextSnapshot) -> MemoryRecord:
        """Persist a context snapshot to memory for later retrieval."""
        from .models import MemoryRecord

        record = MemoryRecord(
            id=snapshot.id,
            memory_type=MemoryType.CONTEXT,
            content=snapshot.to_dict(),
            owner="ContextEngine",
            tags=["context", snapshot.target],
            metadata={"target": snapshot.target},
        )
        saved = await self._engine.store(record)
        await self._event_bus.emit(
            MemoryEventType.CONTEXT_LOADED,
            source="ContextEngine",
            payload={
                "context_id": snapshot.id,
                "target": snapshot.target,
            },
        )
        return saved

    # -- internal ----------------------------------------------------------

    async def _fetch_candidates(
        self, request: ContextRequest
    ) -> List[MemoryRecord]:
        """Fetch candidate records from memory engine.

        If request.use_semantic is True and a semantic retrieval engine
        is available, also fetches semantically relevant records.
        """
        all_records: List[MemoryRecord] = []
        seen_ids: set[str] = set()

        # --- Semantic candidates (Sprint 2.7) ---
        if (
            request.use_semantic
            and self._semantic_retrieval is not None
            and request.query
        ):
            self._semantic_contexts += 1
            try:
                semantic_records = await self._semantic_retrieval.retrieve_for_context(
                    query=request.query,
                    target=request.target,
                    top_k=request.semantic_top_k,
                    owner=request.owner,
                    tags=request.tags,
                )
                for rec in semantic_records:
                    if rec.id not in seen_ids:
                        all_records.append(rec)
                        seen_ids.add(rec.id)
            except Exception:
                logger.warning(
                    "semantic retrieval failed, falling back to text search",
                    exc_info=True,
                )

        # --- Standard text-based candidates ---
        if request.memory_types:
            for mt in request.memory_types:
                records = await self._engine.search(
                    owner=request.owner,
                    memory_type=mt,
                    tags=request.tags,
                    query=request.query,
                    limit=request.max_records,
                )
                for rec in records:
                    if rec.id not in seen_ids:
                        all_records.append(rec)
                        seen_ids.add(rec.id)
        else:
            records = await self._engine.search(
                owner=request.owner,
                tags=request.tags,
                query=request.query,
                limit=request.max_records,
            )
            for rec in records:
                if rec.id not in seen_ids:
                    all_records.append(rec)
                    seen_ids.add(rec.id)

        return all_records

    def _deduplicate(
        self, records: List[MemoryRecord]
    ) -> tuple[List[MemoryRecord], int]:
        """Remove duplicate records based on content hash. Returns (unique, dup_count)."""
        seen: set[str] = set()
        unique: List[MemoryRecord] = []
        dup_count = 0

        # Sort by priority desc so higher priority versions win
        records_sorted = sorted(
            records, key=lambda r: r.priority, reverse=True
        )

        for rec in records_sorted:
            content_hash = self._content_hash(rec)
            if content_hash not in seen:
                seen.add(content_hash)
                unique.append(rec)
            else:
                dup_count += 1

        return unique, dup_count

    @staticmethod
    def _content_hash(record: MemoryRecord) -> str:
        """Generate a simple hash of the record content for dedup."""
        import hashlib

        content_str = str(record.content)
        return hashlib.md5(content_str.encode()).hexdigest()

    def _trim_to_budget(
        self, records: List[MemoryRecord], max_tokens: int
    ) -> tuple[List[MemoryRecord], int]:
        """Trim records to fit within token budget. Returns (trimmed_list, removed_count)."""
        total = 0
        result: List[MemoryRecord] = []
        for rec in records:
            rec_tokens = self._estimate_tokens(rec)
            if total + rec_tokens > max_tokens and result:
                # Don't include this record, it would exceed budget
                break
            result.append(rec)
            total += rec_tokens
        removed = len(records) - len(result)
        return result, removed

    @staticmethod
    def _estimate_tokens(record: MemoryRecord) -> int:
        """Rough token estimate for a record."""
        content_str = str(record.content)
        return max(1, len(content_str) // _CHARS_PER_TOKEN)

    @staticmethod
    def _build_summary(records: List[MemoryRecord]) -> str:
        """Build a brief text summary of the context records."""
        type_counts: Dict[str, int] = {}
        owners: set[str] = set()
        for r in records:
            mt = r.memory_type.value
            type_counts[mt] = type_counts.get(mt, 0) + 1
            owners.add(r.owner)

        parts = [f"{count} {mt}" for mt, count in type_counts.items()]
        summary = ", ".join(parts)
        if owners:
            summary += f" (from: {', '.join(sorted(owners))})"
        return summary