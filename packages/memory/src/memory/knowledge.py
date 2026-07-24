"""Knowledge Memory — long-term persistent knowledge store.

Stores key facts, preferences, and learned information that persists
across all conversation sessions. Unlike conversation memory which
is scoped to individual sessions, knowledge memory is global and
indexed by tags for efficient retrieval.

Sprint 8 — Knowledge Memory.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeEntry:
    """A single knowledge entry in the long-term memory.

    Attributes:
        key: Unique identifier (auto-generated if not provided).
        category: Category tag (preference, fact, instruction, context, custom).
        content: The knowledge content text.
        tags: List of tags for indexing and retrieval.
        source_session: Session ID where this knowledge was created.
        created_at: ISO timestamp of creation.
        updated_at: ISO timestamp of last update.
        access_count: Number of times this entry was retrieved.
        importance: 1-10 importance score (higher = more relevant).
    """

    key: str
    category: str
    content: str
    tags: list[str] = field(default_factory=list)
    source_session: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    access_count: int = 0
    importance: int = 5

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "key": self.key,
            "category": self.category,
            "content": self.content,
            "tags": self.tags,
            "source_session": self.source_session,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": self.access_count,
            "importance": self.importance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeEntry:
        """Deserialize from dictionary."""
        return cls(
            key=data["key"],
            category=data.get("category", "fact"),
            content=data["content"],
            tags=data.get("tags", []),
            source_session=data.get("source_session", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            access_count=data.get("access_count", 0),
            importance=data.get("importance", 5),
        )


class KnowledgeMemory:
    """Long-term knowledge memory store.

    Provides indexed storage and retrieval of knowledge entries across
    all sessions. Uses tag-based indexing with keyword matching for
    efficient retrieval without external dependencies.

    Usage::

        km = KnowledgeMemory()
        km.store("python_version", "User prefers Python 3.12",
                 category="preference", tags=["python", "version"])
        results = km.search("python", limit=5)
    """

    # Standard categories.
    CATEGORY_PREFERENCE = "preference"
    CATEGORY_FACT = "fact"
    CATEGORY_INSTRUCTION = "instruction"
    CATEGORY_CONTEXT = "context"
    CATEGORY_SKILL = "skill"

    def __init__(self, max_entries: int = 10000) -> None:
        self._entries: dict[str, KnowledgeEntry] = {}
        self._tag_index: dict[str, set[str]] = {}  # tag -> set of keys
        self._category_index: dict[str, set[str]] = {}  # category -> set of keys
        self._max_entries = max_entries
        self._entry_counter = 0

    @property
    def entry_count(self) -> int:
        """Number of stored knowledge entries."""
        return len(self._entries)

    def store(
        self,
        key: str | None,
        content: str,
        *,
        category: str = "fact",
        tags: list[str] | None = None,
        source_session: str = "",
        importance: int = 5,
        overwrite: bool = False,
    ) -> str:
        """Store a knowledge entry.

        Args:
            key: Unique identifier. Auto-generated if None.
            content: The knowledge content.
            category: Category tag.
            tags: Indexing tags.
            source_session: Origin session ID.
            importance: 1-10 importance score.
            overwrite: If True, update existing entry with same key.

        Returns:
            The entry key.
        """
        tags = tags or []
        actual_key = key or f"knowledge_{self._entry_counter:06d}"
        self._entry_counter += 1

        now = datetime.now(timezone.utc).isoformat()

        if actual_key in self._entries and not overwrite:
            logger.debug("Knowledge key '%s' already exists, skipping", actual_key)
            return actual_key

        # Update indexes.
        if actual_key in self._entries:
            # Remove old entry from indexes.
            old = self._entries[actual_key]
            self._remove_from_indexes(actual_key, old)

        # Check capacity.
        if len(self._entries) >= self._max_entries and actual_key not in self._entries:
            self._evict_least_important()

        # Create entry.
        entry = KnowledgeEntry(
            key=actual_key,
            category=category,
            content=content,
            tags=tags,
            source_session=source_session,
            created_at=now if actual_key not in self._entries else self._entries[actual_key].created_at,
            updated_at=now,
            importance=max(1, min(10, importance)),
        )

        self._entries[actual_key] = entry
        self._add_to_indexes(actual_key, entry)

        logger.debug(
            "Knowledge stored: key=%s category=%s tags=%s",
            actual_key, category, tags,
        )
        return actual_key

    def retrieve(self, key: str) -> KnowledgeEntry | None:
        """Retrieve a knowledge entry by exact key.

        Args:
            key: The knowledge entry key.

        Returns:
            The KnowledgeEntry or None if not found.
        """
        entry = self._entries.get(key)
        if entry:
            entry.access_count += 1
        return entry

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
        min_importance: int = 1,
    ) -> list[KnowledgeEntry]:
        """Search knowledge entries by query, tags, and/or category.

        Ranking: matches tags first (exact match), then keyword match
        in content, sorted by importance desc, then access count desc.

        Args:
            query: Search text (matched against content).
            category: Filter by category.
            tags: Required tags (all must match).
            limit: Maximum results.
            min_importance: Minimum importance threshold.

        Returns:
            List of matching KnowledgeEntry instances, ranked by relevance.
        """
        # Build candidate set from tag/category filters.
        candidates: set[str] = set(self._entries.keys())

        if tags:
            tag_candidates: set[str] | None = None
            for tag in tags:
                tag_lower = tag.lower()
                matching = set()
                for idx_tag, idx_keys in self._tag_index.items():
                    if tag_lower in idx_tag.lower() or idx_tag.lower() in tag_lower:
                        matching.update(idx_keys)
                if tag_candidates is None:
                    tag_candidates = matching
                else:
                    tag_candidates &= matching
            if tag_candidates is not None:
                candidates &= tag_candidates

        if category:
            cat_candidates = set()
            for idx_cat, idx_keys in self._category_index.items():
                if category.lower() in idx_cat.lower():
                    cat_candidates.update(idx_keys)
            candidates &= cat_candidates

        # Score and rank candidates.
        query_lower = query.lower()
        query_words = set(re.findall(r"\b\w+\b", query_lower))

        scored: list[tuple[float, KnowledgeEntry]] = []
        for key in candidates:
            entry = self._entries[key]
            if entry.importance < min_importance:
                continue

            content_lower = entry.content.lower()
            content_words = set(re.findall(r"\b\w+\b", content_lower))

            # Tag match score (highest priority).
            tag_score = 0.0
            if tags:
                matching_tags = sum(
                    1 for t in tags
                    if t.lower() in [et.lower() for et in entry.tags]
                )
                tag_score = matching_tags / len(tags) * 10.0

            # Keyword match score.
            word_overlap = len(query_words & content_words)
            keyword_score = word_overlap / max(len(query_words), 1) * 5.0

            # Exact substring match bonus.
            substring_score = 2.0 if query_lower in content_lower else 0.0

            total_score = (
                tag_score + keyword_score + substring_score +
                entry.importance * 0.5 +
                min(entry.access_count, 10) * 0.1
            )

            scored.append((total_score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def delete(self, key: str) -> bool:
        """Delete a knowledge entry.

        Args:
            key: The entry key to delete.

        Returns:
            True if deleted, False if not found.
        """
        entry = self._entries.pop(key, None)
        if entry:
            self._remove_from_indexes(key, entry)
            return True
        return False

    def list_categories(self) -> dict[str, int]:
        """List all categories with entry counts.

        Returns:
            Dict mapping category to entry count.
        """
        return {
            cat: len(keys)
            for cat, keys in self._category_index.items()
        }

    def list_tags(self) -> dict[str, int]:
        """List all tags with entry counts.

        Returns:
            Dict mapping tag to entry count.
        """
        return {
            tag: len(keys)
            for tag, keys in self._tag_index.items()
        }

    def get_all_entries(self) -> list[KnowledgeEntry]:
        """Get all entries (for serialization).

        Returns:
            List of all KnowledgeEntry instances.
        """
        return list(self._entries.values())

    def load_entries(self, entries: list[KnowledgeEntry]) -> int:
        """Load entries from a list (for deserialization).

        Args:
            entries: List of KnowledgeEntry instances to load.

        Returns:
            Number of entries loaded.
        """
        count = 0
        for entry in entries:
            self._entries[entry.key] = entry
            self._add_to_indexes(entry.key, entry)
            count += 1
            self._entry_counter = max(
                self._entry_counter,
                int(entry.key.split("_")[-1]) + 1
                if entry.key.startswith("knowledge_") else 0,
            )
        return count

    def clear(self) -> None:
        """Clear all entries and indexes."""
        self._entries.clear()
        self._tag_index.clear()
        self._category_index.clear()
        self._entry_counter = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_to_indexes(self, key: str, entry: KnowledgeEntry) -> None:
        """Add an entry to tag and category indexes."""
        for tag in entry.tags:
            tag_lower = tag.lower()
            if tag_lower not in self._tag_index:
                self._tag_index[tag_lower] = set()
            self._tag_index[tag_lower].add(key)

        cat_lower = entry.category.lower()
        if cat_lower not in self._category_index:
            self._category_index[cat_lower] = set()
        self._category_index[cat_lower].add(key)

    def _remove_from_indexes(self, key: str, entry: KnowledgeEntry) -> None:
        """Remove an entry from tag and category indexes."""
        for tag in entry.tags:
            tag_lower = tag.lower()
            if tag_lower in self._tag_index:
                self._tag_index[tag_lower].discard(key)
                if not self._tag_index[tag_lower]:
                    del self._tag_index[tag_lower]

        cat_lower = entry.category.lower()
        if cat_lower in self._category_index:
            self._category_index[cat_lower].discard(key)
            if not self._category_index[cat_lower]:
                del self._category_index[cat_lower]

    def _evict_least_important(self) -> None:
        """Evict the least important, least accessed entries."""
        if not self._entries:
            return

        sorted_entries = sorted(
            self._entries.values(),
            key=lambda e: (e.importance, e.access_count),
        )
        to_remove = max(1, len(self._entries) - self._max_entries + 100)
        for entry in sorted_entries[:to_remove]:
            self.delete(entry.key)
            logger.debug("Evicted knowledge entry: %s", entry.key)
