"""CapabilityResolver — selección inteligente de agentes (Sprint 2.2/2).

Resuelve un objetivo (string o Capability) a un agente capaz, consultando
el `AgentRegistry`. El algoritmo de selección considera:

- **estado**: sólo IDLE/CREATED + healthy son elegibles.
- **especialidad**: match entre la capability objetivo y las del agente.
- **prioridad**: mayor prioridad del agente gana.
- **costo**: menor costo gana (costo declarado por el agente).
- **historial de éxito**: mayor success_rate gana.
- **carga**: menor usage_count gana (balanceo).
- **latencia**: menor avg_latency_ms gana.

Scoring ponderado (pesos configurables):

    score = w_specialty * specialty_score      (0..1)
          + w_priority  * priority_norm         (0..1)
          + w_cost      * (1 - cost_norm)       (0..1)
          + w_history   * success_rate          (0..1)
          + w_load      * (1 - load_norm)       (0..1)
          + w_latency   * (1 - latency_norm)    (0..1)

Pesos por defecto:

    w_specialty=0.30, w_priority=0.15, w_cost=0.10,
    w_history=0.20, w_load=0.15, w_latency=0.10

El resolver emite eventos:

- `capability.resolved` (con el agente seleccionado y el score).
- `capability.miss` (si no hay agentes capaces).
- `scheduler.selected_agent` (alias para compat con el enunciado).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .agent_registry import AgentRecord, AgentRegistry
from .capability import Capability

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pesos
# ----------------------------------------------------------------------


@dataclass
class ResolverWeights:
    """Pesos del CapabilityResolver. Se normalizan al usar."""

    w_specialty: float = 0.30
    w_priority: float = 0.15
    w_cost: float = 0.10
    w_history: float = 0.20
    w_load: float = 0.15
    w_latency: float = 0.10

    def normalized(self) -> "ResolverWeights":
        total = (
            self.w_specialty + self.w_priority + self.w_cost + self.w_history
            + self.w_load + self.w_latency
        )
        if total <= 0:
            return ResolverWeights()
        return ResolverWeights(
            w_specialty=self.w_specialty / total,
            w_priority=self.w_priority / total,
            w_cost=self.w_cost / total,
            w_history=self.w_history / total,
            w_load=self.w_load / total,
            w_latency=self.w_latency / total,
        )


# ----------------------------------------------------------------------
# Resultado de resolución
# ----------------------------------------------------------------------


@dataclass
class ResolutionResult:
    """Resultado de resolver una capability.

    Attributes:
        capability:     Capability objetivo.
        agent_record:   Agente seleccionado (None si no hay).
        score:          Score del agente seleccionado.
        candidates:     Lista de (record, score) considerados.
        resolved:       True si se seleccionó un agente.
        reason:         Razón humana del resultado.
        duration_ms:    Tiempo de resolución.
    """

    capability: str
    agent_record: AgentRecord | None = None
    score: float = 0.0
    candidates: list[tuple[AgentRecord, float]] = field(default_factory=list)
    resolved: bool = False
    reason: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "resolved": self.resolved,
            "agent": self.agent_record.name if self.agent_record else None,
            "agent_id": self.agent_record.id if self.agent_record else None,
            "score": round(self.score, 4),
            "candidates_count": len(self.candidates),
            "candidates": [
                {"name": r.name, "score": round(s, 4)}
                for r, s in self.candidates
            ],
            "reason": self.reason,
            "duration_ms": round(self.duration_ms, 2),
        }


# ----------------------------------------------------------------------
# CapabilityResolver
# ----------------------------------------------------------------------


class CapabilityResolver:
    """Resuelve capabilities a agentes capaces.

    Uso::

        resolver = CapabilityResolver(registry=registry, event_bus=bus)
        result = await resolver.resolve("code.generate")
        if result.resolved:
            agent = result.agent_record.agent
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        weights: ResolverWeights | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._registry = registry
        self._weights = (weights or ResolverWeights()).normalized()
        self._bus = event_bus

        # Métricas.
        self._metrics = {
            "resolutions_total": 0,
            "resolved_count": 0,
            "miss_count": 0,
            "total_duration_ms": 0.0,
        }

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @property
    def weights(self) -> ResolverWeights:
        return self._weights

    # ------------------------------------------------------------------
    # API principal
    # ------------------------------------------------------------------

    async def resolve(
        self,
        capability: str | Capability,
        *,
        exclude: set[str] | None = None,
        require_all_provided: bool = False,
    ) -> ResolutionResult:
        """Resuelve una capability a un agente.

        Args:
            capability:           String o Capability a resolver.
            exclude:              Nombres de agentes a excluir.
            require_all_provided: Si True, el agente debe proveer
                                  exactamente la capability (no wildcard).

        Returns:
            ResolutionResult.
        """
        start = time.monotonic()
        cap_name = (
            capability.name if isinstance(capability, Capability) else str(capability)
        )
        exclude = exclude or set()

        # 1. Encontrar candidatos.
        candidates = self._registry.find_by_capability(cap_name, only_available=True)
        # Filtrar excluidos.
        candidates = [c for c in candidates if c.name not in exclude]

        result = ResolutionResult(capability=cap_name)

        if not candidates:
            result.resolved = False
            result.reason = f"no available agent provides '{cap_name}'"
            result.duration_ms = (time.monotonic() - start) * 1000.0
            self._record_miss(cap_name, result.duration_ms)
            return result

        # 2. Calcular scores.
        scored: list[tuple[AgentRecord, float]] = []
        # Pre-calcular maximos para normalización.
        max_priority = max((c.priority for c in candidates), default=1) or 1
        max_cost = max((self._get_cost(c) for c in candidates), default=1.0) or 1.0
        max_load = max((c.usage_count for c in candidates), default=1) or 1
        max_latency = max((c.avg_latency_ms for c in candidates), default=1.0) or 1.0

        for record in candidates:
            score = self._score(
                record, cap_name,
                max_priority=max_priority,
                max_cost=max_cost,
                max_load=max_load,
                max_latency=max_latency,
            )
            scored.append((record, score))

        # 3. Ordenar por score (desc).
        scored.sort(key=lambda x: x[1], reverse=True)
        result.candidates = scored

        best_record, best_score = scored[0]
        result.agent_record = best_record
        result.score = best_score
        result.resolved = True
        result.reason = (
            f"selected '{best_record.name}' (score={best_score:.4f}, "
            f"priority={best_record.priority}, usage={best_record.usage_count})"
        )
        result.duration_ms = (time.monotonic() - start) * 1000.0

        self._record_resolution(cap_name, result)
        return result

    def _score(
        self,
        record: AgentRecord,
        capability: str,
        *,
        max_priority: int,
        max_cost: float,
        max_load: int,
        max_latency: float,
    ) -> float:
        """Calcula el score ponderado de un agente para una capability."""
        w = self._weights

        # Specialty: 1.0 si provee exactamente, 0.8 si provee wildcard,
        # 0.6 si provee padre jerárquico.
        specialty = self._specialty_score(record, capability)

        # Priority normalizado (0..1).
        priority_norm = record.priority / max_priority if max_priority > 0 else 0.0

        # Cost: menor = mejor. Normalizar a (0..1) e invertir.
        cost = self._get_cost(record)
        cost_score = 1.0 - (cost / max_cost) if max_cost > 0 else 1.0

        # History: success_rate.
        history = record.success_rate

        # Load: menor usage = mejor.
        load_score = 1.0 - (record.usage_count / max_load) if max_load > 0 else 1.0

        # Latency: menor = mejor.
        latency_score = (
            1.0 - (record.avg_latency_ms / max_latency)
            if max_latency > 0 else 1.0
        )

        return (
            w.w_specialty * specialty
            + w.w_priority * priority_norm
            + w.w_cost * cost_score
            + w.w_history * history
            + w.w_load * load_score
            + w.w_latency * latency_score
        )

    def _specialty_score(self, record: AgentRecord, capability: str) -> float:
        """Score de especialidad (0..1).

        - 1.0 si provee exactamente la capability.
        - 0.8 si provee un wildcard que matchea.
        - 0.6 si provee el padre jerárquico.
        - 0.0 si no provee.
        """
        for cap in record.provided_capabilities:
            if cap == capability:
                return 1.0
            if cap.endswith(".*"):
                prefix = cap[:-2]
                if capability == prefix or capability.startswith(prefix + "."):
                    return 0.8
            # Padre jerárquico: cap="code" matches capability="code.generate".
            if capability.startswith(cap + "."):
                return 0.6
        return 0.0

    def _get_cost(self, record: AgentRecord) -> float:
        """Costo declarado por el agente (de metadata). Default 1.0."""
        return float(record.metadata.get("cost", 1.0))

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        return {
            **self._metrics,
            "avg_resolution_time_ms": (
                round(self._metrics["total_duration_ms"] / self._metrics["resolutions_total"], 2)
                if self._metrics["resolutions_total"] > 0 else 0.0
            ),
            "success_rate": (
                round(self._metrics["resolved_count"] / self._metrics["resolutions_total"], 4)
                if self._metrics["resolutions_total"] > 0 else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _record_resolution(
        self, capability: str, result: ResolutionResult
    ) -> None:
        self._metrics["resolutions_total"] += 1
        self._metrics["resolved_count"] += 1
        self._metrics["total_duration_ms"] += result.duration_ms
        if self._bus is not None:
            try:
                # Emitir capability.resolved.
                import asyncio
                asyncio.create_task(self._bus.publish(
                    "capability.resolved",
                    source="capability_resolver",
                    payload=result.to_dict(),
                ))
                # Emitir scheduler.selected_agent (alias del enunciado).
                asyncio.create_task(self._bus.publish(
                    "scheduler.selected_agent",
                    source="capability_resolver",
                    payload={
                        "capability": capability,
                        "agent": result.agent_record.name if result.agent_record else None,
                        "agent_id": result.agent_record.id if result.agent_record else None,
                        "score": round(result.score, 4),
                        "duration_ms": round(result.duration_ms, 2),
                    },
                ))
            except Exception:  # noqa: BLE001
                logger.exception("Failed to emit resolution events")

    def _record_miss(self, capability: str, duration_ms: float) -> None:
        self._metrics["resolutions_total"] += 1
        self._metrics["miss_count"] += 1
        self._metrics["total_duration_ms"] += duration_ms
        if self._bus is not None:
            try:
                import asyncio
                asyncio.create_task(self._bus.publish(
                    "capability.miss",
                    source="capability_resolver",
                    payload={
                        "capability": capability,
                        "duration_ms": round(duration_ms, 2),
                    },
                ))
            except Exception:  # noqa: BLE001
                logger.exception("Failed to emit capability.miss event")


__all__ = [
    "CapabilityResolver",
    "ResolutionResult",
    "ResolverWeights",
]
