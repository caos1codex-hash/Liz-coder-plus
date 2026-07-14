"""Memory module: persistent conversation memory for Liz Coder Plus.

This module provides the public API surface for the memory system:
- MemoryManager: high-level service for storing and retrieving messages.
- DatabaseManager: low-level SQLite connection management.
- ConversationRepository: data access layer for conversation messages.
"""

from src.memory.manager import MemoryManager

__all__ = ["MemoryManager"]