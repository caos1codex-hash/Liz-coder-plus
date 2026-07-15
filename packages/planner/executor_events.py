"""Sprint 3.4 — Planner execution event names.

This module defines the canonical event-name strings the
:class:`~planner.executor.PlannerExecutor` publishes on the workflow
:class:`~multiagent.workflow.event_bus.EventBus` (and/or any other bus
the caller wires up).  Keeping them in a dedicated module makes them
easy to import from tests and from telemetry collectors without
creating a circular import with :mod:`planner.executor`.

The strings are intentionally **plain strings** (not members of the
``WorkflowEvent`` enum) so they do not collide with the engine's own
events; the workflow EventBus accepts any ``str`` event type.

Event taxonomy (prefix ``planner.``)::

    planner.plan.submitted     — executor received a TaskPlan
    planner.plan.translated    — TaskPlan → Workflow built
    planner.plan.started       — engine.run began
    planner.plan.completed     — workflow COMPLETED
    planner.plan.failed        — workflow FAILED
    planner.plan.cancelled     — workflow CANCELLED
    planner.step.translated    — PlanStep → WfStep mapping emitted
    planner.step.started       — step entered RUNNING
    planner.step.finished      — step reached terminal state
"""

from __future__ import annotations

# -- Plan-level --------------------------------------------------------------
PLAN_SUBMITTED = "planner.plan.submitted"
PLAN_TRANSLATED = "planner.plan.translated"
PLAN_STARTED = "planner.plan.started"
PLAN_COMPLETED = "planner.plan.completed"
PLAN_FAILED = "planner.plan.failed"
PLAN_CANCELLED = "planner.plan.cancelled"

# -- Step-level --------------------------------------------------------------
STEP_TRANSLATED = "planner.step.translated"
STEP_STARTED = "planner.step.started"
STEP_FINISHED = "planner.step.finished"

ALL_EVENTS: tuple[str, ...] = (
    PLAN_SUBMITTED,
    PLAN_TRANSLATED,
    PLAN_STARTED,
    PLAN_COMPLETED,
    PLAN_FAILED,
    PLAN_CANCELLED,
    STEP_TRANSLATED,
    STEP_STARTED,
    STEP_FINISHED,
)

__all__ = [
    "PLAN_SUBMITTED",
    "PLAN_TRANSLATED",
    "PLAN_STARTED",
    "PLAN_COMPLETED",
    "PLAN_FAILED",
    "PLAN_CANCELLED",
    "STEP_TRANSLATED",
    "STEP_STARTED",
    "STEP_FINISHED",
    "ALL_EVENTS",
]
