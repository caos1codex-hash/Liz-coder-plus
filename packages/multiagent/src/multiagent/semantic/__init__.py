"""Semantic Memory & Retrieval Engine for Liz Coder Plus (Sprint 2.7).

Provides a fully decoupled semantic memory layer that enables retrieval
by meaning rather than just text matching. The architecture allows
swapping embedding providers and vector stores without modifying the
rest of the system.

Components:
- EmbeddingProvider: Abstract interface for text embedding generation.
- VectorStore: Abstract interface for vector storage and similarity search.
- SemanticIndex: Bridges embeddings with the memory system.
- SemanticRetrievalEngine: Unified search (semantic, text, hybrid).
- RankingEngine: Configurable multi-signal ranking.
- EmbeddingCache: Caches computed embeddings to avoid recomputation.
- SemanticMetrics: Observability for the semantic layer.
- SemanticEvents: Event types for the semantic memory system.
"""

from .embedding import (
    DummyEmbeddingProvider,
    EmbeddingProvider,
    EmbeddingResult,
)
from .vector_store import (
    InMemoryVectorStore,
    VectorSearchResult,
    VectorStore,
)
from .semantic_index import SemanticIndex
from .retrieval import (
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    SemanticRetrievalEngine,
)
from .ranking import (
    RankingConfig,
    RankingEngine,
    RankingSignal,
)
from .cache import EmbeddingCache
from .events import (
    SemanticEventType,
    extend_memory_events,
)
from .metrics import SemanticMetrics
from .security import SemanticValidator

__all__ = [
    # Embedding
    "EmbeddingProvider",
    "EmbeddingResult",
    "DummyEmbeddingProvider",
    # Vector Store
    "VectorStore",
    "InMemoryVectorStore",
    "VectorSearchResult",
    # Semantic Index
    "SemanticIndex",
    # Retrieval
    "RetrievalMode",
    "RetrievalRequest",
    "RetrievalResult",
    "SemanticRetrievalEngine",
    # Ranking
    "RankingConfig",
    "RankingEngine",
    "RankingSignal",
    # Cache
    "EmbeddingCache",
    # Events
    "SemanticEventType",
    "extend_memory_events",
    # Metrics
    "SemanticMetrics",
    # Security
    "SemanticValidator",
]

__version__ = "0.1.0"