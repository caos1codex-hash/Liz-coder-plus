"""Pluggable memory storage backends.

Provides:
- MemoryStorage: Abstract base class (Protocol-like) defining the storage contract.
- InMemoryStorage: Dict-based backend for testing and ephemeral use.
- FileStorage: JSON-file-based persistent backend for local deployments.

The architecture allows adding SQLite, PostgreSQL, Redis, or external
service backends without modifying the core engine.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import MemoryRecord, MemoryType, _serialize_value

logger = logging.getLogger(__name__)


class MemoryStorage(ABC):
    """Abstract base class for memory storage backends.

    All backends must implement these methods. Implementations should be
    thread-safe and handle their own error recovery.
    """

    @abstractmethod
    async def save(self, record: MemoryRecord) -> MemoryRecord:
        """Persist a new memory record. Returns the saved record (with any storage-generated fields)."""
        ...

    @abstractmethod
    async def update(self, record: MemoryRecord) -> MemoryRecord:
        """Update an existing record. Raises KeyError if not found."""
        ...

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """Delete a record by id. Returns True if deleted, False if not found."""
        ...

    @abstractmethod
    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        """Retrieve a single record by id, or None if not found."""
        ...

    @abstractmethod
    async def exists(self, memory_id: str) -> bool:
        """Check if a record exists."""
        ...

    @abstractmethod
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
        """Search records by criteria. All parameters are optional AND filters."""
        ...

    @abstractmethod
    async def list(
        self,
        owner: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        """List records, optionally filtered by owner and/or type."""
        ...

    @abstractmethod
    async def clear(self, owner: Optional[str] = None) -> int:
        """Delete all records (optionally filtered by owner). Returns count deleted."""
        ...

    @abstractmethod
    async def count(self, owner: Optional[str] = None) -> int:
        """Count records, optionally filtered by owner."""
        ...


# ===========================================================================
# InMemoryStorage
# ===========================================================================


class InMemoryStorage(MemoryStorage):
    """Dictionary-based memory storage.

    Suitable for testing, ephemeral sessions, and development.
    Thread-safe via threading.Lock.
    """

    def __init__(self) -> None:
        self._store: Dict[str, MemoryRecord] = {}
        self._lock = threading.Lock()

    async def save(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock:
            if record.id in self._store:
                raise ValueError(f"duplicate memory id: {record.id}")
            self._store[record.id] = record
        logger.debug("saved memory %s (type=%s)", record.id, record.memory_type.value)
        return record

    async def update(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock:
            if record.id not in self._store:
                raise KeyError(f"memory not found: {record.id}")
            existing = self._store[record.id]
            if record.version != existing.version:
                raise ValueError(
                    f"version conflict: expected {existing.version}, got {record.version}"
                )
            record.bump_version()
            self._store[record.id] = record
        logger.debug("updated memory %s (v%d)", record.id, record.version)
        return record

    async def delete(self, memory_id: str) -> bool:
        with self._lock:
            if memory_id in self._store:
                del self._store[memory_id]
                logger.debug("deleted memory %s", memory_id)
                return True
            return False

    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        with self._lock:
            return self._store.get(memory_id)

    async def exists(self, memory_id: str) -> bool:
        with self._lock:
            return memory_id in self._store

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
        results: List[MemoryRecord] = []
        with self._lock:
            for rec in self._store.values():
                if owner is not None and rec.owner != owner:
                    continue
                if memory_type is not None and rec.memory_type != memory_type:
                    continue
                if tags is not None:
                    if not all(t in rec.tags for t in tags):
                        continue
                if metadata_filter is not None:
                    if not _metadata_matches(rec.metadata, metadata_filter):
                        continue
                if query is not None:
                    if not _text_matches(rec, query):
                        continue
                results.append(rec)
        # Sort by priority desc, then updated_at desc
        results.sort(key=lambda r: (r.priority, r.updated_at), reverse=True)
        return results[offset : offset + limit]

    async def list(
        self,
        owner: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        return await self.search(
            owner=owner, memory_type=memory_type, limit=limit, offset=offset
        )

    async def clear(self, owner: Optional[str] = None) -> int:
        count = 0
        with self._lock:
            if owner is None:
                count = len(self._store)
                self._store.clear()
            else:
                to_delete = [
                    k for k, v in self._store.items() if v.owner == owner
                ]
                for k in to_delete:
                    del self._store[k]
                count = len(to_delete)
        logger.debug("cleared %d memories (owner=%s)", count, owner)
        return count

    async def count(self, owner: Optional[str] = None) -> int:
        with self._lock:
            if owner is None:
                return len(self._store)
            return sum(1 for v in self._store.values() if v.owner == owner)


# ===========================================================================
# FileStorage
# ===========================================================================


class FileStorage(MemoryStorage):
    """JSON-file-based persistent storage.

    Each memory record is stored as a separate JSON file in a directory
    structure: ``<base_path>/<memory_type>/<memory_id>.json``.

    Thread-safe via threading.Lock.
    """

    def __init__(self, base_path: str | Path = ".memory") -> None:
        self._base_path = Path(base_path)
        self._lock = threading.Lock()
        self._index: Dict[str, str] = {}  # id -> relative file path
        self._load_index()

    def _load_index(self) -> None:
        """Scan the base directory to rebuild the in-memory index."""
        self._index.clear()
        if not self._base_path.exists():
            return
        for mt_dir in self._base_path.iterdir():
            if mt_dir.is_dir():
                for f in mt_dir.glob("*.json"):
                    memory_id = f.stem
                    rel = str(f.relative_to(self._base_path))
                    self._index[memory_id] = rel

    def _file_path(self, record: MemoryRecord) -> Path:
        mt_dir = self._base_path / record.memory_type.value
        mt_dir.mkdir(parents=True, exist_ok=True)
        return mt_dir / f"{record.id}.json"

    def _read_record(self, file_path: Path) -> MemoryRecord:
        text = file_path.read_text(encoding="utf-8")
        data = json.loads(text)
        return MemoryRecord.from_dict(data)

    async def save(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock:
            if record.id in self._index:
                raise ValueError(f"duplicate memory id: {record.id}")
            fp = self._file_path(record)
            fp.write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._index[record.id] = str(fp.relative_to(self._base_path))
        logger.debug("saved memory %s to %s", record.id, fp)
        return record

    async def update(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock:
            if record.id not in self._index:
                raise KeyError(f"memory not found: {record.id}")
            # Read existing for version check
            fp = self._file_path(record)
            existing = self._read_record(fp)
            if record.version != existing.version:
                raise ValueError(
                    f"version conflict: expected {existing.version}, got {record.version}"
                )
            record.bump_version()
            fp.write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        logger.debug("updated memory %s (v%d)", record.id, record.version)
        return record

    async def delete(self, memory_id: str) -> bool:
        with self._lock:
            if memory_id not in self._index:
                return False
            rel = self._index.pop(memory_id)
            fp = self._base_path / rel
            if fp.exists():
                fp.unlink()
            logger.debug("deleted memory %s", memory_id)
            return True

    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        with self._lock:
            if memory_id not in self._index:
                return None
            rel = self._index[memory_id]
            fp = self._base_path / rel
            if not fp.exists():
                del self._index[memory_id]
                return None
            return self._read_record(fp)

    async def exists(self, memory_id: str) -> bool:
        with self._lock:
            return memory_id in self._index

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
        results: List[MemoryRecord] = []
        with self._lock:
            for memory_id, rel in self._index.items():
                fp = self._base_path / rel
                if not fp.exists():
                    continue
                try:
                    rec = self._read_record(fp)
                except (json.JSONDecodeError, KeyError, ValueError):
                    logger.warning("corrupt record file: %s", fp)
                    continue
                if owner is not None and rec.owner != owner:
                    continue
                if memory_type is not None and rec.memory_type != memory_type:
                    continue
                if tags is not None:
                    if not all(t in rec.tags for t in tags):
                        continue
                if metadata_filter is not None:
                    if not _metadata_matches(rec.metadata, metadata_filter):
                        continue
                if query is not None:
                    if not _text_matches(rec, query):
                        continue
                results.append(rec)
        results.sort(key=lambda r: (r.priority, r.updated_at), reverse=True)
        return results[offset : offset + limit]

    async def list(
        self,
        owner: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        return await self.search(
            owner=owner, memory_type=memory_type, limit=limit, offset=offset
        )

    async def clear(self, owner: Optional[str] = None) -> int:
        count = 0
        with self._lock:
            if owner is None:
                count = len(self._index)
                # Delete all JSON files
                for mt_dir in self._base_path.iterdir():
                    if mt_dir.is_dir():
                        for f in mt_dir.glob("*.json"):
                            f.unlink()
                self._index.clear()
            else:
                to_delete = []
                for mid, rel in self._index.items():
                    fp = self._base_path / rel
                    try:
                        rec = self._read_record(fp)
                        if rec.owner == owner:
                            to_delete.append(mid)
                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue
                for mid in to_delete:
                    rel = self._index.pop(mid)
                    fp = self._base_path / rel
                    if fp.exists():
                        fp.unlink()
                count = len(to_delete)
        logger.debug("cleared %d memories (owner=%s)", count, owner)
        return count

    async def count(self, owner: Optional[str] = None) -> int:
        with self._lock:
            if owner is None:
                return len(self._index)
            c = 0
            for mid, rel in self._index.items():
                fp = self._base_path / rel
                try:
                    rec = self._read_record(fp)
                    if rec.owner == owner:
                        c += 1
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            return c


# ===========================================================================
# Helpers
# ===========================================================================


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


def _text_matches(record: MemoryRecord, query: str) -> bool:
    """Simple substring match against content, tags, and owner."""
    q = query.lower()
    # Check content (if string or dict with text/content keys)
    content = record.content
    if isinstance(content, str) and q in content.lower():
        return True
    if isinstance(content, dict):
        for v in content.values():
            if isinstance(v, str) and q in v.lower():
                return True
    # Check tags
    for tag in record.tags:
        if q in tag.lower():
            return True
    # Check owner
    if q in record.owner.lower():
        return True
    return False