"""Search index for memory records.

Provides fast in-memory indexing by id, tags, owner, type, and text.
Designed to be extensible — future versions can add semantic/vector search.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .models import MemoryRecord, MemoryType

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result with relevance metadata."""

    record: MemoryRecord
    score: float = 1.0
    matched_by: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "score": self.score,
            "matched_by": self.matched_by,
        }


class MemorySearchIndex:
    """In-memory search index for memory records.

    Maintains indices by:
    - id (primary)
    - tags (inverted index)
    - owner (inverted index)
    - memory_type (inverted index)
    - text content (simple word-level inverted index)
    - metadata keys/values (inverted index)

    All indices are rebuilt on reindex() and cleared on clear().
    """

    def __init__(self) -> None:
        # Primary index
        self._records: Dict[str, MemoryRecord] = {}

        # Inverted indices
        self._by_tag: Dict[str, Set[str]] = defaultdict(set)
        self._by_owner: Dict[str, Set[str]] = defaultdict(set)
        self._by_type: Dict[MemoryType, Set[str]] = defaultdict(set)
        self._by_word: Dict[str, Set[str]] = defaultdict(set)
        self._by_meta_key: Dict[str, Set[str]] = defaultdict(set)
        self._by_meta_kv: Dict[str, Set[str]] = defaultdict(set)

    @property
    def size(self) -> int:
        return len(self._records)

    # -- index management --------------------------------------------------

    def index(self, record: MemoryRecord) -> None:
        """Add or update a record in all indices."""
        rid = record.id
        self._records[rid] = record

        # Tags
        for tag in record.tags:
            self._by_tag[tag].add(rid)

        # Owner
        self._by_owner[record.owner].add(rid)

        # Type
        self._by_type[record.memory_type].add(rid)

        # Text content — tokenize into lowercase words
        words = self._tokenize(record)
        for word in words:
            self._by_word[word].add(rid)

        # Metadata
        for k, v in record.metadata.items():
            self._by_meta_key[k].add(rid)
            kv_key = f"{k}:{_meta_val_str(v)}"
            self._by_meta_kv[kv_key].add(rid)

    def reindex(self, record: MemoryRecord) -> None:
        """Remove and re-add a record (for updates)."""
        # Get the OLD snapshot (the stored record may have been mutated
        # in-place by the caller, so we need the stored copy).
        old = self._records.get(record.id)
        if old is not None:
            # De-index using the stored record's current index data
            # We must de-index from what was previously indexed, not
            # the mutated version. Since index() stored the original,
            # and the caller may have mutated the record object in-place,
            # we need to fully de-index and re-index.
            # Remove all existing index entries for this id
            self._remove_all_indices(record.id)
        # Now index the new version
        self.index(record)

    def _remove_all_indices(self, rid: str) -> None:
        """Remove a record id from ALL index entries."""
        # Tags
        for tag_key in list(self._by_tag.keys()):
            self._by_tag[tag_key].discard(rid)
            if not self._by_tag[tag_key]:
                del self._by_tag[tag_key]
        # Owner
        for owner_key in list(self._by_owner.keys()):
            self._by_owner[owner_key].discard(rid)
            if not self._by_owner[owner_key]:
                del self._by_owner[owner_key]
        # Type
        for mt in list(self._by_type.keys()):
            self._by_type[mt].discard(rid)
            if not self._by_type[mt]:
                del self._by_type[mt]
        # Words
        for word in list(self._by_word.keys()):
            self._by_word[word].discard(rid)
            if not self._by_word[word]:
                del self._by_word[word]
        # Meta
        for k in list(self._by_meta_key.keys()):
            self._by_meta_key[k].discard(rid)
            if not self._by_meta_key[k]:
                del self._by_meta_key[k]
        for kv in list(self._by_meta_kv.keys()):
            self._by_meta_kv[kv].discard(rid)
            if not self._by_meta_kv[kv]:
                del self._by_meta_kv[kv]

    def remove(self, memory_id: str) -> bool:
        """Remove a record from all indices."""
        record = self._records.pop(memory_id, None)
        if record is None:
            return False

        for tag in record.tags:
            self._by_tag[tag].discard(memory_id)
        self._by_owner[record.owner].discard(memory_id)
        self._by_type[record.memory_type].discard(memory_id)

        words = self._tokenize(record)
        for word in words:
            self._by_word[word].discard(memory_id)

        for k, v in record.metadata.items():
            self._by_meta_key[k].discard(memory_id)
            kv_key = f"{k}:{_meta_val_str(v)}"
            self._by_meta_kv[kv_key].discard(memory_id)

        return True

    def clear(self) -> None:
        """Clear all indices."""
        self._records.clear()
        self._by_tag.clear()
        self._by_owner.clear()
        self._by_type.clear()
        self._by_word.clear()
        self._by_meta_key.clear()
        self._by_meta_kv.clear()

    # -- search methods ----------------------------------------------------

    def search_by_id(self, memory_id: str) -> Optional[MemoryRecord]:
        """Exact lookup by id."""
        return self._records.get(memory_id)

    def search_by_tags(
        self, tags: List[str], match_all: bool = True
    ) -> List[MemoryRecord]:
        """Search by tags. If match_all=True, all tags must be present."""
        if not tags:
            return list(self._records.values())

        if match_all:
            # Intersect all tag sets
            sets = [self._by_tag.get(t, set()) for t in tags]
            if not sets:
                return []
            result_ids = sets[0]
            for s in sets[1:]:
                result_ids = result_ids & s
        else:
            # Union all tag sets
            result_ids: Set[str] = set()
            for t in tags:
                result_ids |= self._by_tag.get(t, set())

        return [self._records[rid] for rid in result_ids if rid in self._records]

    def search_by_owner(self, owner: str) -> List[MemoryRecord]:
        """Search by owner."""
        ids = self._by_owner.get(owner, set())
        return [self._records[rid] for rid in ids if rid in self._records]

    def search_by_type(self, memory_type: MemoryType) -> List[MemoryRecord]:
        """Search by memory type."""
        ids = self._by_type.get(memory_type, set())
        return [self._records[rid] for rid in ids if rid in self._records]

    def search_by_text(self, query: str) -> List[SearchResult]:
        """Search by text content. Returns scored results."""
        query_words = set(self._tokenize_text(query))
        if not query_words:
            return []

        # Score each candidate
        scores: Dict[str, float] = defaultdict(float)
        matched_by_map: Dict[str, List[str]] = defaultdict(list)

        for word in query_words:
            rids = self._by_word.get(word, set())
            for rid in rids:
                scores[rid] += 1.0
                matched_by_map[rid].append(f"word:{word}")

        results = []
        for rid, score in scores.items():
            if rid in self._records:
                results.append(
                    SearchResult(
                        record=self._records[rid],
                        score=score,
                        matched_by=matched_by_map[rid],
                    )
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def search_by_metadata(
        self, metadata_filter: Dict[str, Any]
    ) -> List[MemoryRecord]:
        """Search by metadata key-value pairs."""
        if not metadata_filter:
            return list(self._records.values())

        # Intersect all kv sets
        sets = []
        for k, v in metadata_filter.items():
            kv_key = f"{k}:{_meta_val_str(v)}"
            ids = self._by_meta_kv.get(kv_key, set())
            if not ids:
                return []
            sets.append(ids)

        result_ids = sets[0]
        for s in sets[1:]:
            result_ids = result_ids & s

        return [self._records[rid] for rid in result_ids if rid in self._records]

    def combined_search(
        self,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        owner: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
        limit: int = 50,
    ) -> List[SearchResult]:
        """Combined search across multiple criteria. Returns scored results."""
        # Start with all records, then intersect
        candidate_ids: Optional[Set[str]] = None

        # Owner filter
        if owner is not None:
            ids = self._by_owner.get(owner, set())
            candidate_ids = ids if candidate_ids is None else candidate_ids & ids

        # Type filter
        if memory_type is not None:
            ids = self._by_type.get(memory_type, set())
            candidate_ids = ids if candidate_ids is None else candidate_ids & ids

        # Tag filter
        if tags:
            if candidate_ids is None:
                candidate_ids = set(self._records.keys())
            for tag in tags:
                ids = self._by_tag.get(tag, set())
                candidate_ids = candidate_ids & ids

        # Metadata filter
        if metadata_filter:
            for k, v in metadata_filter.items():
                kv_key = f"{k}:{_meta_val_str(v)}"
                ids = self._by_meta_kv.get(kv_key, set())
                candidate_ids = ids if candidate_ids is None else candidate_ids & ids

        # Default to all
        if candidate_ids is None:
            candidate_ids = set(self._records.keys())

        # Score results
        results: List[SearchResult] = []
        for rid in candidate_ids:
            rec = self._records.get(rid)
            if rec is None:
                continue

            score = 0.0
            matched: List[str] = []

            # Text scoring
            if query:
                query_words = set(self._tokenize_text(query))
                for word in query_words:
                    if rid in self._by_word.get(word, set()):
                        score += 1.0
                        matched.append(f"word:{word}")

            # Priority bonus
            score += rec.priority * 0.5
            if rec.priority > 0:
                matched.append(f"priority:{rec.priority}")

            results.append(
                SearchResult(record=rec, score=score, matched_by=matched)
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _tokenize(record: MemoryRecord) -> List[str]:
        """Extract searchable words from a record."""
        words: List[str] = []
        # From content
        content = record.content
        if isinstance(content, str):
            words.extend(_word_tokenize(content))
        elif isinstance(content, dict):
            for v in content.values():
                if isinstance(v, str):
                    words.extend(_word_tokenize(v))
        # From owner
        words.extend(_word_tokenize(record.owner))
        # From tags
        for tag in record.tags:
            words.extend(_word_tokenize(tag))
        return words

    @staticmethod
    def _tokenize_text(text: str) -> List[str]:
        return _word_tokenize(text)


_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")

def _word_tokenize(text: str) -> List[str]:
    """Split text into lowercase word tokens."""
    return [m.group().lower() for m in _WORD_RE.finditer(text)]


def _meta_val_str(value: Any) -> str:
    """Convert a metadata value to a string key component."""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return str(type(value).__name__)