"""Context Engine — top-level orchestrator for context engineering.

Sprint 3.9.

The ContextEngine is the single entry point for all context operations.
It coordinates:

    - Task decomposition (TaskDecomposer + SubTaskGenerator)
    - Context building (ContextBuilder)
    - Context caching (ContextCache)
    - Logging and metrics

Example::

    engine = ContextEngine()
    ctx = await engine.build_context(
        task="Fix login bug",
        agent_name="coder",
        session_id="s1",
        files=["src/auth/login.py", ...],
        memories=[...],
        history=[...],
    )

    # For task decomposition:
    breakdown = engine.decompose_task("Add login with Google OAuth")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from .context_builder import ContextBuilder, ContextBuilderConfig
from .context_budget import ContextBudgetConfig
from .context_cache import ContextCache
from .context_ranker import RankConfig
from .file_selector import FileSelector
from .history_selector import HistorySelector
from .memory_selector import MemorySelector
from .models import (
    AgentContext,
    TaskBreakdown,
)
from .subtask_generator import SubTaskGenerator, SubTaskMeta
from .task_decomposer import TaskDecomposer

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Context engine events (added to src/events.py in integration).
CONTEXT_BUILT = "context.engine.built"
CONTEXT_CACHE_HIT = "context.engine.cache_hit"
CONTEXT_CACHE_MISS = "context.engine.cache_miss"
CONTEXT_FILES_SELECTED = "context.engine.files_selected"
CONTEXT_MEMORIES_SELECTED = "context.engine.memories_selected"
CONTEXT_HISTORY_SELECTED = "context.engine.history_selected"
CONTEXT_COMPRESSED = "context.engine.compressed"
TASK_DECOMPOSED = "context.engine.task_decomposed"


@dataclass
class ContextEngineConfig:
    """Configuration for the ContextEngine.

    Attributes:
        max_context_tokens:     Total token budget for context.
        reserve_response:       Tokens reserved for model response.
        compression_enabled:    Whether to enable compression.
        ranking_enabled:        Whether to enable re-ranking.
        history_limit:          Max history entries to consider.
        memory_limit:           Max memories to consider.
        file_limit:             Max files to consider.
        context_cache_enabled:  Whether context caching is enabled.
        cache_ttl_seconds:      Cache TTL.
        file_ratio:             Token ratio for files.
        memory_ratio:           Token ratio for memories.
        history_ratio:          Token ratio for history.
    """

    max_context_tokens: int = 32000
    reserve_response: int = 8192
    compression_enabled: bool = True
    ranking_enabled: bool = True
    history_limit: int = 30
    memory_limit: int = 15
    file_limit: int = 20
    context_cache_enabled: bool = True
    cache_ttl_seconds: float = 300.0
    file_ratio: float = 0.40
    memory_ratio: float = 0.30
    history_ratio: float = 0.30


class ContextEngine:
    """Top-level context engineering orchestrator.

    Provides a unified API for:
    - Building agent contexts with intelligent selection and ranking.
    - Decomposing tasks into sub-tasks.
    - Caching assembled contexts.
    - Emitting events for observability.

    Args:
        config:    Engine configuration.
        event_bus: Optional event bus for lifecycle events.
    """

    def __init__(
        self,
        config: ContextEngineConfig | None = None,
        *,
        event_bus: Any | None = None,
    ) -> None:
        self._config = config or ContextEngineConfig()
        self._bus = event_bus

        # Budget config from engine config.
        budget_cfg = ContextBudgetConfig(
            max_tokens=self._config.max_context_tokens,
            reserve_response=self._config.reserve_response,
            file_ratio=self._config.file_ratio,
            memory_ratio=self._config.memory_ratio,
            history_ratio=self._config.history_ratio,
        )

        # Rank config.
        rank_cfg = RankConfig()

        # Builder config.
        builder_cfg = ContextBuilderConfig(
            budget_config=budget_cfg,
            rank_config=rank_cfg,
            compression_enabled=self._config.compression_enabled,
            ranking_enabled=self._config.ranking_enabled,
        )

        # Selectors.
        self._file_selector = FileSelector(max_files=self._config.file_limit)
        self._memory_selector = MemorySelector(max_memories=self._config.memory_limit)
        self._history_selector = HistorySelector(max_entries=self._config.history_limit)

        # Builder.
        self._builder = ContextBuilder(
            file_selector=self._file_selector,
            memory_selector=self._memory_selector,
            history_selector=self._history_selector,
            config=builder_cfg,
        )

        # Cache.
        self._cache = ContextCache(
            ttl_seconds=self._config.cache_ttl_seconds,
            enabled=self._config.context_cache_enabled,
        )

        # Task decomposition.
        self._decomposer = TaskDecomposer()
        self._subtask_gen = SubTaskGenerator(decomposer=self._decomposer)

        logger.info(
            "ContextEngine initialized (tokens=%d, cache=%s, compression=%s)",
            self._config.max_context_tokens,
            self._config.context_cache_enabled,
            self._config.compression_enabled,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build_context(
        self,
        task: str,
        agent_name: str = "",
        session_id: str = "",
        *,
        available_files: list[str] | None = None,
        file_contents: dict[str, str] | None = None,
        available_memories: list[dict[str, Any]] | None = None,
        history: list[dict[str, Any]] | None = None,
        system_prompt: str = "",
    ) -> AgentContext:
        """Build context for an agent, using cache when available.

        Args:
            task:                The task or user message.
            agent_name:          Target agent name.
            session_id:          Session identifier (used for cache key).
            available_files:     Candidate file paths.
            file_contents:       Mapping of path -> content.
            available_memories:  Candidate memory entries.
            history:             Conversation history.
            system_prompt:       System-level instructions.

        Returns:
            Fully assembled AgentContext.
        """
        # Check cache first.
        cache_key = self._cache.make_key(
            task=task,
            agent=agent_name,
            session=session_id,
        )

        cached = self._cache.get(cache_key)
        if cached is not None:
            await self._emit(
                CONTEXT_CACHE_HIT,
                {
                    "agent": agent_name,
                    "session_id": session_id,
                    "cache_key": cache_key[:12],
                },
            )
            logger.info(
                "Context cache hit for '%s' in session '%s'",
                agent_name,
                session_id,
            )
            return cached

        await self._emit(
            CONTEXT_CACHE_MISS,
            {
                "agent": agent_name,
                "session_id": session_id,
                "cache_key": cache_key[:12],
            },
        )

        # Build context.
        start = time.monotonic()
        ctx = await self._builder.build(
            task=task,
            agent_name=agent_name,
            available_files=available_files,
            file_contents=file_contents,
            available_memories=available_memories,
            history=history,
            system_prompt=system_prompt,
        )
        build_time_ms = (time.monotonic() - start) * 1000

        # Store in cache.
        self._cache.put(cache_key, ctx)

        # Emit events.
        await self._emit(
            CONTEXT_BUILT,
            {
                "agent": agent_name,
                "session_id": session_id,
                "total_tokens": ctx.total_tokens,
                "files_count": len(ctx.files),
                "memories_count": len(ctx.memories),
                "history_count": len(ctx.history),
                "build_time_ms": round(build_time_ms, 2),
                "compression_ratio": ctx.compression_ratio,
                "cache_key": cache_key[:12],
            },
        )

        if ctx.files:
            await self._emit(
                CONTEXT_FILES_SELECTED,
                {
                    "agent": agent_name,
                    "count": len(ctx.files),
                    "tokens": sum(f.tokens for f in ctx.files),
                    "top_file": ctx.files[0].path if ctx.files else "",
                },
            )
        if ctx.memories:
            await self._emit(
                CONTEXT_MEMORIES_SELECTED,
                {
                    "agent": agent_name,
                    "count": len(ctx.memories),
                    "tokens": sum(m.tokens for m in ctx.memories),
                },
            )
        if ctx.history:
            await self._emit(
                CONTEXT_HISTORY_SELECTED,
                {
                    "agent": agent_name,
                    "count": len(ctx.history),
                    "tokens": sum(h.tokens for h in ctx.history),
                },
            )

        if ctx.compression_ratio < 1.0:
            await self._emit(
                CONTEXT_COMPRESSED,
                {
                    "agent": agent_name,
                    "original_ratio": 1.0,
                    "compressed_ratio": ctx.compression_ratio,
                },
            )

        return ctx

    def prepare_agent_context(
        self,
        agent_name: str,
        task: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Synchronous context preparation for the agent router.

        Merges the engine-built context with the existing pipeline
        context dict. Used by the AgentRouter to enrich agent input.

        Args:
            agent_name:  Target agent name.
            task:        The task description.
            context:     Existing pipeline context dict.

        Returns:
            Merged context dict for agent.handle().
        """
        # If an AgentContext was already built and stored in metadata,
        # convert it to the agent-friendly dict.
        agent_ctx = context.get("agent_context")
        if isinstance(agent_ctx, AgentContext):
            agent_dict = agent_ctx.to_agent_dict()
            # Merge with existing context (engine context takes precedence).
            merged = {**context, **agent_dict}
            merged.pop("agent_context", None)
            return merged

        # Otherwise, return the context as-is with the task.
        merged = dict(context)
        merged["task"] = task
        return merged

    def decompose_task(
        self,
        task: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> TaskBreakdown:
        """Decompose a task into sub-tasks.

        Args:
            task:    The high-level task.
            context: Optional context.

        Returns:
            A TaskBreakdown with ordered sub-tasks.
        """
        breakdown = self._decomposer.decompose(task, context=context)

        logger.info(
            "Task decomposed: '%s' -> %d sub-tasks (strategy=%s)",
            task[:60],
            len(breakdown.subtasks),
            breakdown.strategy,
        )
        return breakdown

    def generate_subtasks(
        self,
        task: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> list[SubTaskMeta]:
        """Generate enriched sub-task metadata.

        Args:
            task:    The high-level task.
            context: Optional context.

        Returns:
            List of SubTaskMeta in execution order.
        """
        metas = self._subtask_gen.generate(task, context=context)

        logger.info(
            "Sub-tasks generated: '%s' -> %d sub-tasks",
            task[:60],
            len(metas),
        )
        return metas

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate_cache(self, pattern: str = "") -> int:
        """Invalidate cache entries.

        Args:
            pattern: If non-empty, only invalidate matching keys.

        Returns:
            Number of entries invalidated.
        """
        if pattern:
            return self._cache.invalidate_pattern(pattern)
        return self._cache.invalidate_all()

    @property
    def cache_stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return self._cache.stats()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def file_selector(self) -> FileSelector:
        return self._file_selector

    @property
    def memory_selector(self) -> MemorySelector:
        return self._memory_selector

    @property
    def history_selector(self) -> HistorySelector:
        return self._history_selector

    @property
    def builder(self) -> ContextBuilder:
        return self._builder

    @property
    def cache(self) -> ContextCache:
        return self._cache

    @property
    def decomposer(self) -> TaskDecomposer:
        return self._decomposer

    @property
    def config(self) -> ContextEngineConfig:
        return self._config

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _emit(self, event_type: str, payload: Any) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event '%s'", event_type)


__all__ = [
    "CONTEXT_BUILT",
    "CONTEXT_CACHE_HIT",
    "CONTEXT_CACHE_MISS",
    "CONTEXT_COMPRESSED",
    "CONTEXT_FILES_SELECTED",
    "CONTEXT_HISTORY_SELECTED",
    "CONTEXT_MEMORIES_SELECTED",
    "TASK_DECOMPOSED",
    "ContextEngine",
    "ContextEngineConfig",
]
