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

# Task lifecycle (Sprint 1.7 — Phase 3)
TASK_CREATED = "task.created"
TASK_STARTED = "task.started"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
TASK_CANCELLED = "task.cancelled"
TASK_PAUSED = "task.paused"
TASK_RESUMED = "task.resumed"

# Workflow lifecycle (Sprint 1.7 — Phase 4)
WORKFLOW_CREATED = "workflow.created"
WORKFLOW_STARTED = "workflow.started"
WORKFLOW_COMPLETED = "workflow.completed"
WORKFLOW_FAILED = "workflow.failed"
WORKFLOW_CANCELLED = "workflow.cancelled"

# Planner lifecycle (Sprint 1.7 — Phase 5)
PLANNER_PLAN_CREATED = "planner.plan.created"
PLANNER_DECISION = "planner.decision"

# Observability (Sprint 1.7 — Phase 6)
METRIC_RECORDED = "metric.recorded"

# System lifecycle
SYSTEM_STARTED = "system.started"
SYSTEM_STOPPED = "system.stopped"
