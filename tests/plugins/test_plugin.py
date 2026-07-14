"""Tests for Plugin base class (Sprint 2.5)."""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "multiagent", "src"))

from multiagent.plugins.base import Plugin, PluginHealth
from multiagent.plugins.enums import PluginState, can_transition_plugin
from multiagent.plugins.manifest import PluginManifest


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


class SimplePlugin(Plugin):
    """Plugin de prueba simple."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="simple",
            name="Simple Plugin",
            version="1.0.0",
        )


class FullPlugin(Plugin):
    """Plugin de prueba con todos los metodos override."""

    def __init__(self) -> None:
        super().__init__()
        self.initialize_called = False
        self.start_called = False
        self.stop_called = False
        self.shutdown_called = False

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="full",
            name="Full Plugin",
            version="1.0.0",
            capabilities=["test.capability"],
        )

    async def initialize(self) -> None:
        self.initialize_called = True
        await super().initialize()

    async def start(self) -> None:
        self.start_called = True
        await super().start()

    async def stop(self) -> None:
        self.stop_called = True
        await super().stop()

    async def shutdown(self) -> None:
        self.shutdown_called = True
        await super().shutdown()


@pytest.fixture
def simple_plugin():
    return SimplePlugin()


@pytest.fixture
def full_plugin():
    return FullPlugin()


# ----------------------------------------------------------------------
# Tests de estado
# ----------------------------------------------------------------------


class TestPluginState:
    """Tests del estado del plugin."""

    def test_initial_state_is_discovered(self, simple_plugin):
        assert simple_plugin.state == PluginState.DISCOVERED

    def test_state_setter(self, simple_plugin):
        simple_plugin.state = PluginState.LOADED
        assert simple_plugin.state == PluginState.LOADED

    def test_is_enabled_initially_false(self, simple_plugin):
        assert simple_plugin.is_enabled is False

    def test_is_initialized_initially_false(self, simple_plugin):
        assert simple_plugin.is_initialized is False

    def test_uptime_s(self, simple_plugin):
        assert simple_plugin.uptime_s >= 0

    def test_errors_initially_empty(self, simple_plugin):
        assert simple_plugin.errors == []
        assert simple_plugin.last_error is None
        assert simple_plugin.error_count == 0


class TestPluginTransitions:
    """Tests de transiciones de estado."""

    def test_discovered_to_loaded(self):
        assert can_transition_plugin(PluginState.DISCOVERED, PluginState.LOADED) is True

    def test_discovered_to_unloaded(self):
        assert can_transition_plugin(PluginState.DISCOVERED, PluginState.UNLOADED) is True

    def test_discovered_to_enabled_directly_false(self):
        assert can_transition_plugin(PluginState.DISCOVERED, PluginState.ENABLED) is False

    def test_unloaded_is_terminal(self):
        assert can_transition_plugin(PluginState.UNLOADED, PluginState.LOADED) is False

    def test_error_to_loaded_recovery(self):
        assert can_transition_plugin(PluginState.ERROR, PluginState.LOADED) is True

    def test_enabled_to_disabled(self):
        assert can_transition_plugin(PluginState.ENABLED, PluginState.DISABLED) is True

    def test_disabled_to_enabled(self):
        assert can_transition_plugin(PluginState.DISABLED, PluginState.ENABLED) is True

    def test_enabled_to_unloaded(self):
        assert can_transition_plugin(PluginState.ENABLED, PluginState.UNLOADED) is True

    def test_all_states_have_transitions(self):
        """Todos los estados deben tener transiciones definidas."""
        for state in PluginState:
            transitions = can_transition_plugin(state, PluginState.LOADED)
            # No verificar que sea True, solo que no lance.
            _ = transitions


class TestPluginLifecycle:
    """Tests del ciclo de vida del plugin."""

    @pytest.mark.asyncio
    async def test_initialize_default(self, simple_plugin):
        await simple_plugin.initialize()
        assert simple_plugin.is_initialized is True

    @pytest.mark.asyncio
    async def test_initialize_override(self, full_plugin):
        await full_plugin.initialize()
        assert full_plugin.initialize_called is True
        assert full_plugin.is_initialized is True

    @pytest.mark.asyncio
    async def test_start_default(self, simple_plugin):
        await simple_plugin.start()
        assert simple_plugin.is_enabled is False  # start != enable

    @pytest.mark.asyncio
    async def test_start_override(self, full_plugin):
        await full_plugin.start()
        assert full_plugin.start_called is True

    @pytest.mark.asyncio
    async def test_stop_default(self, simple_plugin):
        await simple_plugin.stop()

    @pytest.mark.asyncio
    async def test_stop_override(self, full_plugin):
        await full_plugin.stop()
        assert full_plugin.stop_called is True

    @pytest.mark.asyncio
    async def test_shutdown_default(self, simple_plugin):
        await simple_plugin.shutdown()
        assert simple_plugin.is_initialized is False
        assert simple_plugin.is_enabled is False

    @pytest.mark.asyncio
    async def test_shutdown_override(self, full_plugin):
        await full_plugin.shutdown()
        assert full_plugin.shutdown_called is True

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, full_plugin):
        await full_plugin.initialize()
        await full_plugin.start()
        await full_plugin.stop()
        await full_plugin.shutdown()
        assert full_plugin.initialize_called is True
        assert full_plugin.start_called is True
        assert full_plugin.stop_called is True
        assert full_plugin.shutdown_called is True


class TestPluginAgentsAndTools:
    """Tests de agentes y herramientas del plugin."""

    def test_get_agents_default_empty(self, simple_plugin):
        assert simple_plugin.get_agents() == []

    def test_get_tools_default_empty(self, simple_plugin):
        assert simple_plugin.get_tools() == []


class TestPluginHealth:
    """Tests de health check."""

    def test_health_check_discovered(self, simple_plugin):
        health = simple_plugin.health_check()
        assert isinstance(health, PluginHealth)
        assert health.plugin_id == "simple"
        assert health.plugin_name == "Simple Plugin"
        assert health.state == PluginState.DISCOVERED
        assert health.healthy is True
        assert health.agents_count == 0
        assert health.tools_count == 0

    def test_health_check_initialized(self, simple_plugin):
        simple_plugin.state = PluginState.INITIALIZED
        simple_plugin._initialized = True
        health = simple_plugin.health_check()
        assert health.state == PluginState.INITIALIZED
        assert health.healthy is True

    def test_health_check_error(self, simple_plugin):
        simple_plugin.state = PluginState.ERROR
        health = simple_plugin.health_check()
        assert health.healthy is False

    def test_health_check_unloaded(self, simple_plugin):
        simple_plugin.state = PluginState.UNLOADED
        health = simple_plugin.health_check()
        assert health.healthy is False

    def test_health_to_dict(self, simple_plugin):
        d = simple_plugin.health_check().to_dict()
        assert "plugin_id" in d
        assert "healthy" in d
        assert "state" in d


class TestPluginMetrics:
    """Tests de metricas."""

    def test_metrics(self, simple_plugin):
        m = simple_plugin.metrics()
        assert m["id"] == "simple"
        assert m["state"] == "discovered"
        assert m["initialized"] is False
        assert m["enabled"] is False

    def test_metrics_after_initialize(self, simple_plugin):
        simple_plugin.state = PluginState.INITIALIZED
        simple_plugin._initialized = True
        m = simple_plugin.metrics()
        assert m["initialized"] is True

    def test_to_dict(self, simple_plugin):
        d = simple_plugin.to_dict()
        assert "manifest" in d
        assert "state" in d
        assert d["state"] == "discovered"
        assert "metrics" in d

    def test_repr(self, simple_plugin):
        r = repr(simple_plugin)
        assert "SimplePlugin" in r
        assert "simple" in r
        assert "discovered" in r


class TestPluginErrors:
    """Tests de manejo de errores."""

    def test_add_error(self, simple_plugin):
        simple_plugin._add_error("test error")
        assert simple_plugin.error_count == 1
        assert simple_plugin.last_error == "test error"

    def test_add_multiple_errors(self, simple_plugin):
        for i in range(60):
            simple_plugin._add_error(f"error {i}")
        # MAX_ERRORS = 50, solo los ultimos 50 se guardan.
        assert simple_plugin.error_count == 50
        assert simple_plugin.last_error == "error 59"

    def test_errors_list_is_copy(self, simple_plugin):
        simple_plugin._add_error("err")
        errors = simple_plugin.errors
        errors.append("another")
        assert simple_plugin.error_count == 1