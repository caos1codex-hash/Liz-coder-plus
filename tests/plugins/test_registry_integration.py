"""Tests for Registry Integration (Sprint 2.5)."""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "multiagent", "src"))

from multiagent.plugins.base import Plugin
from multiagent.plugins.discovery import DiscoveredPlugin
from multiagent.plugins.enums import PluginEvent
from multiagent.plugins.manifest import PluginManifest
from multiagent.plugins.manager import PluginManager
from multiagent.registry.agent_registry import AgentRegistry
from multiagent.registry.capability import CapabilityRegistry
from multiagent.base import BaseAgent
from multiagent.enums import AgentStatus


# ----------------------------------------------------------------------
# Plugins de prueba que exponen agentes
# ----------------------------------------------------------------------


class MockAgent(BaseAgent):
    """Agente mock para pruebas de integracion."""

    async def _execute_impl(self, task: dict) -> dict:
        return {"status": "ok", "mock": True}


class AgentPlugin(Plugin):
    """Plugin que expone un agente."""

    def __init__(self) -> None:
        super().__init__()
        self._agents = [MockAgent(name="plugin-agent")]

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="agent-plugin",
            name="Agent Plugin",
            version="1.0.0",
            capabilities=["plugin.agent"],
            entry_point="tests.plugins.test_registry_integration:AgentPlugin",
        )

    def get_agents(self) -> list[BaseAgent]:
        return self._agents


class MultiAgentPlugin(Plugin):
    """Plugin que expone multiples agentes."""

    def __init__(self) -> None:
        super().__init__()
        self._agents = [
            MockAgent(name="plugin-agent-1"),
            MockAgent(name="plugin-agent-2"),
            MockAgent(name="plugin-agent-3"),
        ]

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="multi-agent-plugin",
            name="Multi Agent Plugin",
            version="1.0.0",
            capabilities=["plugin.multi"],
            entry_point="tests.plugins.test_registry_integration:MultiAgentPlugin",
        )

    def get_agents(self) -> list[BaseAgent]:
        return self._agents


class ToolPluginForRegistry(Plugin):
    """Plugin que expone herramientas."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="tool-plugin-reg",
            name="Tool Plugin Registry Test",
            version="1.0.0",
            capabilities=["plugin.tool"],
            entry_point="tests.plugins.test_registry_integration:ToolPluginForRegistry",
        )

    def get_tools(self) -> list:
        return [{"name": "test_tool", "description": "A test tool"}]


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def agent_registry():
    return AgentRegistry()


@pytest.fixture
def manager(agent_registry):
    return PluginManager(
        agent_registry=agent_registry,
        system_api_version="1.0.0",
    )


# ----------------------------------------------------------------------
# Tests de registro de agentes
# ----------------------------------------------------------------------


class TestAgentRegistration:
    """Tests de registro automatico de agentes via plugins."""

    @pytest.mark.asyncio
    async def test_plugin_agent_registered_in_registry(self, manager, agent_registry):
        """Un agente de plugin se registra en el AgentRegistry al enable."""
        plugin = AgentPlugin()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:AgentPlugin",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("agent-plugin")

        # El agente debe estar registrado.
        record = agent_registry.get("plugin-agent")
        assert record is not None
        assert record.agent.name == "plugin-agent"

    @pytest.mark.asyncio
    async def test_plugin_agent_unregistered_on_disable(
        self, manager, agent_registry,
    ):
        """Los agentes se desregistran al disable."""
        plugin = AgentPlugin()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:AgentPlugin",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("agent-plugin")
        await manager.disable_plugin("agent-plugin")

        # El agente debe estar desregistrado (por id).
        record = agent_registry.get_by_id(
            manager.get_record("agent-plugin").agents_registered[0]
            if manager.get_record("agent-plugin") and
              manager.get_record("agent-plugin").agents_registered
            else "__none__"
        )
        assert record is None

    @pytest.mark.asyncio
    async def test_multiple_agents_registered(self, manager, agent_registry):
        """Multiples agentes de un plugin se registran."""
        plugin = MultiAgentPlugin()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:MultiAgentPlugin",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("multi-agent-plugin")

        for name in ["plugin-agent-1", "plugin-agent-2", "plugin-agent-3"]:
            record = agent_registry.get(name)
            assert record is not None, f"Agent '{name}' not registered"

    @pytest.mark.asyncio
    async def test_plugin_record_tracks_registered_agents(
        self, manager, agent_registry,
    ):
        """El PluginRecord trackea los IDs de agentes registrados."""
        plugin = MultiAgentPlugin()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:MultiAgentPlugin",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("multi-agent-plugin")

        record = manager.get_record("multi-agent-plugin")
        assert record is not None
        assert len(record.agents_registered) == 3


# ----------------------------------------------------------------------
# Tests de registro de herramientas
# ----------------------------------------------------------------------


class TestToolRegistration:
    """Tests de registro de herramientas via plugins."""

    @pytest.mark.asyncio
    async def test_plugin_tool_registered(self, manager):
        """Las herramientas se trackean en el PluginRecord."""
        plugin = ToolPluginForRegistry()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:ToolPluginForRegistry",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("tool-plugin-reg")

        record = manager.get_record("tool-plugin-reg")
        assert record is not None
        assert len(record.tools_registered) == 1
        assert "test_tool" in str(record.tools_registered)

    @pytest.mark.asyncio
    async def test_plugin_tools_unregistered_on_disable(self, manager):
        """Las herramientas se trackean al desregistrar."""
        plugin = ToolPluginForRegistry()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:ToolPluginForRegistry",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("tool-plugin-reg")
        await manager.disable_plugin("tool-plugin-reg")

        record = manager.get_record("tool-plugin-reg")
        assert record is not None
        assert len(record.tools_registered) == 0


# ----------------------------------------------------------------------
# Tests de eventos de registro
# ----------------------------------------------------------------------


class TestRegistrationEvents:
    """Tests de eventos emitidos durante el registro."""

    @pytest.mark.asyncio
    async def test_agent_registered_event(self, manager, agent_registry):
        """Se emite evento agent.registered al habilitar un plugin con agentes."""
        events_received = []

        async def capture(event):
            events_received.append(event)

        await manager.event_bus.subscribe(
            capture, event_type=PluginEvent.AGENT_REGISTERED,
        )

        plugin = AgentPlugin()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:AgentPlugin",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("agent-plugin")

        agent_events = [
            e for e in events_received
            if e.plugin_id == "agent-plugin"
        ]
        assert len(agent_events) >= 1

    @pytest.mark.asyncio
    async def test_tool_registered_event(self, manager):
        """Se emite evento tool.registered al habilitar un plugin con herramientas."""
        events_received = []

        async def capture(event):
            events_received.append(event)

        await manager.event_bus.subscribe(
            capture, event_type=PluginEvent.TOOL_REGISTERED,
        )

        plugin = ToolPluginForRegistry()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:ToolPluginForRegistry",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("tool-plugin-reg")

        tool_events = [
            e for e in events_received
            if e.plugin_id == "tool-plugin-reg"
        ]
        assert len(tool_events) >= 1


# ----------------------------------------------------------------------
# Tests de integracion con CapabilityResolver
# ----------------------------------------------------------------------


class TestCapabilityIntegration:
    """Tests de que las capabilities de plugins estan disponibles."""

    @pytest.mark.asyncio
    async def test_plugin_capabilities_in_registry(
        self, manager, agent_registry,
    ):
        """Las capabilities del plugin estan disponibles despues del enable."""
        plugin = AgentPlugin()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:AgentPlugin",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("agent-plugin")

        # El agente del plugin debe estar en el registry.
        record = agent_registry.get("plugin-agent")
        assert record is not None
        # Las capabilities del manifest del agente registrado.
        assert "plugin.agent" in record.provided_capabilities

    @pytest.mark.asyncio
    async def test_workflow_can_find_plugin_agents(
        self, manager, agent_registry,
    ):
        """El CapabilityResolver puede encontrar agentes de plugins."""
        from multiagent.registry.resolver import CapabilityResolver

        cap_registry = CapabilityRegistry()
        resolver = CapabilityResolver(
            registry=agent_registry,
        )

        plugin = AgentPlugin()
        dp = DiscoveredPlugin(
            manifest=plugin.manifest(),
            entry_point="tests.plugins.test_registry_integration:AgentPlugin",
            source_path="tests/plugins",
            discovery_type="test",
        )
        await manager.install_plugin(dp)
        await manager.enable_plugin("agent-plugin")

        result = await resolver.resolve("plugin.agent")
        assert result is not None
        assert result.resolved is True
        assert result.agent_record is not None
        assert result.agent_record.name == "plugin-agent"