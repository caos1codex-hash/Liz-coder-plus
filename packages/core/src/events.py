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

# Plan execution lifecycle (Sprint 3.6)
PLAN_CREATED = "plan.created"
PLAN_VALIDATING = "plan.validating"
PLAN_READY = "plan.ready"
PLAN_STARTED = "plan.started"
PLAN_PAUSED = "plan.paused"
PLAN_COMPLETED = "plan.completed"
PLAN_FAILED = "plan.failed"
PLAN_CANCELLED = "plan.cancelled"

PLAN_TASK_ASSIGNED = "plan.task.assigned"
PLAN_TASK_STARTED = "plan.task.started"
PLAN_TASK_COMPLETED = "plan.task.completed"
PLAN_TASK_FAILED = "plan.task.failed"
PLAN_TASK_SKIPPED = "plan.task.skipped"
PLAN_TASK_RETRY = "plan.task.retry"

# Observability (Sprint 1.7 — Phase 6)
METRIC_RECORDED = "metric.recorded"

# Scheduler lifecycle (Sprint 1.8 — Phase 3)
SCHEDULER_JOB_SCHEDULED = "scheduler.job.scheduled"
SCHEDULER_JOB_STARTED = "scheduler.job.started"
SCHEDULER_JOB_COMPLETED = "scheduler.job.completed"
SCHEDULER_JOB_FAILED = "scheduler.job.failed"
SCHEDULER_JOB_CANCELLED = "scheduler.job.cancelled"
SCHEDULER_JOB_RETRIED = "scheduler.job.retried"
SCHEDULER_STARTED = "scheduler.started"
SCHEDULER_STOPPED = "scheduler.stopped"

# Recovery lifecycle (Sprint 1.8 — Phase 4)
RECOVERY_STARTED = "recovery.started"
RECOVERY_COMPLETED = "recovery.completed"
RECOVERY_TASK_RESUMED = "recovery.task.resumed"
RECOVERY_WORKFLOW_RESUMED = "recovery.workflow.resumed"
CHECKPOINT_SAVED = "checkpoint.saved"
CHECKPOINT_RESTORED = "checkpoint.restored"

# System lifecycle
SYSTEM_STARTED = "system.started"
SYSTEM_STOPPED = "system.stopped"

# Plan execution tracking (Sprint 3.7)
TRACKING_PLAN_PENDING = "tracking.plan.pending"
TRACKING_PLAN_PLANNING = "tracking.plan.planning"
TRACKING_PLAN_EXECUTING = "tracking.plan.executing"
TRACKING_PLAN_RUNNING_STEP = "tracking.plan.running_step"
TRACKING_PLAN_BLOCKED = "tracking.plan.blocked"
TRACKING_PLAN_RESUMED = "tracking.plan.resumed"
TRACKING_STEP_PROGRESS = "tracking.step.progress"
TRACKING_SNAPSHOT_SAVED = "tracking.snapshot.saved"

# Execution graph lifecycle (Sprint 3.8)
EXEC_GRAPH_TASK_READY = "exec_graph.task.ready"
EXEC_GRAPH_TASK_RUNNING = "exec_graph.task.running"
EXEC_GRAPH_TASK_COMPLETED = "exec_graph.task.completed"
EXEC_GRAPH_TASK_FAILED = "exec_graph.task.failed"
EXEC_GRAPH_TASK_RETRY = "exec_graph.task.retry"
EXEC_GRAPH_TASK_SKIPPED = "exec_graph.task.skipped"
EXEC_GRAPH_PLAN_RECOVERED = "exec_graph.plan.recovered"
EXEC_GRAPH_PLAN_RESUMED = "exec_graph.plan.resumed"
EXEC_GRAPH_PARALLEL_BATCH = "exec_graph.parallel.batch"
EXEC_GRAPH_CRITICAL_PATH = "exec_graph.critical_path"
