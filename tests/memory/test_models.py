"""Tests for memory models: MemoryRecord, ConversationMemory, ProjectMemory,
WorkflowMemory, KnowledgeMemory, ContextSnapshot."""

import pytest
from datetime import datetime, timezone

from packages.multiagent.src.multiagent.memctx.models import (
    ConversationMemory,
    ContextSnapshot,
    KnowledgeMemory,
    MemoryRecord,
    MemoryType,
    ProjectMemory,
    WorkflowMemory,
    _serialize_value,
    _validate_id,
    _validate_metadata,
    _validate_tags,
    was_trimmed_set,
)


# =========================================================================
# MemoryRecord
# =========================================================================


class TestMemoryRecord:
    """Tests for the base MemoryRecord model."""

    def test_create_with_defaults(self):
        rec = MemoryRecord()
        assert rec.id
        assert rec.memory_type == MemoryType.KNOWLEDGE
        assert rec.content is None
        assert rec.owner == "system"
        assert rec.tags == []
        assert rec.metadata == {}
        assert rec.version == 1
        assert rec.created_at
        assert rec.updated_at
        assert rec.ttl_seconds is None
        assert rec.priority == 0

    def test_create_with_custom_id(self):
        rec = MemoryRecord(id="test-123")
        assert rec.id == "test-123"

    def test_create_with_all_fields(self):
        rec = MemoryRecord(
            id="full-1",
            memory_type=MemoryType.CONVERSATION,
            content={"text": "hello"},
            owner="agent-1",
            tags=["greeting", "test"],
            metadata={"key": "val"},
            ttl_seconds=3600,
            priority=5,
        )
        assert rec.id == "full-1"
        assert rec.memory_type == MemoryType.CONVERSATION
        assert rec.content == {"text": "hello"}
        assert rec.owner == "agent-1"
        assert rec.tags == ["greeting", "test"]
        assert rec.metadata == {"key": "val"}
        assert rec.ttl_seconds == 3600
        assert rec.priority == 5

    def test_to_dict_roundtrip(self):
        rec = MemoryRecord(
            id="round-1",
            memory_type=MemoryType.PROJECT,
            content={"desc": "test project"},
            owner="coder",
            tags=["project", "test"],
            metadata={"version": 2},
            priority=3,
        )
        d = rec.to_dict()
        assert d["id"] == "round-1"
        assert d["memory_type"] == "project"
        assert d["content"] == {"desc": "test project"}
        assert d["owner"] == "coder"
        assert d["tags"] == ["project", "test"]
        assert d["priority"] == 3

    def test_from_dict(self):
        d = {
            "id": "from-1",
            "memory_type": "conversation",
            "content": {"text": "hi"},
            "owner": "user",
            "tags": ["chat"],
            "metadata": {},
            "version": 2,
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T01:00:00+00:00",
            "ttl_seconds": 600,
            "priority": 1,
        }
        rec = MemoryRecord.from_dict(d)
        assert rec.id == "from-1"
        assert rec.memory_type == MemoryType.CONVERSATION
        assert rec.version == 2
        assert rec.ttl_seconds == 600

    def test_from_dict_missing_optional_fields(self):
        d = {"id": "min-1", "memory_type": "knowledge", "content": "text"}
        rec = MemoryRecord.from_dict(d)
        assert rec.owner == "system"
        assert rec.tags == []
        assert rec.version == 1
        assert rec.ttl_seconds is None
        assert rec.priority == 0

    def test_bump_version(self):
        rec = MemoryRecord(id="bump-1", version=1)
        old_updated = rec.updated_at
        rec.bump_version()
        assert rec.version == 2
        assert rec.updated_at != old_updated

    def test_is_expired_no_ttl(self):
        rec = MemoryRecord(id="no-ttl")
        assert rec.is_expired() is False

    def test_is_expired_not_yet(self):
        rec = MemoryRecord(id="fresh", ttl_seconds=999999)
        assert rec.is_expired() is False

    def test_is_expired_old(self):
        rec = MemoryRecord(id="old", ttl_seconds=1)
        # Manually set created_at to the past
        past = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        rec.created_at = past
        assert rec.is_expired() is True

    def test_equality(self):
        r1 = MemoryRecord(id="eq-1", version=1)
        r2 = MemoryRecord(id="eq-1", version=1)
        r3 = MemoryRecord(id="eq-1", version=2)
        r4 = MemoryRecord(id="eq-2", version=1)
        assert r1 == r2
        assert r1 != r3
        assert r1 != r4

    def test_hash(self):
        r1 = MemoryRecord(id="h-1", version=1)
        r2 = MemoryRecord(id="h-1", version=1)
        assert hash(r1) == hash(r2)


# =========================================================================
# Validation
# =========================================================================


class TestValidation:
    """Tests for ID, tag, and metadata validation."""

    def test_valid_id(self):
        _validate_id("test_123")
        _validate_id("a" * 128)

    def test_empty_id_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _validate_id("")

    def test_none_id_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _validate_id(None)

    def test_long_id_raises(self):
        with pytest.raises(ValueError, match="128"):
            _validate_id("a" * 129)

    def test_invalid_char_id_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            _validate_id("has spaces")

    def test_valid_tags(self):
        _validate_tags(["tag1", "tag-2", "tag_3"])

    def test_empty_tag_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _validate_tags([""])

    def test_long_tag_raises(self):
        with pytest.raises(ValueError, match="too long"):
            _validate_tags(["a" * 65])

    def test_invalid_tag_char_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            _validate_tags(["has spaces"])

    def test_tags_not_list_raises(self):
        with pytest.raises(ValueError, match="list"):
            _validate_tags("tag1")

    def test_valid_metadata(self):
        _validate_metadata({"key": "val"})

    def test_metadata_not_dict_raises(self):
        with pytest.raises(ValueError, match="dict"):
            _validate_metadata("not a dict")

    def test_metadata_empty_key_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _validate_metadata({"": "val"})

    def test_invalid_memory_type_raises(self):
        with pytest.raises(ValueError, match="MemoryType"):
            MemoryRecord(id="bad-type", memory_type="invalid")

    def test_negative_priority_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            MemoryRecord(id="neg-pri", priority=-1)

    def test_zero_ttl_raises(self):
        with pytest.raises(ValueError, match="positive"):
            MemoryRecord(id="zero-ttl", ttl_seconds=0)

    def test_negative_ttl_raises(self):
        with pytest.raises(ValueError, match="positive"):
            MemoryRecord(id="neg-ttl", ttl_seconds=-100)


# =========================================================================
# Serialization
# =========================================================================


class TestSerialization:
    """Tests for _serialize_value."""

    def test_serialize_primitives(self):
        assert _serialize_value("hello") == "hello"
        assert _serialize_value(42) == 42
        assert _serialize_value(3.14) == 3.14
        assert _serialize_value(True) is True
        assert _serialize_value(None) is None

    def test_serialize_datetime(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = _serialize_value(dt)
        assert isinstance(result, str)
        assert "2025" in result

    def test_serialize_enum(self):
        result = _serialize_value(MemoryType.CONVERSATION)
        assert result == "conversation"

    def test_serialize_list(self):
        assert _serialize_value([1, "two", None]) == [1, "two", None]

    def test_serialize_dict(self):
        assert _serialize_value({"a": 1}) == {"a": 1}

    def test_serialize_unsupported_raises(self):
        class Custom:
            pass

        with pytest.raises(TypeError, match="unserializable"):
            _serialize_value(Custom())


# =========================================================================
# ConversationMemory
# =========================================================================


class TestConversationMemory:
    def test_create_defaults(self):
        cm = ConversationMemory()
        assert cm.memory_type == MemoryType.CONVERSATION
        assert cm.content == {"text": ""}
        assert cm.session_id == ""
        assert cm.role == "user"

    def test_create_custom(self):
        cm = ConversationMemory(
            id="conv-1",
            session_id="sess-1",
            role="assistant",
            content={"text": "Hello!"},
        )
        assert cm.session_id == "sess-1"
        assert cm.role == "assistant"

    def test_roundtrip(self):
        cm = ConversationMemory(
            id="conv-rt",
            session_id="s1",
            role="user",
            content={"text": "hi"},
            tags=["chat"],
        )
        d = cm.to_dict()
        # from_dict creates base MemoryRecord — verify shared fields
        cm2 = MemoryRecord.from_dict(d)
        assert cm2.id == cm.id
        assert cm2.owner == cm.owner
        assert cm2.tags == cm.tags


# =========================================================================
# ProjectMemory
# =========================================================================


class TestProjectMemory:
    def test_create_defaults(self):
        pm = ProjectMemory()
        assert pm.memory_type == MemoryType.PROJECT
        assert pm.content == {"description": ""}
        assert pm.project_id == ""
        assert pm.category == "general"

    def test_create_custom(self):
        pm = ProjectMemory(
            id="proj-1",
            project_id="p1",
            category="decision",
            content={"description": "Use PostgreSQL"},
        )
        assert pm.project_id == "p1"
        assert pm.category == "decision"


# =========================================================================
# WorkflowMemory
# =========================================================================


class TestWorkflowMemory:
    def test_create_defaults(self):
        wm = WorkflowMemory()
        assert wm.memory_type == MemoryType.WORKFLOW
        assert wm.content == {"result": None}
        assert wm.workflow_id == ""
        assert wm.step_id == ""
        assert wm.status == "pending"

    def test_create_custom(self):
        wm = WorkflowMemory(
            id="wf-1",
            workflow_id="w1",
            step_id="s1",
            status="completed",
            content={"result": "success"},
        )
        assert wm.workflow_id == "w1"
        assert wm.status == "completed"


# =========================================================================
# KnowledgeMemory
# =========================================================================


class TestKnowledgeMemory:
    def test_create_defaults(self):
        km = KnowledgeMemory()
        assert km.memory_type == MemoryType.KNOWLEDGE
        assert km.content == {"text": ""}
        assert km.confidence == 1.0
        assert km.source == ""
        assert km.knowledge_type == "fact"

    def test_invalid_confidence_high_raises(self):
        with pytest.raises(ValueError, match="0.0 and 1.0"):
            KnowledgeMemory(id="kh-1", confidence=1.5)

    def test_invalid_confidence_low_raises(self):
        with pytest.raises(ValueError, match="0.0 and 1.0"):
            KnowledgeMemory(id="kl-1", confidence=-0.1)

    def test_valid_confidence_boundary(self):
        km0 = KnowledgeMemory(id="k0", confidence=0.0)
        km1 = KnowledgeMemory(id="k1", confidence=1.0)
        assert km0.confidence == 0.0
        assert km1.confidence == 1.0


# =========================================================================
# ContextSnapshot
# =========================================================================


class TestContextSnapshot:
    def test_create_defaults(self):
        cs = ContextSnapshot()
        assert cs.id
        assert cs.target == ""
        assert cs.records == []
        assert cs.total_tokens_estimate == 0
        assert cs.was_trimmed is False
        assert cs.trimmed_count == 0

    def test_to_dict(self):
        cs = ContextSnapshot(
            target="agent-1",
            records=[{"id": "r1"}],
            summary="1 knowledge",
            was_trimmed=True,
            trimmed_count=5,
        )
        d = cs.to_dict()
        assert d["target"] == "agent-1"
        assert d["records"] == [{"id": "r1"}]
        assert d["was_trimmed"] is True

    def test_from_dict(self):
        d = {
            "id": "cs-1",
            "target": "wf-1",
            "records": [],
            "summary": "",
            "total_tokens_estimate": 100,
            "max_tokens": 4096,
            "was_trimmed": False,
            "trimmed_count": 0,
            "sources": ["agent-1"],
            "created_at": "2025-01-01T00:00:00+00:00",
            "metadata": {},
        }
        cs = ContextSnapshot.from_dict(d)
        assert cs.id == "cs-1"
        assert cs.target == "wf-1"
        assert cs.total_tokens_estimate == 100

    def test_was_trimmed_set_helper(self):
        cs = ContextSnapshot(was_trimmed=True)
        assert was_trimmed_set(cs) is True