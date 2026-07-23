"""LoadBalancer para el workflow engine (Sprint 2.2).

Distribuye trabajo entre agentes según:

- **estado**: IDLE > BUSY > PAUSED > ERROR > STOPPED (sólo IDLE acepta)
- **CPU**: best-effort (psutil si está disponible, 0 si no)
- **RAM**: best-effort RSS actual
- **cola**: número de steps pendientes asignados al agente
- **especialidad**: match entre `step.action` y `agent.objectives`
- **historial**: success_rate y avg_duration_ms por (agent, action)

El LoadBalancer no ejecuta steps; sólo devuelve el nombre del agente
recomendado. El Scheduler decide cuándo usar esa recomendación.

Algoritmo: scoring ponderado.

    score = w_state   * state_score      (0..1)
          + w_cpu     * (1 - cpu)        (0..1)
          + w_ram     * (1 - ram_norm)   (0..1)
          + w_queue   * (1 - queue_norm) (0..1)
          + w_specialty * specialty_match(0..1)
          + w_history * success_rate     (0..1)

Pesos por defecto (configurables):

    w_state=0.30, w_cpu=0.10, w_ram=0.10, w_queue=0.20,
    w_specialty=0.20, w_history=0.10
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..base import BaseAgent
from ..enums import AgentStatus

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Métricas por agente
# ----------------------------------------------------------------------


@dataclass
class AgentMetrics:
    """Métricas operativas de un agente para el load balancer.

    Attributes:
        name:              Nombre del agente.
        status:            AgentStatus actual.
        cpu_percent:       0..100 (best-effort).
        ram_kb:            RSS en KB (best-effort).
        queue_size:        Número de steps pendientes asignados.
        success_rate:      0..1 (rolling).
        avg_duration_ms:   Duración media de las últimas N ejecuciones.
        total_runs:        Total de ejecuciones registradas.
        specialty_score:   0..1 (match con el step actual).
    """

    name: str = ""
    status: AgentStatus = AgentStatus.IDLE
    cpu_percent: float = 0.0
    ram_kb: int = 0
    queue_size: int = 0
    success_rate: float = 1.0
    avg_duration_ms: float = 0.0
    total_runs: int = 0
    specialty_score: float = 0.0


# ----------------------------------------------------------------------
# Pesos
# ----------------------------------------------------------------------


@dataclass
class LoadBalancerWeights:
    """Pesos del LoadBalancer. Deben sumar 1.0 (se normalizan si no)."""

    w_state: float = 0.30
    w_cpu: float = 0.10
    w_ram: float = 0.10
    w_queue: float = 0.20
    w_specialty: float = 0.20
    w_history: float = 0.10

    def normalized(self) -> "LoadBalancerWeights":
        total = (
            self.w_state + self.w_cpu + self.w_ram + self.w_queue
            + self.w_specialty + self.w_history
        )
        if total <= 0:
            return LoadBalancerWeights()
        return LoadBalancerWeights(
            w_state=self.w_state / total,
            w_cpu=self.w_cpu / total,
            w_ram=self.w_ram / total,
            w_queue=self.w_queue / total,
            w_specialty=self.w_specialty / total,
            w_history=self.w_history / total,
        )


# ----------------------------------------------------------------------
# LoadBalancer
# ----------------------------------------------------------------------


class LoadBalancer:
    """Balanceador de carga con scoring ponderado.

    Mantiene un historial de ejecuciones por (agent, action) para
    calcular `success_rate` y `avg_duration_ms`.
    """

    HISTORY_LIMIT = 100  # últimas N ejecuciones por (agent, action)

    def __init__(
        self,
        *,
        weights: LoadBalancerWeights | None = None,
        enable_cpu_probe: bool = True,
    ) -> None:
        self._weights = (weights or LoadBalancerWeights()).normalized()
        self._enable_cpu_probe = enable_cpu_probe
        # Historial: {(agent, action): [(success, duration_ms), ...]}
        self._history: dict[tuple[str, str], list[tuple[bool, float]]] = {}
        # Asignaciones pendientes: {agent_name: pending_count}
        self._pending: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Selección
    # ------------------------------------------------------------------

    def select(
        self,
        candidates: list[BaseAgent],
        *,
        action: str = "",
        exclude: set[str] | None = None,
    ) -> BaseAgent | None:
        """Selecciona el mejor agente para ejecutar `action`.

        Args:
            candidates: Lista de agentes candidatos.
            action:     Acción/step a ejecutar (para scoring de especialidad).
            exclude:    Nombres de agentes a excluir (ya fallaron, p.ej.).
        """
        exclude = exclude or set()
        eligible = [
            a for a in candidates
            if a.name not in exclude and a.status == AgentStatus.IDLE
        ]
        if not eligible:
            return None

        if len(eligible) == 1:
            return eligible[0]

        # Calcular scores.
        scored: list[tuple[float, BaseAgent]] = []
        # Pre-calcular max queue y max ram para normalización.
        max_queue = max((self._pending.get(a.name, 0) for a in eligible), default=1) or 1
        max_ram = max((self._probe_ram(a) for a in eligible), default=1) or 1

        for agent in eligible:
            metrics = self._collect_metrics(agent, action, max_queue, max_ram)
            score = self._score(metrics)
            scored.append((score, agent))

        # Mayor score gana.
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _score(self, m: AgentMetrics) -> float:
        """Calcula el score ponderado (mayor = mejor)."""
        w = self._weights
        # state_score: IDLE=1.0 (ya filtrado arriba, pero mantén por consistencia)
        state_score = 1.0 if m.status == AgentStatus.IDLE else 0.0
        cpu_score = (100.0 - m.cpu_percent) / 100.0
        ram_score = 1.0 - (m.ram_kb / max(m.ram_kb * 2, 1))  # normalización suave
        # Normalización más estable: usar 1 - (ram / max_ram_observed).
        # Aquí usamos una heurística simple.
        ram_score = max(0.0, min(1.0, ram_score))
        queue_score = 1.0 - (m.queue_size / max(m.queue_size + 1, 1))
        specialty_score = m.specialty_score
        history_score = m.success_rate

        return (
            w.w_state * state_score
            + w.w_cpu * cpu_score
            + w.w_ram * ram_score
            + w.w_queue * queue_score
            + w.w_specialty * specialty_score
            + w.w_history * history_score
        )

    # ------------------------------------------------------------------
    # Recolección de métricas
    # ------------------------------------------------------------------

    def _collect_metrics(
        self,
        agent: BaseAgent,
        action: str,
        max_queue: int,
        max_ram: int,
    ) -> AgentMetrics:
        m = AgentMetrics(
            name=agent.name,
            status=agent.status,
            cpu_percent=self._probe_cpu(agent),
            ram_kb=self._probe_ram(agent),
            queue_size=self._pending.get(agent.name, 0),
            specialty_score=self._specialty_match(agent, action),
        )
        # Historial.
        if action:
            hist = self._history.get((agent.name, action), [])
            if hist:
                successes = sum(1 for s, _ in hist if s)
                m.success_rate = successes / len(hist)
                m.avg_duration_ms = sum(d for _, d in hist) / len(hist)
                m.total_runs = len(hist)
        return m

    def _specialty_match(self, agent: BaseAgent, action: str) -> float:
        """Devuelve 0..1 indicando qué tan bien el agente cubre `action`."""
        if not action:
            return 0.5  # neutral
        action_lower = action.lower()
        # 1. Match directo en objectives.
        for obj in agent.objectives:
            if obj.lower() in action_lower or action_lower in obj.lower():
                return 1.0
        # 2. Match en name.
        if agent.name.lower() in action_lower:
            return 0.8
        # 3. Match en tools.
        for t in agent.tools:
            if t.lower() in action_lower:
                return 0.6
        return 0.3  # match débil

    # ------------------------------------------------------------------
    # Probes (best-effort)
    # ------------------------------------------------------------------

    def _probe_cpu(self, agent: BaseAgent) -> float:
        """Best-effort CPU%. 0 si no se puede medir."""
        if not self._enable_cpu_probe:
            return 0.0
        try:
            import psutil  # type: ignore[import-not-found]
            return psutil.cpu_percent(interval=None)
        except ImportError:
            return 0.0
        except Exception:  # noqa: BLE001
            return 0.0

    def _probe_ram(self, agent: BaseAgent) -> int:
        """Best-effort RSS en KB."""
        try:
            # /proc/self/status en Linux.
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except (FileNotFoundError, ValueError, OSError):
            pass
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------------
    # Registro de ejecuciones (para historial)
    # ------------------------------------------------------------------

    def record_execution(
        self,
        agent_name: str,
        action: str,
        *,
        success: bool,
        duration_ms: float,
    ) -> None:
        """Registra el resultado de una ejecución para el historial."""
        key = (agent_name, action)
        self._history.setdefault(key, []).append((success, duration_ms))
        # Truncar al límite.
        if len(self._history[key]) > self.HISTORY_LIMIT:
            self._history[key] = self._history[key][-self.HISTORY_LIMIT:]

    # ------------------------------------------------------------------
    # Pending queue (asignaciones pendientes)
    # ------------------------------------------------------------------

    def assign(self, agent_name: str) -> None:
        """Marca un step como asignado al agente (incrementa queue_size)."""
        self._pending[agent_name] = self._pending.get(agent_name, 0) + 1

    def release(self, agent_name: str) -> None:
        """Marca un step como completado por el agente."""
        if agent_name in self._pending:
            self._pending[agent_name] = max(0, self._pending[agent_name] - 1)

    def pending_for(self, agent_name: str) -> int:
        return self._pending.get(agent_name, 0)

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def agent_metrics(self, agent: BaseAgent, action: str = "") -> AgentMetrics:
        """Devuelve las métricas actuales de un agente."""
        return self._collect_metrics(
            agent, action, max_queue=1, max_ram=1
        )

    def stats(self) -> dict[str, Any]:
        """Estadísticas del load balancer."""
        total_history = sum(len(v) for v in self._history.values())
        return {
            "weights": {
                "state": self._weights.w_state,
                "cpu": self._weights.w_cpu,
                "ram": self._weights.w_ram,
                "queue": self._weights.w_queue,
                "specialty": self._weights.w_specialty,
                "history": self._weights.w_history,
            },
            "history_entries": total_history,
            "tracked_actions": len(self._history),
            "pending_assignments": dict(self._pending),
        }

    def reset(self) -> None:
        """Limpia historial y pending."""
        self._history.clear()
        self._pending.clear()


__all__ = ["AgentMetrics", "LoadBalancer", "LoadBalancerWeights"]
