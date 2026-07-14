"""Tests for PluginLoader (Sprint 2.5)."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "multiagent", "src"))

from multiagent.plugins.loader import PluginLoader, LoadResult
from multiagent.plugins.base import Plugin
from multiagent.plugins.manifest import PluginManifest


# ----------------------------------------------------------------------
# Plugin de prueba accesible como modulo
# ----------------------------------------------------------------------


class LoaderTestPlugin(Plugin):
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="loader-test",
            name="Loader Test",
            version="1.0.0",
            entry_point="tests.plugins.test_loader:LoaderTestPlugin",
        )


@pytest.fixture
def loader():
    return PluginLoader()


# ----------------------------------------------------------------------
# Tests de carga exitosa
# ----------------------------------------------------------------------


class TestPluginLoaderSuccess:
    """Tests de carga exitosa."""

    def test_load_existing_plugin(self, loader):
        result = loader.load(
            "tests.plugins.test_loader:LoaderTestPlugin"
        )
        assert result.success is True
        assert result.plugin is not None
        assert result.manifest is not None
        assert result.manifest.id == "loader-test"

    def test_load_returns_plugin_instance(self, loader):
        result = loader.load(
            "tests.plugins.test_loader:LoaderTestPlugin"
        )
        assert isinstance(result.plugin, Plugin)

    def test_load_returns_manifest(self, loader):
        result = loader.load(
            "tests.plugins.test_loader:LoaderTestPlugin"
        )
        assert isinstance(result.manifest, PluginManifest)

    def test_load_duration_ms(self, loader):
        result = loader.load(
            "tests.plugins.test_loader:LoaderTestPlugin"
        )
        assert result.duration_ms >= 0

    def test_load_count_increments(self, loader):
        assert loader.load_count == 0
        loader.load("tests.plugins.test_loader:LoaderTestPlugin")
        assert loader.load_count == 1


# ----------------------------------------------------------------------
# Tests de carga fallida
# ----------------------------------------------------------------------


class TestPluginLoaderFailure:
    """Tests de carga fallida."""

    def test_load_nonexistent_module(self, loader):
        result = loader.load("nonexistent.module:SomeClass")
        assert result.success is False
        assert "Failed to import" in result.error or "No module" in result.error
        assert result.plugin is None
        assert result.manifest is None

    def test_load_nonexistent_class(self, loader):
        result = loader.load("tests.plugins.test_loader:NonexistentClass")
        assert result.success is False
        assert "not found" in result.error

    def test_load_non_plugin_class(self, loader):
        result = loader.load("tests.plugins.test_loader:NotAPlugin")
        assert result.success is False
        assert "not a Plugin" in result.error

    def test_load_no_colon(self, loader):
        result = loader.load("just_a_module")
        assert result.success is False
        assert "Invalid entry_point" in result.error

    def test_load_empty_string(self, loader):
        result = loader.load("")
        assert result.success is False

    def test_error_count_increments_on_failure(self, loader):
        loader.load("nonexistent.module:Class")
        assert loader.error_count == 1
        loader.load("another.nonexistent:Class")
        assert loader.error_count == 2


# ----------------------------------------------------------------------
# Tests de metricas
# ----------------------------------------------------------------------


class TestPluginLoaderMetrics:
    """Tests de metricas del loader."""

    def test_metrics_initial(self, loader):
        m = loader.metrics()
        assert m["load_count"] == 0
        assert m["error_count"] == 0
        assert m["success_rate"] == 0.0

    def test_metrics_after_load(self, loader):
        loader.load("tests.plugins.test_loader:LoaderTestPlugin")
        m = loader.metrics()
        assert m["load_count"] == 1
        assert m["error_count"] == 0
        assert m["success_rate"] == 1.0

    def test_metrics_mixed(self, loader):
        loader.load("tests.plugins.test_loader:LoaderTestPlugin")
        loader.load("nonexistent.module:Class")
        m = loader.metrics()
        assert m["load_count"] == 1
        assert m["error_count"] == 1
        assert m["success_rate"] == 0.5


# ----------------------------------------------------------------------
# Tests de serializacion
# ----------------------------------------------------------------------


class TestLoadResult:
    """Tests del LoadResult."""

    def test_success_to_dict(self, loader):
        result = loader.load(
            "tests.plugins.test_loader:LoaderTestPlugin"
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["plugin_id"] == "loader-test"
        assert d["plugin_name"] == "Loader Test"
        assert "duration_ms" in d

    def test_failure_to_dict(self, loader):
        result = loader.load("nonexistent.module:Class")
        d = result.to_dict()
        assert d["success"] is False
        assert d["plugin_id"] is None
        assert d["error"] != ""


# Dummy class that is NOT a Plugin.
class NotAPlugin:
    pass