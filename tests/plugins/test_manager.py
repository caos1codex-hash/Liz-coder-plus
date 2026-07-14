"""Tests for PluginManager (Sprint 2.5)."""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "multiagent", "src"))

from multiagent.plugins.base import Plugin
from multiagent.plugins.discovery import DiscoveredPlugin
from multiagent.plugins.enums import PluginEvent, PluginState
from multiagent.plugins.manifest import PluginManifest
from multiagent.plugins.manager import PluginManager, PluginRecord


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


class TestPlugin1(Plugin):
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="test-plugin-1",
            name="Test Plugin 1",
            version="1.0.0",
            capabilities=["test.capability"],
            entry_point="tests.plugins.test_manager:TestPlugin1",
        )


class TestPlugin2(Plugin):
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="test-plugin-2",
            name="Test Plugin 2",
            version="1.0.0",
            api_version="2.0.0",  # incompatible
            entry_point="tests.plugins.test_manager:TestPlugin2",
        )


class TestPluginWithDeps(Plugin):
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="test-plugin-deps",
            name="Test Plugin With Deps",
            version="1.0.0",
            dependencies=[
                {"id": "nonexistent", "minimum_version": "1.0.0"},
            ],
            entry_point="tests.plugins.test_manager:TestPluginWithDeps",
        )


class FailingPlugin(Plugin):
    """Plugin que falla al inicializar."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="failing-plugin",
            name="Failing Plugin",
            version="1.0.0",
            entry_point="tests.plugins.test_manager:FailingPlugin",
        )

    async def initialize(self) -> None:
        raise RuntimeError("Init failed!")


@pytest.fixture
def manager():
    return PluginManager(system_api_version="1.0.0")


@pytest.fixture
def discovered_plugin1():
    plugin = TestPlugin1()
    return DiscoveredPlugin(
        manifest=plugin.manifest(),
        entry_point="tests.plugins.test_manager:TestPlugin1",
        source_path="tests/plugins",
        discovery_type="test",
    )


# ----------------------------------------------------------------------
# Tests de creacion y propiedades
# ----------------------------------------------------------------------


class TestPluginManagerCreation:
    """Tests de creacion del PluginManager."""

    def test_create_manager(self, manager):
        assert manager.plugin_count == 0

    def test_create_manager_with_api_version(self):
        m = PluginManager(system_api_version="2.0.0")
        assert m.plugin_count == 0

    def test_event_bus_default(self, manager):
        assert manager.event_bus is not None

    def test_loader_accessible(self, manager):
        assert manager.loader is not None


# ----------------------------------------------------------------------
# Tests de load_plugin
# ----------------------------------------------------------------------


class TestPluginManagerLoad:
    """Tests de carga de plugins."""

    @pytest.mark.asyncio
    async def test_load_plugin_directly(self, manager):
        result = await manager.load_plugin(
            "tests.plugins.test_manager:TestPlugin1"
        )
        assert result.success is True
        assert result.manifest is not None
        assert result.manifest.id == "test-plugin-1"

    @pytest.mark.asyncio
    async def test_load_plugin_increases_count(self, manager):
        await manager.load_plugin(
            "tests.plugins.test_manager:TestPlugin1"
        )
        assert manager.plugin_count == 1

    @pytest.mark.asyncio
    async def test_load_duplicate_fails(self, manager):
        await manager.load_plugin(
            "tests.plugins.test_manager:TestPlugin1"
        )
        result = await manager.load_plugin(
            "tests.plugins.test_manager:TestPlugin1"
        )
        assert result.success is False
        assert "already loaded" in result.error

    @pytest.mark.asyncio
    async def test_load_invalid_entry_point(self, manager):
        result = await manager.load_plugin("nonexistent.module:Class")
        assert result.success is False


# ----------------------------------------------------------------------
# Tests de install_plugin
# ----------------------------------------------------------------------


class TestPluginManagerInstall:
    """Tests de instalacion de plugins."""

    @pytest.mark.asyncio
    async def test_install_plugin(self, manager, discovered_plugin1):
        result = await manager.install_plugin(discovered_plugin1)
        assert result.success is True
        assert manager.exists("test-plugin-1")

    @pytest.mark.asyncio
    async def test_install_duplicate_fails(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        result = await manager.install_plugin(discovered_plugin1)
        assert result.success is False
        assert "already installed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_install_incompatible_api_version(self, manager):
        plugin = TestPlugin2()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_manager:TestPlugin2",
            source_path="tests/plugins",
            discovery_type="test",
        )
        result = await manager.install_plugin(dp)
        assert result.success is False
        assert "incompatible" in result.error.lower() or "API version" in result.error

    @pytest.mark.asyncio
    async def test_install_missing_dependency(self, manager):
        plugin = TestPluginWithDeps()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_manager:TestPluginWithDeps",
            source_path="tests/plugins",
            discovery_type="test",
        )
        result = await manager.install_plugin(dp)
        assert result.success is False
        assert "dependency" in result.error.lower()


# ----------------------------------------------------------------------
# Tests de enable/disable
# ----------------------------------------------------------------------


class TestPluginManagerEnableDisable:
    """Tests de habilitacion y deshabilitacion."""

    @pytest.mark.asyncio
    async def test_enable_loaded_plugin(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        result = await manager.enable_plugin("test-plugin-1")
        assert result is True
        record = manager.get_record("test-plugin-1")
        assert record is not None
        assert record.enabled is True
        assert record.state == PluginState.ENABLED

    @pytest.mark.asyncio
    async def test_enable_nonexistent_plugin(self, manager):
        result = await manager.enable_plugin("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_disable_enabled_plugin(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        await manager.enable_plugin("test-plugin-1")
        result = await manager.disable_plugin("test-plugin-1")
        assert result is True
        record = manager.get_record("test-plugin-1")
        assert record is not None
        assert record.enabled is False
        assert record.state == PluginState.DISABLED

    @pytest.mark.asyncio
    async def test_disable_nonexistent_plugin(self, manager):
        result = await manager.disable_plugin("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_re_enable_disabled_plugin(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        await manager.enable_plugin("test-plugin-1")
        await manager.disable_plugin("test-plugin-1")
        result = await manager.enable_plugin("test-plugin-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_enable_failing_plugin(self, manager):
        plugin = FailingPlugin()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_manager:FailingPlugin",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        result = await manager.enable_plugin("failing-plugin")
        assert result is False
        record = manager.get_record("failing-plugin")
        assert record is not None
        assert record.state == PluginState.ERROR


# ----------------------------------------------------------------------
# Tests de unload/remove/reload
# ----------------------------------------------------------------------


class TestPluginManagerUnload:
    """Tests de descarga y remocion."""

    @pytest.mark.asyncio
    async def test_unload_loaded_plugin(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        result = await manager.unload_plugin("test-plugin-1")
        assert result is True
        assert not manager.exists("test-plugin-1")

    @pytest.mark.asyncio
    async def test_unload_enabled_plugin(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        await manager.enable_plugin("test-plugin-1")
        result = await manager.unload_plugin("test-plugin-1")
        assert result is True
        assert not manager.exists("test-plugin-1")

    @pytest.mark.asyncio
    async def test_unload_nonexistent(self, manager):
        result = await manager.unload_plugin("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_remove_plugin(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        result = await manager.remove_plugin("test-plugin-1")
        assert result is True
        assert not manager.exists("test-plugin-1")

    @pytest.mark.asyncio
    async def test_reload_plugin(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        await manager.enable_plugin("test-plugin-1")
        result = await manager.reload_plugin("test-plugin-1")
        assert result is True
        # Despues de reload debe seguir existiendo.
        assert manager.exists("test-plugin-1")


# ----------------------------------------------------------------------
# Tests de consultas
# ----------------------------------------------------------------------


class TestPluginManagerQueries:
    """Tests de consultas del manager."""

    @pytest.mark.asyncio
    async def test_list_plugins_empty(self, manager):
        assert manager.list_plugins() == []

    @pytest.mark.asyncio
    async def test_list_plugins_after_install(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        plugins = manager.list_plugins()
        assert len(plugins) == 1

    @pytest.mark.asyncio
    async def test_list_plugins_enabled_only(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        enabled = manager.list_plugins(enabled_only=True)
        assert len(enabled) == 0
        await manager.enable_plugin("test-plugin-1")
        enabled = manager.list_plugins(enabled_only=True)
        assert len(enabled) == 1

    @pytest.mark.asyncio
    async def test_get_plugin(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        plugin = manager.get_plugin("test-plugin-1")
        assert plugin is not None
        assert isinstance(plugin, Plugin)

    @pytest.mark.asyncio
    async def test_get_plugin_nonexistent(self, manager):
        assert manager.get_plugin("nonexistent") is None

    @pytest.mark.asyncio
    async def test_exists(self, manager, discovered_plugin1):
        assert manager.exists("test-plugin-1") is False
        await manager.install_plugin(discovered_plugin1)
        assert manager.exists("test-plugin-1") is True

    @pytest.mark.asyncio
    async def test_get_manifest(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        mf = manager.get_manifest("test-plugin-1")
        assert mf is not None
        assert mf.id == "test-plugin-1"


# ----------------------------------------------------------------------
# Tests de health y metricas
# ----------------------------------------------------------------------


class TestPluginManagerHealthMetrics:
    """Tests de health y metricas del manager."""

    def test_metrics_empty(self, manager):
        m = manager.metrics()
        assert m["total_plugins"] == 0
        assert m["enabled_plugins"] == 0
        assert m["total_loads"] == 0
        assert m["total_errors"] == 0

    def test_health_nonexistent(self, manager):
        h = manager.health("nonexistent")
        assert "error" in h

    @pytest.mark.asyncio
    async def test_metrics_after_install(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        m = manager.metrics()
        assert m["total_plugins"] == 1
        assert m["total_loads"] == 1

    @pytest.mark.asyncio
    async def test_health_single_plugin(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        h = manager.health("test-plugin-1")
        assert h["plugin_id"] == "test-plugin-1"
        assert h["healthy"] is True

    @pytest.mark.asyncio
    async def test_health_all_plugins(self, manager, discovered_plugin1):
        await manager.install_plugin(discovered_plugin1)
        h = manager.health()
        assert "test-plugin-1" in h