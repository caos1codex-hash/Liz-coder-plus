"""Memory Engine — backend-agnostic CRUD and lifecycle management.

The MemoryEngine is the primary interface for storing, retrieving,
updating, and deleting memory records. It delegates storage to a
pluggable MemoryStorage backend and adds:
- Event emission on all mutations
- Metrics collection
- Duplicate detection
- Version conflict handling
- Consistency validation
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .events import MemoryEventBus, MemoryEventType
from .models import (
    ConversationMemory,
    KnowledgeMemory,
    MemoryRecord,
    MemoryType,
    ProjectMemory,
    WorkflowMemory,
)
from .metrics import MemoryMetrics
from .search import MemorySearchIndex
from .storage import InMemoryStorage, MemoryStorage

logger = logging.getLogger(__name__)


class MemoryEngine:
    """Core memory management engine.

    Provides CRUD operations, search, and lifecycle management
    independent of the underlying storage backend.
    """

    def __init__(
        self,
        storage: Optional[MemoryStorage] = None,
        event_bus: Optional[MemoryEventBus] = None,
        metrics: Optional[MemoryMetrics] = None,
        search_index: Optional[MemorySearchIndex] = None,
    ) -> None:
        self._storage = storage or InMemoryStorage()
        self._event_bus = event_bus or MemoryEventBus()
        self._metrics = metrics or MemoryMetrics()
        self._search_index = search_index or MemorySearchIndex()
        self._id_set: set[str] = set()
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the engine, warm up the ID set from storage."""
        if self._started:
            return
        # Rebuild ID set from existing records
        records = await self._storage.list(limit=100_000)
        self._id_set = {r.id for r in records}
        for r in records:
            self._search_index.index(r)
        self._started = True
        logger.info(
            "MemoryEngine started with %d existing records", len(self._id_set)
        )

    async def stop(self) -> None:
        """Stop the engine and clear caches."""
        self._id_set.clear()
        self._search_index.clear()
        self._started = False
        logger.info("MemoryEngine stopped")

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def storage(self) -> MemoryStorage:
        return self._storage

    @property
    def event_bus(self) -> MemoryEventBus:
        return self._event_bus

    @property
    def metrics(self) -> MemoryMetrics:
        return self._metrics

    # -- CRUD --------------------------------------------------------------

    async def store(self, record: MemoryRecord) -> MemoryRecord:
        """Store a new memory record.

        Validates for duplicates, persists, indexes, emits events, tracks metrics.
        """
        t0 = time.monotonic()
        if not self._started:
            raise RuntimeError("MemoryEngine not started")

        # Duplicate detection
        if record.id in self._id_set:
            raise ValueError(f"duplicate memory id: {record.id}")

        # Persist
        saved = await self._storage.save(record)
        self._id_set.add(saved.id)
        self._search_index.index(saved)

        # Events & metrics
        elapsed_ms = (time.monotonic() - t0) * 1000
        self._metrics.record_store(saved.memory_type, elapsed_ms)
        await self._event_bus.emit(
            MemoryEventType.MEMORY_CREATED,
            source="MemoryEngine",
            payload={
                "id": saved.id,
                "memory_type": saved.memory_type.value,
                "owner": saved.owner,
                "tags": list(saved.tags),
            },
        )
        logger.debug("stored memory %s (type=%s)", saved.id, saved.memory_type.value)
        return saved

    async def retrieve(self, memory_id: str) -> Optional[MemoryRecord]:
        """Retrieve a memory record by id."""
        t0 = time.monotonic()
        record = await self._storage.get(memory_id)
        elapsed_ms = (time.monotonic() - t0) * 1000
        self._metrics.record_retrieve(elapsed_ms, record is not None)
        return record

    async def update(self, record: MemoryRecord) -> MemoryRecord:
        """Update an existing memory record.

        Handles version conflicts and re-indexing.
        """
        t0 = time.monotonic()
        if record.id not in self._id_set:
            raise KeyError(f"memory not found: {record.id}")

        updated = await self._storage.update(record)
        self._search_index.reindex(updated)

        elapsed_ms = (time.monotonic() - t0) * 1000
        self._metrics.record_update(updated.memory_type, elapsed_ms)
        await self._event_bus.emit(
            MemoryEventType.MEMORY_UPDATED,
            source="MemoryEngine",
            payload={
                "id": updated.id,
                "memory_type": updated.memory_type.value,
                "version": updated.version,
                "owner": updated.owner,
            },
        )
        logger.debug("updated memory %s (v%d)", updated.id, updated.version)
        return updated

    async def remove(self, memory_id: str) -> bool:
        """Delete a memory record by id."""
        t0 = time.monotonic()
        deleted = await self._storage.delete(memory_id)
        if deleted:
            self._id_set.discard(memory_id)
            self._search_index.remove(memory_id)

            elapsed_ms = (time.monotonic() - t0) * 1000
            self._metrics.record_delete(elapsed_ms)
            await self._event_bus.emit(
                MemoryEventType.MEMORY_DELETED,
                source="MemoryEngine",
                payload={"id": memory_id},
            )
            logger.debug("removed memory %s", memory_id)
        return deleted

    async def exists(self, memory_id: str) -> bool:
        """Check if a memory record exists."""
        return memory_id in self._id_set

    # -- bulk ---------------------------------------------------------------

    async def store_many(self, records: List[MemoryRecord]) -> List[MemoryRecord]:
        """Store multiple records. Returns list of successfully stored records."""
        results = []
        for rec in records:
            try:
                results.append(await self.store(rec))
            except (ValueError, RuntimeError) as exc:
                logger.warning("store_many skipped %s: %s", rec.id, exc)
        return results

    async def remove_many(self, memory_ids: List[str]) -> int:
        """Remove multiple records. Returns count of deleted."""
        count = 0
        for mid in memory_ids:
            if await self.remove(mid):
                count += 1
        return count

    # -- search ------------------------------------------------------------

    async def search(
        self,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        owner: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        """Search memory records with optional filters."""
        t0 = time.monotonic()
        results = await self._storage.search(
            query=query,
            tags=tags,
            owner=owner,
            memory_type=memory_type,
            metadata_filter=metadata_filter,
            limit=limit,
            offset=offset,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        self._metrics.record_search(elapsed_ms, len(results))
        return results

    async def list_records(
        self,
        owner: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        """List records, optionally filtered."""
        return await self._storage.list(
            owner=owner, memory_type=memory_type, limit=limit, offset=offset
        )

    async def count(self, owner: Optional[str] = None) -> int:
        """Count records, optionally filtered by owner."""
        return await self._storage.count(owner=owner)

    async def clear(self, owner: Optional[str] = None) -> int:
        """Clear all records or those of a specific owner. Returns count."""
        count = await self._storage.clear(owner=owner)
        self._id_set.clear()
        self._search_index.clear()
        return count

    # -- convenience -------------------------------------------------------

    async def store_conversation(
        self,
        session_id: str,
        role: str,
        text: str,
        owner: str = "system",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        """Create and store a conversation memory record."""
        meta = metadata or {}
        meta["session_id"] = session_id
        record = ConversationMemory(
            content={"text": text},
            owner=owner,
            tags=tags or ["conversation", session_id],
            metadata=meta,
            session_id=session_id,
            role=role,
        )
        return await self.store(record)

    async def store_project(
        self,
        project_id: str,
        description: str,
        category: str = "general",
        owner: str = "system",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        """Create and store a project memory record."""
        record = ProjectMemory(
            content={"description": description},
            owner=owner,
            tags=tags or ["project", project_id, category],
            metadata=metadata or {},
            project_id=project_id,
            category=category,
        )
        return await self.store(record)

    async def store_workflow(
        self,
        workflow_id: str,
        step_id: str,
        result: Any,
        status: str = "completed",
        owner: str = "system",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        """Create and store a workflow memory record."""
        meta = metadata or {}
        meta["workflow_id"] = workflow_id
        meta["step_id"] = step_id
        record = WorkflowMemory(
            content={"result": _safe_val(result)},
            owner=owner,
            tags=tags or ["workflow", workflow_id, step_id],
            metadata=meta,
            workflow_id=workflow_id,
            step_id=step_id,
            status=status,
        )
        return await self.store(record)

    async def store_knowledge(
        self,
        text: str,
        source: str = "",
        confidence: float = 1.0,
        knowledge_type: str = "fact",
        owner: str = "system",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        """Create and store a knowledge memory record."""
        record = KnowledgeMemory(
            content={"text": text},
            owner=owner,
            tags=tags or ["knowledge", knowledge_type],
            metadata=metadata or {},
            confidence=confidence,
            source=source,
            knowledge_type=knowledge_type,
        )
        return await self.store(record)

    # -- health ------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        """Return health status of the memory engine."""
        total = await self.count()
        return {
            "status": "healthy" if self._started else "stopped",
            "total_records": total,
            "indexed_records": self._search_index.size,
            "metrics": self._metrics.snapshot(),
        }


def _safe_val(value: Any) -> Any:
    """Make a value safe for storage."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return str(value)