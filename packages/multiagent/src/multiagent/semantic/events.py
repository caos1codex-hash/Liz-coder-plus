"""Semantic memory events.

Extends the MemoryEventBus with 8 new event types specific to the
semantic memory layer. These events integrate seamlessly with the
existing event system.
"""

from __future__ import annotations

from enum import Enum


class SemanticEventType(str, Enum):
    """Event types for the semantic memory system."""

    EMBEDDING_CREATED = "semantic.embedding_created"
    EMBEDDING_UPDATED = "semantic.embedding_updated"
    SEMANTIC_SEARCH = "semantic.semantic_search"
    HYBRID_SEARCH = "semantic.hybrid_search"
    VECTOR_INDEX_UPDATED = "semantic.vector_index_updated"
    RANKING_COMPLETED = "semantic.ranking_completed"
    CACHE_HIT = "semantic.cache_hit"
    CACHE_MISS = "semantic.cache_miss"


def extend_memory_events() -> list[str]:
    """Return a list of all semantic event type values.

    Useful for registering semantic event types with event buses
    that require explicit registration.
    """
    return [et.value for et in SemanticEventType]