"""Data models for the Context Engineering Engine.

Sprint 3.9.

Defines the core data structures used across the context engine:
context files, memories, history entries, budgets, rankings,
task breakdowns and the final assembled ``AgentContext``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _jsonable(value: Any) -> bool:
    try:
        import json

        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


class RelevanceLevel(str, Enum):
    """Qualitative relevance bucket for ranking display."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class SubTaskPriority(str, Enum):
    """Priority levels for decomposed sub-tasks."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class SubTaskRisk(str, Enum):
    """Risk levels for decomposed sub-tasks."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ContextFile:
    """A file selected as relevant context for an agent.

    Attributes:
        path:          Absolute or project-relative file path.
        content:       File content (may be truncated by budget).
        language:      Programming language or file type.
        score:         Numeric relevance score (0.0 - 1.0).
        relevance:     Qualitative relevance bucket.
        size_bytes:    Original file size in bytes.
        tokens:        Estimated token count for the content.
        metadata:      Extra metadata (last modified, dependencies, etc.).
    """

    path: str
    content: str = ""
    language: str = ""
    score: float = 0.0
    relevance: RelevanceLevel = RelevanceLevel.NONE
    size_bytes: int = 0
    tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content": self.content,
            "language": self.language,
            "score": round(self.score, 4),
            "relevance": self.relevance.value,
            "size_bytes": self.size_bytes,
            "tokens": self.tokens,
            "metadata": dict(self.metadata),
        }


@dataclass
class ContextMemory:
    """A memory entry selected as relevant context for an agent.

    Attributes:
        key:        Memory store key.
        value:      Stored value (summary or raw).
        category:   Memory category (api, ui, config, domain, ...).
        score:      Numeric relevance score (0.0 - 1.0).
        relevance:  Qualitative relevance bucket.
        tokens:     Estimated token count.
        created_at: When this memory was created.
        metadata:   Extra metadata.
    """

    key: str
    value: Any = None
    category: str = ""
    score: float = 0.0
    relevance: RelevanceLevel = RelevanceLevel.NONE
    tokens: int = 0
    created_at: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value if _jsonable(self.value) else str(self.value),
            "category": self.category,
            "score": round(self.score, 4),
            "relevance": self.relevance.value,
            "tokens": self.tokens,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class ContextHistory:
    """A conversation history entry selected as relevant context.

    Attributes:
        role:       Message role (user, assistant, system).
        content:    Message content.
        score:      Numeric relevance score (0.0 - 1.0).
        relevance:  Qualitative relevance bucket.
        tokens:     Estimated token count.
        timestamp:  When the message was sent.
        metadata:   Extra metadata.
    """

    role: str = "user"
    content: str = ""
    score: float = 0.0
    relevance: RelevanceLevel = RelevanceLevel.NONE
    tokens: int = 0
    timestamp: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "score": round(self.score, 4),
            "relevance": self.relevance.value,
            "tokens": self.tokens,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


@dataclass
class RankableItem:
    """Generic wrapper for any item that can be ranked.

    Attributes:
        key:      Unique identifier.
        content:  The raw content.
        tokens:   Estimated token count.
        score:    Numeric relevance score.
        category: Item category (file, memory, history).
        metadata: Extra metadata.
    """

    key: str
    content: str = ""
    tokens: int = 0
    score: float = 0.0
    category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RankedContext:
    """A ranked collection of context items ready for budget enforcement.

    Attributes:
        files:       Ranked file entries.
        memories:    Ranked memory entries.
        history:     Ranked history entries.
        total_score: Sum of all individual scores.
        total_tokens: Sum of all estimated tokens.
    """

    files: list[ContextFile] = field(default_factory=list)
    memories: list[ContextMemory] = field(default_factory=list)
    history: list[ContextHistory] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        files_score = sum(f.score for f in self.files)
        mem_score = sum(m.score for m in self.memories)
        hist_score = sum(h.score for h in self.history)
        return files_score + mem_score + hist_score

    @property
    def total_tokens(self) -> int:
        return (
            sum(f.tokens for f in self.files)
            + sum(m.tokens for m in self.memories)
            + sum(h.tokens for h in self.history)
        )

    def all_items(self) -> list[RankableItem]:
        """Flatten all ranked items into a single list."""
        items: list[RankableItem] = []
        for f in self.files:
            items.append(
                RankableItem(
                    key=f.path,
                    content=f.content,
                    tokens=f.tokens,
                    score=f.score,
                    category="file",
                    metadata=f.metadata,
                )
            )
        for m in self.memories:
            val = str(m.value) if m.value is not None else ""
            items.append(
                RankableItem(
                    key=m.key,
                    content=val,
                    tokens=m.tokens,
                    score=m.score,
                    category="memory",
                    metadata=m.metadata,
                )
            )
        for h in self.history:
            items.append(
                RankableItem(
                    key=f"{h.role}:{h.timestamp[:19]}",
                    content=h.content,
                    tokens=h.tokens,
                    score=h.score,
                    category="history",
                    metadata=h.metadata,
                )
            )
        return items

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [f.to_dict() for f in self.files],
            "memories": [m.to_dict() for m in self.memories],
            "history": [h.to_dict() for h in self.history],
            "total_score": round(self.total_score, 4),
            "total_tokens": self.total_tokens,
        }


@dataclass
class ContextBudget:
    """Token budget allocation for a single agent context.

    Attributes:
        max_tokens:       Total token budget for the model context window.
        reserve_response: Tokens reserved for the model response.
        available:        Tokens available for context (computed).
        file_budget:      Token allocation for files.
        memory_budget:    Token allocation for memories.
        history_budget:   Token allocation for history.
        system_budget:    Token allocation for system prompt overhead.
        used_tokens:      Tokens actually used (filled after assembly).
    """

    max_tokens: int = 32000
    reserve_response: int = 8192
    system_budget: int = 1024
    file_budget: int = 0
    memory_budget: int = 0
    history_budget: int = 0
    used_tokens: int = 0

    def __post_init__(self) -> None:
        if (
            self.file_budget == 0
            and self.memory_budget == 0
            and self.history_budget == 0
        ):
            self._distribute_evenly()
        self._recompute_available()

    def _distribute_evenly(self) -> None:
        """Distribute available tokens evenly across categories."""
        available = self.max_tokens - self.reserve_response - self.system_budget
        per_category = max(0, available // 3)
        self.file_budget = per_category
        self.memory_budget = per_category
        self.history_budget = per_category

    def _recompute_available(self) -> None:
        self.available = max(
            0,
            self.max_tokens - self.reserve_response - self.system_budget,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "reserve_response": self.reserve_response,
            "system_budget": self.system_budget,
            "available": self.available,
            "file_budget": self.file_budget,
            "memory_budget": self.memory_budget,
            "history_budget": self.history_budget,
            "used_tokens": self.used_tokens,
        }


@dataclass
class AgentContext:
    """The final assembled context delivered to an agent.

    Attributes:
        agent_name:      Target agent name.
        task:            Task description or query.
        files:           Selected file contexts.
        memories:        Selected memory contexts.
        history:         Selected history contexts.
        system_prompt:   System-level instructions.
        total_tokens:    Total tokens in the assembled context.
        budget:          Budget used for assembly.
        compression_ratio: Ratio of original to compressed tokens.
        metadata:        Extra metadata (cache_hit, build_time_ms, etc.).
        created_at:      When this context was assembled.
    """

    agent_name: str = ""
    task: str = ""
    files: list[ContextFile] = field(default_factory=list)
    memories: list[ContextMemory] = field(default_factory=list)
    history: list[ContextHistory] = field(default_factory=list)
    system_prompt: str = ""
    total_tokens: int = 0
    budget: ContextBudget = field(default_factory=ContextBudget)
    compression_ratio: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    @property
    def context_tokens(self) -> int:
        return (
            sum(f.tokens for f in self.files)
            + sum(m.tokens for m in self.memories)
            + sum(h.tokens for h in self.history)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "task": self.task,
            "files": [f.to_dict() for f in self.files],
            "memories": [m.to_dict() for m in self.memories],
            "history": [h.to_dict() for h in self.history],
            "system_prompt": self.system_prompt,
            "total_tokens": self.total_tokens,
            "budget": self.budget.to_dict(),
            "compression_ratio": round(self.compression_ratio, 4),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    def to_agent_dict(self) -> dict[str, Any]:
        """Produce the flat context dict passed to agent.handle()."""
        return {
            "task": self.task,
            "files": [
                {"path": f.path, "content": f.content, "language": f.language}
                for f in self.files
            ],
            "memories": [
                {"key": m.key, "value": m.value, "category": m.category}
                for m in self.memories
            ],
            "history": [{"role": h.role, "content": h.content} for h in self.history],
            "system_prompt": self.system_prompt,
        }


@dataclass
class SubTaskDecomposed:
    """A single sub-task produced by the TaskDecomposer.

    Attributes:
        name:            Short unique name.
        description:     What this sub-task should accomplish.
        priority:        Priority level.
        risk:            Risk assessment.
        estimated_tokens: Token budget estimate.
        dependencies:    Names of sibling sub-tasks that must finish first.
        tools_needed:    Suggested tools for this sub-task.
        agent_suggestion: Suggested agent type.
        metadata:        Extra metadata.
    """

    name: str
    description: str = ""
    priority: SubTaskPriority = SubTaskPriority.NORMAL
    risk: SubTaskRisk = SubTaskRisk.MEDIUM
    estimated_tokens: int = 0
    dependencies: list[str] = field(default_factory=list)
    tools_needed: list[str] = field(default_factory=list)
    agent_suggestion: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "priority": self.priority.value,
            "risk": self.risk.value,
            "estimated_tokens": self.estimated_tokens,
            "dependencies": list(self.dependencies),
            "tools_needed": list(self.tools_needed),
            "agent_suggestion": self.agent_suggestion,
            "metadata": dict(self.metadata),
        }


@dataclass
class TaskBreakdown:
    """Result of decomposing a task into sub-tasks.

    Attributes:
        original_task:   The original user request.
        subtasks:        Ordered list of decomposed sub-tasks.
        total_estimated_tokens: Sum of sub-task token estimates.
        strategy:        Decomposition strategy used.
        metadata:        Extra metadata.
        created_at:      When this breakdown was created.
    """

    original_task: str = ""
    subtasks: list[SubTaskDecomposed] = field(default_factory=list)
    total_estimated_tokens: int = 0
    strategy: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        self.total_estimated_tokens = sum(st.estimated_tokens for st in self.subtasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_task": self.original_task,
            "subtasks": [st.to_dict() for st in self.subtasks],
            "total_estimated_tokens": self.total_estimated_tokens,
            "strategy": self.strategy,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


__all__ = [
    "AgentContext",
    "ContextBudget",
    "ContextFile",
    "ContextHistory",
    "ContextMemory",
    "RankableItem",
    "RankedContext",
    "RelevanceLevel",
    "SubTaskDecomposed",
    "SubTaskPriority",
    "SubTaskRisk",
    "TaskBreakdown",
]
