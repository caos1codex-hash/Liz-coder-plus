"""Tests for PluginDiscovery (Sprint 2.5)."""

import asyncio
import json
import os
import pytest
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "multiagent", "src"))

from multiagent.plugins.discovery import (
    DiscoveredPlugin,
    DiscoveryResult,
    PluginDiscovery,
)
from multiagent.plugins.manifest import PluginManifest


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def temp_plugin_dir(tmp_path):
    """Crea un directorio temporal con un plugin de prueba."""
    plugin_dir = tmp_path / "test_plugin"
    plugin_dir.mkdir()

    manifest_data = {
        "id": "test-discovery",
        "name": "Test Discovery Plugin",
        "version": "1.0.0",
        "entry_point": "test_discovery.plugin:TestDiscoveryPlugin",
        "capabilities": ["test.discovery"],
    }
    with open(plugin_dir / "manifest.json", "w") as f:
        json.dump(manifest_data, f)

    return tmp_path


@pytest.fixture
def empty_dir(tmp_path):
    """Crea un directorio vacio."""
    return tmp_path


@pytest.fixture
def discovery():
    return PluginDiscovery()


# ----------------------------------------------------------------------
# Tests de escaneo
# ----------------------------------------------------------------------


class TestPluginDiscoveryScan:
    """Tests del escaneo de descubrimiento."""

    @pytest.mark.asyncio
    async def test_scan_empty_dir(self, discovery, empty_dir):
        discovery.add_plugin_dir(str(empty_dir))
        result = await discovery.scan()
        assert len(result.discovered) == 0

    @pytest.mark.asyncio
    async def test_scan_finds_manifest_json(self, discovery, temp_plugin_dir):
        discovery.add_plugin_dir(str(temp_plugin_dir))
        result = await discovery.scan()
        assert len(result.discovered) == 1
        dp = result.discovered[0]
        assert dp.manifest.id == "test-discovery"
        assert dp.discovery_type == "manifest_json"

    @pytest.mark.asyncio
    async def test_scan_nonexistent_dir(self, discovery):
        discovery.add_plugin_dir("/nonexistent/path/12345")
        result = await discovery.scan()
        assert len(result.discovered) == 0

    @pytest.mark.asyncio
    async def test_scan_multiple_plugins(self, tmp_path, discovery):
        """Escanea un directorio con multiples plugins."""
        for i in range(3):
            plugin_dir = tmp_path / f"plugin_{i}"
            plugin_dir.mkdir()
            manifest = {
                "id": f"plugin-{i}",
                "name": f"Plugin {i}",
                "version": "1.0.0",
                "entry_point": f"plugin_{i}.plugin:Plugin{i}",
            }
            with open(plugin_dir / "manifest.json", "w") as f:
                json.dump(manifest, f)

        discovery.add_plugin_dir(str(tmp_path))
        result = await discovery.scan()
        assert len(result.discovered) == 3


# ----------------------------------------------------------------------
# Tests de manejo de errores
# ----------------------------------------------------------------------


class TestPluginDiscoveryErrors:
    """Tests de manejo de errores en el discovery."""

    @pytest.mark.asyncio
    async def test_invalid_manifest_json(self, tmp_path, discovery):
        """Un manifest JSON invalido se ignora."""
        plugin_dir = tmp_path / "bad_plugin"
        plugin_dir.mkdir()
        with open(plugin_dir / "manifest.json", "w") as f:
            f.write("not valid json {{{")

        discovery.add_plugin_dir(str(tmp_path))
        result = await discovery.scan()
        assert len(result.discovered) == 0

    @pytest.mark.asyncio
    async def test_invalid_manifest_data(self, tmp_path, discovery):
        """Un manifest con datos invalidos se ignora."""
        plugin_dir = tmp_path / "invalid_plugin"
        plugin_dir.mkdir()
        manifest = {
            "id": "INVALID-ID",  # uppercase not allowed
            "name": "Test",
            "version": "1.0.0",
        }
        with open(plugin_dir / "manifest.json", "w") as f:
            json.dump(manifest, f)

        discovery.add_plugin_dir(str(tmp_path))
        result = await discovery.scan()
        assert len(result.discovered) == 0
        # Debe haber un error reportado.
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_missing_manifest_fields(self, tmp_path, discovery):
        """Un manifest sin campos obligatorios se ignora."""
        plugin_dir = tmp_path / "incomplete"
        plugin_dir.mkdir()
        manifest = {"name": "Missing ID and version"}
        with open(plugin_dir / "manifest.json", "w") as f:
            json.dump(manifest, f)

        discovery.add_plugin_dir(str(tmp_path))
        result = await discovery.scan()
        assert len(result.discovered) == 0


# ----------------------------------------------------------------------
# Tests de DiscoveredPlugin
# ----------------------------------------------------------------------


class TestDiscoveredPlugin:
    """Tests del DiscoveredPlugin."""

    def test_to_dict(self):
        mf = PluginManifest(
            id="test", name="T", version="1.0.0",
            entry_point="mod:Cls",
        )
        dp = DiscoveredPlugin(
            manifest=mf,
            entry_point="mod:Cls",
            source_path="/tmp/plugins/test",
            discovery_type="manifest_json",
        )
        d = dp.to_dict()
        assert d["manifest"]["id"] == "test"
        assert d["entry_point"] == "mod:Cls"
        assert d["source_path"] == "/tmp/plugins/test"
        assert d["discovery_type"] == "manifest_json"


# ----------------------------------------------------------------------
# Tests de DiscoveryResult
# ----------------------------------------------------------------------


class TestDiscoveryResult:
    """Tests del DiscoveryResult."""

    def test_empty_result(self):
        r = DiscoveryResult()
        assert r.discovered == []
        assert r.errors == []
        assert r.scanned_paths == []
        assert r.duration_ms == 0.0

    def test_duration_ms(self):
        r = DiscoveryResult(duration_ms=42.5)
        assert r.duration_ms == 42.5


# ----------------------------------------------------------------------
# Tests de configuracion
# ----------------------------------------------------------------------


class TestPluginDiscoveryConfig:
    """Tests de configuracion del discovery."""

    def test_default_plugin_dirs(self):
        d = PluginDiscovery()
        assert "plugins" in d.plugin_dirs

    def test_custom_plugin_dirs(self):
        d = PluginDiscovery(plugin_dirs=["/custom/path"])
        assert d.plugin_dirs == ["/custom/path"]

    def test_add_plugin_dir(self):
        d = PluginDiscovery()
        d.add_plugin_dir("/new/path")
        assert "/new/path" in d.plugin_dirs

    def test_add_duplicate_dir_ignored(self):
        d = PluginDiscovery(plugin_dirs=["/path"])
        d.add_plugin_dir("/path")
        assert d.plugin_dirs.count("/path") == 1

    def test_metrics(self, discovery):
        m = discovery.metrics()
        assert "discovery_count" in m
        assert "plugin_dirs" in m