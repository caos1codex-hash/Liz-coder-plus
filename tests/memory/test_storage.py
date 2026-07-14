"""Tests for memory storage backends: InMemoryStorage and FileStorage."""

import json
import os
import tempfile

import pytest

from packages.multiagent.src.multiagent.memctx.models import (
    MemoryRecord,
    MemoryType,
)
from packages.multiagent.src.multiagent.memctx.storage import (
    FileStorage,
    InMemoryStorage,
    _metadata_matches,
    _text_matches,
)


# =========================================================================
# Helpers
# =========================================================================


def _make_record(
    id: str = "r1",
    content: str = "hello world",
    owner: str = "agent-1",
    tags: list | None = None,
    memory_type: MemoryType = MemoryType.KNOWLEDGE,
    priority: int = 0,
    metadata: dict | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=id,
        content=content,
        owner=owner,
        tags=tags or [],
        memory_type=memory_type,
        priority=priority,
        metadata=metadata or {},
    )


# =========================================================================
# InMemoryStorage
# =========================================================================


class TestInMemoryStorage:
    @pytest.fixture
    def storage(self):
        return InMemoryStorage()

    @pytest.mark.asyncio
    async def test_save_and_get(self, storage):
        rec = _make_record()
        saved = await storage.save(rec)
        assert saved.id == "r1"
        got = await storage.get("r1")
        assert got is not None
        assert got.content == "hello world"

    @pytest.mark.asyncio
    async def test_save_duplicate_raises(self, storage):
        rec = _make_record()
        await storage.save(rec)
        with pytest.raises(ValueError, match="duplicate"):
            await storage.save(rec)

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, storage):
        assert await storage.get("nope") is None

    @pytest.mark.asyncio
    async def test_exists(self, storage):
        rec = _make_record()
        await storage.save(rec)
        assert await storage.exists("r1") is True
        assert await storage.exists("nope") is False

    @pytest.mark.asyncio
    async def test_update(self, storage):
        rec = _make_record(content="original")
        await storage.save(rec)
        rec.content = "updated"
        updated = await storage.update(rec)
        assert updated.content == "updated"
        assert updated.version == 2

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self, storage):
        rec = _make_record()
        with pytest.raises(KeyError, match="not found"):
            await storage.update(rec)

    @pytest.mark.asyncio
    async def test_update_version_conflict(self, storage):
        rec = _make_record()
        await storage.save(rec)
        # Simulate stale version
        stale = _make_record()
        stale.version = 99
        with pytest.raises(ValueError, match="version conflict"):
            await storage.update(stale)

    @pytest.mark.asyncio
    async def test_delete(self, storage):
        rec = _make_record()
        await storage.save(rec)
        assert await storage.delete("r1") is True
        assert await storage.exists("r1") is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, storage):
        assert await storage.delete("nope") is False

    @pytest.mark.asyncio
    async def test_clear_all(self, storage):
        await storage.save(_make_record("a"))
        await storage.save(_make_record("b"))
        count = await storage.clear()
        assert count == 2
        assert await storage.count() == 0

    @pytest.mark.asyncio
    async def test_clear_by_owner(self, storage):
        await storage.save(_make_record("a", owner="agent-1"))
        await storage.save(_make_record("b", owner="agent-2"))
        count = await storage.clear(owner="agent-1")
        assert count == 1
        assert await storage.exists("a") is False
        assert await storage.exists("b") is True

    @pytest.mark.asyncio
    async def test_count(self, storage):
        assert await storage.count() == 0
        await storage.save(_make_record("a"))
        await storage.save(_make_record("b"))
        assert await storage.count() == 2

    @pytest.mark.asyncio
    async def test_count_by_owner(self, storage):
        await storage.save(_make_record("a", owner="agent-1"))
        await storage.save(_make_record("b", owner="agent-2"))
        await storage.save(_make_record("c", owner="agent-1"))
        assert await storage.count(owner="agent-1") == 2
        assert await storage.count(owner="agent-2") == 1

    @pytest.mark.asyncio
    async def test_search_by_tag(self, storage):
        await storage.save(_make_record("a", tags=["python", "code"]))
        await storage.save(_make_record("b", tags=["java", "code"]))
        await storage.save(_make_record("c", tags=["design"]))
        results = await storage.search(tags=["code"])
        ids = {r.id for r in results}
        assert ids == {"a", "b"}

    @pytest.mark.asyncio
    async def test_search_by_owner(self, storage):
        await storage.save(_make_record("a", owner="agent-1"))
        await storage.save(_make_record("b", owner="agent-2"))
        results = await storage.search(owner="agent-1")
        assert len(results) == 1
        assert results[0].id == "a"

    @pytest.mark.asyncio
    async def test_search_by_type(self, storage):
        await storage.save(_make_record("a", memory_type=MemoryType.CONVERSATION))
        await storage.save(_make_record("b", memory_type=MemoryType.PROJECT))
        results = await storage.search(memory_type=MemoryType.CONVERSATION)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_by_query(self, storage):
        await storage.save(_make_record("a", content="python programming guide"))
        await storage.save(_make_record("b", content="java cookbook"))
        results = await storage.search(query="python")
        assert len(results) == 1
        assert results[0].id == "a"

    @pytest.mark.asyncio
    async def test_search_by_metadata(self, storage):
        await storage.save(
            _make_record("a", metadata={"env": "prod", "region": "us"})
        )
        await storage.save(
            _make_record("b", metadata={"env": "dev", "region": "us"})
        )
        results = await storage.search(metadata_filter={"env": "prod"})
        assert len(results) == 1
        assert results[0].id == "a"

    @pytest.mark.asyncio
    async def test_search_combined_filters(self, storage):
        await storage.save(
            _make_record("a", owner="a1", tags=["python"], memory_type=MemoryType.KNOWLEDGE)
        )
        await storage.save(
            _make_record("b", owner="a1", tags=["java"], memory_type=MemoryType.KNOWLEDGE)
        )
        await storage.save(
            _make_record("c", owner="a2", tags=["python"], memory_type=MemoryType.KNOWLEDGE)
        )
        results = await storage.search(
            owner="a1", tags=["python"], memory_type=MemoryType.KNOWLEDGE
        )
        assert len(results) == 1
        assert results[0].id == "a"

    @pytest.mark.asyncio
    async def test_search_limit_offset(self, storage):
        for i in range(10):
            await storage.save(_make_record(f"r{i}"))
        page1 = await storage.search(limit=3, offset=0)
        page2 = await storage.search(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0].id != page2[0].id

    @pytest.mark.asyncio
    async def test_search_priority_sort(self, storage):
        await storage.save(_make_record("low", priority=1))
        await storage.save(_make_record("high", priority=10))
        await storage.save(_make_record("mid", priority=5))
        results = await storage.search()
        assert results[0].id == "high"
        assert results[1].id == "mid"
        assert results[2].id == "low"

    @pytest.mark.asyncio
    async def test_list(self, storage):
        await storage.save(_make_record("a", owner="o1"))
        await storage.save(_make_record("b", owner="o2"))
        results = await storage.list(owner="o1")
        assert len(results) == 1


# =========================================================================
# FileStorage
# =========================================================================


class TestFileStorage:
    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path

    @pytest.fixture
    def storage(self, tmp_dir):
        return FileStorage(base_path=str(tmp_dir / ".memory"))

    @pytest.mark.asyncio
    async def test_save_and_get(self, storage):
        rec = _make_record()
        saved = await storage.save(rec)
        got = await storage.get("r1")
        assert got is not None
        assert got.content == "hello world"
        assert got.owner == "agent-1"

    @pytest.mark.asyncio
    async def test_files_created(self, storage, tmp_dir):
        rec = _make_record(memory_type=MemoryType.CONVERSATION)
        await storage.save(rec)
        type_dir = tmp_dir / ".memory" / "conversation"
        assert type_dir.exists()
        assert (type_dir / "r1.json").exists()

    @pytest.mark.asyncio
    async def test_file_content(self, storage, tmp_dir):
        rec = _make_record()
        await storage.save(rec)
        fpath = tmp_dir / ".memory" / "knowledge" / "r1.json"
        data = json.loads(fpath.read_text())
        assert data["id"] == "r1"
        assert data["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_save_duplicate_raises(self, storage):
        rec = _make_record()
        await storage.save(rec)
        with pytest.raises(ValueError, match="duplicate"):
            await storage.save(rec)

    @pytest.mark.asyncio
    async def test_update(self, storage):
        rec = _make_record(content="v1")
        await storage.save(rec)
        rec.content = "v2"
        updated = await storage.update(rec)
        assert updated.content == "v2"
        assert updated.version == 2

    @pytest.mark.asyncio
    async def test_delete(self, storage, tmp_dir):
        rec = _make_record()
        await storage.save(rec)
        assert await storage.delete("r1") is True
        # File should be deleted
        fpath = tmp_dir / ".memory" / "knowledge" / "r1.json"
        assert not fpath.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, storage):
        assert await storage.delete("nope") is False

    @pytest.mark.asyncio
    async def test_clear(self, storage):
        await storage.save(_make_record("a"))
        await storage.save(_make_record("b"))
        count = await storage.clear()
        assert count == 2
        assert await storage.count() == 0

    @pytest.mark.asyncio
    async def test_search(self, storage):
        await storage.save(_make_record("a", tags=["python"]))
        await storage.save(_make_record("b", tags=["java"]))
        results = await storage.search(tags=["python"])
        assert len(results) == 1
        assert results[0].id == "a"

    @pytest.mark.asyncio
    async def test_corrupt_file_handling(self, storage, tmp_dir):
        # Save a valid record
        rec = _make_record("a")
        await storage.save(rec)
        # Corrupt the file
        fpath = tmp_dir / ".memory" / "knowledge" / "a.json"
        fpath.write_text("NOT JSON{{{")
        # Search should skip corrupt file
        results = await storage.search()
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_index_rebuild(self, storage, tmp_dir):
        await storage.save(_make_record("a"))
        # Create a new storage instance (simulates restart)
        storage2 = FileStorage(base_path=str(tmp_dir / ".memory"))
        assert await storage2.exists("a") is True


# =========================================================================
# Helper functions
# =========================================================================


class TestHelpers:
    def test_metadata_matches_exact(self):
        assert _metadata_matches({"env": "prod"}, {"env": "prod"}) is True

    def test_metadata_matches_partial(self):
        assert _metadata_matches({"env": "prod", "region": "us"}, {"env": "prod"}) is True

    def test_metadata_matches_missing_key(self):
        assert _metadata_matches({"env": "prod"}, {"region": "us"}) is False

    def test_metadata_matches_wrong_value(self):
        assert _metadata_matches({"env": "prod"}, {"env": "dev"}) is False

    def test_text_matches_content(self):
        rec = _make_record(content="python programming")
        assert _text_matches(rec, "python") is True
        assert _text_matches(rec, "ruby") is False

    def test_text_matches_dict_content(self):
        rec = _make_record(content={"text": "hello world"})
        assert _text_matches(rec, "hello") is True

    def test_text_matches_tag(self):
        rec = _make_record(tags=["python-code"])
        assert _text_matches(rec, "python") is True

    def test_text_matches_owner(self):
        rec = _make_record(owner="AgentOne")
        assert _text_matches(rec, "agentone") is True

    def test_text_matches_case_insensitive(self):
        rec = _make_record(content="Python Programming")
        assert _text_matches(rec, "python") is True
        assert _text_matches(rec, "PYTHON") is True