"""Context Engineering Engine.

Sprint 3.9.

Provides intelligent context selection, ranking, compression and
budgeting so that every agent receives only the most relevant
information -- files, memory, history and prior results -- without
noise or unnecessary tokens.

Modules:
    - models:            Data models for context artifacts.
    - file_selector:     Relevance-based file selection and scoring.
    - memory_selector:   Relevance-based memory selection and scoring.
    - history_selector:  Relevance-based conversation history selection.
    - context_ranker:    Unified scoring and ranking algorithm.
    - context_budget:    Token budget management and enforcement.
    - context_builder:   Assemble a complete AgentContext from components.
    - context_cache:     In-memory cache with automatic invalidation.
    - context_engine:    Top-level orchestrator for the full pipeline.
    - task_decomposer:   Intelligent task decomposition.
    - subtask_generator: Automatic sub-task generation with metadata.
"""

from .context_budget import ContextBudget, ContextBudgetConfig
from .context_builder import ContextBuilder
from .context_cache import ContextCache, CacheEntry
from .context_engine import ContextEngine, ContextEngineConfig
from .context_ranker import ContextRanker, RankConfig
from .file_selector import FileSelector, FileScore
from .history_selector import HistorySelector, HistoryScore
from .memory_selector import MemorySelector, MemoryScore
from .models import (
    AgentContext,
    ContextFile,
    ContextHistory,
    ContextMemory,
    RankableItem,
    RankedContext,
    SubTaskDecomposed,
    TaskBreakdown,
)
from .subtask_generator import SubTaskGenerator, SubTaskMeta
from .task_decomposer import TaskDecomposer

__all__ = [
    # Models
    "AgentContext",
    "ContextFile",
    "ContextHistory",
    "ContextMemory",
    "RankableItem",
    "RankedContext",
    "SubTaskDecomposed",
    "TaskBreakdown",
    # Selectors
    "FileSelector",
    "FileScore",
    "MemorySelector",
    "MemoryScore",
    "HistorySelector",
    "HistoryScore",
    # Ranking
    "ContextRanker",
    "RankConfig",
    # Budget
    "ContextBudget",
    "ContextBudgetConfig",
    # Builder
    "ContextBuilder",
    # Cache
    "ContextCache",
    "CacheEntry",
    # Engine
    "ContextEngine",
    "ContextEngineConfig",
    # Decomposition
    "TaskDecomposer",
    "SubTaskGenerator",
    "SubTaskMeta",
]
