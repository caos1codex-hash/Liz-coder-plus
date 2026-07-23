"""Tools package: external tools for Liz Coder Plus.

Sprint 1.6 — Full tool system with lifecycle, permission levels,
validation, structured metadata, and built-in operational tools.

Sprint 4.2 — Tool Intelligence: automatic tool selection engine
with filtering, multi-factor ranking, and configurable weights.

Sprint 4.3 — Tool Execution Pipeline: validate, prepare, execute,
and capture results with session tracking, timeout, cancellation,
and comprehensive error handling.

Public API — Base:
  - ``BaseTool`` — abstract base class with lifecycle and validation.
  - ``ToolCategory`` — SHELL, FILE, WEB, APP, SYSTEM, CUSTOM.
  - ``ToolState`` — AVAILABLE, EXECUTING, COMPLETED, ERROR, DISABLED.
  - ``PermissionLevel`` — LOW, MEDIUM, HIGH.
  - ``ToolError`` — raised on validation failures.
  - ``ToolRegistry`` — tool registration with validation.
  - ``FileTool`` — read, write, list files and directories.
  - ``SystemTool`` — system information and status.
  - ``TerminalTool`` — execute shell commands safely.

Public API — Selection Engine (Sprint 4.2):
  - ``ToolSelectionEngine`` — automatic best-tool picker.
  - ``EngineConfig`` — engine configuration.
  - ``SelectionContext`` — contextual input for filters and ranking.
  - ``ExecutionMode`` — AUTOMATIC, MANUAL_CONFIRM, BACKGROUND, INTERACTIVE.
  - ``RankingWeights`` — configurable per-factor weights.
  - ``RankingResult`` — per-tool score with reason.
  - ``ToolRanker`` — multi-factor scoring and ranking.
  - ``BaseFilter`` / ``DEFAULT_FILTER_CHAIN`` — filter infrastructure.

Public API — Execution Pipeline (Sprint 4.3):
  - ``ToolExecutionPipeline`` — full validate→execute→result orchestrator.
  - ``PipelineConfig`` — pipeline configuration.
  - ``ToolExecutor`` — low-level async runner with timeout/cancel.
  - ``ToolExecutionValidator`` — pre-execution validation layer.
  - ``ExecutionLifecycleManager`` — session tracking.
  - ``ExecutionSession`` / ``ExecutionResult`` / ``ExecutionStatus`` — data models.
  - ``ExecutionContext`` / ``ToolArguments`` / ``ToolSelectionResult`` — input models.
  - ``SecurityRestrictions`` — argument security configuration.
"""

from src.base import (
    BaseTool,
    PermissionLevel,
    ToolCategory,
    ToolError,
    ToolState,
)
from src.exceptions import (
    AmbiguousSelectionError,
    ExecutionCancelledError,
    ExecutionTimeoutError,
    ExecutionValidationError,
    InvalidArgumentsError,
    NoToolFoundError,
    PermissionDeniedError,
    RankingError,
    ToolExecutionError,
    ToolSelectionError,
)
from src.execution import PipelineConfig, ToolExecutionPipeline
from src.executor import DEFAULT_TIMEOUT_MS, ToolExecutor
from src.filters import (
    DEFAULT_FILTER_CHAIN,
    AvailabilityFilter,
    BaseFilter,
    CapabilityFilter,
    CostFilter,
    ExecutionMode,
    ExecutionModeFilter,
    LatencyFilter,
    PermissionFilter,
    SafetyFilter,
    SelectionContext,
    UserPreferenceFilter,
)
from src.lifecycle import ExecutionLifecycleManager
from src.ranking import RankingResult, RankingWeights, ToolRanker
from src.registry import ToolRegistry
from src.results import (
    ExecutionContext,
    ExecutionResult,
    ExecutionSession,
    ExecutionStatus,
    ToolArguments,
    ToolSelectionResult,
)
from src.selection import EngineConfig, ToolSelectionEngine
from src.tools.file_tool import FileTool
from src.tools.system_tool import SystemTool
from src.tools.terminal_tool import TerminalTool
from src.validation import (
    SecurityRestrictions,
    ToolExecutionValidator,
)

__version__ = "0.4.0"

__all__ = [
    # Base tool system (Sprint 1.6)
    "BaseTool",
    "ToolCategory",
    "ToolState",
    "PermissionLevel",
    "ToolError",
    "ToolRegistry",
    "FileTool",
    "SystemTool",
    "TerminalTool",
    # Selection Engine (Sprint 4.2)
    "ToolSelectionEngine",
    "EngineConfig",
    "SelectionContext",
    "ExecutionMode",
    "RankingWeights",
    "RankingResult",
    "ToolRanker",
    "BaseFilter",
    "DEFAULT_FILTER_CHAIN",
    "AvailabilityFilter",
    "CapabilityFilter",
    "ExecutionModeFilter",
    "PermissionFilter",
    "CostFilter",
    "LatencyFilter",
    "SafetyFilter",
    "UserPreferenceFilter",
    "ToolSelectionError",
    "NoToolFoundError",
    "AmbiguousSelectionError",
    "RankingError",
    "FilterError",
    # Execution Pipeline (Sprint 4.3)
    "ToolExecutionPipeline",
    "PipelineConfig",
    "ToolExecutor",
    "DEFAULT_TIMEOUT_MS",
    "ToolExecutionValidator",
    "SecurityRestrictions",
    "ExecutionLifecycleManager",
    "ExecutionSession",
    "ExecutionResult",
    "ExecutionStatus",
    "ExecutionContext",
    "ToolArguments",
    "ToolSelectionResult",
    "ToolExecutionError",
    "InvalidArgumentsError",
    "PermissionDeniedError",
    "ExecutionValidationError",
    "ExecutionTimeoutError",
    "ExecutionCancelledError",
]
