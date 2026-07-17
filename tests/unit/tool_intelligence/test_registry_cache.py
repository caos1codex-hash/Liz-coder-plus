"""Tests for tool_intelligence.registry_cache.

Sprint 4.1 — Tool Intelligence Foundation.
"""

from __future__ import annotations

import time

import pytest
from tool_intelligence import (
    CapabilityIndex,
    RegistryCache,
    ToolIntelligenceError,
    ToolMetadata,
)

# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


class TestRegistryCacheConstruction:
    def test_default_construction(self) -> None:
        cache = RegistryCache(loader=lambda: [])
        assert cache.is_built is False
        assert cache.ttl_seconds is None
        assert cache.last_error is None

    def test_with_ttl(self) -> None:
        cache = RegistryCache(loader=lambda: [], ttl_seconds=10.0)
        assert cache.ttl_seconds == 10.0

    def test_invalid_loader_raises(self) -> None:
        with pytest.raises(TypeError):
            RegistryCache(loader="not callable")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Building & Getting
# ----------------------------------------------------------------------


class TestRegistryCacheBuild:
    def test_get_builds_on_first_call(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        assert cache.is_built is False
        idx = cache.get()
        assert cache.is_built is True
        assert isinstance(idx, CapabilityIndex)
        assert len(idx) == 1

    def test_get_returns_cached(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        idx1 = cache.get()
        idx2 = cache.get()
        assert idx1 is idx2  # same object, no rebuild

    def test_built_at_timestamp(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        assert cache.built_at == 0.0
        cache.get()
        assert cache.built_at > 0.0

    def test_age_is_zero_before_build(self) -> None:
        cache = RegistryCache(loader=lambda: [])
        assert cache.age == 0.0

    def test_age_increases(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        age1 = cache.age
        time.sleep(0.01)
        age2 = cache.age
        assert age2 > age1


# ----------------------------------------------------------------------
# TTL
# ----------------------------------------------------------------------


class TestRegistryCacheTTL:
    def test_not_expired_without_ttl(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        assert cache.is_expired is False

    def test_expired_after_ttl(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(
            loader=lambda: [file_tool_meta], ttl_seconds=0.01
        )
        cache.get()
        assert cache.is_expired is False
        time.sleep(0.05)
        assert cache.is_expired is True

    def test_get_rebuilds_after_ttl(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(
            loader=lambda: [file_tool_meta], ttl_seconds=0.01
        )
        idx1 = cache.get()
        time.sleep(0.05)
        idx2 = cache.get()
        assert idx1 is not idx2  # rebuilt

    def test_expired_when_not_built(self) -> None:
        cache = RegistryCache(loader=lambda: [], ttl_seconds=10.0)
        assert cache.is_expired is True


# ----------------------------------------------------------------------
# Invalidation & Refresh
# ----------------------------------------------------------------------


class TestRegistryCacheInvalidate:
    def test_invalidate_drops_cache(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        assert cache.is_built is True
        cache.invalidate()
        assert cache.is_built is False
        assert cache.built_at == 0.0

    def test_get_after_invalidate_rebuilds(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        idx1 = cache.get()
        cache.invalidate()
        idx2 = cache.get()
        assert idx1 is not idx2

    def test_refresh_forces_rebuild(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        idx1 = cache.get()
        idx2 = cache.refresh()
        assert idx1 is not idx2
        assert cache.is_built is True


# ----------------------------------------------------------------------
# Incremental updates
# ----------------------------------------------------------------------


class TestRegistryCacheIncremental:
    def test_upsert_adds_new(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [])
        cache.upsert(file_tool_meta)
        idx = cache.get()
        assert len(idx) == 1
        assert idx.get(file_tool_meta.id) is not None

    def test_upsert_updates_existing(
        self, file_tool_meta: ToolMetadata
    ) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        updated = ToolMetadata(
            id=file_tool_meta.id,
            name=file_tool_meta.name,
            description="updated description",
        )
        cache.upsert(updated)
        idx = cache.get()
        retrieved = idx.get(file_tool_meta.id)
        assert retrieved is not None
        assert retrieved.description == "updated description"

    def test_remove_existing(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        assert cache.remove(file_tool_meta.id) is True
        idx = cache.get()
        assert len(idx) == 0

    def test_remove_nonexistent_returns_false(self) -> None:
        cache = RegistryCache(loader=lambda: [])
        cache.get()
        assert cache.remove("nope") is False

    def test_remove_before_build_returns_false(self) -> None:
        cache = RegistryCache(loader=lambda: [])
        assert cache.remove("nope") is False

    def test_incremental_update_batch(
        self,
        file_tool_meta: ToolMetadata,
        shell_tool_meta: ToolMetadata,
        web_search_tool_meta: ToolMetadata,
    ) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        result = cache.incremental_update(
            added=[shell_tool_meta],
            updated=[file_tool_meta],
            removed=["nonexistent"],
        )
        assert result["added"] == 1
        assert result["updated"] == 1
        assert result["removed"] == 0
        idx = cache.get()
        assert len(idx) == 2

    def test_incremental_update_added_with_existing_name_treated_as_update(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        # Same id, different description.
        updated = ToolMetadata(
            id=file_tool_meta.id,
            name=file_tool_meta.name,
            description="changed",
        )
        result = cache.incremental_update(added=[updated])
        assert result["added"] == 0
        assert result["updated"] == 1

    def test_incremental_update_removed_existing(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        result = cache.incremental_update(removed=[file_tool_meta.id])
        assert result["removed"] == 1
        idx = cache.get()
        assert len(idx) == 0

    def test_incremental_update_triggers_build_if_empty(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        # Don't call get() first.
        assert cache.is_built is False
        cache.incremental_update(added=[])
        assert cache.is_built is True


# ----------------------------------------------------------------------
# Loader errors
# ----------------------------------------------------------------------


class TestRegistryCacheLoaderErrors:
    def test_loader_exception_propagates(self) -> None:
        def bad_loader() -> list[ToolMetadata]:
            raise RuntimeError("boom")

        cache = RegistryCache(loader=bad_loader)
        with pytest.raises(ToolIntelligenceError, match="Loader failed"):
            cache.get()
        assert cache.last_error is not None
        assert "boom" in cache.last_error.message

    def test_loader_yielding_non_metadata_raises(self) -> None:
        cache = RegistryCache(loader=lambda: ["not metadata"])  # type: ignore[list-item]
        with pytest.raises(TypeError, match="must yield ToolMetadata"):
            cache.get()

    def test_loader_with_duplicate_skips(self, file_tool_meta: ToolMetadata) -> None:
        # Loader returns the same tool twice.
        cache = RegistryCache(loader=lambda: [file_tool_meta, file_tool_meta])
        idx = cache.get()
        assert len(idx) == 1  # duplicate skipped


# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------


class TestRegistryCacheStats:
    def test_stats_initial(self) -> None:
        cache = RegistryCache(loader=lambda: [])
        s = cache.stats()
        assert s["is_built"] is False
        assert s["tools"] == 0
        assert s["build_count"] == 0
        assert s["invalidate_count"] == 0
        assert s["refresh_count"] == 0
        assert s["upsert_count"] == 0
        assert s["remove_count"] == 0
        assert s["last_error"] is None

    def test_stats_after_build(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        s = cache.stats()
        assert s["is_built"] is True
        assert s["tools"] == 1
        assert s["build_count"] == 1

    def test_stats_after_invalidate(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        cache.invalidate()
        s = cache.stats()
        assert s["is_built"] is False
        assert s["invalidate_count"] == 1

    def test_stats_after_refresh(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        cache.refresh()
        s = cache.stats()
        assert s["refresh_count"] == 1
        assert s["build_count"] == 2  # initial get + refresh

    def test_stats_after_upsert(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [])
        cache.upsert(file_tool_meta)
        s = cache.stats()
        assert s["upsert_count"] == 1

    def test_stats_after_remove(self, file_tool_meta: ToolMetadata) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()
        cache.remove(file_tool_meta.id)
        s = cache.stats()
        assert s["remove_count"] == 1

    def test_stats_with_loader_error(self) -> None:
        def bad_loader() -> list[ToolMetadata]:
            raise ValueError("fail")

        cache = RegistryCache(loader=bad_loader)
        try:
            cache.get()
        except ToolIntelligenceError:
            pass
        s = cache.stats()
        assert s["last_error"] is not None
        assert "fail" in s["last_error"]
