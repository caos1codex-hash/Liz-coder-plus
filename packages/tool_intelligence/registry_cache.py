"""Registry Cache for the Tool Intelligence package.

Sprint 4.1 — Tool Intelligence Foundation.

The ``RegistryCache`` wraps a :class:`CapabilityIndex` and adds:

  - **lazy build**:    the index is constructed on first access and
                       cached for subsequent reads.
  - **invalidation**: drop the cached index so it is rebuilt on next
                       access (``invalidate``).
  - **refresh**:       force an immediate rebuild (``refresh``).
  - **incremental**:   add/update/remove individual tools without
                       rebuilding the whole index (``upsert`` /
                       ``remove``).
  - **TTL**:           optional time-to-live; if the index is older
                       than ``ttl_seconds`` it is rebuilt automatically.

The cache is intentionally agnostic about *where* the tool metadata
comes from: it accepts a ``loader`` callable that returns an iterable
of :class:`ToolMetadata`. Callers typically pass a function that walks
the existing ``ToolRegistry`` and converts each tool via
``ToolMetadata.from_base_tool``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable

from .capability_index import CapabilityIndex
from .exceptions import ToolIntelligenceError
from .models import ToolMetadata

logger = logging.getLogger(__name__)

# Type alias for the loader callable.
Loader = Callable[[], Iterable[ToolMetadata]]


class RegistryCache:
    """Cached view of a tool registry, exposed as a CapabilityIndex.

    Args:
        loader:        Callable that returns the full tool list. Used
                       on first access, on ``refresh``, and when the
                       cache TTL expires.
        ttl_seconds:   Optional TTL. When set, the cache is rebuilt
                       automatically if it is older than the TTL.
                       ``None`` disables TTL (cache lives until
                       ``invalidate`` / ``refresh`` is called).
    """

    def __init__(
        self,
        loader: Loader,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        if not callable(loader):
            raise TypeError("loader must be callable")
        self._loader = loader
        self._ttl = ttl_seconds
        self._index: CapabilityIndex | None = None
        self._built_at: float = 0.0
        self._build_count: int = 0
        self._invalidate_count: int = 0
        self._refresh_count: int = 0
        self._upsert_count: int = 0
        self._remove_count: int = 0
        self._last_error: ToolIntelligenceError | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def ttl_seconds(self) -> float | None:
        return self._ttl

    @property
    def is_built(self) -> bool:
        """Return True if the cache has been built at least once."""
        return self._index is not None

    @property
    def built_at(self) -> float:
        """Monotonic timestamp of the last build."""
        return self._built_at

    @property
    def age(self) -> float:
        """Seconds since the last build (0.0 if never built)."""
        if self._built_at == 0.0:
            return 0.0
        return time.monotonic() - self._built_at

    @property
    def is_expired(self) -> bool:
        """Return True if the TTL has expired (or the cache is empty)."""
        if self._index is None:
            return True
        if self._ttl is None:
            return False
        return self.age > self._ttl

    @property
    def last_error(self) -> ToolIntelligenceError | None:
        return self._last_error

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> CapabilityIndex:
        """Return the cached index, building it on first access or TTL expiry."""
        if self._index is None or self.is_expired:
            self._build()
        assert self._index is not None  # noqa: S101 — for type checkers
        return self._index

    def invalidate(self) -> None:
        """Drop the cached index. It will be rebuilt on next ``get``."""
        self._index = None
        self._built_at = 0.0
        self._invalidate_count += 1
        logger.debug("Registry cache invalidated")

    def refresh(self) -> CapabilityIndex:
        """Force an immediate rebuild and return the new index."""
        self._build()
        self._refresh_count += 1
        assert self._index is not None  # noqa: S101 — for type checkers
        return self._index

    def upsert(self, metadata: ToolMetadata) -> None:
        """Insert or update a single tool in the cached index.

        If the cache is empty, it is built first (so the new tool is
        merged with the existing set rather than replacing it).
        ``CapabilityIndex.update`` already delegates to ``add`` when the
        id is unknown, so no extra fallback is needed here.
        """
        if self._index is None:
            self._build()
        assert self._index is not None  # noqa: S101 — for type checkers
        self._index.update(metadata)
        self._upsert_count += 1
        logger.debug("Upserted tool '%s' into cache", metadata.name)

    def remove(self, tool_id: str) -> bool:
        """Remove a single tool from the cached index.

        Returns:
            True if the tool was present and removed, False otherwise.
        """
        if self._index is None:
            return False
        removed = self._index.remove(tool_id)
        if removed:
            self._remove_count += 1
            logger.debug("Removed tool '%s' from cache", tool_id)
        return removed

    def incremental_update(
        self,
        *,
        added: Iterable[ToolMetadata] | None = None,
        updated: Iterable[ToolMetadata] | None = None,
        removed: Iterable[str] | None = None,
    ) -> dict[str, int]:
        """Apply a batch of changes to the cached index without rebuilding.

        Returns:
            A dict with the number of tools added, updated and removed.
        """
        if self._index is None:
            self._build()
        assert self._index is not None  # noqa: S101 — for type checkers
        added_list = list(added or [])
        updated_list = list(updated or [])
        removed_list = list(removed or [])

        added_count = 0
        updated_count = 0
        removed_count = 0

        for meta in added_list:
            try:
                self._index.add(meta)
                added_count += 1
            except ToolIntelligenceError:
                # Already present: treat as update.
                self._index.update(meta)
                updated_count += 1

        for meta in updated_list:
            self._index.update(meta)
            updated_count += 1

        for tool_id in removed_list:
            if self._index.remove(tool_id):
                removed_count += 1

        self._upsert_count += added_count + updated_count
        self._remove_count += removed_count
        logger.debug(
            "Incremental update: +%d ~%d -%d",
            added_count,
            updated_count,
            removed_count,
        )
        return {
            "added": added_count,
            "updated": updated_count,
            "removed": removed_count,
        }

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "is_built": self.is_built,
            "built_at": self._built_at,
            "age_seconds": round(self.age, 3),
            "ttl_seconds": self._ttl,
            "is_expired": self.is_expired,
            "tools": len(self._index) if self._index is not None else 0,
            "build_count": self._build_count,
            "invalidate_count": self._invalidate_count,
            "refresh_count": self._refresh_count,
            "upsert_count": self._upsert_count,
            "remove_count": self._remove_count,
            "last_error": (
                self._last_error.message if self._last_error is not None else None
            ),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build(self) -> None:
        """Build a fresh index by invoking the loader."""
        start = time.monotonic()
        try:
            tools = list(self._loader())
        except Exception as exc:
            self._last_error = ToolIntelligenceError(
                f"Loader failed: {exc}",
                details={"exception": type(exc).__name__},
            )
            logger.exception("Registry cache loader failed")
            raise self._last_error from exc

        new_index = CapabilityIndex()
        for meta in tools:
            if not isinstance(meta, ToolMetadata):
                raise TypeError(
                    f"Loader must yield ToolMetadata, got {type(meta).__name__}"
                )
            try:
                new_index.add(meta)
            except ToolIntelligenceError as exc:
                # Skip duplicates silently — loader may return them.
                logger.warning(
                    "Skipping duplicate tool '%s' from loader: %s",
                    meta.name,
                    exc.message,
                )
        self._index = new_index
        self._built_at = time.monotonic()
        self._build_count += 1
        self._last_error = None
        logger.debug(
            "Registry cache built with %d tools in %.3fs",
            len(new_index),
            time.monotonic() - start,
        )


__all__ = ["Loader", "RegistryCache"]
