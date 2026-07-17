"""Models for the Tool Intelligence package.

Sprint 4.1 — Tool Intelligence Foundation.

This module defines the canonical ``ToolMetadata`` data class that
describes everything the intelligence layer needs to know about a tool
without instantiating it. Metadata is intentionally a *snapshot* — it
captures cost, latency, capabilities, permissions and compatibility
requirements at registration time so the selector and ranker can
reason about tools statically.

The metadata model is intentionally compatible with the existing
``BaseTool`` from ``packages/tools``: the helper ``from_base_tool``
extracts the relevant fields without forcing a hard dependency at
import time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------


class RiskLevel(str, Enum):
    """Risk classification used by the permission system.

    LOW:      Read-only / informational operations.
    MEDIUM:   Controlled modifications (write files, create resources).
    HIGH:     Execution of commands, critical system actions.
    CRITICAL: Irreversible operations (delete, format, deploy).
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    def numeric(self) -> int:
        """Return a numeric weight for sorting (higher = riskier)."""
        return {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }[self]


class ToolCategory(str, Enum):
    """High-level category for grouping tools in the index."""

    SHELL = "shell"
    FILE = "file"
    WEB = "web"
    APP = "app"
    SYSTEM = "system"
    LLM = "llm"
    MEMORY = "memory"
    NETWORK = "network"
    DATABASE = "database"
    CUSTOM = "custom"


# ----------------------------------------------------------------------
# ToolMetadata
# ----------------------------------------------------------------------


@dataclass
class ToolMetadata:
    """Static, serializable description of a tool.

    This is the unit of information that the CapabilityIndex, the
    ToolSelector and the ToolRanker operate on. Keeping it separate
    from the tool instance lets us index tools without holding live
    objects in memory.

    Attributes:
        id:                   Unique identifier (stable across runs).
        name:                 Human-readable name (must be unique in a registry).
        description:          Free-form description (1-3 sentences).
        category:             High-level category.
        capabilities:         Hierarchical capability strings (e.g. ``filesystem.read``).
        input_schema:         JSON-schema-like dict describing inputs.
        output_schema:        JSON-schema-like dict describing outputs.
        risk_level:           Risk classification for permission gating.
        estimated_cost:       Relative cost (0.0 = free, 1.0 = expensive).
        estimated_latency:    Expected latency in milliseconds.
        requires_confirmation:Whether the user must confirm before execution.
        supports_parallel:    Whether the tool can run in parallel with others.
        supported_platforms:  Set of platforms where the tool works.
        aliases:              Alternative names for fuzzy lookup.
        tags:                 Free-form tags for filtering.
        keywords:             Keywords extracted from description for matching.
        version:              Semantic version of the tool.
        permissions:          Permissions required to execute (e.g. ``["fs:write"]``).
        dependencies:         External dependencies (packages, binaries).
        required_model:       Optional model identifier the tool needs (e.g. ``"gpt-4"``).
        plugins:              Plugin identifiers the tool depends on.
        reliability:          Historical reliability score (0.0..1.0).
        metadata:             Free-form extension dict.
    """

    id: str
    name: str
    description: str = ""
    category: ToolCategory = ToolCategory.CUSTOM
    capabilities: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    estimated_cost: float = 0.0
    estimated_latency: float = 0.0
    requires_confirmation: bool = False
    supports_parallel: bool = True
    supported_platforms: list[str] = field(default_factory=lambda: ["linux", "macos", "windows"])
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    version: str = "0.1.0"
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    required_model: str | None = None
    plugins: list[str] = field(default_factory=list)
    reliability: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ToolMetadata.id cannot be empty")
        if not self.name:
            raise ValueError("ToolMetadata.name cannot be empty")
        if not isinstance(self.category, ToolCategory):
            raise TypeError(
                f"category must be a ToolCategory, got {type(self.category).__name__}"
            )
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError(
                f"risk_level must be a RiskLevel, got {type(self.risk_level).__name__}"
            )
        if not (0.0 <= self.estimated_cost <= 1.0):
            raise ValueError(
                f"estimated_cost must be in [0.0, 1.0], got {self.estimated_cost}"
            )
        if not (0.0 <= self.reliability <= 1.0):
            raise ValueError(
                f"reliability must be in [0.0, 1.0], got {self.reliability}"
            )
        if self.estimated_latency < 0:
            raise ValueError(
                f"estimated_latency must be >= 0, got {self.estimated_latency}"
            )

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        name: str,
        *,
        description: str = "",
        category: ToolCategory | str = ToolCategory.CUSTOM,
        capabilities: list[str] | None = None,
        risk_level: RiskLevel | str = RiskLevel.LOW,
        **kwargs: Any,
    ) -> "ToolMetadata":
        """Convenience factory that auto-generates an id and accepts str enums."""
        if isinstance(category, str):
            category = ToolCategory(category)
        if isinstance(risk_level, str):
            risk_level = RiskLevel(risk_level)
        return cls(
            id=kwargs.pop("id", f"tool_{uuid.uuid4().hex[:12]}"),
            name=name,
            description=description,
            category=category,
            capabilities=capabilities or [],
            risk_level=risk_level,
            **kwargs,
        )

    @classmethod
    def from_base_tool(cls, tool: Any) -> "ToolMetadata":
        """Build a ToolMetadata from a ``BaseTool``-like object.

        Avoids a hard import dependency on ``packages.tools`` so this
        module can be used standalone. The duck-typed protocol requires
        the tool to expose ``name``, ``description``, ``category``,
        ``permission_level`` and ``metadata``.
        """
        # Map BaseTool.permission_level -> RiskLevel
        perm = getattr(tool, "permission_level", None)
        perm_value = getattr(perm, "value", str(perm)) if perm is not None else "low"
        risk_map = {
            "low": RiskLevel.LOW,
            "medium": RiskLevel.MEDIUM,
            "high": RiskLevel.HIGH,
        }
        risk = risk_map.get(perm_value, RiskLevel.LOW)

        # Map BaseTool.category -> ToolCategory
        cat = getattr(tool, "category", None)
        cat_value = getattr(cat, "value", str(cat)) if cat is not None else "custom"
        try:
            category = ToolCategory(cat_value)
        except ValueError:
            category = ToolCategory.CUSTOM

        base_meta = getattr(tool, "metadata", {}) or {}
        params_schema = base_meta.get("parameters_schema", {}) if isinstance(base_meta, dict) else {}

        return cls(
            id=getattr(tool, "tool_id", None) or f"tool_{uuid.uuid4().hex[:12]}",
            name=getattr(tool, "name", "unknown"),
            description=getattr(tool, "description", "") or "",
            category=category,
            capabilities=cls._extract_capabilities(tool),
            input_schema=params_schema if isinstance(params_schema, dict) else {},
            risk_level=risk,
            version=getattr(tool, "version", "0.1.0"),
            permissions=[f"category:{category.value}"],
        )

    @staticmethod
    def _extract_capabilities(tool: Any) -> list[str]:
        """Pull capabilities from a tool-like object.

        Looks for ``capabilities`` attribute first, then falls back to
        a single capability derived from the category.
        """
        caps = getattr(tool, "capabilities", None)
        if caps and isinstance(caps, (list, tuple)):
            return [str(c) for c in caps]
        cat = getattr(tool, "category", None)
        cat_value = getattr(cat, "value", str(cat)) if cat is not None else "custom"
        return [cat_value]

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def has_capability(self, capability: str) -> bool:
        """Return True if the tool exposes the given capability.

        Supports hierarchical matching: ``"filesystem"`` matches
        ``"filesystem.read"`` and ``"filesystem.write"``.
        """
        for cap in self.capabilities:
            if cap == capability:
                return True
            if cap.startswith(capability + "."):
                return True
            if capability.startswith(cap + "."):
                return True
        return False

    def supports_platform(self, platform: str) -> bool:
        """Return True if the tool supports the given platform.

        An empty ``supported_platforms`` list is treated as "all platforms".
        """
        if not self.supported_platforms:
            return True
        return platform.lower() in {p.lower() for p in self.supported_platforms}

    def matches_keyword(self, keyword: str) -> bool:
        """Return True if the keyword appears in name, aliases, tags or keywords."""
        kw = keyword.lower()
        if kw in self.name.lower():
            return True
        if kw in self.description.lower():
            return True
        if any(kw in a.lower() for a in self.aliases):
            return True
        if any(kw == t.lower() for t in self.tags):
            return True
        if any(kw == k.lower() for k in self.keywords):
            return True
        return False

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "capabilities": list(self.capabilities),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "risk_level": self.risk_level.value,
            "estimated_cost": self.estimated_cost,
            "estimated_latency": self.estimated_latency,
            "requires_confirmation": self.requires_confirmation,
            "supports_parallel": self.supports_parallel,
            "supported_platforms": list(self.supported_platforms),
            "aliases": list(self.aliases),
            "tags": list(self.tags),
            "keywords": list(self.keywords),
            "version": self.version,
            "permissions": list(self.permissions),
            "dependencies": list(self.dependencies),
            "required_model": self.required_model,
            "plugins": list(self.plugins),
            "reliability": self.reliability,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolMetadata":
        """Reconstruct a ToolMetadata from its serialized form."""
        # Coerce enum fields.
        category = data.get("category", "custom")
        if isinstance(category, str):
            category = ToolCategory(category)
        risk_level = data.get("risk_level", "low")
        if isinstance(risk_level, str):
            risk_level = RiskLevel(risk_level)
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            category=category,
            capabilities=list(data.get("capabilities", [])),
            input_schema=dict(data.get("input_schema", {})),
            output_schema=dict(data.get("output_schema", {})),
            risk_level=risk_level,
            estimated_cost=float(data.get("estimated_cost", 0.0)),
            estimated_latency=float(data.get("estimated_latency", 0.0)),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
            supports_parallel=bool(data.get("supports_parallel", True)),
            supported_platforms=list(data.get("supported_platforms", ["linux", "macos", "windows"])),
            aliases=list(data.get("aliases", [])),
            tags=list(data.get("tags", [])),
            keywords=list(data.get("keywords", [])),
            version=data.get("version", "0.1.0"),
            permissions=list(data.get("permissions", [])),
            dependencies=list(data.get("dependencies", [])),
            required_model=data.get("required_model"),
            plugins=list(data.get("plugins", [])),
            reliability=float(data.get("reliability", 1.0)),
            metadata=dict(data.get("metadata", {})),
        )


# ----------------------------------------------------------------------
# UserIntent & SelectionContext
# ----------------------------------------------------------------------


@dataclass
class UserIntent:
    """Lightweight description of what the user wants to accomplish.

    The selector uses this to match against tool capabilities and
    keywords. All fields are optional; the selector falls back to
    keyword matching when capabilities are not provided.
    """

    message: str = ""
    capabilities: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    preferred_category: ToolCategory | None = None
    max_risk_level: RiskLevel = RiskLevel.HIGH
    language: str = "en"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "capabilities": list(self.capabilities),
            "keywords": list(self.keywords),
            "preferred_category": (
                self.preferred_category.value if self.preferred_category else None
            ),
            "max_risk_level": self.max_risk_level.value,
            "language": self.language,
        }


@dataclass
class SelectionContext:
    """Runtime context that influences tool selection.

    Attributes:
        platform:          Current OS (linux, macos, windows).
        available_models:  Models currently loaded (for required_model check).
        granted_permissions: Permission tokens the user/session has.
        loaded_plugins:    Plugin identifiers currently loaded.
        max_cost:          Upper bound on estimated_cost.
        max_latency:       Upper bound on estimated_latency in ms.
        require_parallel:  If True, only tools with supports_parallel=True.
        history:           Optional mapping of tool_name -> ToolHistoryStats.
    """

    platform: str = "linux"
    available_models: list[str] = field(default_factory=list)
    granted_permissions: list[str] = field(default_factory=list)
    loaded_plugins: list[str] = field(default_factory=list)
    max_cost: float = 1.0
    max_latency: float = float("inf")
    require_parallel: bool = False
    history: dict[str, "ToolHistoryStats"] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "available_models": list(self.available_models),
            "granted_permissions": list(self.granted_permissions),
            "loaded_plugins": list(self.loaded_plugins),
            "max_cost": self.max_cost,
            "max_latency": self.max_latency,
            "require_parallel": self.require_parallel,
            "history": {k: v.to_dict() for k, v in self.history.items()},
        }


@dataclass
class ToolHistoryStats:
    """Historical execution stats for a tool.

    Attributes:
        executions:    Total invocations.
        successes:     Successful invocations.
        failures:      Failed invocations.
        avg_latency_ms: Average observed latency in milliseconds.
        avg_cost:      Average observed cost (relative units).
    """

    executions: int = 0
    successes: int = 0
    failures: int = 0
    avg_latency_ms: float = 0.0
    avg_cost: float = 0.0

    @property
    def success_rate(self) -> float:
        """Reliability ratio in [0.0, 1.0]."""
        if self.executions == 0:
            return 0.0
        return self.successes / self.executions

    def to_dict(self) -> dict[str, Any]:
        return {
            "executions": self.executions,
            "successes": self.successes,
            "failures": self.failures,
            "avg_latency_ms": self.avg_latency_ms,
            "avg_cost": self.avg_cost,
            "success_rate": self.success_rate,
        }


# ----------------------------------------------------------------------
# RankedCandidate
# ----------------------------------------------------------------------


@dataclass
class RankedCandidate:
    """A tool candidate paired with its ranking score and explanation.

    Attributes:
        metadata:        The ToolMetadata being ranked.
        score:           Final score in [0.0, 1.0] (higher is better).
        breakdown:       Per-factor contribution to the score.
        rank:            Position in the ranked list (1-indexed).
    """

    metadata: ToolMetadata
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.metadata.id,
            "tool_name": self.metadata.name,
            "score": round(self.score, 4),
            "rank": self.rank,
            "breakdown": {k: round(v, 4) for k, v in self.breakdown.items()},
            "metadata": self.metadata.to_dict(),
        }


__all__ = [
    "RankedCandidate",
    "RiskLevel",
    "SelectionContext",
    "ToolCategory",
    "ToolHistoryStats",
    "ToolMetadata",
    "UserIntent",
]
