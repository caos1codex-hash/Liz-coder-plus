"""Memory & Context Engine for Liz Coder Plus (Sprint 2.6).

Provides a decoupled, backend-agnostic memory system with pluggable
storage, context construction, search indexing, policies, and events.
"""

from .models import (
    ContextSnapshot,
    ConversationMemory,
    KnowledgeMemory,
    MemoryRecord,
    MemoryType,
    ProjectMemory,
    WorkflowMemory,
)
from .storage import FileStorage, InMemoryStorage, MemoryStorage
from .engine import MemoryEngine
from .context import ContextEngine, ContextRequest, ContextResult
from .search import MemorySearchIndex, SearchResult
from .policies import MemoryPolicy, MemoryPolicyManager
from .events import MemoryEvent, MemoryEventBus, MemoryEventType
from .metrics import MemoryMetrics

__all__ = [
    # Models
    "MemoryRecord",
    "MemoryType",
    "ConversationMemory",
    "ProjectMemory",
    "WorkflowMemory",
    "KnowledgeMemory",
    "ContextSnapshot",
    # Storage
    "MemoryStorage",
    "InMemoryStorage",
    "FileStorage",
    # Engine
    "MemoryEngine",
    # Context
    "ContextEngine",
    "ContextRequest",
    "ContextResult",
    # Search
    "MemorySearchIndex",
    "SearchResult",
    # Policies
    "MemoryPolicy",
    "MemoryPolicyManager",
    # Events
    "MemoryEvent",
    "MemoryEventBus",
    "MemoryEventType",
    # Metrics
    "MemoryMetrics",
]

__version__ = "0.1.0"