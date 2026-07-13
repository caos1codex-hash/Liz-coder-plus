"""Observabilidad para workflows (Sprint 2.2).

Métricas expuestas:

- `workflow_duration`: histograma por workflow (ms).
- `step_duration`: histograma por step (ms).
- `parallelism`: nivel de paralelismo máximo alcanzado por workflow.
- `queue_time`: tiempo en READY antes de RUNNING (ms).
- `agent_usage`: tiempo total ocupado por agente (ms).
- `retry_count`: reintentos totales.
- `success_rate`: tasa de éxito por workflow / por agente / por step type.

Implementación: ring buffers en memoria (sin dependencias externas).
El `MetricsCollector` se suscribe al `EventBus` para construir las
métricas pasivamente, sin acoplar el engine.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .enums import WorkflowEvent
from .event_bus import EventBus, Event

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Histogram
# ----------------------------------------------------------------------


@dataclass
class Histogram:
    """Histograma simple con percentiles.

    Attributes:
        samples:      Buffer circular de muestras.
        count:        Número total de muestras.
        sum:          Suma de todas las muestras.
        min:          Mínimo.
        max:          Máximo.
    """

    capacity: int = 1000
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    count: int = 0
    sum: float = 0.0
    min: float | None = None
    max: float | None = None

    def record(self, value: float) -> None:
        self.samples.append(value)
        self.count += 1
        self.sum += value
        if self.min is None or value < self.min:
            self.min = value
        if self.max is None or value > self.max:
            self.max = value

    def mean(self) -> float:
        if self.count == 0:
            return 0.0
        return self.sum / self.count

    def percentile(self, p: float) -> float:
        """Devuelve el percentil p (0..100)."""
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        k = max(0, min(len(sorted_samples) - 1, int((p / 100.0) * len(sorted_samples))))
        return sorted_samples[k]

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean": round(self.mean(), 3),
            "min": self.min if self.min is not None else 0.0,
            "max": self.max if self.max is not None else 0.0,
            "p50": round(self.percentile(50), 3),
            "p95": round(self.percentile(95), 3),
            "p99": round(self.percentile(99), 3),
        }


# ----------------------------------------------------------------------
# MetricsCollector
# ----------------------------------------------------------------------


class MetricsCollector:
    """Recoge métricas de workflows suscribiéndose al EventBus.

    Métricas mantenidas:

    - `workflow_duration`:   Histogram por (workflow_name).
    - `step_duration`:       Histogram por (step_action).
    - `parallelism`:         Lista de niveles máximos por workflow.
    - `queue_time`:          Histogram por (step_action).
    - `agent_usage`:         Dict {agent_name: total_ms}.
    - `retry_count`:         Conteo total.
    - `success_rate`:        Conteo por (workflow / agent / step_action).
    - `workflow_count`:      Conteos por estado.
    """

    def __init__(
        self,
        event_bus: EventBus,
        *,
        history_size: int = 1000,
    ) -> None:
        self._bus = event_bus
        self._lock = asyncio.Lock()

        # Histogramas.
        self._workflow_duration: dict[str, Histogram] = defaultdict(
            lambda: Histogram(capacity=history_size)
        )
        self._step_duration: dict[str, Histogram] = defaultdict(
            lambda: Histogram(capacity=history_size)
        )
        self._queue_time: dict[str, Histogram] = defaultdict(
            lambda: Histogram(capacity=history_size)
        )

        # Contadores.
        self._agent_usage: dict[str, float] = defaultdict(float)
        self._retry_count: int = 0
        self._workflow_counts: dict[str, int] = defaultdict(int)
        self._success_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"success": 0, "failure": 0}
        )
        self._agent_success: dict[str, dict[str, int]] = defaultdict(
            lambda: {"success": 0, "failure": 0}
        )

        # Paralelismo.
        self._parallelism_samples: list[int] = []

        # Estado temporal para correlacionar eventos.
        self._step_start_times: dict[str, float] = {}  # step_id -> monotonic
        self._step_ready_times: dict[str, float] = {}  # step_id -> monotonic
        self._workflow_max_parallelism: dict[str, int] = defaultdict(int)
        self._workflow_current_parallelism: dict[str, int] = defaultdict(int)

        # Subscription tokens.
        self._tokens: list[str] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Se suscribe a los eventos relevantes del EventBus."""
        self._tokens.append(await self._bus.subscribe(
            self._on_workflow_started, event_type=WorkflowEvent.WORKFLOW_STARTED
        ))
        self._tokens.append(await self._bus.subscribe(
            self._on_workflow_completed, event_type=WorkflowEvent.WORKFLOW_COMPLETED
        ))
        self._tokens.append(await self._bus.subscribe(
            self._on_workflow_failed, event_type=WorkflowEvent.WORKFLOW_FAILED
        ))
        self._tokens.append(await self._bus.subscribe(
            self._on_workflow_cancelled, event_type=WorkflowEvent.WORKFLOW_CANCELLED
        ))
        self._tokens.append(await self._bus.subscribe(
            self._on_step_started, event_type=WorkflowEvent.STEP_STARTED
        ))
        self._tokens.append(await self._bus.subscribe(
            self._on_step_finished, event_type=WorkflowEvent.STEP_FINISHED
        ))
        self._tokens.append(await self._bus.subscribe(
            self._on_step_failed, event_type=WorkflowEvent.STEP_FAILED
        ))
        self._tokens.append(await self._bus.subscribe(
            self._on_step_retry, event_type=WorkflowEvent.STEP_RETRY
        ))
        logger.info("MetricsCollector started (%d subscriptions)", len(self._tokens))

    async def stop(self) -> None:
        """Se desuscribe."""
        for token in self._tokens:
            await self._bus.unsubscribe(token)
        self._tokens.clear()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _on_workflow_started(self, event: Event) -> None:
        async with self._lock:
            wf_id = event.payload.get("workflow_id", "")
            self._workflow_current_parallelism[wf_id] = 0
        self._workflow_counts["started"] += 1

    async def _on_workflow_completed(self, event: Event) -> None:
        async with self._lock:
            wf_id = event.payload.get("workflow_id", "")
            duration_ms = event.payload.get("duration_ms", 0.0)
            wf_name = event.payload.get("name", "unknown")
            self._workflow_duration[wf_name].record(float(duration_ms))
            max_par = self._workflow_max_parallelism.pop(wf_id, 0)
            self._parallelism_samples.append(max_par)
            self._workflow_current_parallelism.pop(wf_id, None)
            self._success_counts["workflow"]["success"] += 1
        self._workflow_counts["completed"] += 1

    async def _on_workflow_failed(self, event: Event) -> None:
        async with self._lock:
            wf_id = event.payload.get("workflow_id", "")
            duration_ms = event.payload.get("duration_ms", 0.0)
            wf_name = event.payload.get("name", "unknown")
            self._workflow_duration[wf_name].record(float(duration_ms))
            self._workflow_max_parallelism.pop(wf_id, None)
            self._workflow_current_parallelism.pop(wf_id, None)
            self._success_counts["workflow"]["failure"] += 1
        self._workflow_counts["failed"] += 1

    async def _on_workflow_cancelled(self, event: Event) -> None:
        async with self._lock:
            wf_id = event.payload.get("workflow_id", "")
            self._workflow_max_parallelism.pop(wf_id, None)
            self._workflow_current_parallelism.pop(wf_id, None)
        self._workflow_counts["cancelled"] += 1

    async def _on_step_started(self, event: Event) -> None:
        async with self._lock:
            step_id = event.payload.get("step_id", "")
            wf_id = event.payload.get("workflow_id", "")
            self._step_start_times[step_id] = time.monotonic()
            self._workflow_current_parallelism[wf_id] += 1
            current = self._workflow_current_parallelism[wf_id]
            if current > self._workflow_max_parallelism[wf_id]:
                self._workflow_max_parallelism[wf_id] = current

    async def _on_step_finished(self, event: Event) -> None:
        await self._record_step_end(event, success=True)

    async def _on_step_failed(self, event: Event) -> None:
        await self._record_step_end(event, success=False)

    async def _on_step_retry(self, event: Event) -> None:
        async with self._lock:
            self._retry_count += 1

    async def _record_step_end(self, event: Event, *, success: bool) -> None:
        async with self._lock:
            step_id = event.payload.get("step_id", "")
            wf_id = event.payload.get("workflow_id", "")
            action = event.payload.get("action", "unknown")
            agent = event.payload.get("agent", "unknown")
            duration_ms = float(event.payload.get("duration_ms", 0.0))

            start = self._step_start_times.pop(step_id, None)
            if start is not None:
                # step_duration: si el evento trae duration_ms, usarlo;
                # si no, calcularlo.
                actual_duration = duration_ms or (
                    (time.monotonic() - start) * 1000.0
                )
                self._step_duration[action].record(actual_duration)
            else:
                self._step_duration[action].record(duration_ms)

            # queue_time: si tenemos start_time del READY previo, usarlo.
            ready_time = self._step_ready_times.pop(step_id, None)
            if ready_time is not None and start is not None:
                queue_ms = (start - ready_time) * 1000.0
                self._queue_time[action].record(queue_ms)

            # agent_usage
            self._agent_usage[agent] += duration_ms

            # success_count por agent
            if success:
                self._agent_success[agent]["success"] += 1
            else:
                self._agent_success[agent]["failure"] += 1

            # success_count por step action
            key = f"step:{action}"
            if success:
                self._success_counts[key]["success"] += 1
            else:
                self._success_counts[key]["failure"] += 1

            # Decrement paralelismo.
            self._workflow_current_parallelism[wf_id] = max(
                0, self._workflow_current_parallelism[wf_id] - 1
            )

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Devuelve todas las métricas agregadas."""
        # success_rate global.
        wf_success = self._success_counts["workflow"]["success"]
        wf_failure = self._success_counts["workflow"]["failure"]
        wf_total = wf_success + wf_failure
        wf_rate = (wf_success / wf_total) if wf_total > 0 else 0.0

        return {
            "workflow_duration": {
                name: h.to_dict() for name, h in self._workflow_duration.items()
            },
            "step_duration": {
                action: h.to_dict() for action, h in self._step_duration.items()
            },
            "queue_time": {
                action: h.to_dict() for action, h in self._queue_time.items()
            },
            "parallelism": {
                "samples": len(self._parallelism_samples),
                "max": max(self._parallelism_samples, default=0),
                "mean": round(
                    statistics.mean(self._parallelism_samples), 2
                ) if self._parallelism_samples else 0.0,
            },
            "agent_usage": {
                agent: round(ms, 2) for agent, ms in self._agent_usage.items()
            },
            "retry_count": self._retry_count,
            "success_rate": {
                "workflow": round(wf_rate, 4),
                "by_agent": {
                    a: round(
                        s["success"] / max(s["success"] + s["failure"], 1), 4
                    )
                    for a, s in self._agent_success.items()
                },
                "by_step_action": {
                    k.split(":", 1)[1] if ":" in k else k: round(
                        s["success"] / max(s["success"] + s["failure"], 1), 4
                    )
                    for k, s in self._success_counts.items()
                    if k.startswith("step:")
                },
            },
            "workflow_counts": dict(self._workflow_counts),
            "agent_success": {
                a: dict(s) for a, s in self._agent_success.items()
            },
        }

    async def reset(self) -> None:
        """Limpia todas las métricas."""
        async with self._lock:
            self._workflow_duration.clear()
            self._step_duration.clear()
            self._queue_time.clear()
            self._agent_usage.clear()
            self._retry_count = 0
            self._workflow_counts.clear()
            self._success_counts.clear()
            self._agent_success.clear()
            self._parallelism_samples.clear()
            self._step_start_times.clear()
            self._step_ready_times.clear()
            self._workflow_max_parallelism.clear()
            self._workflow_current_parallelism.clear()


__all__ = ["Histogram", "MetricsCollector"]
