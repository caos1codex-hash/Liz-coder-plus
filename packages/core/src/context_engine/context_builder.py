"""Context Builder — assembles a complete AgentContext from components.

Sprint 3.9.

The builder orchestrates the full context assembly pipeline:

    1. Select files via FileSelector
    2. Select memories via MemorySelector
    3. Select history via HistorySelector
    4. Rank everything via ContextRanker
    5. Enforce budget via ContextBudgetManager
    6. Compress if needed via ContextCompressor
    7. Produce the final AgentContext

Example::

    builder = ContextBuilder(file_selector, memory_selector, history_selector)
    ctx = await builder.build(
        task="Fix login bug",
        agent_name="coder",
        files=["src/auth/login.py", ...],
        memories=[{"key": "api_patterns", ...}],
        history=[{"role": "user", "content": "Fix login"}, ...],
    )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .context_budget import (
    ContextBudgetConfig,
    ContextBudgetManager,
    ContextCompressor,
)
from .context_ranker import ContextRanker, RankConfig
from .file_selector import FileSelector
from .history_selector import HistorySelector
from .memory_selector import MemorySelector
from .models import (
    AgentContext,
    RankedContext,
)

logger = logging.getLogger(__name__)


@dataclass
class ContextBuilderConfig:
    """Configuration for the ContextBuilder.

    Attributes:
        budget_config:  Token budget configuration.
        rank_config:    Ranking configuration.
        compression_enabled: Whether to enable compression.
        ranking_enabled:     Whether to enable re-ranking.
    """

    budget_config: ContextBudgetConfig = field(default_factory=ContextBudgetConfig)
    rank_config: RankConfig = field(default_factory=RankConfig)
    compression_enabled: bool = True
    ranking_enabled: bool = True


class ContextBuilder:
    """Assembles a complete AgentContext from raw inputs.

    Coordinates selectors, ranker, budget manager and compressor
    into a single pipeline.

    Args:
        file_selector:    File selector instance.
        memory_selector:  Memory selector instance.
        history_selector: History selector instance.
        config:           Builder configuration.
        ranker:           Optional custom ranker.
        budget_manager:   Optional custom budget manager.
        compressor:       Optional custom compressor.
    """

    def __init__(
        self,
        file_selector: FileSelector | None = None,
        memory_selector: MemorySelector | None = None,
        history_selector: HistorySelector | None = None,
        *,
        config: ContextBuilderConfig | None = None,
        ranker: ContextRanker | None = None,
        budget_manager: ContextBudgetManager | None = None,
        compressor: ContextCompressor | None = None,
    ) -> None:
        self._file_sel = file_selector or FileSelector()
        self._mem_sel = memory_selector or MemorySelector()
        self._hist_sel = history_selector or HistorySelector()
        self._config = config or ContextBuilderConfig()
        self._ranker = ranker or ContextRanker(self._config.rank_config)
        self._budget_mgr = budget_manager or ContextBudgetManager(
            self._config.budget_config
        )
        self._compressor = compressor or ContextCompressor()

    @property
    def config(self) -> ContextBuilderConfig:
        return self._config

    async def build(
        self,
        task: str,
        agent_name: str = "",
        *,
        available_files: list[str] | None = None,
        file_contents: dict[str, str] | None = None,
        available_memories: list[dict[str, Any]] | None = None,
        history: list[dict[str, Any]] | None = None,
        system_prompt: str = "",
    ) -> AgentContext:
        """Build a complete AgentContext.

        Args:
            task:                The task description or user message.
            agent_name:          Target agent name.
            available_files:     Candidate file paths.
            file_contents:       Mapping of path -> content.
            available_memories:  Candidate memory entries.
            history:             Conversation history entries.
            system_prompt:       System-level instructions.

        Returns:
            Fully assembled AgentContext.
        """
        start = time.monotonic()
        available_files = available_files or []
        available_memories = available_memories or []
        history = history or []

        # Step 1: Select relevant items.
        file_scores = self._file_sel.select(
            task,
            available_files,
            file_contents=file_contents,
        )
        memory_scores = self._mem_sel.select(task, available_memories)
        history_scores = self._hist_sel.select(task, history)

        # Step 2: Convert to typed models.
        files = self._file_sel.to_context_files(file_scores)
        memories = self._mem_sel.to_context_memories(memory_scores)
        hist_entries = self._hist_sel.to_context_history(history_scores)

        # Step 3: Assemble RankedContext.
        ranked = RankedContext(
            files=files,
            memories=memories,
            history=hist_entries,
        )

        # Step 4: Re-rank if enabled.
        if self._config.ranking_enabled:
            ranked = self._ranker.rank_context(ranked)

        # Step 5: Enforce budget.
        budget = self._budget_mgr.get_budget()
        ranked, budget_stats = self._budget_mgr.enforce(ranked)

        # Step 6: Compress if needed and enabled.
        compression_stats: dict[str, Any] = {}
        if self._config.compression_enabled and budget_stats.get(
            "compression_recommended"
        ):
            ranked, compression_stats = self._compressor.compress_context(
                ranked,
                budget.available,
            )

        # Step 7: Produce AgentContext.
        total_tokens = ranked.total_tokens + len(system_prompt) // 4
        build_time_ms = (time.monotonic() - start) * 1000

        ctx = AgentContext(
            agent_name=agent_name,
            task=task,
            files=ranked.files,
            memories=ranked.memories,
            history=ranked.history,
            system_prompt=system_prompt,
            total_tokens=total_tokens,
            budget=budget,
            compression_ratio=compression_stats.get(
                "compression_ratio",
                1.0,
            ),
            metadata={
                "build_time_ms": round(build_time_ms, 2),
                "files_selected": len(file_scores),
                "memories_selected": len(memory_scores),
                "history_selected": len(history_scores),
                "budget_stats": budget_stats,
                "compression_stats": compression_stats,
            },
        )
        budget.used_tokens = total_tokens

        logger.info(
            "Context built for '%s': %d files, %d mem, %d hist, %d tokens (%.1fms)",
            agent_name,
            len(ctx.files),
            len(ctx.memories),
            len(ctx.history),
            total_tokens,
            build_time_ms,
        )
        return ctx


__all__ = [
    "ContextBuilder",
    "ContextBuilderConfig",
]
