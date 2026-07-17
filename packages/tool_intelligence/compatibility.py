"""Compatibility Checker for the Tool Intelligence package.

Sprint 4.1 — Tool Intelligence Foundation.

The ``CompatibilityChecker`` decides whether a tool can actually run
in the current environment before the selector/ranker consider it. It
verifies:

  - **OS / platform**:        the tool's ``supported_platforms`` includes
                              the runtime platform.
  - **Dependencies**:         every entry in ``dependencies`` is
                              resolvable (via a pluggable resolver).
  - **Plugins**:              every entry in ``plugins`` is present in
                              the runtime's ``loaded_plugins`` list.
  - **Permissions**:          every entry in ``permissions`` is in the
                              session's ``granted_permissions`` list.
  - **Required model**:       ``required_model`` (if set) is present
                              in ``available_models``.

The checker returns a structured :class:`CompatibilityReport` rather
than raising, so callers can decide whether to skip the tool, ask
the user for the missing permission, or fail the request.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable

from .exceptions import ToolNotCompatible
from .models import SelectionContext, ToolMetadata

logger = logging.getLogger(__name__)

# A dependency resolver returns True if the dependency is satisfied.
DependencyResolver = Callable[[str, SelectionContext], bool]


def binary_dependency_resolver(dependency: str, _ctx: SelectionContext) -> bool:
    """Default resolver: check if ``dependency`` is an executable on PATH.

    The check is intentionally simple: ``shutil.which`` returns the
    path to the binary or ``None``. We treat a non-empty result as
    "satisfied". This avoids importing heavy package metadata libs.
    """
    return shutil.which(dependency) is not None


@dataclass
class CompatibilityReport:
    """Structured result of a compatibility check.

    Attributes:
        tool_id:      The id of the checked tool.
        tool_name:    The name of the checked tool.
        compatible:   True if every check passed.
        checks:       Per-check result map (check_name -> bool).
        failures:     List of human-readable failure messages.
    """

    tool_id: str
    tool_name: str
    compatible: bool = True
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "compatible": self.compatible,
            "checks": dict(self.checks),
            "failures": list(self.failures),
        }


class CompatibilityChecker:
    """Verify that a tool can run in the current environment.

    Args:
        dependency_resolver: Callable used to verify each entry in a
            tool's ``dependencies`` list. Defaults to
            :func:`binary_dependency_resolver`, which uses
            ``shutil.which``.
    """

    def __init__(
        self,
        *,
        dependency_resolver: DependencyResolver = binary_dependency_resolver,
    ) -> None:
        self._resolve_dep = dependency_resolver
        # Cache of recent reports to avoid re-checking the same tool
        # in tight loops. Keyed by (tool_id, platform, plugins, perms, models).
        self._cache: dict[
            tuple[str, str, frozenset[str], frozenset[str], frozenset[str]],
            CompatibilityReport,
        ] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        metadata: ToolMetadata,
        ctx: SelectionContext,
        *,
        use_cache: bool = True,
    ) -> CompatibilityReport:
        """Run every compatibility check for ``metadata`` under ``ctx``."""
        cache_key = (
            metadata.id,
            ctx.platform,
            frozenset(ctx.loaded_plugins),
            frozenset(ctx.granted_permissions),
            frozenset(ctx.available_models),
        )
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        report = CompatibilityReport(
            tool_id=metadata.id,
            tool_name=metadata.name,
        )

        self._check_platform(metadata, ctx, report)
        self._check_dependencies(metadata, ctx, report)
        self._check_plugins(metadata, ctx, report)
        self._check_permissions(metadata, ctx, report)
        self._check_required_model(metadata, ctx, report)

        report.compatible = all(report.checks.values())
        if use_cache:
            self._cache[cache_key] = report
        return report

    def check_or_raise(
        self,
        metadata: ToolMetadata,
        ctx: SelectionContext,
    ) -> CompatibilityReport:
        """Like :meth:`check` but raises ``ToolNotCompatible`` on failure."""
        report = self.check(metadata, ctx)
        if not report.compatible:
            raise ToolNotCompatible(
                f"Tool '{metadata.name}' is not compatible: "
                f"{'; '.join(report.failures)}",
                details={
                    "tool_id": metadata.id,
                    "tool_name": metadata.name,
                    "failures": report.failures,
                    "checks": report.checks,
                },
            )
        return report

    def filter_compatible(
        self,
        tools: list[ToolMetadata],
        ctx: SelectionContext,
    ) -> list[tuple[ToolMetadata, CompatibilityReport]]:
        """Filter a list of tools, returning the compatible ones with reports."""
        results: list[tuple[ToolMetadata, CompatibilityReport]] = []
        for meta in tools:
            report = self.check(meta, ctx)
            if report.compatible:
                results.append((meta, report))
        return results

    def clear_cache(self) -> None:
        """Drop all cached compatibility reports."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_platform(
        self,
        meta: ToolMetadata,
        ctx: SelectionContext,
        report: CompatibilityReport,
    ) -> None:
        ok = meta.supports_platform(ctx.platform)
        report.checks["platform"] = ok
        if not ok:
            report.failures.append(
                f"platform '{ctx.platform}' not in {meta.supported_platforms}"
            )

    def _check_dependencies(
        self,
        meta: ToolMetadata,
        ctx: SelectionContext,
        report: CompatibilityReport,
    ) -> None:
        if not meta.dependencies:
            report.checks["dependencies"] = True
            return
        missing: list[str] = []
        for dep in meta.dependencies:
            try:
                satisfied = self._resolve_dep(dep, ctx)
            except Exception:  # noqa: BLE001
                logger.exception("Dependency resolver raised for '%s'", dep)
                satisfied = False
            if not satisfied:
                missing.append(dep)
        ok = not missing
        report.checks["dependencies"] = ok
        if not ok:
            report.failures.append(f"missing dependencies: {missing}")

    def _check_plugins(
        self,
        meta: ToolMetadata,
        ctx: SelectionContext,
        report: CompatibilityReport,
    ) -> None:
        if not meta.plugins:
            report.checks["plugins"] = True
            return
        loaded = {p.lower() for p in ctx.loaded_plugins}
        missing = [p for p in meta.plugins if p.lower() not in loaded]
        ok = not missing
        report.checks["plugins"] = ok
        if not ok:
            report.failures.append(f"missing plugins: {missing}")

    def _check_permissions(
        self,
        meta: ToolMetadata,
        ctx: SelectionContext,
        report: CompatibilityReport,
    ) -> None:
        if not meta.permissions:
            report.checks["permissions"] = True
            return
        granted = {p.lower() for p in ctx.granted_permissions}
        missing = [p for p in meta.permissions if p.lower() not in granted]
        ok = not missing
        report.checks["permissions"] = ok
        if not ok:
            report.failures.append(f"missing permissions: {missing}")

    def _check_required_model(
        self,
        meta: ToolMetadata,
        ctx: SelectionContext,
        report: CompatibilityReport,
    ) -> None:
        if meta.required_model is None:
            report.checks["required_model"] = True
            return
        available = {m.lower() for m in ctx.available_models}
        ok = meta.required_model.lower() in available
        report.checks["required_model"] = ok
        if not ok:
            report.failures.append(
                f"required model '{meta.required_model}' not in {ctx.available_models}"
            )


__all__ = [
    "CompatibilityChecker",
    "CompatibilityReport",
    "DependencyResolver",
    "binary_dependency_resolver",
]
