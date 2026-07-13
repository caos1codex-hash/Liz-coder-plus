"""System event definitions.

These event type strings are published to the internal EventBus and
subscribed to by interested modules (UI, logging, auditing, etc.).
"""

from __future__ import annotations

# User lifecycle
USER_MESSAGE_RECEIVED = "user.message.received"
ASSISTANT_RESPONSE_SENT = "assistant.response.sent"

# Agent lifecycle
AGENT_INVOKED = "agent.invoked"
AGENT_COMPLETED = "agent.completed"
AGENT_FAILED = "agent.failed"

# Memory lifecycle
MEMORY_STORED = "memory.stored"
MEMORY_RETRIEVED = "memory.retrieved"

# Tool lifecycle
TOOL_REQUESTED = "tool.requested"
TOOL_APPROVED = "tool.approved"
TOOL_DENIED = "tool.denied"
TOOL_EXECUTED = "tool.executed"
TOOL_FAILED = "tool.failed"

# Permission lifecycle
PERMISSION_PROMPTED = "permission.prompted"
PERMISSION_GRANTED = "permission.granted"
PERMISSION_REVOKED = "permission.revoked"

# System lifecycle
SYSTEM_STARTED = "system.started"
SYSTEM_STOPPED = "system.stopped"
