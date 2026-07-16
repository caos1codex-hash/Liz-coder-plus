"""Context Budget — token budget management and enforcement.

Sprint 3.9.

Manages token budgets for agent contexts. When assembled context
exceeds the budget, the budget manager trims items from the lowest-
ranked entries until the budget is satisfied.

Example::

    budget = ContextBudgetConfig(max_tokens=32000, reserve_response=8192)
    manager = ContextBudgetManager(budget)
    result = manager.enforce(ranked_context)
    # result has trimmed items to fit within the budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .models import (
    ContextBudget,
    ContextFile,
    ContextHistory,
    ContextMemory,
    RankedContext,
)

logger = logging.getLogger(__name__)


@dataclass
class ContextBudgetConfig:
    """Configuration for the context budget manager.

    Attributes:
        max_tokens:        Total model context window size.
        reserve_response:  Tokens reserved for model response.
        system_budget:     Tokens reserved for system prompt.
        file_ratio:        Proportion of available tokens for files (0-1).
        memory_ratio:      Proportion of available tokens for memories.
        history_ratio:     Proportion of available tokens for history.
        compression_threshold: Ratio at which to trigger compression (0-1).
    """

    max_tokens: int = 32000
    reserve_response: int = 8192
    system_budget: int = 1024
    file_ratio: float = 0.40
    memory_ratio: float = 0.30
    history_ratio: float = 0.30
    compression_threshold: float = 0.80

    def to_context_budget(self) -> ContextBudget:
        """Convert to a ContextBudget with computed allocations."""
        available = max(
            0,
            self.max_tokens - self.reserve_response - self.system_budget,
        )
        total_ratio = self.file_ratio + self.memory_ratio + self.history_ratio
        if total_ratio > 0:
            file_b = int(available * self.file_ratio / total_ratio)
            mem_b = int(available * self.memory_ratio / total_ratio)
            hist_b = int(available * self.history_ratio / total_ratio)
        else:
            per = available // 3
            file_b = mem_b = hist_b = per

        return ContextBudget(
            max_tokens=self.max_tokens,
            reserve_response=self.reserve_response,
            system_budget=self.system_budget,
            file_budget=file_b,
            memory_budget=mem_b,
            history_budget=hist_b,
        )


class ContextBudgetManager:
    """Enforces token budgets on a RankedContext.

    Trims lowest-ranked items from each category until the budget
    is satisfied. Items are trimmed in reverse-score order (lowest
    first) to preserve the most relevant content.

    Args:
        config: Budget configuration.
    """

    def __init__(self, config: ContextBudgetConfig | None = None) -> None:
        self._config = config or ContextBudgetConfig()

    @property
    def config(self) -> ContextBudgetConfig:
        return self._config

    def get_budget(self) -> ContextBudget:
        """Create a fresh ContextBudget from config."""
        return self._config.to_context_budget()

    def enforce(
        self,
        context: RankedContext,
    ) -> tuple[RankedContext, dict[str, Any]]:
        """Enforce budget on a RankedContext.

        Trims items in reverse-score order until each category fits
        within its budget allocation.

        Args:
            context: The ranked context to trim.

        Returns:
            Tuple of (trimmed RankedContext, enforcement stats).
        """
        budget = self.get_budget()
        stats: dict[str, Any] = {
            "original_tokens": context.total_tokens,
            "original_files": len(context.files),
            "original_memories": len(context.memories),
            "original_history": len(context.history),
            "files_trimmed": 0,
            "memories_trimmed": 0,
            "history_trimmed": 0,
            "final_tokens": 0,
            "compression_recommended": False,
        }

        # Trim each category independently.
        trimmed_files = self._trim_list(
            [f for f in context.files],
            budget.file_budget,
            lambda f: f.tokens,
        )
        stats["files_trimmed"] = len(context.files) - len(trimmed_files)

        trimmed_memories = self._trim_list(
            [m for m in context.memories],
            budget.memory_budget,
            lambda m: m.tokens,
        )
        stats["memories_trimmed"] = len(context.memories) - len(trimmed_memories)

        trimmed_history = self._trim_list(
            [h for h in context.history],
            budget.history_budget,
            lambda h: h.tokens,
        )
        stats["history_trimmed"] = len(context.history) - len(trimmed_history)

        result = RankedContext(
            files=trimmed_files,
            memories=trimmed_memories,
            history=trimmed_history,
        )

        stats["final_tokens"] = result.total_tokens
        usage_ratio = (
            result.total_tokens / max(1, budget.available)
            if budget.available > 0
            else 0.0
        )
        stats["compression_recommended"] = (
            usage_ratio >= self._config.compression_threshold
        )
        stats["budget"] = budget.to_dict()
        stats["usage_ratio"] = round(usage_ratio, 4)

        logger.debug(
            "BudgetManager: %d -> %d tokens (%d files, %d mem, %d hist trimmed)",
            stats["original_tokens"],
            stats["final_tokens"],
            stats["files_trimmed"],
            stats["memories_trimmed"],
            stats["history_trimmed"],
        )
        return result, stats

    def _trim_list[T](
        self,
        items: list[T],
        budget_tokens: int,
        token_fn: "callable[[T], int]",
    ) -> list[T]:
        """Trim items in reverse order until they fit in the budget.

        Items are already sorted by score (descending) from the ranker,
        so we remove from the end.
        """
        total = sum(token_fn(item) for item in items)
        while total > budget_tokens and items:
            removed = items.pop()
            total -= token_fn(removed)
        return items


class ContextCompressor:
    """Compresses context to fit within token budgets.

    Compression strategies:
    1. Remove duplicate content.
    2. Strip comments and whitespace.
    3. Truncate low-relevance items.
    4. Summarize verbose sections (placeholder — heuristic truncation).
    """

    def __init__(self) -> None:
        pass

    def compress_context(
        self, context: RankedContext, target_tokens: int
    ) -> tuple[RankedContext, dict[str, Any]]:
        """Compress context to fit within target_tokens.

        Returns:
            Tuple of (compressed RankedContext, compression stats).
        """
        stats: dict[str, Any] = {
            "original_tokens": context.total_tokens,
            "target_tokens": target_tokens,
            "duplicates_removed": 0,
            "comments_stripped": 0,
            "items_truncated": 0,
            "compression_ratio": 1.0,
        }

        if context.total_tokens <= target_tokens:
            return context, stats

        # Step 1: Remove duplicate content.
        context, dup_count = self._remove_duplicates(context)
        stats["duplicates_removed"] = dup_count

        # Step 2: Strip comments from code files.
        context, comment_count = self._strip_comments(context)
        stats["comments_stripped"] = comment_count

        # Step 3: Truncate lowest-relevance items if still over budget.
        context, trunc_count = self._truncate_low_relevance(context, target_tokens)
        stats["items_truncated"] = trunc_count

        final_tokens = context.total_tokens
        original = max(1, stats["original_tokens"])
        stats["compression_ratio"] = round(final_tokens / original, 4)
        stats["final_tokens"] = final_tokens

        logger.debug(
            "Compressor: %d -> %d tokens (ratio=%.2f, dups=%d, comments=%d, truncs=%d)",
            stats["original_tokens"],
            final_tokens,
            stats["compression_ratio"],
            dup_count,
            comment_count,
            trunc_count,
        )
        return context, stats

    def _remove_duplicates(self, context: RankedContext) -> tuple[RankedContext, int]:
        """Remove items with duplicate content."""
        seen: dict[str, str] = {}
        dup_count = 0

        new_files: list[ContextFile] = []
        for f in context.files:
            content_hash = str(hash(f.content))
            if content_hash in seen:
                dup_count += 1
                continue
            seen[content_hash] = f.path
            new_files.append(f)

        new_memories: list[ContextMemory] = []
        for m in context.memories:
            val_str = str(m.value)
            content_hash = str(hash(val_str))
            if content_hash in seen:
                dup_count += 1
                continue
            seen[content_hash] = m.key
            new_memories.append(m)

        new_history: list[ContextHistory] = []
        for h in context.history:
            content_hash = str(hash(h.content))
            if content_hash in seen:
                dup_count += 1
                continue
            seen[content_hash] = f"{h.role}:{h.timestamp}"
            new_history.append(h)

        return (
            RankedContext(files=new_files, memories=new_memories, history=new_history),
            dup_count,
        )

    def _strip_comments(self, context: RankedContext) -> tuple[RankedContext, int]:
        """Strip comments from code files (Python, JS, etc.)."""

        comment_count = 0
        new_files: list[ContextFile] = []

        for f in context.files:
            lang = f.language.lower()
            if lang not in ("python", "javascript", "typescript", "tsx", "jsx"):
                new_files.append(f)
                continue

            lines = f.content.split("\n")
            stripped: list[str] = []
            in_block_comment = False

            for line in lines:
                if lang == "python":
                    stripped_line, was_comment = self._strip_python_line(
                        line, in_block_comment
                    )
                    if was_comment:
                        comment_count += 1
                    if '"""' in line or "'''" in line:
                        in_block_comment = not in_block_comment
                else:
                    stripped_line, was_comment = self._strip_js_line(
                        line, in_block_comment
                    )
                    if was_comment:
                        comment_count += 1
                    if "/*" in line:
                        in_block_comment = True
                    if "*/" in line and in_block_comment:
                        in_block_comment = False

                stripped.append(stripped_line)

            f = ContextFile(
                path=f.path,
                content="\n".join(stripped),
                language=f.language,
                score=f.score,
                relevance=f.relevance,
                tokens=max(1, len("\n".join(stripped)) // 4),
                metadata=f.metadata,
            )
            new_files.append(f)

        return (
            RankedContext(
                files=new_files,
                memories=list(context.memories),
                history=list(context.history),
            ),
            comment_count,
        )

    @staticmethod
    def _strip_python_line(line: str, in_block: bool) -> tuple[str, bool]:
        """Strip a single Python line of comments."""
        stripped = line.strip()
        if in_block:
            return "", True
        if stripped.startswith("#"):
            return "", True
        # Inline comment.
        if "#" in stripped:
            # Be careful not to strip # inside strings.
            in_string = False
            for i, ch in enumerate(stripped):
                if ch == '"':
                    in_string = not in_string
                elif ch == "#" and not in_string:
                    return stripped[:i].rstrip(), True
        return line, False

    @staticmethod
    def _strip_js_line(line: str, in_block: bool) -> tuple[str, bool]:
        """Strip a single JS/TS line of comments."""
        stripped = line.strip()
        if in_block:
            return "" if "*/" not in line else line.split("*/", 1)[-1], True
        if stripped.startswith("//"):
            return "", True
        if stripped.startswith("/*"):
            return "", True
        return line, False

    def _truncate_low_relevance(
        self, context: RankedContext, target_tokens: int
    ) -> tuple[RankedContext, int]:
        """Truncate lowest-relevance items to fit budget."""
        from .models import RelevanceLevel

        trunc_count = 0
        total = context.total_tokens
        if total <= target_tokens:
            return context, 0

        # Truncate from the end (lowest relevance first).
        # Files first, then memories, then history.
        all_items: list[tuple[str, Any]] = (
            [("file", f) for f in context.files]
            + [("memory", m) for m in context.memories]
            + [("history", h) for h in context.history]
        )
        # Sort by score ascending (truncate lowest first).
        all_items.sort(key=lambda x: x[1].score if hasattr(x[1], "score") else 0)

        for item_type, item in all_items:
            if total <= target_tokens:
                break
            if hasattr(item, "relevance") and item.relevance in (
                RelevanceLevel.NONE,
                RelevanceLevel.LOW,
            ):
                # Truncate this item's content.
                if hasattr(item, "content") and item.content:
                    old_tokens = item.tokens
                    # Truncate to 50% of original.
                    new_len = len(item.content) // 2
                    item.content = item.content[:new_len]
                    item.tokens = max(1, new_len // 4)
                    total -= old_tokens - item.tokens
                    trunc_count += 1

        # Rebuild context with truncated items.
        new_files = [f for f in context.files if f.tokens > 0]
        new_memories = [m for m in context.memories if m.tokens > 0]
        new_history = [h for h in context.history if h.tokens > 0]

        return (
            RankedContext(
                files=new_files,
                memories=new_memories,
                history=new_history,
            ),
            trunc_count,
        )


__all__ = [
    "ContextBudgetConfig",
    "ContextBudgetManager",
    "ContextBudget",
    "ContextCompressor",
]
