"""Tests for memory policies and MemoryPolicyManager."""

import pytest

from packages.multiagent.src.multiagent.memctx.engine import MemoryEngine
from packages.multiagent.src.multiagent.memctx.models import (
    MemoryRecord,
    MemoryType,
)
from packages.multiagent.src.multiagent.memctx.policies import (
    MemoryPolicy,
    MemoryPolicyManager,
    PolicyAction,
)


def _rec(id="r1", owner="a1", tags=None, memory_type=MemoryType.KNOWLEDGE, ttl_seconds=None, priority=0, created_at=None):
    return MemoryRecord(
        id=id, content="test", owner=owner, tags=tags or [],
        memory_type=memory_type, ttl_seconds=ttl_seconds, priority=priority,
    )


class TestMemoryPolicy:
    def test_applies_to_type(self):
        p = MemoryPolicy(
            name="conv-only",
            memory_types=[MemoryType.CONVERSATION],
        )
        assert p.applies_to(_rec(memory_type=MemoryType.CONVERSATION)) is True
        assert p.applies_to(_rec(memory_type=MemoryType.KNOWLEDGE)) is False

    def test_applies_to_owner(self):
        p = MemoryPolicy(name="a1-only", owners=["a1"])
        assert p.applies_to(_rec(owner="a1")) is True
        assert p.applies_to(_rec(owner="a2")) is False

    def test_applies_to_both(self):
        p = MemoryPolicy(
            name="a1-conv",
            memory_types=[MemoryType.CONVERSATION],
            owners=["a1"],
        )
        assert p.applies_to(_rec(owner="a1", memory_type=MemoryType.CONVERSATION)) is True
        assert p.applies_to(_rec(owner="a2", memory_type=MemoryType.CONVERSATION)) is False
        assert p.applies_to(_rec(owner="a1", memory_type=MemoryType.KNOWLEDGE)) is False

    def test_applies_to_all_when_empty(self):
        p = MemoryPolicy(name="all")
        assert p.applies_to(_rec()) is True

    def test_disabled_never_applies(self):
        p = MemoryPolicy(name="disabled", enabled=False)
        assert p.applies_to(_rec()) is False

    def test_is_expired_with_ttl(self):
        p = MemoryPolicy(name="short", ttl_seconds=60)
        # Record with TTL=1 and old created_at
        rec = _rec(ttl_seconds=1)
        from datetime import datetime, timezone
        rec.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        assert p.is_expired(rec) is True

    def test_is_expired_no_ttl(self):
        p = MemoryPolicy(name="no-ttl")
        rec = _rec()
        assert p.is_expired(rec) is False

    def test_to_dict(self):
        p = MemoryPolicy(
            name="test",
            ttl_seconds=3600,
            action=PolicyAction.EVICT,
        )
        d = p.to_dict()
        assert d["name"] == "test"
        assert d["ttl_seconds"] == 3600
        assert d["action"] == "evict"
        assert isinstance(d["memory_types"], list)


class TestPolicyManager:
    def test_add_policy(self):
        mgr = MemoryPolicyManager()
        p = MemoryPolicy(name="p1")
        mgr.add_policy(p)
        assert len(mgr.policies) == 1

    def test_add_duplicate_name_raises(self):
        mgr = MemoryPolicyManager()
        mgr.add_policy(MemoryPolicy(name="p1"))
        with pytest.raises(ValueError, match="duplicate"):
            mgr.add_policy(MemoryPolicy(name="p1"))

    def test_add_empty_name_raises(self):
        mgr = MemoryPolicyManager()
        with pytest.raises(ValueError, match="name"):
            mgr.add_policy(MemoryPolicy(name=""))

    def test_remove_policy(self):
        mgr = MemoryPolicyManager()
        mgr.add_policy(MemoryPolicy(name="p1"))
        assert mgr.remove_policy("p1") is True
        assert len(mgr.policies) == 0

    def test_remove_nonexistent(self):
        mgr = MemoryPolicyManager()
        assert mgr.remove_policy("nope") is False

    def test_get_policy(self):
        mgr = MemoryPolicyManager()
        p = MemoryPolicy(name="p1", ttl_seconds=100)
        mgr.add_policy(p)
        got = mgr.get_policy("p1")
        assert got is not None
        assert got.ttl_seconds == 100

    def test_enable_disable(self):
        mgr = MemoryPolicyManager()
        mgr.add_policy(MemoryPolicy(name="p1", enabled=False))
        assert mgr.get_policy("p1").enabled is False
        assert mgr.enable_policy("p1") is True
        assert mgr.get_policy("p1").enabled is True
        assert mgr.disable_policy("p1") is True
        assert mgr.get_policy("p1").enabled is False

    def test_evaluate(self):
        mgr = MemoryPolicyManager()
        mgr.add_policy(MemoryPolicy(
            name="conv-only",
            memory_types=[MemoryType.CONVERSATION],
        ))
        conv_rec = _rec(memory_type=MemoryType.CONVERSATION)
        know_rec = _rec(memory_type=MemoryType.KNOWLEDGE)
        assert len(mgr.evaluate(conv_rec)) == 1
        assert len(mgr.evaluate(know_rec)) == 0

    def test_should_expire(self):
        mgr = MemoryPolicyManager()
        mgr.add_policy(MemoryPolicy(
            name="short-ttl",
            ttl_seconds=60,
        ))
        rec = _rec(ttl_seconds=1)
        from datetime import datetime, timezone
        rec.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        assert mgr.should_expire(rec) is True

    def test_get_action(self):
        mgr = MemoryPolicyManager()
        mgr.add_policy(MemoryPolicy(
            name="evict-old",
            ttl_seconds=60,
            action=PolicyAction.EVICT,
        ))
        rec = _rec(ttl_seconds=1)
        from datetime import datetime, timezone
        rec.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        action = mgr.get_action(rec)
        assert action == PolicyAction.EVICT

    def test_get_action_none_when_not_expired(self):
        mgr = MemoryPolicyManager()
        mgr.add_policy(MemoryPolicy(name="never-expire"))
        action = mgr.get_action(_rec())
        assert action is None


class TestPolicyManagerCleanup:
    @pytest.fixture
    async def setup(self):
        mgr = MemoryPolicyManager()
        engine = MemoryEngine()
        await engine.start()
        yield mgr, engine
        await engine.stop()

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, setup):
        mgr, engine = setup
        # Add a policy that expires everything
        mgr.add_policy(MemoryPolicy(
            name="expire-all",
            ttl_seconds=1,
            action=PolicyAction.EXPIRE,
            memory_types=[MemoryType.KNOWLEDGE],
        ))
        # Store a record with expired TTL
        rec = MemoryRecord(
            id="old-rec",
            content="old",
            memory_type=MemoryType.KNOWLEDGE,
            ttl_seconds=1,
        )
        rec.created_at = "2020-01-01T00:00:00+00:00"
        await engine.store(rec)
        # Run cleanup
        results = await mgr.cleanup(engine)
        assert results["expired"] >= 1
        assert results["total_cleaned"] >= 1

    @pytest.mark.asyncio
    async def test_cleanup_no_match(self, setup):
        mgr, engine = setup
        mgr.add_policy(MemoryPolicy(name="no-ttl", ttl_seconds=None))
        await engine.store(_rec("fresh"))
        results = await mgr.cleanup(engine)
        assert results["total_cleaned"] == 0

    @pytest.mark.asyncio
    async def test_stats(self, setup):
        mgr, engine = setup
        mgr.add_policy(MemoryPolicy(name="p1"))
        mgr.add_policy(MemoryPolicy(name="p2", enabled=False))
        stats = mgr.stats()
        assert stats["total_policies"] == 2
        assert stats["enabled_policies"] == 1