"""Capability Index for the Tool Intelligence package.

Sprint 4.1 — Tool Intelligence Foundation.

The ``CapabilityIndex`` is the searchable catalog of all known tools.
It indexes by:

  - id            (exact match)
  - name          (exact + alias)
  - category      (grouping)
  - capabilities  (hierarchical wildcard match)
  - tags          (set membership)
  - keywords      (free-form text match)
  - description   (full-text substring)

The index is deliberately kept separate from the ``ToolRegistry`` in
``packages/tools``: the registry manages live tool instances and
execution, while the index only stores static ``ToolMetadata`` for
fast selection and ranking.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Iterable

from .exceptions import ToolIntelligenceError
from .models import RiskLevel, ToolCategory, ToolMetadata

logger = logging.getLogger(__name__)


# Words ignored when extracting keywords from a description.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "else", "for", "to", "in",
    "of", "on", "at", "by", "with", "from", "is", "are", "be", "been",
    "this", "that", "these", "those", "it", "as", "can", "will", "shall",
    "must", "may", "might", "do", "does", "did", "have", "has", "had",
    "tool", "execute", "execution", "run", "running", "use", "using",
})

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_\-]{1,40}")


def extract_keywords(text: str, *, extra_stopwords: Iterable[str] = ()) -> list[str]:
    """Tokenize ``text`` into lowercase keywords, removing stopwords.

    Keywords are kept in insertion order and de-duplicated. Tokens must
    start with a letter and be 2-41 chars long; tokens shorter than 2
    chars are dropped to avoid noise.
    """
    if not text:
        return []
    lower = text.lower()
    stop = _STOPWORDS | {w.lower() for w in extra_stopwords}
    seen: dict[str, None] = {}
    for match in _TOKEN_RE.finditer(lower):
        token = match.group(0)
        # _TOKEN_RE already enforces len(token) >= 2 (one [a-z] plus
        # one or more [a-z0-9_-]), so no extra length check is needed.
        if token in stop:
            continue
        seen.setdefault(token, None)
    return list(seen.keys())


class CapabilityIndex:
    """In-memory searchable index of tool metadata.

    The index maintains several inverted lists so that lookup by any
    supported key is O(1) plus the size of the result set. Adding,
    updating, and removing tools keeps all internal structures in sync.

    The index is intentionally synchronous and CPU-bound: it operates
    on static metadata only, with no I/O. Persisting or loading the
    index is the responsibility of the ``RegistryCache``.
    """

    def __init__(self) -> None:
        # Primary storage keyed by id.
        self._by_id: dict[str, ToolMetadata] = {}
        # Secondary indexes (rebuilt on every mutation for simplicity;
        # for thousands of tools this is fast enough and keeps the
        # implementation obviously correct).
        self._by_name: dict[str, str] = {}              # name -> id
        self._by_alias: dict[str, set[str]] = defaultdict(set)  # alias -> ids
        self._by_category: dict[ToolCategory, set[str]] = defaultdict(set)
        self._by_capability: dict[str, set[str]] = defaultdict(set)
        self._by_tag: dict[str, set[str]] = defaultdict(set)
        self._by_keyword: dict[str, set[str]] = defaultdict(set)
        # Counters for metrics.
        self._add_count: int = 0
        self._update_count: int = 0
        self._remove_count: int = 0
        self._lookup_count: int = 0
        self._hit_count: int = 0

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add(self, metadata: ToolMetadata) -> None:
        """Add a new tool to the index.

        Raises:
            ToolIntelligenceError: If a tool with the same id or name is
            already registered. Use ``update`` to replace.
        """
        if not isinstance(metadata, ToolMetadata):
            raise TypeError(
                f"Expected ToolMetadata, got {type(metadata).__name__}"
            )
        if metadata.id in self._by_id:
            raise ToolIntelligenceError(
                f"Tool with id '{metadata.id}' already exists; use update()",
                details={"id": metadata.id, "name": metadata.name},
            )
        if metadata.name in self._by_name:
            raise ToolIntelligenceError(
                f"Tool with name '{metadata.name}' already exists",
                details={"name": metadata.name},
            )
        # Auto-extract keywords if none were provided.
        if not metadata.keywords:
            metadata.keywords = extract_keywords(
                f"{metadata.name} {metadata.description}"
            )
        self._by_id[metadata.id] = metadata
        self._index(metadata)
        self._add_count += 1
        logger.debug("Indexed tool '%s' (id=%s)", metadata.name, metadata.id)

    def update(self, metadata: ToolMetadata) -> None:
        """Replace an existing tool in the index.

        If the tool does not exist, it is added. The id is used as the
        identity key; the name may change.
        """
        if metadata.id not in self._by_id:
            self.add(metadata)
            return
        # Remove the old version then re-add.
        self._remove_from_secondary(self._by_id[metadata.id])
        if not metadata.keywords:
            metadata.keywords = extract_keywords(
                f"{metadata.name} {metadata.description}"
            )
        # Handle name change: clear the old name mapping.
        old_name = self._by_id[metadata.id].name
        if old_name != metadata.name:
            self._by_name.pop(old_name, None)
            if metadata.name in self._by_name and self._by_name[metadata.name] != metadata.id:
                raise ToolIntelligenceError(
                    f"Tool with name '{metadata.name}' already exists",
                    details={"name": metadata.name},
                )
        self._by_id[metadata.id] = metadata
        self._index(metadata)
        self._update_count += 1
        logger.debug("Updated tool '%s' (id=%s)", metadata.name, metadata.id)

    def remove(self, tool_id: str) -> bool:
        """Remove a tool by id. Returns True if removed, False if not found."""
        meta = self._by_id.pop(tool_id, None)
        if meta is None:
            return False
        self._remove_from_secondary(meta)
        self._remove_count += 1
        logger.debug("Removed tool '%s' (id=%s)", meta.name, meta.id)
        return True

    def remove_by_name(self, name: str) -> bool:
        """Remove a tool by name. Returns True if removed, False if not found."""
        tool_id = self._by_name.get(name)
        if tool_id is None:
            return False
        return self.remove(tool_id)

    def clear(self) -> None:
        """Drop every tool from the index."""
        self._by_id.clear()
        self._by_name.clear()
        self._by_alias.clear()
        self._by_category.clear()
        self._by_capability.clear()
        self._by_tag.clear()
        self._by_keyword.clear()

    # ------------------------------------------------------------------
    # Internal indexing helpers
    # ------------------------------------------------------------------

    def _index(self, meta: ToolMetadata) -> None:
        """Populate secondary indexes for ``meta``."""
        self._by_name[meta.name] = meta.id
        for alias in meta.aliases:
            self._by_alias[alias.lower()].add(meta.id)
        self._by_category[meta.category].add(meta.id)
        for cap in meta.capabilities:
            # Index the full capability and all of its ancestors so
            # that "filesystem" matches "filesystem.read".
            parts = cap.split(".")
            for i in range(1, len(parts) + 1):
                self._by_capability[".".join(parts[:i])].add(meta.id)
        for tag in meta.tags:
            self._by_tag[tag.lower()].add(meta.id)
        for kw in meta.keywords:
            self._by_keyword[kw.lower()].add(meta.id)

    def _remove_from_secondary(self, meta: ToolMetadata) -> None:
        """Remove ``meta`` from all secondary indexes."""
        self._by_name.pop(meta.name, None)
        for alias in meta.aliases:
            ids = self._by_alias.get(alias.lower())
            if ids is not None:
                ids.discard(meta.id)
                if not ids:
                    self._by_alias.pop(alias.lower(), None)
        cat_set = self._by_category.get(meta.category)
        if cat_set is not None:
            cat_set.discard(meta.id)
            if not cat_set:
                self._by_category.pop(meta.category, None)
        for cap in meta.capabilities:
            parts = cap.split(".")
            for i in range(1, len(parts) + 1):
                key = ".".join(parts[:i])
                ids = self._by_capability.get(key)
                if ids is not None:
                    ids.discard(meta.id)
                    if not ids:
                        self._by_capability.pop(key, None)
        for tag in meta.tags:
            ids = self._by_tag.get(tag.lower())
            if ids is not None:
                ids.discard(meta.id)
                if not ids:
                    self._by_tag.pop(tag.lower(), None)
        for kw in meta.keywords:
            ids = self._by_keyword.get(kw.lower())
            if ids is not None:
                ids.discard(meta.id)
                if not ids:
                    self._by_keyword.pop(kw.lower(), None)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, tool_id: str) -> ToolMetadata | None:
        """Exact lookup by id."""
        self._lookup_count += 1
        meta = self._by_id.get(tool_id)
        if meta is not None:
            self._hit_count += 1
        return meta

    def get_by_name(self, name: str) -> ToolMetadata | None:
        """Lookup by exact name or alias."""
        self._lookup_count += 1
        tool_id = self._by_name.get(name)
        if tool_id is None:
            # Try aliases (case-insensitive).
            ids = self._by_alias.get(name.lower())
            if ids:
                tool_id = next(iter(ids))
        if tool_id is not None:
            self._hit_count += 1
            return self._by_id.get(tool_id)
        return None

    def by_category(self, category: ToolCategory) -> list[ToolMetadata]:
        """Return all tools in a category."""
        ids = self._by_category.get(category, set())
        return [self._by_id[i] for i in ids if i in self._by_id]

    def by_capability(self, capability: str) -> list[ToolMetadata]:
        """Return all tools providing the given capability.

        Hierarchical: ``"filesystem"`` matches ``"filesystem.read"``.
        Wildcard: ``"filesystem.*"`` matches every sub-capability.
        """
        if capability.endswith(".*"):
            prefix = capability[:-2]
            return [
                self._by_id[i]
                for i, meta in self._by_id.items()
                if any(
                    cap == prefix or cap.startswith(prefix + ".")
                    for cap in meta.capabilities
                )
            ]
        ids = self._by_capability.get(capability, set())
        return [self._by_id[i] for i in ids if i in self._by_id]

    def by_tag(self, tag: str) -> list[ToolMetadata]:
        """Return all tools with the given tag (case-insensitive)."""
        ids = self._by_tag.get(tag.lower(), set())
        return [self._by_id[i] for i in ids if i in self._by_id]

    def by_keyword(self, keyword: str) -> list[ToolMetadata]:
        """Return all tools whose keywords contain ``keyword`` (case-insensitive)."""
        ids = self._by_keyword.get(keyword.lower(), set())
        return [self._by_id[i] for i in ids if i in self._by_id]

    def search(
        self,
        query: str,
        *,
        limit: int = 50,
    ) -> list[ToolMetadata]:
        """Free-text search across name, aliases, tags, keywords and description.

        The query is tokenized with :func:`extract_keywords`. Each token
        contributes one point per tool. Results are sorted by score
        descending, then by name.
        """
        tokens = extract_keywords(query)
        if not tokens:
            return []
        scores: dict[str, int] = defaultdict(int)
        for token in tokens:
            for meta in self.by_keyword(token):
                scores[meta.id] += 1
            # Also include name matches (token substring of name).
            for meta in self._by_id.values():
                if token in meta.name.lower():
                    scores[meta.id] += 1
                elif token in meta.description.lower():
                    scores[meta.id] += 1
        ranked = sorted(
            scores.items(),
            key=lambda kv: (-kv[1], self._by_id[kv[0]].name),
        )
        return [self._by_id[i] for i, _ in ranked[:limit]]

    def find(
        self,
        *,
        category: ToolCategory | None = None,
        capabilities: list[str] | None = None,
        tags: list[str] | None = None,
        keywords: list[str] | None = None,
        max_risk_level: RiskLevel | None = None,
    ) -> list[ToolMetadata]:
        """Filter tools by multiple criteria (AND semantics).

        Each criterion that is provided acts as a filter. ``capabilities``
        uses hierarchical matching (a tool matches if it provides any of
        the requested capabilities or a sub-capability thereof).
        """
        # Start with the full set if no criterion narrows it down.
        candidate_ids: set[str] | None = None
        if category is not None:
            candidate_ids = set(self._by_category.get(category, set()))
        if capabilities:
            cap_ids: set[str] = set()
            for cap in capabilities:
                cap_ids.update(m.id for m in self.by_capability(cap))
            candidate_ids = cap_ids if candidate_ids is None else candidate_ids & cap_ids
        if tags:
            tag_ids: set[str] = set()
            for t in tags:
                tag_ids.update(m.id for m in self.by_tag(t))
            candidate_ids = tag_ids if candidate_ids is None else candidate_ids & tag_ids
        if keywords:
            kw_ids: set[str] = set()
            for k in keywords:
                kw_ids.update(m.id for m in self.by_keyword(k))
            candidate_ids = kw_ids if candidate_ids is None else candidate_ids & kw_ids
        if candidate_ids is None:
            candidate_ids = set(self._by_id.keys())
        results = [self._by_id[i] for i in candidate_ids if i in self._by_id]
        if max_risk_level is not None:
            results = [
                m for m in results if m.risk_level.numeric() <= max_risk_level.numeric()
            ]
        return sorted(results, key=lambda m: m.name)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def all(self) -> list[ToolMetadata]:
        """Return every tool in the index (sorted by name)."""
        return sorted(self._by_id.values(), key=lambda m: m.name)

    def names(self) -> list[str]:
        """Return all tool names (sorted)."""
        return sorted(self._by_name.keys())

    def categories(self) -> list[ToolCategory]:
        """Return all categories that have at least one tool."""
        return sorted(self._by_category.keys(), key=lambda c: c.value)

    def capabilities(self) -> list[str]:
        """Return all distinct capabilities indexed."""
        return sorted(self._by_capability.keys())

    def tags(self) -> list[str]:
        """Return all distinct tags (sorted)."""
        return sorted(self._by_tag.keys())

    def keywords(self) -> list[str]:
        """Return all distinct keywords (sorted)."""
        return sorted(self._by_keyword.keys())

    def summary(self) -> dict[str, Any]:
        """Return a summary of the index state."""
        by_cat: dict[str, int] = {}
        for cat, ids in self._by_category.items():
            by_cat[cat.value] = len(ids)
        by_risk: dict[str, int] = defaultdict(int)
        for meta in self._by_id.values():
            by_risk[meta.risk_level.value] += 1
        return {
            "total": len(self._by_id),
            "categories": len(self._by_category),
            "capabilities": len(self._by_capability),
            "tags": len(self._by_tag),
            "keywords": len(self._by_keyword),
            "by_category": by_cat,
            "by_risk_level": dict(by_risk),
            "mutations": {
                "adds": self._add_count,
                "updates": self._update_count,
                "removes": self._remove_count,
            },
            "lookups": {
                "total": self._lookup_count,
                "hits": self._hit_count,
                "hit_rate": (
                    round(self._hit_count / self._lookup_count, 4)
                    if self._lookup_count > 0
                    else 0.0
                ),
            },
        }

    # ------------------------------------------------------------------
    # Dunder methods
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, tool_id: str) -> bool:
        return tool_id in self._by_id

    def __iter__(self) -> Iterable[ToolMetadata]:
        return iter(self._by_id.values())


__all__ = ["CapabilityIndex", "extract_keywords"]
