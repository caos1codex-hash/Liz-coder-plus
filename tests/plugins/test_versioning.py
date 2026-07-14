"""Tests for PluginCompatibilityChecker (Sprint 2.5)."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "multiagent", "src"))

from multiagent.plugins.manifest import PluginManifest
from multiagent.plugins.versioning import (
    CompatibilityResult,
    PluginCompatibilityChecker,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def checker():
    return PluginCompatibilityChecker(system_api_version="1.0.0")


@pytest.fixture
def compatible_manifest():
    return PluginManifest(
        id="compatible",
        name="Compatible",
        version="1.0.0",
        api_version="1.0.0",
    )


@pytest.fixture
def incompatible_manifest():
    return PluginManifest(
        id="incompatible",
        name="Incompatible",
        version="1.0.0",
        api_version="2.0.0",
    )


# ----------------------------------------------------------------------
# Tests de compatibilidad de API version
# ----------------------------------------------------------------------


class TestApiVersionCompatibility:
    """Tests de compatibilidad de version de API."""

    def test_same_major_compatible(self, checker, compatible_manifest):
        result = checker.check(compatible_manifest)
        assert result.compatible is True
        assert result.errors == []

    def test_different_major_incompatible(
        self, checker, incompatible_manifest,
    ):
        result = checker.check(incompatible_manifest)
        assert result.compatible is False
        assert len(result.errors) > 0
        assert "API version" in result.errors[0]

    def test_minor_version_compatible(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            api_version="1.5.0",
        )
        result = checker.check(m)
        assert result.compatible is True

    def test_patch_version_compatible(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            api_version="1.0.99",
        )
        result = checker.check(m)
        assert result.compatible is True

    def test_zero_major_compatible(self, checker):
        m = PluginManifest(
            id="test", name="T", version="0.1.0",
            api_version="0.5.0",
        )
        # 0.x vs 1.x son majors diferentes → incompatible.
        result = checker.check(m)
        assert result.compatible is False

    def test_system_api_version_property(self, checker):
        assert checker.system_api_version == "1.0.0"

    def test_custom_system_api_version(self):
        c = PluginCompatibilityChecker(system_api_version="2.0.0")
        assert c.system_api_version == "2.0.0"

    def test_custom_system_api_version_check(self):
        c = PluginCompatibilityChecker(system_api_version="2.0.0")
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            api_version="2.5.0",
        )
        result = c.check(m)
        assert result.compatible is True


# ----------------------------------------------------------------------
# Tests de dependencias
# ----------------------------------------------------------------------


class TestDependencyChecking:
    """Tests de verificacion de dependencias."""

    def test_no_dependencies_compatible(self, checker, compatible_manifest):
        result = checker.check(
            compatible_manifest,
            available_plugins={},
        )
        assert result.compatible is True

    def test_dependency_satisfied(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[{"id": "core", "minimum_version": "1.0.0"}],
        )
        result = checker.check(
            m,
            available_plugins={"core": "1.5.0"},
        )
        assert result.compatible is True

    def test_dependency_missing(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[{"id": "core", "minimum_version": "1.0.0"}],
        )
        result = checker.check(
            m,
            available_plugins={},
        )
        assert result.compatible is False
        assert any("Missing dependency" in e for e in result.errors)

    def test_dependency_version_too_low(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[{"id": "core", "minimum_version": "2.0.0"}],
        )
        result = checker.check(
            m,
            available_plugins={"core": "1.5.0"},
        )
        assert result.compatible is False
        assert any(
            "does not satisfy minimum_version" in e for e in result.errors
        )

    def test_dependency_max_version_exceeded(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[{"id": "core", "maximum_version": "1.5.0"}],
        )
        result = checker.check(
            m,
            available_plugins={"core": "2.0.0"},
        )
        assert result.compatible is False
        assert any(
            "exceeds maximum_version" in e for e in result.errors
        )

    def test_dependency_within_range(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[
                {"id": "core", "minimum_version": "1.0.0", "maximum_version": "2.0.0"},
            ],
        )
        result = checker.check(
            m,
            available_plugins={"core": "1.5.0"},
        )
        assert result.compatible is True

    def test_multiple_dependencies_all_satisfied(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[
                {"id": "core", "minimum_version": "1.0.0"},
                {"id": "utils", "minimum_version": "0.5.0"},
            ],
        )
        result = checker.check(
            m,
            available_plugins={"core": "1.0.0", "utils": "0.9.0"},
        )
        assert result.compatible is True

    def test_multiple_dependencies_one_missing(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[
                {"id": "core", "minimum_version": "1.0.0"},
                {"id": "utils", "minimum_version": "0.5.0"},
            ],
        )
        result = checker.check(
            m,
            available_plugins={"core": "1.0.0"},
        )
        assert result.compatible is False

    def test_dependency_without_version_constraint(self, checker):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[{"id": "core"}],
        )
        result = checker.check(
            m,
            available_plugins={"core": "0.1.0"},
        )
        assert result.compatible is True


# ----------------------------------------------------------------------
# Tests de CompatibilityResult
# ----------------------------------------------------------------------


class TestCompatibilityResult:
    """Tests del CompatibilityResult."""

    def test_compatible_result(self):
        r = CompatibilityResult(compatible=True, plugin_id="test")
        assert r.compatible is True
        assert r.plugin_id == "test"
        assert r.errors == []
        assert r.warnings == []

    def test_incompatible_result(self):
        r = CompatibilityResult(
            compatible=False,
            plugin_id="test",
            errors=["error 1", "error 2"],
            warnings=["warning 1"],
        )
        assert r.compatible is False
        assert len(r.errors) == 2
        assert len(r.warnings) == 1

    def test_frozen(self):
        r = CompatibilityResult(compatible=True, plugin_id="test")
        with pytest.raises(AttributeError):
            r.compatible = False  # type: ignore[misc]


# ----------------------------------------------------------------------
# Tests de parseo de versiones
# ----------------------------------------------------------------------


class TestVersionParsing:
    """Tests de parseo de versiones internas."""

    def test_parse_major(self):
        assert PluginCompatibilityChecker._parse_major("1.0.0") == 1
        assert PluginCompatibilityChecker._parse_major("2.5.3") == 2
        assert PluginCompatibilityChecker._parse_major("0.1.0") == 0

    def test_version_gte(self):
        c = PluginCompatibilityChecker()
        assert c._version_gte("1.5.0", "1.0.0") is True
        assert c._version_gte("1.0.0", "1.0.0") is True
        assert c._version_gte("0.9.0", "1.0.0") is False

    def test_version_lte(self):
        c = PluginCompatibilityChecker()
        assert c._version_lte("1.0.0", "1.5.0") is True
        assert c._version_lte("1.0.0", "1.0.0") is True
        assert c._version_lte("2.0.0", "1.5.0") is False

    def test_parse_version_tuple(self):
        c = PluginCompatibilityChecker()
        assert c._parse_version_tuple("1.2.3") == (1, 2, 3)
        assert c._parse_version_tuple("1.2") == (1, 2)
        # Pre-release strips to numeric prefix.
        assert c._parse_version_tuple("1.0.0-alpha.1") == (1, 0, 0, 1)