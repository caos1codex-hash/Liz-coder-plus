"""Unit tests for communication.protocol (Sprint 2.9)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMM_SRC = ROOT / "packages" / "communication" / "src"
sys.path.insert(0, str(COMM_SRC))

for _k in [k for k in list(sys.modules) if k == "communication" or k.startswith("communication.")]:
    sys.modules.pop(_k, None)

from communication.exceptions import CommunicationError  # noqa: E402
from communication.protocol import (  # noqa: E402
    AgentMessage,
    MessagePriority,
    MessageType,
    priority_weight,
)

# ----------------------------------------------------------------------
# MessageType
# ----------------------------------------------------------------------


def test_message_type_values():
    assert MessageType.TASK_REQUEST.value == "task_request"
    assert MessageType.TASK_RESPONSE.value == "task_response"
    assert MessageType.STATUS_UPDATE.value == "status_update"
    assert MessageType.ERROR_REPORT.value == "error_report"
    assert MessageType.CONTEXT_TRANSFER.value == "context_transfer"
    assert MessageType.HANDOFF.value == "handoff"
    assert MessageType.SYSTEM.value == "system"
    assert MessageType.EVENT.value == "event"


def test_message_type_count():
    # 6 required + 2 aliases.
    assert len(list(MessageType)) == 8


# ----------------------------------------------------------------------
# MessagePriority
# ----------------------------------------------------------------------


def test_priority_weight_ordering():
    assert priority_weight(MessagePriority.CRITICAL) < priority_weight(MessagePriority.HIGH)
    assert priority_weight(MessagePriority.HIGH) < priority_weight(MessagePriority.NORMAL)
    assert priority_weight(MessagePriority.NORMAL) < priority_weight(MessagePriority.LOW)


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_basic_construction():
    msg = AgentMessage(
        sender_agent="planner",
        receiver_agent="coder",
        message_type=MessageType.TASK_REQUEST,
    )
    assert msg.sender_agent == "planner"
    assert msg.receiver_agent == "coder"
    assert msg.message_type == MessageType.TASK_REQUEST
    assert msg.message_id.startswith("msg-")
    assert msg.priority == MessagePriority.NORMAL


def test_empty_sender_allowed_for_replies():
    # Empty sender is allowed (for broadcast replies where the caller
    # fills in the sender after reply()).
    msg = AgentMessage(sender_agent="", receiver_agent="b")
    assert msg.sender_agent == ""


def test_empty_receiver_allowed():
    msg = AgentMessage(sender_agent="a", receiver_agent="")
    assert msg.receiver_agent == ""


def test_post_init_copies_payload():
    payload = {"x": 1}
    msg = AgentMessage(
        sender_agent="a",
        receiver_agent="b",
        payload=payload,
    )
    payload["x"] = 2
    assert msg.payload["x"] == 1


def test_post_init_copies_metadata():
    meta = {"k": "v"}
    msg = AgentMessage(
        sender_agent="a",
        receiver_agent="b",
        metadata=meta,
    )
    meta["k"] = "changed"
    assert msg.metadata["k"] == "v"


# ----------------------------------------------------------------------
# Factory constructors
# ----------------------------------------------------------------------


def test_task_request_factory():
    msg = AgentMessage.task_request("planner", "coder", payload={"action": "write"})
    assert msg.message_type == MessageType.TASK_REQUEST
    assert msg.sender_agent == "planner"
    assert msg.receiver_agent == "coder"
    assert msg.payload == {"action": "write"}
    assert msg.priority == MessagePriority.NORMAL


def test_task_response_factory():
    msg = AgentMessage.task_response(
        "coder",
        "planner",
        payload={"result": "done"},
        in_reply_to="msg-123",
    )
    assert msg.message_type == MessageType.TASK_RESPONSE
    assert msg.in_reply_to == "msg-123"
    assert msg.payload == {"result": "done"}


def test_status_update_factory():
    msg = AgentMessage.status_update("coder", status="running", progress=0.5)
    assert msg.message_type == MessageType.STATUS_UPDATE
    assert msg.receiver_agent == "*"  # default broadcast
    assert msg.payload["status"] == "running"
    assert msg.payload["progress"] == 0.5
    assert msg.priority == MessagePriority.LOW


def test_error_report_factory():
    msg = AgentMessage.error_report("coder", error="boom", code="INTERNAL")
    assert msg.message_type == MessageType.ERROR_REPORT
    assert msg.receiver_agent == "orchestrator"  # default
    assert msg.payload["error"] == "boom"
    assert msg.payload["code"] == "INTERNAL"
    assert msg.priority == MessagePriority.HIGH


def test_context_transfer_factory():
    msg = AgentMessage.context_transfer(
        "planner",
        "coder",
        context={"files": ["a.py"]},
    )
    assert msg.message_type == MessageType.CONTEXT_TRANSFER
    assert msg.payload["context"] == {"files": ["a.py"]}
    assert msg.priority == MessagePriority.HIGH


def test_handoff_factory():
    msg = AgentMessage.handoff(
        "planner",
        "coder",
        task_id="t1",
        reason="specialized",
    )
    assert msg.message_type == MessageType.HANDOFF
    assert msg.task_id == "t1"
    assert msg.payload["reason"] == "specialized"
    assert msg.priority == MessagePriority.HIGH


# ----------------------------------------------------------------------
# Reply
# ----------------------------------------------------------------------


def test_reply_swaps_sender_receiver():
    msg = AgentMessage.task_request("planner", "coder", payload={"x": 1})
    reply = msg.reply(MessageType.TASK_RESPONSE, payload={"result": "ok"})
    assert reply.sender_agent == "coder"
    assert reply.receiver_agent == "planner"
    assert reply.in_reply_to == msg.message_id
    assert reply.message_type == MessageType.TASK_RESPONSE


def test_reply_preserves_correlation_id():
    msg = AgentMessage.task_request(
        "planner",
        "coder",
        correlation_id="corr-123",
    )
    reply = msg.reply(MessageType.TASK_RESPONSE)
    assert reply.correlation_id == "corr-123"


def test_reply_preserves_task_id():
    msg = AgentMessage.task_request("planner", "coder", task_id="t1")
    reply = msg.reply(MessageType.TASK_RESPONSE)
    assert reply.task_id == "t1"


def test_reply_inherits_priority():
    msg = AgentMessage.task_request(
        "planner",
        "coder",
        priority=MessagePriority.CRITICAL,
    )
    reply = msg.reply(MessageType.TASK_RESPONSE)
    assert reply.priority == MessagePriority.CRITICAL


def test_reply_can_override_priority():
    msg = AgentMessage.task_request(
        "planner",
        "coder",
        priority=MessagePriority.CRITICAL,
    )
    reply = msg.reply(
        MessageType.TASK_RESPONSE,
        priority=MessagePriority.LOW,
    )
    assert reply.priority == MessagePriority.LOW


# ----------------------------------------------------------------------
# Queries
# ----------------------------------------------------------------------


def test_is_broadcast():
    msg = AgentMessage(sender_agent="a", receiver_agent="*")
    assert msg.is_broadcast is True


def test_is_not_broadcast():
    msg = AgentMessage(sender_agent="a", receiver_agent="b")
    assert msg.is_broadcast is False


def test_is_expired_no_ttl():
    msg = AgentMessage(sender_agent="a", receiver_agent="b", ttl=0)
    assert msg.is_expired is False


def test_is_expired_with_future_ttl():
    msg = AgentMessage(sender_agent="a", receiver_agent="b", ttl=100)
    assert msg.is_expired is False


def test_is_expired_with_past_ttl():
    # Create a message with a timestamp in the past.
    from datetime import datetime, timezone, timedelta

    past = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat(timespec="seconds")
    msg = AgentMessage(
        sender_agent="a",
        receiver_agent="b",
        timestamp=past,
        ttl=1.0,
    )
    assert msg.is_expired is True


def test_weight():
    msg = AgentMessage(
        sender_agent="a",
        receiver_agent="b",
        priority=MessagePriority.CRITICAL,
    )
    assert msg.weight == priority_weight(MessagePriority.CRITICAL)


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------


def test_serialize_roundtrip():
    msg = AgentMessage.task_request(
        "planner",
        "coder",
        task_id="t1",
        payload={"action": "write"},
        correlation_id="corr-1",
        priority=MessagePriority.HIGH,
        ttl=30.0,
    )
    msg.in_reply_to = "msg-prev"
    s = msg.serialize()
    msg2 = AgentMessage.deserialize(s)
    assert msg2.message_id == msg.message_id
    assert msg2.sender_agent == msg.sender_agent
    assert msg2.receiver_agent == msg.receiver_agent
    assert msg2.task_id == msg.task_id
    assert msg2.timestamp == msg.timestamp
    assert msg2.message_type == msg.message_type
    assert msg2.payload == msg.payload
    assert msg2.metadata == msg.metadata
    assert msg2.correlation_id == msg.correlation_id
    assert msg2.priority == msg.priority
    assert msg2.ttl == msg.ttl
    assert msg2.in_reply_to == msg.in_reply_to


def test_deserialize_unknown_type_raises():
    with pytest.raises(ValueError):
        AgentMessage.deserialize(
            {
                "sender_agent": "a",
                "receiver_agent": "b",
                "message_type": "unknown_type",
            }
        )


def test_deserialize_unknown_priority_raises():
    with pytest.raises(ValueError):
        AgentMessage.deserialize(
            {
                "sender_agent": "a",
                "receiver_agent": "b",
                "message_type": "task_request",
                "priority": "unknown_priority",
            }
        )


def test_deserialize_missing_fields_uses_defaults():
    msg = AgentMessage.deserialize(
        {
            "sender_agent": "a",
            "receiver_agent": "b",
            "message_type": "task_request",
        }
    )
    assert msg.priority == MessagePriority.NORMAL
    assert msg.ttl == 0.0
    assert msg.payload == {}


# ----------------------------------------------------------------------
# Repr
# ----------------------------------------------------------------------


def test_repr():
    msg = AgentMessage.task_request("planner", "coder")
    r = repr(msg)
    assert "AgentMessage" in r
    assert "task_request" in r
    assert "planner" in r
    assert "coder" in r


# ----------------------------------------------------------------------
# Bridge to wire format (skip if multiagent not available)
# ----------------------------------------------------------------------


def test_to_from_message_roundtrip():
    """Test conversion to/from the existing multiagent.Message wire format."""
    pytest.importorskip("multiagent")
    msg = AgentMessage.task_request(
        "planner",
        "coder",
        task_id="t1",
        payload={"action": "write"},
        correlation_id="corr-1",
        priority=MessagePriority.HIGH,
        ttl=30.0,
    )
    wire = msg.to_message()
    assert wire is not None
    msg2 = AgentMessage.from_message(wire)
    assert msg2.sender_agent == msg.sender_agent
    assert msg2.receiver_agent == msg.receiver_agent
    assert msg2.task_id == msg.task_id
    assert msg2.message_type == msg.message_type
    assert msg2.correlation_id == msg.correlation_id
    assert msg2.priority == msg.priority
    assert msg2.ttl == msg.ttl


def test_to_message_preserves_payload():
    pytest.importorskip("multiagent")
    msg = AgentMessage.task_request(
        "planner",
        "coder",
        payload={"action": "write", "count": 42},
    )
    wire = msg.to_message()
    msg2 = AgentMessage.from_message(wire)
    assert msg2.payload.get("action") == "write"
    assert msg2.payload.get("count") == 42


# ----------------------------------------------------------------------
# Exception hierarchy
# ----------------------------------------------------------------------


def test_communication_error_to_dict():
    e = CommunicationError("msg", details={"k": "v"})
    d = e.to_dict()
    assert d["error"] == "CommunicationError"
    assert d["message"] == "msg"
    assert d["details"] == {"k": "v"}


def test_communication_error_str():
    e = CommunicationError("msg", details={"k": "v"})
    assert "msg" in str(e)
    assert "k" in str(e)


def test_communication_error_no_message():
    e = CommunicationError()
    assert str(e) == "CommunicationError"
