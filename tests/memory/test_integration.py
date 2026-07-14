"""Integration tests: MemoryEngine + ContextEngine + Events + Policies working together."""

import pytest

from packages.multiagent.src.multiagent.memctx.context import (
    ContextEngine,
    ContextRequest,
)
from packages.multiagent.src.multiagent.memctx.engine import MemoryEngine
from packages.multiagent.src.multiagent.memctx.events import MemoryEventType
from packages.multiagent.src.multiagent.memctx.integration import (
    PluginMemoryBridge,
    RegistryMemoryAdapter,
    WorkflowMemoryAdapter,
)
from packages.multiagent.src.multiagent.memctx.metrics import MemoryMetrics
from packages.multiagent.src.multiagent.memctx.models import (
    MemoryRecord,
    MemoryType,
)
from packages.multiagent.src.multiagent.memctx.policies import (
    MemoryPolicy,
    MemoryPolicyManager,
    PolicyAction,
)


def _rec(id, content="test", owner="a1", tags=None, memory_type=None, priority=0):
    return MemoryRecord(
        id=id, content=content, owner=owner, tags=tags or [],
        memory_type=memory_type or MemoryType.KNOWLEDGE, priority=priority,
    )


# =========================================================================
# Full pipeline integration
# =========================================================================


class TestFullPipeline:
    """End-to-end test: store → search → context build → event verification."""

    @pytest.mark.asyncio
    async def test_store_search_context_pipeline(self):
        engine = MemoryEngine()
        await engine.start()
        ctx_engine = ContextEngine(engine)

        # Store various records
        await engine.store_conversation("sess-1", "user", "Hello!")
        await engine.store_conversation("sess-1", "assistant", "Hi there!")
        await engine.store_knowledge("Use Python 3.11+", source="docs")
        await engine.store_project("proj-1", "Microservices architecture")

        # Build context for an agent
        snap = await ctx_engine.build_agent_context("coder")
        assert len(snap.records) > 0
        assert snap.total_tokens_estimate > 0

        # Check metrics
        m = engine.metrics.snapshot()
        assert m["operations"]["store"] == 4
        assert m["by_type"]["conversation"] == 2
        assert m["by_type"]["knowledge"] == 1
        assert m["by_type"]["project"] == 1

        await engine.stop()

    @pytest.mark.asyncio
    async def test_event_flow_store_update_delete(self):
        bus_events = []
        engine = MemoryEngine()
        await engine.start()

        async def collect(e):
            bus_events.append(e)

        await engine.event_bus.subscribe_all(collect)

        # Store
        await engine.store(_rec("e1"))
        created = [e for e in bus_events if "created" in str(e.type)]
        assert len(created) == 1

        # Update
        rec = await engine.retrieve("e1")
        rec.content = "updated"
        await engine.update(rec)
        updated = [e for e in bus_events if "updated" in str(e.type)]
        assert len(updated) == 1

        # Delete
        await engine.remove("e1")
        deleted = [e for e in bus_events if "deleted" in str(e.type)]
        assert len(deleted) == 1

        await engine.stop()


# =========================================================================
# WorkflowMemoryAdapter
# =========================================================================


class TestWorkflowMemoryAdapter:
    @pytest.fixture
    async def adapter(self):
        engine = MemoryEngine()
        await engine.start()
        adap = WorkflowMemoryAdapter(engine)
        yield adap, engine
        await engine.stop()

    @pytest.mark.asyncio
    async def test_save_and_load_context(self, adapter):
        adap, engine = adapter
        await adap.save_workflow_context("wf-1", {"objective": "Build API"})
        records = await adap.load_workflow_context("wf-1")
        assert len(records) >= 1
        assert records[0].content["context"]["objective"] == "Build API"

    @pytest.mark.asyncio
    async def test_record_step_result(self, adapter):
        adap, _ = adapter
        rec = await adap.record_step_result("wf-1", "step-1", "output.txt")
        assert rec.content["result"] == "output.txt"

    @pytest.mark.asyncio
    async def test_record_workflow_result(self, adapter):
        adap, _ = adapter
        rec = await adap.record_workflow_result("wf-1", {"status": "ok"})
        assert rec.priority == 10
        assert "final" in rec.tags

    @pytest.mark.asyncio
    async def test_get_workflow_history(self, adapter):
        adap, _ = adapter
        await adap.record_step_result("wf-1", "s1", "step1")
        await adap.record_step_result("wf-1", "s2", "step2")
        history = await adap.get_workflow_history("wf-1")
        assert len(history) >= 2

    @pytest.mark.asyncio
    async def test_disabled_raises(self, adapter):
        adap, _ = adapter
        adap.enabled = False
        with pytest.raises(RuntimeError, match="disabled"):
            await adap.save_workflow_context("wf-1", {})


# =========================================================================
# RegistryMemoryAdapter
# =========================================================================


class TestRegistryMemoryAdapter:
    @pytest.fixture
    async def adapter(self):
        engine = MemoryEngine()
        await engine.start()
        adap = RegistryMemoryAdapter(engine)
        yield adap, engine
        await engine.stop()

    @pytest.mark.asyncio
    async def test_register_and_get_agent_memory(self, adapter):
        adap, engine = adapter
        adap.register_agent_memory("coder")
        await adap.store_agent_memory("coder", "prefers_tabs")
        records = await adap.get_agent_memory("coder")
        assert len(records) >= 1

    @pytest.mark.asyncio
    async def test_unregister(self, adapter):
        adap, _ = adapter
        adap.register_agent_memory("coder")
        assert adap.unregister_agent_memory("coder") is None  # no return
        assert adap.unregister_agent_memory("nonexistent") is None

    @pytest.mark.asyncio
    async def test_store_agent_memory(self, adapter):
        adap, _ = adapter
        rec = await adap.store_agent_memory(
            "coder", {"pref": "dark mode"}, tags=["preference", "agent"]
        )
        assert rec.owner == "coder"
        assert "agent" in rec.tags

    @pytest.mark.asyncio
    async def test_get_agent_context(self, adapter):
        adap, _ = adapter
        ctx = await adap.get_agent_context("coder")
        assert "records" in ctx
        assert "target" in ctx


# =========================================================================
# Policy cleanup integration
# =========================================================================


class TestPolicyCleanupIntegration:
    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_records(self):
        engine = MemoryEngine()
        await engine.start()
        mgr = MemoryPolicyManager()

        # Policy: expire knowledge records with custom TTL
        mgr.add_policy(MemoryPolicy(
            name="expire-knowledge",
            memory_types=[MemoryType.KNOWLEDGE],
            ttl_seconds=60,
            action=PolicyAction.EXPIRE,
        ))

        # Store a fresh record
        await engine.store(_rec("fresh-k", memory_type=MemoryType.KNOWLEDGE))

        # Store an old record (manually set created_at)
        old = MemoryRecord(
            id="old-k",
            content="old data",
            memory_type=MemoryType.KNOWLEDGE,
            ttl_seconds=1,
        )
        old.created_at = "2020-01-01T00:00:00+00:00"
        await engine.store(old)

        # Cleanup
        results = await mgr.cleanup(engine)
        assert results["expired"] >= 1

        # Fresh should still exist
        assert await engine.exists("fresh-k") is True

        await engine.stop()


# =========================================================================
# Metrics integration
# =========================================================================


class TestMetricsIntegration:
    @pytest.mark.asyncio
    async def test_metrics_across_operations(self):
        metrics = MemoryMetrics()
        engine = MemoryEngine(metrics=metrics)
        await engine.start()

        await engine.store(_rec("m1"))
        await engine.retrieve("m1")
        await engine.retrieve("nope")
        await engine.search(query="test")
        rec = await engine.retrieve("m1")
        rec.content = "updated"
        await engine.update(rec)
        await engine.remove("m1")

        s = metrics.snapshot()
        assert s["operations"]["store"] == 1
        assert s["operations"]["retrieve"] == 3  # 2 explicit + 1 before update
        assert s["operations"]["search"] == 1
        assert s["operations"]["update"] == 1
        assert s["operations"]["delete"] == 1
        assert s["hit_rate"]["retrieve_hits"] == 2  # m1 hit both times

        await engine.stop()

    @pytest.mark.asyncio
    async def test_health_check_includes_metrics(self):
        engine = MemoryEngine()
        await engine.start()
        await engine.store(_rec("h1"))
        health = await engine.health_check()
        assert "metrics" in health
        assert health["metrics"]["operations"]["store"] == 1
        await engine.stop()


# =========================================================================
# PluginMemoryBridge
# =========================================================================


class TestPluginMemoryBridge:
    @pytest.mark.asyncio
    async def test_connect_without_plugin_bus(self):
        from packages.multiagent.src.multiagent.memctx.events import MemoryEventBus
        mem_bus = MemoryEventBus()
        bridge = PluginMemoryBridge(mem_bus)
        await bridge.connect()  # Should not raise
        await bridge.disconnect()  # Should not raise

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        from packages.multiagent.src.multiagent.memctx.events import MemoryEventBus

        mem_bus = MemoryEventBus()
        plugin_events = []
        received = []

        class FakePluginBus:
            async def emit(self, event_type, source, payload):
                plugin_events.append({"type": event_type, "payload": payload})

        bridge = PluginMemoryBridge(mem_bus, FakePluginBus())
        await bridge.connect()
        await mem_bus.emit(MemoryEventType.MEMORY_CREATED, source="test")
        await bridge.disconnect()

        assert len(plugin_events) == 1
        assert plugin_events[0]["type"] == "memory.memory.created"