"""Memory module: persistent conversation memory for Liz Coder Plus.

This module provides the public API surface for the memory system:
- MemoryManager: high-level service for storing and retrieving messages.
- DatabaseManager: low-level SQLite connection management.
- ConversationRepository: data access layer for conversation messages.
- KnowledgeMemory: long-term cross-session knowledge store (Sprint 8).
"""

from src.memory.manager import MemoryManager
from src.memory.knowledge import KnowledgeMemory, KnowledgeEntry

__all__ = ["MemoryManager", "KnowledgeMemory", "KnowledgeEntry"]