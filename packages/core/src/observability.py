"""Observability: metrics, structured logging, and event auditing.

Sprint 1.7 — Phase 6.

This module provides:

  - ``MetricsCollector``: subscribes to the EventBus and maintains
    counters / histograms for tasks, tools, agents and workflows.
  - ``StructuredLogger``: emits JSON-formatted log lines with
    ``task_id``, ``workflow_id`` and ``correlation_id`` fields.
  - ``AuditRecorder``: keeps a bounded in-memory audit trail of
    every event seen, useful for debugging and tests.

The metrics collector is intentionally in-process: no Prometheus /
OpenTelemetry dependency is required. Future sprints can add an
exporter that scrapes ``snapshot()`` periodically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from src.events import (
    AGENT_COMPLETED,
    AGENT_FAILED,
    AGENT_INVOKED,
    METRIC_RECORDED,
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_CREATED,
    TASK_FAILED,
    TASK_PAUSED,
    TASK_RESUMED,
    TASK_STARTED,
    TOOL_DENIED,
    TOOL_EXECUTED,
    TOOL_FAILED,
    TOOL_REQUESTED,
    WORKFLOW_CANCELLED,
    WORKFLOW_COMPLETED,
    WORKFLOW_CREATED,
    WORKFLOW_FAILED,
    WORKFLOW_STARTED,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Metrics data structures
# ----------------------------------------------------------------------


@dataclass
class Counter:
    """A monotonically increasing counter."""

    value: int = 0

    def inc(self, n: int = 1) -> None:
        self.value += n


@dataclass
class Histogram:
    """A simple histogram with min/max/avg/count/sum.

    Buckets are not stored; this is a lightweight summary suitable
    for in-process inspection. For real percentiles, future sprints
    can plug in ``prometheus_client`` or similar.
    """

    count: int = 0
    sum: float = 0.0
    min: float = float("inf")
    max: float = 0.0

    def observe(self, value: float) -> None:
        self.count += 1
        self.sum += value
        if value < self.min:
            self.min = value
        if value > self.max:
            self.max = value

    @property
    def avg(self) -> float:
        return self.sum / self.count if self.count > 0 else 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "count": self.count,
            "sum": self.sum,
            "min": self.min if self.count > 0 else 0.0,
            "max": self.max,
            "avg": self.avg,
        }


# ----------------------------------------------------------------------
# Metrics Collector
# ----------------------------------------------------------------------


class MetricsCollector:
    """Subscribes to the EventBus and records metrics.

    Usage::

        bus = EventBus()
        metrics = MetricsCollector(bus)
        await metrics.start()
        # ... system runs, events are emitted ...
        snapshot = metrics.snapshot()
        await metrics.stop()

    The collector is safe to use without an event bus (in which case
    ``record_*`` methods can be called directly).
    """

    # Maximum number of audit entries to keep in memory.
    AUDIT_BUFFER = 500

    def __init__(self, event_bus: Any | None = None) -> None:
        self._bus = event_bus
        self._counters: dict[str, Counter] = defaultdict(Counter)
        self._histograms: dict[str, Histogram] = defaultdict(Histogram)
        self._audit: deque[dict[str, Any]] = deque(maxlen=self.AUDIT_BUFFER)
        self._lock = threading.Lock()
        self._subscriptions: list[tuple[str, Any]] = []
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to all known lifecycle events."""
        if self._started or self._bus is None:
            self._started = True
            return

        # Map event types to handler methods.
        subscriptions = [
            (TASK_CREATED, self._on_task_created),
            (TASK_STARTED, self._on_task_started),
            (TASK_COMPLETED, self._on_task_completed),
            (TASK_FAILED, self._on_task_failed),
            (TASK_CANCELLED, self._on_task_cancelled),
            (TASK_PAUSED, self._on_task_paused),
            (TASK_RESUMED, self._on_task_resumed),
            (WORKFLOW_CREATED, self._on_workflow_created),
            (WORKFLOW_STARTED, self._on_workflow_started),
            (WORKFLOW_COMPLETED, self._on_workflow_completed),
            (WORKFLOW_FAILED, self._on_workflow_failed),
            (WORKFLOW_CANCELLED, self._on_workflow_cancelled),
            (AGENT_INVOKED, self._on_agent_invoked),
            (AGENT_COMPLETED, self._on_agent_completed),
            (AGENT_FAILED, self._on_agent_failed),
            (TOOL_REQUESTED, self._on_tool_requested),
            (TOOL_EXECUTED, self._on_tool_executed),
            (TOOL_FAILED, self._on_tool_failed),
            (TOOL_DENIED, self._on_tool_denied),
        ]
        for event_type, handler in subscriptions:
            self._bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))

        self._started = True
        logger.info("MetricsCollector started (%d subscriptions)",
                    len(self._subscriptions))

    async def stop(self) -> None:
        """Unsubscribe all handlers."""
        if self._bus is None:
            self._started = False
            return
        for event_type, handler in self._subscriptions:
            try:
                self._bus.unsubscribe(event_type, handler)
            except Exception:  # noqa: BLE001
                pass
        self._subscriptions.clear()
        self._started = False

    # ------------------------------------------------------------------
    # Public manual recording (for use without a bus)
    # ------------------------------------------------------------------

    def inc_counter(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name].inc(n)

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._histograms[name].observe(value)

    def record_audit(self, event_type: str, payload: Any) -> None:
        """Record an audit entry."""
        with self._lock:
            self._audit.append({
                "ts": time.monotonic(),
                "event_type": event_type,
                "payload": payload,
            })

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of all metrics."""
        with self._lock:
            counters = {k: v.value for k, v in self._counters.items()}
            histograms = {k: v.to_dict() for k, v in self._histograms.items()}
            audit = list(self._audit)
        return {
            "counters": counters,
            "histograms": histograms,
            "audit_count": len(audit),
            "audit_last_20": audit[-20:],
        }

    def counter_value(self, name: str) -> int:
        """Return the current value of a counter."""
        with self._lock:
            return self._counters.get(name, Counter()).value

    def histogram_value(self, name: str) -> dict[str, float]:
        """Return the current histogram summary."""
        with self._lock:
            return self._histograms.get(name, Histogram()).to_dict()

    # ------------------------------------------------------------------
    # Event handlers (async, called by EventBus)
    # ------------------------------------------------------------------

    async def _on_task_created(self, payload: Any) -> None:
        self.inc_counter("tasks_created")
        self.record_audit(TASK_CREATED, payload)

    async def _on_task_started(self, payload: Any) -> None:
        self.inc_counter("tasks_started")
        self.record_audit(TASK_STARTED, payload)

    async def _on_task_completed(self, payload: Any) -> None:
        self.inc_counter("tasks_completed")
        self.record_audit(TASK_COMPLETED, payload)

    async def _on_task_failed(self, payload: Any) -> None:
        self.inc_counter("tasks_failed")
        # Track errors by type if available.
        error_type = "unknown"
        if isinstance(payload, dict):
            error_type = payload.get("error_type", "unknown")
        self.inc_counter(f"task_errors_by_type.{error_type}")
        self.record_audit(TASK_FAILED, payload)

    async def _on_task_cancelled(self, payload: Any) -> None:
        self.inc_counter("tasks_cancelled")
        self.record_audit(TASK_CANCELLED, payload)

    async def _on_task_paused(self, payload: Any) -> None:
        self.inc_counter("tasks_paused")
        self.record_audit(TASK_PAUSED, payload)

    async def _on_task_resumed(self, payload: Any) -> None:
        self.inc_counter("tasks_resumed")
        # Count retries separately.
        if isinstance(payload, dict) and payload.get("reason") == "retry":
            self.inc_counter("task_retries")
        self.record_audit(TASK_RESUMED, payload)

    async def _on_workflow_created(self, payload: Any) -> None:
        self.inc_counter("workflows_created")
        self.record_audit(WORKFLOW_CREATED, payload)

    async def _on_workflow_started(self, payload: Any) -> None:
        self.inc_counter("workflows_started")
        self.record_audit(WORKFLOW_STARTED, payload)

    async def _on_workflow_completed(self, payload: Any) -> None:
        self.inc_counter("workflows_completed")
        self.record_audit(WORKFLOW_COMPLETED, payload)

    async def _on_workflow_failed(self, payload: Any) -> None:
        self.inc_counter("workflows_failed")
        self.record_audit(WORKFLOW_FAILED, payload)

    async def _on_workflow_cancelled(self, payload: Any) -> None:
        self.inc_counter("workflows_cancelled")
        self.record_audit(WORKFLOW_CANCELLED, payload)

    async def _on_agent_invoked(self, payload: Any) -> None:
        self.inc_counter("agent_invocations")
        if isinstance(payload, dict):
            agent_name = payload.get("agent_name", "unknown")
            self.inc_counter(f"agent_invocations.{agent_name}")
        self.record_audit(AGENT_INVOKED, payload)

    async def _on_agent_completed(self, payload: Any) -> None:
        self.inc_counter("agent_completions")
        if isinstance(payload, dict):
            agent_name = payload.get("agent_name", "unknown")
            duration_ms = payload.get("duration_ms", 0)
            self.observe(f"agent_duration_ms.{agent_name}", float(duration_ms))
            self.observe("agent_duration_ms.total", float(duration_ms))
        self.record_audit(AGENT_COMPLETED, payload)

    async def _on_agent_failed(self, payload: Any) -> None:
        self.inc_counter("agent_failures")
        if isinstance(payload, dict):
            error_type = payload.get("error_type", "unknown")
            self.inc_counter(f"agent_errors_by_type.{error_type}")
        self.record_audit(AGENT_FAILED, payload)

    async def _on_tool_requested(self, payload: Any) -> None:
        self.inc_counter("tool_requests")
        if isinstance(payload, dict):
            tool_name = payload.get("tool_name", "unknown")
            self.inc_counter(f"tool_requests.{tool_name}")
        self.record_audit(TOOL_REQUESTED, payload)

    async def _on_tool_executed(self, payload: Any) -> None:
        self.inc_counter("tool_executions")
        if isinstance(payload, dict):
            tool_name = payload.get("tool_name", "unknown")
            duration_ms = payload.get("duration_ms", 0)
            self.inc_counter(f"tool_executions.{tool_name}")
            self.observe(f"tool_duration_ms.{tool_name}", float(duration_ms))
            self.observe("tool_duration_ms.total", float(duration_ms))
        self.record_audit(TOOL_EXECUTED, payload)

    async def _on_tool_failed(self, payload: Any) -> None:
        self.inc_counter("tool_failures")
        if isinstance(payload, dict):
            tool_name = payload.get("tool_name", "unknown")
            error_type = payload.get("error_type", "unknown")
            self.inc_counter(f"tool_failures.{tool_name}")
            self.inc_counter(f"tool_errors_by_type.{error_type}")
        self.record_audit(TOOL_FAILED, payload)

    async def _on_tool_denied(self, payload: Any) -> None:
        self.inc_counter("tool_denials")
        if isinstance(payload, dict):
            tool_name = payload.get("tool_name", "unknown")
            self.inc_counter(f"tool_denials.{tool_name}")
        self.record_audit(TOOL_DENIED, payload)


# ----------------------------------------------------------------------
# Structured Logger
# ----------------------------------------------------------------------


class StructuredLogger:
    """JSON-line logger that adds task/workflow/correlation IDs.

    Wraps the standard ``logging`` module: every log record is
    serialized to a single JSON line with consistent fields.

    Usage::

        slog = StructuredLogger("orchestrator")
        slog.info("message received", task_id="t1", session_id="s1")
    """

    def __init__(
        self,
        name: str,
        *,
        level: int = logging.INFO,
        correlation_id: str | None = None,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._correlation_id = correlation_id
        # Ensure we don't double-install handlers.
        if not any(
            isinstance(h, _StructuredHandler) for h in self._logger.handlers
        ):
            self._logger.addHandler(_StructuredHandler())

    @property
    def correlation_id(self) -> str | None:
        return self._correlation_id

    @correlation_id.setter
    def correlation_id(self, value: str | None) -> None:
        self._correlation_id = value

    def with_correlation_id(self, cid: str) -> "StructuredLogger":
        """Return a new logger with the given correlation id."""
        return StructuredLogger(
            self._logger.name,
            level=self._logger.level,
            correlation_id=cid,
        )

    def debug(self, msg: str, **fields: Any) -> None:
        self._log(logging.DEBUG, msg, fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._log(logging.INFO, msg, fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._log(logging.WARNING, msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._log(logging.ERROR, msg, fields)

    def exception(self, msg: str, **fields: Any) -> None:
        self._log(logging.ERROR, msg, fields, exc_info=True)

    def _log(
        self,
        level: int,
        msg: str,
        fields: dict[str, Any],
        *,
        exc_info: bool = False,
    ) -> None:
        extra: dict[str, Any] = dict(fields)
        if self._correlation_id is not None:
            extra.setdefault("correlation_id", self._correlation_id)
        extra["structured"] = True
        self._logger.log(level, msg, extra=extra, exc_info=exc_info)


class _StructuredHandler(logging.Handler):
    """Logging handler that emits JSON lines."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload: dict[str, Any] = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            # Pull structured fields out of the ``extra`` kwarg.
            for key in ("task_id", "workflow_id", "correlation_id",
                        "session_id", "agent_name", "tool_name",
                        "stage", "error_type", "duration_ms"):
                value = getattr(record, key, None)
                if value is not None:
                    payload[key] = value
            # Include any other extras (excluding the standard ones).
            standard = set(vars(record).keys()) - {"structured"}
            for k, v in vars(record).items():
                if k in standard and k not in payload and k != "structured":
                    if k.startswith("_"):
                        continue
                    payload[k] = v
            if record.exc_info:
                payload["exc"] = self.format(record)
            print(json.dumps(payload, default=str), flush=True)
        except Exception:  # noqa: BLE001
            # Never let logging break the application.
            self.handleError(record)


# ----------------------------------------------------------------------
# Convenience: a single audit recorder for tests / debugging
# ----------------------------------------------------------------------


class AuditRecorder:
    """Tiny in-memory audit log of every event seen.

    Useful for tests and ad-hoc debugging. Not designed for production
    scale (no persistence, bounded buffer).
    """

    def __init__(self, capacity: int = 1000) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)

    async def record(self, event_type: str, payload: Any) -> None:
        self._entries.append({
            "ts": time.monotonic(),
            "event_type": event_type,
            "payload": payload,
        })

    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def by_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self._entries if e["event_type"] == event_type]

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


__all__ = [
    "AuditRecorder",
    "Counter",
    "Histogram",
    "MetricsCollector",
    "StructuredLogger",
]
