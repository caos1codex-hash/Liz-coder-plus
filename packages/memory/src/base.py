"""Memory backend protocol.

Every memory backend (SQLite, future vector DB, etc.) must implement
this Protocol so the orchestrator can talk to it uniformly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryBackend(Protocol):
    """Contract for persistent memory backends."""

    async def initialize(self) -> None:
        """Initialize the backend (create tables, open files, etc.)."""

    async def store(self, key: str, value: Any, *, namespace: str = "default") -> None:
        """Store a value under the given key and namespace."""

    async def retrieve(self, key: str, *, namespace: str = "default") -> Any | None:
        """Retrieve a value by key, or None if missing."""

    async def delete(self, key: str, *, namespace: str = "default") -> bool:
        """Delete a value. Returns True if a row was removed."""

    async def list_keys(self, *, namespace: str = "default") -> list[str]:
        """List all keys in the given namespace."""

    async def close(self) -> None:
        """Release resources held by the backend."""
