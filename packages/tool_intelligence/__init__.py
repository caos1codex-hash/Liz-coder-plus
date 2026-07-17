"""Tool Intelligence package.

Sprint 4.1 — Tool Intelligence Foundation.

This package adds a reasoning layer over the existing ToolRegistry:
instead of executing tools blindly, the orchestrator can ask the
Tool Intelligence layer *which* tools are best suited for a given
user intent, and only then dispatch execution.

Public API:

  - ``ToolMetadata``        — static description of a tool.
  - ``UserIntent``          — what the user wants to accomplish.
  - ``SelectionContext``    — runtime environment (platform, perms...).
  - ``ToolHistoryStats``    — observed past performance.
  - ``RankedCandidate``     — a tool paired with its ranking score.
  - ``RiskLevel`` / ``ToolCategory`` — enumerations.
  - ``CapabilityIndex``     — searchable catalog of tool metadata.
  - ``extract_keywords``    — helper used by the index.
  - ``RegistryCache``       — cached, incrementally-updatable index.
  - ``CompatibilityChecker`` / ``CompatibilityReport`` — env checks.
  - ``ToolRanker`` / ``RankerWeights`` — scoring and sorting.
  - ``ToolSelector`` / ``SelectionResult`` / ``SubTaskSelection`` —
    high-level selection API.
  - Exceptions: ``ToolIntelligenceError``, ``ToolNotCompatible``,
    ``CapabilityMissing``, ``ToolSelectionError``, ``RankingError``.
"""

from __future__ import annotations

from .capability_index import CapabilityIndex, extract_keywords
from .compatibility import (
    CompatibilityChecker,
    CompatibilityReport,
    DependencyResolver,
    binary_dependency_resolver,
)
from .exceptions import (
    CapabilityMissing,
    RankingError,
    ToolIntelligenceError,
    ToolNotCompatible,
    ToolSelectionError,
)
from .models import (
    RankedCandidate,
    RiskLevel,
    SelectionContext,
    ToolCategory,
    ToolHistoryStats,
    ToolMetadata,
    UserIntent,
)
from .registry_cache import Loader, RegistryCache
from .tool_ranker import RankerWeights, ToolRanker
from .tool_selector import SelectionResult, SubTaskSelection, ToolSelector

__version__ = "0.1.0"

__all__ = [
    # Models
    "RankedCandidate",
    "RiskLevel",
    "SelectionContext",
    "ToolCategory",
    "ToolHistoryStats",
    "ToolMetadata",
    "UserIntent",
    # Capability index
    "CapabilityIndex",
    "extract_keywords",
    # Registry cache
    "Loader",
    "RegistryCache",
    # Compatibility
    "CompatibilityChecker",
    "CompatibilityReport",
    "DependencyResolver",
    "binary_dependency_resolver",
    # Ranker
    "RankerWeights",
    "ToolRanker",
    # Selector
    "SelectionResult",
    "SubTaskSelection",
    "ToolSelector",
    # Exceptions
    "CapabilityMissing",
    "RankingError",
    "ToolIntelligenceError",
    "ToolNotCompatible",
    "ToolSelectionError",
]
