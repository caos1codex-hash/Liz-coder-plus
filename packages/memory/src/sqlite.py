"""SQLite memory backend (stub).

Sprint 1 - Prompt 1 (Foundation): the class exists and implements the
MemoryBackend protocol shape, but the actual SQL operations are stubbed.
Full implementation lands in Sprint 2.
"""

from __future__ import annotations

import logging
from typing import Any

from src.base import MemoryBackend  # noqa: F401 (re-exported for convenience)

logger = logging.getLogger(__name__)


class SQLiteMemoryBackend:
    """SQLite-backed persistent memory.

    Uses SQLAlchemy 2.0 + aiosqlite for async access. Sprint 1 only
    declares the surface; Sprint 2 wires up the engine, session factory,
    and CRUD operations.
    """

    def __init__(self, database_url: str = "sqlite+aiosqlite:///./lizcoderplus.db") -> None:
        self._database_url = database_url
        self._engine = None
        self._session_factory = None

    async def initialize(self) -> None:
        """Initialize the database engine and create tables."""
        logger.info("SQLiteMemoryBackend.initialize() stub - implemented in Sprint 2")

    async def store(self, key: str, value: Any, *, namespace: str = "default") -> None:
        """Store a value (stub)."""
        logger.debug("store stub: ns=%s key=%s", namespace, key)

    async def retrieve(self, key: str, *, namespace: str = "default") -> Any | None:
        """Retrieve a value (stub)."""
        logger.debug("retrieve stub: ns=%s key=%s", namespace, key)
        return None

    async def delete(self, key: str, *, namespace: str = "default") -> bool:
        """Delete a value (stub)."""
        logger.debug("delete stub: ns=%s key=%s", namespace, key)
        return False

    async def list_keys(self, *, namespace: str = "default") -> list[str]:
        """List keys in a namespace (stub)."""
        logger.debug("list_keys stub: ns=%s", namespace)
        return []

    async def close(self) -> None:
        """Release resources (stub)."""
        logger.info("SQLiteMemoryBackend.close() stub")
