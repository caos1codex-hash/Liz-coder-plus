"""Tests for tool_intelligence.capability_index.

Sprint 4.1 — Tool Intelligence Foundation.
"""

from __future__ import annotations

import pytest
from tool_intelligence import (
    CapabilityIndex,
    RiskLevel,
    ToolCategory,
    ToolIntelligenceError,
    ToolMetadata,
)
from tool_intelligence.capability_index import extract_keywords

# ----------------------------------------------------------------------
# extract_keywords
# ----------------------------------------------------------------------


class TestExtractKeywords:
    def test_empty(self) -> None:
        assert extract_keywords("") == []

    def test_none(self) -> None:
        assert extract_keywords(None) == []  # type: ignore[arg-type]

    def test_basic(self) -> None:
        kws = extract_keywords("Read files from disk")
        assert "read" in kws
        assert "files" in kws
        assert "disk" in kws

    def test_stopwords_removed(self) -> None:
        kws = extract_keywords("the tool can read a file")
        assert "the" not in kws
        assert "can" not in kws
        assert "a" not in kws
        assert "read" in kws
        assert "file" in kws

    def test_dedup(self) -> None:
        kws = extract_keywords("file file file")
        assert kws == ["file"]

    def test_lowercase(self) -> None:
        kws = extract_keywords("File FILE file")
        assert kws == ["file"]

    def test_extra_stopwords(self) -> None:
        kws = extract_keywords("read file", extra_stopwords=["read"])
        assert "read" not in kws
        assert "file" in kws

    def test_short_tokens_skipped(self) -> None:
        # Single-char tokens are skipped by the regex (needs 2+ chars).
        kws = extract_keywords("a b c file")
        assert "file" in kws
        assert all(len(k) >= 2 for k in kws)

    def test_preserves_order(self) -> None:
        kws = extract_keywords("first second third")
        assert kws == ["first", "second", "third"]

    def test_underscore_and_dash_tokens(self) -> None:
        kws = extract_keywords("file_reader write-file")
        assert "file_reader" in kws
        assert "write-file" in kws


# ----------------------------------------------------------------------
# CapabilityIndex — Mutations
# ----------------------------------------------------------------------


class TestCapabilityIndexAdd:
    def test_add_tool(self, file_tool_meta: ToolMetadata) -> None:
        idx = CapabilityIndex()
        idx.add(file_tool_meta)
        assert len(idx) == 1
        assert file_tool_meta.id in idx

    def test_add_duplicate_id_raises(self, file_tool_meta: ToolMetadata) -> None:
        idx = CapabilityIndex()
        idx.add(file_tool_meta)
        with pytest.raises(ToolIntelligenceError, match="already exists"):
            idx.add(file_tool_meta)

    def test_add_duplicate_name_raises(self, file_tool_meta: ToolMetadata) -> None:
        idx = CapabilityIndex()
        idx.add(file_tool_meta)
        # Same name, different id.
        dup = ToolMetadata(
            id="different_id",
            name=file_tool_meta.name,
            description="different",
        )
        with pytest.raises(ToolIntelligenceError, match="name 'file_read' already"):
            idx.add(dup)

    def test_add_non_metadata_raises(self) -> None:
        idx = CapabilityIndex()
        with pytest.raises(TypeError):
            idx.add("not a metadata")  # type: ignore[arg-type]

    def test_add_auto_extracts_keywords(self) -> None:
        idx = CapabilityIndex()
        meta = ToolMetadata(
            id="t1",
            name="my_tool",
            description="reads files from disk",
        )
        idx.add(meta)
        # The auto-extracted keywords should be present.
        assert "reads" in meta.keywords
        assert "files" in meta.keywords
        assert "disk" in meta.keywords


class TestCapabilityIndexUpdate:
    def test_update_existing(self, file_tool_meta: ToolMetadata) -> None:
        idx = CapabilityIndex()
        idx.add(file_tool_meta)
        # Change the description.
        updated = ToolMetadata(
            id=file_tool_meta.id,
            name=file_tool_meta.name,
            description="new description",
            category=file_tool_meta.category,
            capabilities=file_tool_meta.capabilities,
        )
        idx.update(updated)
        retrieved = idx.get(file_tool_meta.id)
        assert retrieved is not None
        assert retrieved.description == "new description"

    def test_update_nonexistent_adds(self, file_tool_meta: ToolMetadata) -> None:
        idx = CapabilityIndex()
        idx.update(file_tool_meta)
        assert len(idx) == 1

    def test_update_with_name_change(self, file_tool_meta: ToolMetadata) -> None:
        idx = CapabilityIndex()
        idx.add(file_tool_meta)
        updated = ToolMetadata(
            id=file_tool_meta.id,
            name="renamed_tool",
            description="renamed",
            category=file_tool_meta.category,
            capabilities=file_tool_meta.capabilities,
        )
        idx.update(updated)
        assert idx.get_by_name("renamed_tool") is not None
        assert idx.get_by_name(file_tool_meta.name) is None

    def test_update_name_collision_raises(
        self, file_tool_meta: ToolMetadata, shell_tool_meta: ToolMetadata
    ) -> None:
        idx = CapabilityIndex()
        idx.add(file_tool_meta)
        idx.add(shell_tool_meta)
        # Try to rename shell to file_read's name.
        renamed = ToolMetadata(
            id=shell_tool_meta.id,
            name=file_tool_meta.name,  # collides
            description="x",
        )
        with pytest.raises(ToolIntelligenceError, match="already exists"):
            idx.update(renamed)


class TestCapabilityIndexRemove:
    def test_remove_by_id(self, file_tool_meta: ToolMetadata) -> None:
        idx = CapabilityIndex()
        idx.add(file_tool_meta)
        assert idx.remove(file_tool_meta.id) is True
        assert len(idx) == 0
        assert file_tool_meta.id not in idx

    def test_remove_nonexistent_returns_false(self) -> None:
        idx = CapabilityIndex()
        assert idx.remove("nope") is False

    def test_remove_by_name(self, file_tool_meta: ToolMetadata) -> None:
        idx = CapabilityIndex()
        idx.add(file_tool_meta)
        assert idx.remove_by_name(file_tool_meta.name) is True
        assert idx.remove_by_name(file_tool_meta.name) is False

    def test_remove_by_name_nonexistent(self) -> None:
        idx = CapabilityIndex()
        assert idx.remove_by_name("nope") is False

    def test_remove_cleans_secondary_indexes(
        self,
        file_tool_meta: ToolMetadata,
        shell_tool_meta: ToolMetadata,
    ) -> None:
        idx = CapabilityIndex()
        idx.add(file_tool_meta)
        idx.add(shell_tool_meta)
        idx.remove(file_tool_meta.id)
        # file_read's capabilities should no longer be indexed.
        results = idx.by_capability("filesystem.read")
        assert results == []
        results = idx.by_tag("io")
        # shell_tool doesn't have the "io" tag, so should be empty.
        assert results == []


class TestCapabilityIndexClear:
    def test_clear(self, populated_index: CapabilityIndex) -> None:
        assert len(populated_index) > 0
        populated_index.clear()
        assert len(populated_index) == 0
        assert populated_index.categories() == []


# ----------------------------------------------------------------------
# CapabilityIndex — Lookups
# ----------------------------------------------------------------------


class TestCapabilityIndexLookups:
    def test_get_by_id(
        self, populated_index: CapabilityIndex, file_tool_meta: ToolMetadata
    ) -> None:
        result = populated_index.get(file_tool_meta.id)
        assert result is not None
        assert result.id == file_tool_meta.id

    def test_get_by_id_miss(self, populated_index: CapabilityIndex) -> None:
        assert populated_index.get("nonexistent") is None

    def test_get_by_name(
        self, populated_index: CapabilityIndex, file_tool_meta: ToolMetadata
    ) -> None:
        result = populated_index.get_by_name(file_tool_meta.name)
        assert result is not None
        assert result.name == file_tool_meta.name

    def test_get_by_alias(
        self, populated_index: CapabilityIndex, file_tool_meta: ToolMetadata
    ) -> None:
        # "cat" is one of file_read's aliases.
        result = populated_index.get_by_name("cat")
        assert result is not None
        assert result.id == file_tool_meta.id

    def test_get_by_name_miss(self, populated_index: CapabilityIndex) -> None:
        assert populated_index.get_by_name("nonexistent") is None

    def test_by_category(
        self, populated_index: CapabilityIndex
    ) -> None:
        file_tools = populated_index.by_category(ToolCategory.FILE)
        assert len(file_tools) == 2  # file_read + file_write
        names = {m.name for m in file_tools}
        assert "file_read" in names
        assert "file_write" in names

    def test_by_category_empty(self, populated_index: CapabilityIndex) -> None:
        # No MEMORY tools in the populated index.
        assert populated_index.by_category(ToolCategory.MEMORY) == []

    def test_by_capability_exact(
        self, populated_index: CapabilityIndex
    ) -> None:
        results = populated_index.by_capability("filesystem.read")
        assert len(results) == 1
        assert results[0].name == "file_read"

    def test_by_capability_parent_matches_child(
        self, populated_index: CapabilityIndex
    ) -> None:
        # "filesystem" should match both filesystem.read and filesystem.write.
        results = populated_index.by_capability("filesystem")
        names = {m.name for m in results}
        assert "file_read" in names
        assert "file_write" in names

    def test_by_capability_wildcard(
        self, populated_index: CapabilityIndex
    ) -> None:
        results = populated_index.by_capability("filesystem.*")
        names = {m.name for m in results}
        assert "file_read" in names
        assert "file_write" in names

    def test_by_capability_no_match(
        self, populated_index: CapabilityIndex
    ) -> None:
        assert populated_index.by_capability("nonexistent.cap") == []

    def test_by_tag(
        self, populated_index: CapabilityIndex
    ) -> None:
        results = populated_index.by_tag("io")
        names = {m.name for m in results}
        assert "file_read" in names
        assert "file_write" in names

    def test_by_tag_case_insensitive(
        self, populated_index: CapabilityIndex
    ) -> None:
        results = populated_index.by_tag("IO")
        assert len(results) >= 2

    def test_by_tag_no_match(
        self, populated_index: CapabilityIndex
    ) -> None:
        assert populated_index.by_tag("nonexistent") == []

    def test_by_keyword(
        self, populated_index: CapabilityIndex
    ) -> None:
        results = populated_index.by_keyword("disk")
        names = {m.name for m in results}
        assert "file_read" in names
        assert "file_write" in names

    def test_by_keyword_no_match(
        self, populated_index: CapabilityIndex
    ) -> None:
        assert populated_index.by_keyword("nonexistent") == []


# ----------------------------------------------------------------------
# CapabilityIndex — Search & Find
# ----------------------------------------------------------------------


class TestCapabilityIndexSearch:
    def test_search_returns_relevant(self, populated_index: CapabilityIndex) -> None:
        results = populated_index.search("read file from disk")
        assert len(results) > 0
        # file_read should be the top result.
        assert results[0].name == "file_read"

    def test_search_empty_query(self, populated_index: CapabilityIndex) -> None:
        assert populated_index.search("") == []

    def test_search_no_match(self, populated_index: CapabilityIndex) -> None:
        assert populated_index.search("zzznonexistent") == []

    def test_search_limit(self, populated_index: CapabilityIndex) -> None:
        results = populated_index.search("file", limit=1)
        assert len(results) <= 1

    def test_search_with_stopwords_only(self, populated_index: CapabilityIndex) -> None:
        # All stopwords -> no tokens -> empty result.
        assert populated_index.search("the a an") == []


class TestCapabilityIndexFind:
    def test_find_by_category(self, populated_index: CapabilityIndex) -> None:
        results = populated_index.find(category=ToolCategory.SHELL)
        assert len(results) == 1
        assert results[0].name == "shell_exec"

    def test_find_by_capabilities(self, populated_index: CapabilityIndex) -> None:
        results = populated_index.find(capabilities=["filesystem.read"])
        assert len(results) == 1
        assert results[0].name == "file_read"

    def test_find_by_tags(self, populated_index: CapabilityIndex) -> None:
        results = populated_index.find(tags=["io"])
        assert len(results) == 2

    def test_find_by_keywords(self, populated_index: CapabilityIndex) -> None:
        results = populated_index.find(keywords=["disk"])
        assert len(results) == 2

    def test_find_with_multiple_criteria_and(
        self, populated_index: CapabilityIndex
    ) -> None:
        # Tools that are FILE category AND have "io" tag.
        results = populated_index.find(
            category=ToolCategory.FILE, tags=["io"]
        )
        assert len(results) == 2

    def test_find_with_max_risk_level(
        self, populated_index: CapabilityIndex
    ) -> None:
        results = populated_index.find(max_risk_level=RiskLevel.LOW)
        # Only LOW risk tools: file_read, web_search, llm_summarize.
        names = {m.name for m in results}
        assert "file_read" in names
        assert "web_search" in names
        assert "llm_summarize" in names
        assert "shell_exec" not in names  # HIGH
        assert "file_write" not in names  # MEDIUM
        assert "db_query" not in names  # MEDIUM

    def test_find_with_no_criteria_returns_all(
        self, populated_index: CapabilityIndex
    ) -> None:
        results = populated_index.find()
        assert len(results) == 6


# ----------------------------------------------------------------------
# CapabilityIndex — Inspection
# ----------------------------------------------------------------------


class TestCapabilityIndexInspection:
    def test_all_sorted(self, populated_index: CapabilityIndex) -> None:
        all_tools = populated_index.all()
        names = [m.name for m in all_tools]
        assert names == sorted(names)

    def test_names(self, populated_index: CapabilityIndex) -> None:
        names = populated_index.names()
        assert "file_read" in names
        assert names == sorted(names)

    def test_categories(self, populated_index: CapabilityIndex) -> None:
        cats = populated_index.categories()
        cat_values = {c.value for c in cats}
        assert "file" in cat_values
        assert "shell" in cat_values
        assert "web" in cat_values

    def test_capabilities(self, populated_index: CapabilityIndex) -> None:
        caps = populated_index.capabilities()
        assert "filesystem.read" in caps
        assert "filesystem.write" in caps
        assert "filesystem" in caps  # parent should be indexed

    def test_tags(self, populated_index: CapabilityIndex) -> None:
        tags = populated_index.tags()
        assert "io" in tags
        assert "disk" in tags

    def test_keywords(self, populated_index: CapabilityIndex) -> None:
        kws = populated_index.keywords()
        assert "read" in kws
        assert "file" in kws

    def test_summary(self, populated_index: CapabilityIndex) -> None:
        s = populated_index.summary()
        assert s["total"] == 6
        assert s["categories"] >= 4
        assert s["by_category"]["file"] == 2
        assert s["by_risk_level"]["low"] == 3  # file_read, web_search, llm
        assert s["mutations"]["adds"] == 6
        assert s["lookups"]["total"] >= 0


# ----------------------------------------------------------------------
# CapabilityIndex — Dunder methods
# ----------------------------------------------------------------------


class TestCapabilityIndexDunders:
    def test_len(self, populated_index: CapabilityIndex) -> None:
        assert len(populated_index) == 6

    def test_contains(self, populated_index: CapabilityIndex, file_tool_meta: ToolMetadata) -> None:
        assert file_tool_meta.id in populated_index
        assert "nonexistent" not in populated_index

    def test_iter(self, populated_index: CapabilityIndex) -> None:
        tools = list(populated_index)
        assert len(tools) == 6
