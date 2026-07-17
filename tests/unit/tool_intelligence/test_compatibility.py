"""Tests for tool_intelligence.compatibility.

Sprint 4.1 — Tool Intelligence Foundation.
"""

from __future__ import annotations

import pytest
from tool_intelligence import (
    CompatibilityChecker,
    CompatibilityReport,
    SelectionContext,
    ToolMetadata,
    ToolNotCompatible,
)
from tool_intelligence.compatibility import binary_dependency_resolver

# ----------------------------------------------------------------------
# binary_dependency_resolver
# ----------------------------------------------------------------------


class TestBinaryDependencyResolver:
    def test_resolves_existing_binary(self) -> None:
        # 'python' or 'sh' should always be on PATH on POSIX.
        ctx = SelectionContext()
        # Try a few common binaries; at least one should be present.
        candidates = ["python3", "python", "sh", "bash", "ls"]
        found = [c for c in candidates if binary_dependency_resolver(c, ctx)]
        assert len(found) > 0, "Expected at least one common binary on PATH"

    def test_missing_binary_returns_false(self) -> None:
        ctx = SelectionContext()
        assert binary_dependency_resolver("nonexistent_bin_xyz", ctx) is False


# ----------------------------------------------------------------------
# CompatibilityReport
# ----------------------------------------------------------------------


class TestCompatibilityReport:
    def test_default_construction(self) -> None:
        report = CompatibilityReport(tool_id="t1", tool_name="t1")
        assert report.tool_id == "t1"
        assert report.tool_name == "t1"
        assert report.compatible is True
        assert report.checks == {}
        assert report.failures == []

    def test_to_dict(self) -> None:
        report = CompatibilityReport(
            tool_id="t1",
            tool_name="t1",
            compatible=False,
            checks={"platform": False},
            failures=["platform 'macos' not in ['linux']"],
        )
        d = report.to_dict()
        assert d["tool_id"] == "t1"
        assert d["tool_name"] == "t1"
        assert d["compatible"] is False
        assert d["checks"] == {"platform": False}
        assert d["failures"] == ["platform 'macos' not in ['linux']"]


# ----------------------------------------------------------------------
# CompatibilityChecker — individual checks
# ----------------------------------------------------------------------


class TestPlatformCheck:
    def test_supported_platform(self, file_tool_meta: ToolMetadata) -> None:
        checker = CompatibilityChecker()
        # Provide the permissions the tool needs so the only variable
        # under test is platform compatibility.
        ctx = SelectionContext(
            platform="linux",
            granted_permissions=list(file_tool_meta.permissions),
        )
        report = checker.check(file_tool_meta, ctx)
        assert report.checks["platform"] is True
        assert report.compatible is True

    def test_unsupported_platform(self, file_tool_meta: ToolMetadata) -> None:
        checker = CompatibilityChecker()
        # Restrict platforms.
        meta = ToolMetadata(
            id=file_tool_meta.id,
            name=file_tool_meta.name,
            description=file_tool_meta.description,
            supported_platforms=["windows"],
        )
        ctx = SelectionContext(platform="linux")
        report = checker.check(meta, ctx)
        assert report.checks["platform"] is False
        assert report.compatible is False
        assert any("platform" in f for f in report.failures)


class TestDependenciesCheck:
    def test_no_dependencies_passes(self, file_tool_meta: ToolMetadata) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext()
        report = checker.check(file_tool_meta, ctx)
        assert report.checks["dependencies"] is True

    def test_satisfied_dependency(
        self, shell_tool_meta: ToolMetadata
    ) -> None:
        # shell_tool_meta depends on "bash".
        # On the test machine bash is usually available, but to be
        # deterministic we install a stub resolver.
        checker = CompatibilityChecker(
            dependency_resolver=lambda dep, ctx: dep == "bash"
        )
        ctx = SelectionContext()
        report = checker.check(shell_tool_meta, ctx)
        assert report.checks["dependencies"] is True

    def test_missing_dependency(
        self, shell_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker(
            dependency_resolver=lambda dep, ctx: False
        )
        ctx = SelectionContext()
        report = checker.check(shell_tool_meta, ctx)
        assert report.checks["dependencies"] is False
        assert any("missing dependencies" in f for f in report.failures)

    def test_dependency_resolver_raises_treated_as_missing(
        self, shell_tool_meta: ToolMetadata
    ) -> None:
        def bad_resolver(dep: str, ctx: SelectionContext) -> bool:
            raise RuntimeError("kaboom")

        checker = CompatibilityChecker(dependency_resolver=bad_resolver)
        ctx = SelectionContext()
        report = checker.check(shell_tool_meta, ctx)
        assert report.checks["dependencies"] is False


class TestPluginsCheck:
    def test_no_plugins_passes(self, file_tool_meta: ToolMetadata) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext()
        report = checker.check(file_tool_meta, ctx)
        assert report.checks["plugins"] is True

    def test_satisfied_plugin(
        self, plugin_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(loaded_plugins=["postgres"])
        report = checker.check(plugin_tool_meta, ctx)
        assert report.checks["plugins"] is True

    def test_missing_plugin(
        self, plugin_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(loaded_plugins=[])
        report = checker.check(plugin_tool_meta, ctx)
        assert report.checks["plugins"] is False
        assert any("missing plugins" in f for f in report.failures)

    def test_plugin_case_insensitive(
        self, plugin_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(loaded_plugins=["Postgres"])
        report = checker.check(plugin_tool_meta, ctx)
        assert report.checks["plugins"] is True


class TestPermissionsCheck:
    def test_no_permissions_passes(self, file_tool_meta: ToolMetadata) -> None:
        # file_tool_meta.permissions = ["fs:read"] — non-empty.
        # Build a fresh tool with no permissions.
        meta = ToolMetadata(id="t", name="t", permissions=[])
        checker = CompatibilityChecker()
        ctx = SelectionContext()
        report = checker.check(meta, ctx)
        assert report.checks["permissions"] is True

    def test_satisfied_permission(
        self, file_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(granted_permissions=["fs:read"])
        report = checker.check(file_tool_meta, ctx)
        assert report.checks["permissions"] is True

    def test_missing_permission(
        self, file_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(granted_permissions=[])
        report = checker.check(file_tool_meta, ctx)
        assert report.checks["permissions"] is False
        assert any("missing permissions" in f for f in report.failures)

    def test_permission_case_insensitive(
        self, file_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(granted_permissions=["FS:READ"])
        report = checker.check(file_tool_meta, ctx)
        assert report.checks["permissions"] is True


class TestRequiredModelCheck:
    def test_no_required_model_passes(self, file_tool_meta: ToolMetadata) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext()
        report = checker.check(file_tool_meta, ctx)
        assert report.checks["required_model"] is True

    def test_satisfied_model(
        self, llm_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(available_models=["gpt-4"])
        report = checker.check(llm_tool_meta, ctx)
        assert report.checks["required_model"] is True

    def test_missing_model(
        self, llm_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(available_models=["gpt-3.5"])
        report = checker.check(llm_tool_meta, ctx)
        assert report.checks["required_model"] is False
        assert any("required model" in f for f in report.failures)

    def test_model_case_insensitive(
        self, llm_tool_meta: ToolMetadata
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(available_models=["GPT-4"])
        report = checker.check(llm_tool_meta, ctx)
        assert report.checks["required_model"] is True


# ----------------------------------------------------------------------
# CompatibilityChecker — combined behavior
# ----------------------------------------------------------------------


class TestCompatibilityCheckerCombined:
    def test_all_checks_pass(
        self,
        file_tool_meta: ToolMetadata,
        default_context: SelectionContext,
    ) -> None:
        checker = CompatibilityChecker(
            dependency_resolver=lambda dep, ctx: True
        )
        report = checker.check(file_tool_meta, default_context)
        assert report.compatible is True
        assert all(report.checks.values())
        assert report.failures == []

    def test_multiple_failures(
        self,
        llm_tool_meta: ToolMetadata,
    ) -> None:
        # llm_tool_meta requires gpt-4 model and "llm:call" permission.
        meta = ToolMetadata(
            id=llm_tool_meta.id,
            name=llm_tool_meta.name,
            description=llm_tool_meta.description,
            required_model="gpt-4",
            permissions=["llm:call"],
            plugins=["myplugin"],
            dependencies=["mydep"],
        )
        checker = CompatibilityChecker(
            dependency_resolver=lambda dep, ctx: False
        )
        ctx = SelectionContext(
            platform="linux",
            available_models=[],
            granted_permissions=[],
            loaded_plugins=[],
        )
        report = checker.check(meta, ctx)
        assert report.compatible is False
        assert report.checks["required_model"] is False
        assert report.checks["permissions"] is False
        assert report.checks["plugins"] is False
        assert report.checks["dependencies"] is False
        assert len(report.failures) == 4


class TestCheckOrRaise:
    def test_compatible_does_not_raise(
        self,
        file_tool_meta: ToolMetadata,
        default_context: SelectionContext,
    ) -> None:
        checker = CompatibilityChecker()
        report = checker.check_or_raise(file_tool_meta, default_context)
        assert report.compatible is True

    def test_incompatible_raises(
        self,
        llm_tool_meta: ToolMetadata,
    ) -> None:
        checker = CompatibilityChecker()
        ctx = SelectionContext(available_models=[])
        with pytest.raises(ToolNotCompatible, match="not compatible"):
            checker.check_or_raise(llm_tool_meta, ctx)


class TestFilterCompatible:
    def test_filter_returns_only_compatible(
        self,
        file_tool_meta: ToolMetadata,
        llm_tool_meta: ToolMetadata,
        default_context: SelectionContext,
    ) -> None:
        # Remove llm:call from granted permissions to make llm_tool_meta incompatible.
        ctx = SelectionContext(
            platform=default_context.platform,
            available_models=[],  # no gpt-4 -> llm_tool_meta fails
            granted_permissions=default_context.granted_permissions,
            loaded_plugins=default_context.loaded_plugins,
        )
        checker = CompatibilityChecker()
        results = checker.filter_compatible(
            [file_tool_meta, llm_tool_meta], ctx
        )
        # file_tool_meta should be compatible, llm_tool_meta should not.
        compatible_names = [m.name for m, _ in results]
        assert "file_read" in compatible_names
        assert "llm_summarize" not in compatible_names

    def test_filter_empty_input(self, default_context: SelectionContext) -> None:
        checker = CompatibilityChecker()
        assert checker.filter_compatible([], default_context) == []


# ----------------------------------------------------------------------
# Caching
# ----------------------------------------------------------------------


class TestCompatibilityCheckerCache:
    def test_same_result_for_same_inputs(
        self,
        file_tool_meta: ToolMetadata,
        default_context: SelectionContext,
    ) -> None:
        checker = CompatibilityChecker()
        r1 = checker.check(file_tool_meta, default_context)
        r2 = checker.check(file_tool_meta, default_context)
        # Same cached object.
        assert r1 is r2

    def test_disable_cache_always_recomputes(
        self,
        file_tool_meta: ToolMetadata,
        default_context: SelectionContext,
    ) -> None:
        checker = CompatibilityChecker()
        r1 = checker.check(file_tool_meta, default_context, use_cache=False)
        r2 = checker.check(file_tool_meta, default_context, use_cache=False)
        assert r1 is not r2
        assert r1.compatible == r2.compatible

    def test_cache_invalidated_on_different_platform(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        checker = CompatibilityChecker()
        ctx1 = SelectionContext(platform="linux")
        ctx2 = SelectionContext(platform="macos")
        r1 = checker.check(file_tool_meta, ctx1)
        r2 = checker.check(file_tool_meta, ctx2)
        # Different cache keys -> different objects.
        assert r1 is not r2

    def test_clear_cache(
        self,
        file_tool_meta: ToolMetadata,
        default_context: SelectionContext,
    ) -> None:
        checker = CompatibilityChecker()
        r1 = checker.check(file_tool_meta, default_context)
        checker.clear_cache()
        r2 = checker.check(file_tool_meta, default_context)
        assert r1 is not r2
