"""Memory Manager service.

High-level public API for the persistent memory system. This is the
single entry point that the rest of Liz Coder Plus should use to
interact with conversation memory.

Responsibilities:
- Initialize and hold references to DatabaseManager and ConversationRepository.
- Provide a clean async API: add_message, get_context, clear_session.
- Enforce max_history limits.
- Maintain a RAM cache for the most recent messages per session to
  avoid hitting SQLite on every context lookup.
- Sprint 6: Semantic search via embeddings and cosine similarity.

Sprint 1 - Prompt 3.
Sprint 6 - Phase 3: Semantic memory retrieval.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

from src.memory.database import DatabaseManager
from src.memory.repositories.conversation_repository import ConversationRepository
from src.memory.knowledge import KnowledgeMemory

if TYPE_CHECKING:
    from src.memory.repositories.execution_repository import ExecutionRepository
    from src.memory.repositories.task_repository import TaskRepository
    from src.memory.repositories.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)


class MemoryManager:
    """Public API for the memory system.

    Usage::

        mem = MemoryManager(max_history=200)
        await mem.initialize("data/liz_memory.db")

        await mem.add_message("session-1", "user", "Hola Liz")
        context = await mem.get_context("session-1")

        await mem.close()

    The manager maintains an in-memory cache of recent messages per
    session for fast reads. The cache is populated lazily on the first
    ``get_context`` call and updated on every ``add_message``.
    """

    def __init__(self, *, max_history: int = 200, embedding_fn: Any | None = None) -> None:
        self._max_history = max_history
        self._db: DatabaseManager | None = None
        self._repo: ConversationRepository | None = None
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._initialized = False
        self._task_repo: Any | None = None
        self._workflow_repo: Any | None = None
        self._execution_repo: Any | None = None
        self._embedding_fn = embedding_fn  # Sprint 6: callable(text) -> list[float]
        self._knowledge = KnowledgeMemory()  # Sprint 8: long-term knowledge

        logger.info("MemoryManager created (max_history=%d)", max_history)

    @property
    def is_initialized(self) -> bool:
        """Whether the memory system has been initialized."""
        return self._initialized

    @property
    def max_history(self) -> int:
        """Maximum messages kept per session."""
        return self._max_history

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, database_path: str) -> None:
        """Initialize the memory system with the given database path.

        Creates the DatabaseManager, runs migrations, and instantiates
        the ConversationRepository.

        Safe to call multiple times; subsequent calls are no-ops.

        Args:
            database_path: Path to the SQLite database file.
        """
        if self._initialized:
            logger.debug("MemoryManager already initialized")
            return

        self._db = DatabaseManager(database_path)
        await self._db.initialize()

        self._repo = ConversationRepository(self._db)
        self._initialized = True

        logger.info(
            "MemoryManager initialized (db=%s, max_history=%d)",
            database_path,
            self._max_history,
        )

    @property
    def db(self) -> DatabaseManager | None:
        """The underlying DatabaseManager (or None)."""
        return self._db

    @property
    def task_repository(self) -> Any | None:
        """TaskRepository, available after ``initialize_task_persistence()``."""
        return self._task_repo

    @property
    def workflow_repository(self) -> Any | None:
        """WorkflowRepository, available after ``initialize_task_persistence()``."""
        return self._workflow_repo

    @property
    def execution_repository(self) -> Any | None:
        """ExecutionRepository, available after ``initialize_task_persistence()``."""
        return self._execution_repo

    @property
    def knowledge(self) -> KnowledgeMemory:
        """Long-term knowledge memory store (Sprint 8)."""
        return self._knowledge

    async def initialize_task_persistence(self) -> None:
        """Run the sprint 1.8 tasks/workflows migration and create repos.

        Must be called after ``initialize()``.  Instantiates the
        TaskRepository, WorkflowRepository and ExecutionRepository and
        stores them as instance attributes (accessible via the
        ``task_repository``, ``workflow_repository`` and
        ``execution_repository`` properties).

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if self._db is None:
            raise RuntimeError(
                "MemoryManager not initialized — call initialize() first"
            )
        if self._task_repo is not None:
            logger.debug("Task persistence already initialized")
            return

        await self._db.run_migration("sprint_1_8_tasks_workflows.sql")

        from src.memory.repositories.task_repository import TaskRepository
        from src.memory.repositories.workflow_repository import WorkflowRepository
        from src.memory.repositories.execution_repository import ExecutionRepository

        self._task_repo = TaskRepository(self._db)
        self._workflow_repo = WorkflowRepository(self._db)
        self._execution_repo = ExecutionRepository(self._db)

        logger.info("Task/workflow persistence initialized")

    async def close(self) -> None:
        """Release all resources.

        Clears the in-memory cache and closes the database connection.
        Safe to call multiple times.
        """
        if not self._initialized:
            return

        self._cache.clear()

        self._task_repo = None
        self._workflow_repo = None
        self._execution_repo = None

        if self._db is not None:
            await self._db.close()
            self._db = None
            self._repo = None

        self._initialized = False
        logger.info("MemoryManager closed")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a message to a session's conversation history.

        The message is persisted to SQLite and appended to the in-memory
        cache. If the cache exceeds ``max_history``, the oldest entries
        are trimmed.

        Args:
            session_id: The conversation session identifier.
            role:       Message role (user, assistant, system).
            content:    The message text.
            metadata:   Optional dict of extra data.
        """
        if not self._initialized:
            raise RuntimeError("MemoryManager not initialized")

        if self._repo is None:
            raise RuntimeError("MemoryManager repository not available")

        # Validate role.
        valid_roles = ("user", "assistant", "system")
        if role not in valid_roles:
            raise ValueError(
                f"Invalid role '{role}'. Must be one of: {valid_roles}"
            )

        # Validate content is not empty.
        if not content.strip():
            raise ValueError("Message content must not be empty")

        # Persist to SQLite.
        row_id = await self._repo.save_message(
            session_id, role, content, metadata
        )

        # Update RAM cache.
        msg = {
            "id": row_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }

        if session_id not in self._cache:
            self._cache[session_id] = []

        self._cache[session_id].append(msg)

        # Trim cache to max_history.
        if len(self._cache[session_id]) > self._max_history:
            overflow = len(self._cache[session_id]) - self._max_history
            self._cache[session_id] = self._cache[session_id][overflow:]

        logger.debug(
            "add_message: session=%s role=%s id=%d (cache=%d)",
            session_id,
            role,
            row_id,
            len(self._cache[session_id]),
        )

    async def get_context(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve conversation context for a session.

        If the session has a warm cache, the cache is returned (trimmed
        to ``limit`` if provided). Otherwise, the full history is loaded
        from SQLite and cached.

        Args:
            session_id: The session to query.
            limit:      Maximum messages to return. Defaults to
                        ``max_history``.

        Returns:
            List of message dicts ordered chronologically (oldest first).
        """
        if not self._initialized:
            raise RuntimeError("MemoryManager not initialized")

        effective_limit = limit if limit is not None else self._max_history

        # Return from cache if available and sufficient.
        cached = self._cache.get(session_id, [])
        if cached:
            return cached[-effective_limit:] if effective_limit < len(cached) else cached

        # Cold cache: load from SQLite.
        if self._repo is None:
            raise RuntimeError("MemoryManager repository not available")

        history = await self._repo.get_session_history(session_id, limit=effective_limit)

        # Warm the cache.
        self._cache[session_id] = history

        return history

    async def clear_session(self, session_id: str) -> bool:
        """Clear all messages for a session.

        Removes messages from both SQLite and the in-memory cache.

        Args:
            session_id: The session to clear.

        Returns:
            True if messages were deleted, False otherwise.
        """
        if not self._initialized:
            raise RuntimeError("MemoryManager not initialized")

        if self._repo is None:
            raise RuntimeError("MemoryManager repository not available")

        # Remove from SQLite.
        deleted = await self._repo.delete_session(session_id)

        # Remove from cache.
        self._cache.pop(session_id, None)

        if deleted:
            logger.info("Session cleared: %s", session_id)

        return deleted

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _invalidate_cache(self, session_id: str) -> None:
        """Remove the cache entry for a session, forcing a DB load next time."""
        self._cache.pop(session_id, None)

    # ------------------------------------------------------------------
    # Semantic Search (Sprint 6)
    # ------------------------------------------------------------------

    def set_embedding_fn(self, fn: Any) -> None:
        """Set the embedding function for semantic search.

        Args:
            fn: An async callable that takes a string and returns
                a list[float] (embedding vector).
        """
        self._embedding_fn = fn
        logger.info("Embedding function set for semantic memory")

    async def search_semantic(
        self,
        query: str,
        *,
        limit: int = 5,
        threshold: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Search conversation history using semantic similarity.

        Sprint 6: When an embedding function is available, converts
        the query and cached messages to embeddings and returns the
        most semantically similar messages.

        Falls back to simple keyword matching if no embedding function
        is configured.

        Args:
            query: The search query.
            limit: Maximum results to return.
            threshold: Minimum cosine similarity score (0-1).

        Returns:
            List of message dicts with an added 'score' key, sorted
            by descending similarity.
        """
        if not self._embedding_fn:
            return self._search_keyword(query, limit=limit)

        # Collect all cached messages.
        all_messages: list[dict[str, Any]] = []
        for session_id, msgs in self._cache.items():
            all_messages.extend(msgs)

        if not all_messages:
            return []

        try:
            # Compute query embedding.
            query_embedding = await self._embedding_fn(query)

            # Compute embeddings for all messages (batch-like).
            results: list[tuple[float, dict[str, Any]]] = []
            for msg in all_messages:
                content = msg.get("content", "")
                if not content.strip():
                    continue

                try:
                    msg_embedding = await self._embedding_fn(content)
                    score = self._cosine_similarity(query_embedding, msg_embedding)
                    if score >= threshold:
                        results.append((score, {**msg, "score": score}))
                except Exception:
                    logger.debug("Failed to embed message %s", msg.get("id", "?"))

            # Sort by descending similarity.
            results.sort(key=lambda x: x[0], reverse=True)
            return [r[1] for r in results[:limit]]

        except Exception:
            logger.exception("Semantic search failed; falling back to keyword")
            return self._search_keyword(query, limit=limit)

    def _search_keyword(
        self, query: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Simple keyword-based fallback search."""
        query_lower = query.lower()
        results: list[dict[str, Any]] = []

        for session_id, msgs in self._cache.items():
            for msg in msgs:
                content = msg.get("content", "").lower()
                if query_lower in content:
                    results.append(msg)
                    if len(results) >= limit:
                        return results

        return results

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)