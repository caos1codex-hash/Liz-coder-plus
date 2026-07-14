"""Tests for the MemorySearchIndex."""

import pytest

from packages.multiagent.src.multiagent.memctx.models import (
    MemoryRecord,
    MemoryType,
)
from packages.multiagent.src.multiagent.memctx.search import (
    MemorySearchIndex,
    SearchResult,
)


def _rec(id, content="test", owner="a1", tags=None, metadata=None, priority=0, memory_type=None):
    return MemoryRecord(
        id=id, content=content, owner=owner, tags=tags or [],
        metadata=metadata or {}, priority=priority,
        memory_type=memory_type or MemoryType.KNOWLEDGE,
    )


class TestSearchIndexBasic:
    def test_empty_index(self):
        idx = MemorySearchIndex()
        assert idx.size == 0

    def test_index_and_retrieve(self):
        idx = MemorySearchIndex()
        rec = _rec("r1", "hello world")
        idx.index(rec)
        assert idx.size == 1
        found = idx.search_by_id("r1")
        assert found is not None
        assert found.content == "hello world"

    def test_remove(self):
        idx = MemorySearchIndex()
        rec = _rec("r1")
        idx.index(rec)
        assert idx.remove("r1") is True
        assert idx.size == 0
        assert idx.search_by_id("r1") is None

    def test_remove_nonexistent(self):
        idx = MemorySearchIndex()
        assert idx.remove("nope") is False

    def test_clear(self):
        idx = MemorySearchIndex()
        for i in range(10):
            idx.index(_rec(f"r{i}"))
        idx.clear()
        assert idx.size == 0


class TestSearchByTag:
    def test_single_tag(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", tags=["python"]))
        idx.index(_rec("r2", tags=["java"]))
        results = idx.search_by_tags(["python"])
        assert len(results) == 1
        assert results[0].id == "r1"

    def test_multiple_tags_match_all(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", tags=["python", "backend"]))
        idx.index(_rec("r2", tags=["python", "frontend"]))
        idx.index(_rec("r3", tags=["java", "backend"]))
        results = idx.search_by_tags(["python", "backend"])
        assert len(results) == 1
        assert results[0].id == "r1"

    def test_multiple_tags_match_any(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", tags=["python"]))
        idx.index(_rec("r2", tags=["java"]))
        results = idx.search_by_tags(["python", "java"], match_all=False)
        assert len(results) == 2

    def test_no_tags_returns_all(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1"))
        idx.index(_rec("r2"))
        results = idx.search_by_tags([])
        assert len(results) == 2


class TestSearchByOwner:
    def test_single_owner(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", owner="agent-1"))
        idx.index(_rec("r2", owner="agent-2"))
        results = idx.search_by_owner("agent-1")
        assert len(results) == 1

    def test_no_results(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", owner="agent-1"))
        results = idx.search_by_owner("agent-99")
        assert len(results) == 0


class TestSearchByType:
    def test_by_type(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", memory_type=MemoryType.CONVERSATION))
        idx.index(_rec("r2", memory_type=MemoryType.PROJECT))
        results = idx.search_by_type(MemoryType.CONVERSATION)
        assert len(results) == 1


class TestSearchByText:
    def test_single_word(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", content="python programming guide"))
        idx.index(_rec("r2", content="java cookbook"))
        results = idx.search_by_text("python")
        assert len(results) == 1
        assert results[0].score >= 1.0

    def test_multiple_words(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", content="python programming language"))
        idx.index(_rec("r2", content="python snake animal"))
        results = idx.search_by_text("python programming")
        assert results[0].record.id == "r1"  # Both words match
        assert results[0].score >= 2.0

    def test_case_insensitive(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", content="Python Programming"))
        results = idx.search_by_text("python")
        assert len(results) == 1

    def test_searches_owner_text(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", content="stuff", owner="PythonAgent"))
        results = idx.search_by_text("PythonAgent")
        assert len(results) == 1

    def test_searches_tag_text(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", content="stuff", tags=["python-code"]))
        results = idx.search_by_text("python")
        assert len(results) == 1

    def test_empty_query(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", content="something"))
        results = idx.search_by_text("")
        assert len(results) == 0

    def test_result_sorted_by_score(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", content="python"))
        idx.index(_rec("r2", content="python programming language guide"))
        results = idx.search_by_text("python programming")
        assert results[0].score >= results[1].score

    def test_search_result_dict(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", content="test"))
        results = idx.search_by_text("test")
        assert len(results) == 1
        d = results[0].to_dict()
        assert "record" in d
        assert "score" in d
        assert "matched_by" in d


class TestSearchByMetadata:
    def test_single_key_value(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", metadata={"env": "prod"}))
        idx.index(_rec("r2", metadata={"env": "dev"}))
        results = idx.search_by_metadata({"env": "prod"})
        assert len(results) == 1

    def test_multiple_key_values(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", metadata={"env": "prod", "region": "us"}))
        idx.index(_rec("r2", metadata={"env": "prod", "region": "eu"}))
        results = idx.search_by_metadata({"env": "prod", "region": "us"})
        assert len(results) == 1

    def test_no_match(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", metadata={"env": "prod"}))
        results = idx.search_by_metadata({"env": "staging"})
        assert len(results) == 0

    def test_empty_filter(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1"))
        results = idx.search_by_metadata({})
        assert len(results) == 1


class TestCombinedSearch:
    def test_owner_and_tag(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", owner="a1", tags=["python"]))
        idx.index(_rec("r2", owner="a1", tags=["java"]))
        idx.index(_rec("r3", owner="a2", tags=["python"]))
        results = idx.combined_search(owner="a1", tags=["python"])
        assert len(results) == 1
        assert results[0].record.id == "r1"

    def test_type_and_text(self):
        idx = MemorySearchIndex()
        idx.index(_rec("r1", "python guide", memory_type=MemoryType.KNOWLEDGE))
        idx.index(_rec("r2", "python chat", memory_type=MemoryType.CONVERSATION))
        results = idx.combined_search(
            memory_type=MemoryType.KNOWLEDGE, query="python"
        )
        assert len(results) == 1

    def test_limit(self):
        idx = MemorySearchIndex()
        for i in range(10):
            idx.index(_rec(f"r{i}"))
        results = idx.combined_search(limit=3)
        assert len(results) == 3

    def test_priority_scoring(self):
        idx = MemorySearchIndex()
        idx.index(_rec("low", priority=1))
        idx.index(_rec("high", priority=10))
        results = idx.combined_search()
        # High priority should have higher score
        high = next(r for r in results if r.record.id == "high")
        low = next(r for r in results if r.record.id == "low")
        assert high.score > low.score


class TestReindex:
    def test_reindex_updates(self):
        idx = MemorySearchIndex()
        rec = _rec("r1", "old-content-xyz", tags=["old-tag-xyz"])
        idx.index(rec)
        rec.content = "new-content-xyz"
        rec.tags = ["new-tag-xyz"]
        idx.reindex(rec)
        # Old tag should be gone
        results = idx.search_by_tags(["old-tag-xyz"])
        assert len(results) == 0
        # New tag should work
        results = idx.search_by_tags(["new-tag-xyz"])
        assert len(results) == 1