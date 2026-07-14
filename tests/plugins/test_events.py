"""Tests for Plugin Events (Sprint 2.5)."""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "multiagent", "src"))

from multiagent.plugins.enums import PluginEvent
from multiagent.plugins.events import (
    PluginEventBus,
    PluginSystemEvent,
)


# ----------------------------------------------------------------------
# Tests de PluginSystemEvent
# ----------------------------------------------------------------------


class TestPluginSystemEvent:
    """Tests del evento del sistema."""

    def test_create_with_enum(self):
        e = PluginSystemEvent(PluginEvent.PLUGIN_LOADED, plugin_id="test")
        assert e.type == "plugin.loaded"
        assert e.plugin_id == "test"
        assert e.source == "PluginManager"
        assert e.timestamp != ""

    def test_create_with_string(self):
        e = PluginSystemEvent("custom.event", plugin_id="test")
        assert e.type == "custom.event"

    def test_create_with_payload(self):
        e = PluginSystemEvent(
            PluginEvent.PLUGIN_FAILED,
            plugin_id="test",
            payload={"error": "init failed"},
        )
        assert e.payload["error"] == "init failed"

    def test_to_dict(self):
        e = PluginSystemEvent(
            PluginEvent.PLUGIN_LOADED,
            source="test-source",
            plugin_id="p1",
            correlation_id="corr-1",
        )
        d = e.to_dict()
        assert d["type"] == "plugin.loaded"
        assert d["source"] == "test-source"
        assert d["plugin_id"] == "p1"
        assert d["correlation_id"] == "corr-1"
        assert "timestamp" in d

    def test_repr(self):
        e = PluginSystemEvent(PluginEvent.PLUGIN_LOADED, plugin_id="test")
        r = repr(e)
        assert "plugin.loaded" in r
        assert "test" in r

    def test_type_str_property(self):
        e = PluginSystemEvent(PluginEvent.PLUGIN_ENABLED)
        assert e.type_str == "plugin.enabled"


# ----------------------------------------------------------------------
# Tests de PluginEventBus
# ----------------------------------------------------------------------


class TestPluginEventBus:
    """Tests del bus de eventos de plugins."""

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        bus = PluginEventBus()
        received = []

        async def handler(event: PluginSystemEvent):
            received.append(event)

        await bus.subscribe(handler, event_type=PluginEvent.PLUGIN_LOADED)
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="test")

        assert len(received) == 1
        assert received[0].plugin_id == "test"
        assert received[0].type == "plugin.loaded"

    @pytest.mark.asyncio
    async def test_subscribe_all_events(self):
        bus = PluginEventBus()
        received = []

        async def handler(event: PluginSystemEvent):
            received.append(event)

        await bus.subscribe(handler)  # no event_type = all
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="p1")
        await bus.publish(PluginEvent.PLUGIN_ENABLED, plugin_id="p2")

        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = PluginEventBus()
        received = []

        async def handler(event: PluginSystemEvent):
            received.append(event)

        token = await bus.subscribe(handler)
        await bus.unsubscribe(token)
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="test")

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_filter_fn(self):
        bus = PluginEventBus()
        received = []

        async def handler(event: PluginSystemEvent):
            received.append(event)

        # Solo eventos de plugin "p1".
        await bus.subscribe(
            handler,
            filter_fn=lambda e: e.plugin_id == "p1",
        )
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="p1")
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="p2")

        assert len(received) == 1
        assert received[0].plugin_id == "p1"

    @pytest.mark.asyncio
    async def test_handler_error_isolation(self):
        """Un handler que falla no afecta a otros."""
        bus = PluginEventBus()
        received = []

        async def failing_handler(event: PluginSystemEvent):
            raise RuntimeError("oops")

        async def good_handler(event: PluginSystemEvent):
            received.append(event)

        await bus.subscribe(failing_handler)
        await bus.subscribe(good_handler)
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="test")

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_history(self):
        bus = PluginEventBus()
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="p1")
        await bus.publish(PluginEvent.PLUGIN_ENABLED, plugin_id="p2")
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="p3")

        history = bus.history()
        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_history_filter_by_type(self):
        bus = PluginEventBus()
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="p1")
        await bus.publish(PluginEvent.PLUGIN_ENABLED, plugin_id="p2")
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="p3")

        history = bus.history(event_type=PluginEvent.PLUGIN_LOADED)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_history_filter_by_plugin_id(self):
        bus = PluginEventBus()
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="p1")
        await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id="p2")

        history = bus.history(plugin_id="p1")
        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_history_limit(self):
        bus = PluginEventBus()
        for i in range(10):
            await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id=f"p{i}")

        history = bus.history(limit=3)
        assert len(history) == 3
        # Los mas recientes primero en reversed, luego invertidos.
        assert history[0].plugin_id == "p7"

    @pytest.mark.asyncio
    async def test_history_max_size(self):
        """El historial tiene un maximo de eventos."""
        bus = PluginEventBus()
        # Maximo 500 por defecto.
        for i in range(600):
            await bus.publish(PluginEvent.PLUGIN_LOADED, plugin_id=f"p{i}")
        history = bus.history()
        assert len(history) <= 500

    @pytest.mark.asyncio
    async def test_metrics(self):
        bus = PluginEventBus()
        await bus.subscribe(lambda e: None)
        await bus.publish(PluginEvent.PLUGIN_LOADED)
        m = bus.metrics()
        assert m["subscriber_count"] == 1
        assert m["history_size"] == 1
        assert m["sequence"] == 1

    @pytest.mark.asyncio
    async def test_publish_returns_event(self):
        bus = PluginEventBus()
        event = await bus.publish(
            PluginEvent.PLUGIN_LOADED,
            plugin_id="test",
            source="test-source",
        )
        assert isinstance(event, PluginSystemEvent)
        assert event.sequence == 1
        assert event.source == "test-source"

    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_event(self):
        """Multiples suscriptores reciben el mismo evento."""
        bus = PluginEventBus()
        received_a = []
        received_b = []

        async def handler_a(event: PluginSystemEvent):
            received_a.append(event)

        async def handler_b(event: PluginSystemEvent):
            received_b.append(event)

        await bus.subscribe(handler_a, event_type=PluginEvent.PLUGIN_LOADED)
        await bus.subscribe(handler_b, event_type=PluginEvent.PLUGIN_LOADED)
        await bus.publish(PluginEvent.PLUGIN_LOADED)

        assert len(received_a) == 1
        assert len(received_b) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_token(self):
        bus = PluginEventBus()
        result = await bus.unsubscribe("nonexistent-token")
        assert result is False