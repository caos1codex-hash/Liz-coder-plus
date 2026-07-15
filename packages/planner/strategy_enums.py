"""Sprint 3.5 — Execution Strategy enums.

Defines the execution-mode and decision enums used by the
:class:`~planner.strategy.ExecutionStrategy` layer to decide how
each step (or group of steps) should be run.

These enums are intentionally string-based to match the project
convention (``str, Enum``) and keep their values JSON-serialisable
for plan persistence.
"""

from __future__ import annotations

from enum import Enum


class ExecutionMode(str, Enum):
    """How a step (or batch of steps) should be executed.

    Attributes:
        SEQUENTIAL: Run after the previous step finishes (default).
        PARALLEL: Run concurrently with sibling steps that share
            the same dependency closure.
        CONDITIONAL: Run only if a runtime predicate evaluates True.
        RETRY: Re-run a previously failed step with adjusted parameters.
        ESCALATE: Re-assign to a higher-tier agent or human review.
        HUMAN_CONFIRM: Pause and wait for explicit user approval.
    """

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    RETRY = "retry"
    ESCALATE = "escalate"
    HUMAN_CONFIRM = "human_confirm"


class FailureDecision(str, Enum):
    """Decision returned by the strategy layer when a step fails.

    Attributes:
        RETRY: Re-run the same step (up to ``max_retries``).
        ESCALATE: Switch to a higher-priority / different agent.
        SKIP: Mark the step as SKIPPED and continue.
        FAIL_PLAN: Abort the whole plan.
        HUMAN_REVIEW: Pause the plan and ask a human.
    """

    RETRY = "retry"
    ESCALATE = "escalate"
    SKIP = "skip"
    FAIL_PLAN = "fail_plan"
    HUMAN_REVIEW = "human_review"


class CapabilityRequestStatus(str, Enum):
    """Status of a request for a missing capability.

    Attributes:
        PENDING: Request raised but not yet satisfied.
        FULFILLED: An agent providing the capability was registered.
        DENIED: The request cannot be satisfied (e.g. user rejected).
        TIMEOUT: No agent appeared within the deadline.
    """

    PENDING = "pending"
    FULFILLED = "fulfilled"
    DENIED = "denied"
    TIMEOUT = "timeout"


__all__ = [
    "ExecutionMode",
    "FailureDecision",
    "CapabilityRequestStatus",
]
