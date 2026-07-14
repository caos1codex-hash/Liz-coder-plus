"""AgentRegistry — registro único de agentes (Sprint 2.2/2).

Responsabilidades:

- `register(agent)` / `unregister(id_or_name)` / `replace(old, new)`
- `discover(...)` — auto-discovery de agentes
- `get(id_or_name)` / `get_all()` / `exists(id_or_name)`
- `health()` — health check de todos los agentes
- `metrics()` — métricas del registry

Cada agente registrado se envuelve en un `AgentRecord` que añade:

- id (UUID), name, version, description
- capabilities (provided/required/preferred)
- priority, status, metadata
- created_at, last_seen, last_heartbeat
- health (HealthState), errors, uptime, usage

El registry es **asyncio-safe** y emite eventos al `EventBus`:

- `agent.registered`
- `agent.removed`
- `agent.heartbeat.received`
- `agent.health.changed`

El registry **no ejecuta agentes**; sólo los trackea. La selección
se hace vía `CapabilityResolver`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..base import BaseAgent
from ..enums import AgentStatus, HealthState
from .capability import Capability, CapabilityRegistry

if TYPE_CHECKING:
    from .manifest import AgentManifest

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return time.time()


# ----------------------------------------------------------------------
# AgentRecord
# ----------------------------------------------------------------------


@dataclass
class AgentRecord:
    """Wrapper sobre un BaseAgent con metadatos de registro.

    Attributes:
        agent:           El BaseAgent registrado.
        id:              UUID único del registro.
        name:            Nombre del agente (debe ser único en el registry).
        version:         Versión del agente.
        description:     Descripción humana.
        provided_capabilities: Capabilities que el agente provee.
        required_capabilities: Capabilities que el agente necesita de otros.
        preferred_capabilities: Capabilities preferidas (para routing suave).
        priority:        Prioridad del agente (mayor = más prioritario).
        status:          AgentStatus actual (snapshot).
        metadata:        Metadatos libres.
        created_at:      ISO timestamp de registro.
        last_seen:       ISO timestamp de última actividad.
        last_heartbeat:  ISO timestamp del último heartbeat.
        health_state:    HealthState (alive/busy/idle/error/dead).
        errors:          Lista de errores recientes (máx 20).
        uptime_s:        Segundos desde created_at.
        usage_count:     Número de veces que se seleccionó.
        success_count:   Número de selecciones exitosas.
        failure_count:   Número de selecciones fallidas.
        total_duration_ms: Tiempo total ocupado (ms).
        avg_latency_ms:  Latencia media de las últimas N ejecuciones.
    """

    agent: BaseAgent
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    provided_capabilities: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    preferred_capabilities: list[str] = field(default_factory=list)
    priority: int = 0
    status: AgentStatus = AgentStatus.CREATED
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    last_seen: str = field(default_factory=_now_iso)
    last_heartbeat: str = field(default_factory=_now_iso)
    health_state: HealthState = HealthState.ALIVE
    errors: list[str] = field(default_factory=list)
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_ms: float = 0.0
    _latencies: list[float] = field(default_factory=list)

    MAX_ERRORS = 20
    MAX_LATENCIES = 100

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.agent.name
        if not self.description:
            self.description = self.agent.description
        if not self.version:
            self.version = getattr(self.agent, "version", "0.1.0")

    # ------------------------------------------------------------------
    # Health / heartbeat
    # ------------------------------------------------------------------

    def heartbeat(self) -> None:
        """Actualiza last_heartbeat y last_seen."""
        self.last_heartbeat = _now_iso()
        self.last_seen = _now_iso()

    def update_status(self) -> None:
        """Snapshot del estado del agente subyacente."""
        self.status = self.agent.status
        report = self.agent.health() if hasattr(self.agent, "health") else None
        if report is not None:
            self.health_state = report.state

    def record_usage(
        self,
        *,
        success: bool,
        duration_ms: float = 0.0,
    ) -> None:
        """Registra una selección del agente."""
        self.usage_count += 1
        self.total_duration_ms += duration_ms
        self._latencies.append(duration_ms)
        if len(self._latencies) > self.MAX_LATENCIES:
            self._latencies = self._latencies[-self.MAX_LATENCIES:]
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
            # Sincronizar errores del agente si los tiene.
            last_err = getattr(self.agent, "last_error", None)
            if last_err:
                self.add_error(last_err)
        self.last_seen = _now_iso()

    def add_error(self, error: str) -> None:
        """Añade un error al historial (con cap)."""
        self.errors.append(error)
        if len(self.errors) > self.MAX_ERRORS:
            self.errors = self.errors[-self.MAX_ERRORS:]

    @property
    def avg_latency_ms(self) -> float:
        if not self._latencies:
            return 0.0
        return sum(self._latencies) / len(self._latencies)

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total

    @property
    def uptime_s(self) -> float:
        """Segundos desde created_at (aproximado, basado en epoch)."""
        try:
            created = datetime.fromisoformat(self.created_at).timestamp()
            return _now_epoch() - created
        except (ValueError, OSError):
            return 0.0

    @property
    def is_healthy(self) -> bool:
        """True si health_state es ALIVE o IDLE."""
        return self.health_state in (HealthState.ALIVE, HealthState.IDLE)

    @property
    def is_available(self) -> bool:
        """True si está disponible para selección (no DEAD/ERROR/STOPPED)."""
        return (
            self.status in (AgentStatus.IDLE, AgentStatus.CREATED)
            and self.health_state in (HealthState.ALIVE, HealthState.IDLE)
        )

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def provides(self, capability: str) -> bool:
        """True si el agente provee la capability (con wildcard match)."""
        for cap in self.provided_capabilities:
            # Match exacto.
            if cap == capability:
                return True
            # Match wildcard: cap="code.*" matches capability="code.generate".
            if cap.endswith(".*"):
                prefix = cap[:-2]
                if capability == prefix or capability.startswith(prefix + "."):
                    return True
            # Match jerárquico: cap="code" matches capability="code.generate"
            # (el agente que provee "code" puede manejar sub-capabilities).
            if capability.startswith(cap + "."):
                return True
        return False

    def requires_satisfied(self, available_caps: set[str]) -> bool:
        """True si todas las capabilities requeridas están disponibles."""
        for req in self.required_capabilities:
            if not any(
                req == c or req.startswith(c + ".") or
                (c.endswith(".*") and (req == c[:-2] or req.startswith(c[:-2] + ".")))
                for c in available_caps
            ):
                return False
        return True

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "provided_capabilities": list(self.provided_capabilities),
            "required_capabilities": list(self.required_capabilities),
            "preferred_capabilities": list(self.preferred_capabilities),
            "priority": self.priority,
            "status": self.status.value,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "last_heartbeat": self.last_heartbeat,
            "health_state": self.health_state.value,
            "errors": list(self.errors),
            "uptime_s": round(self.uptime_s, 2),
            "usage_count": self.usage_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": round(self.success_rate, 4),
            "total_duration_ms": round(self.total_duration_ms, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
        }


# ----------------------------------------------------------------------
# AgentRegistry
# ----------------------------------------------------------------------


class AgentRegistry:
    """Registro único de agentes.

    Uso::

        registry = AgentRegistry(capability_registry=cap_reg, event_bus=bus)
        await registry.register(my_agent, provided_capabilities=["code.generate"])
        record = await registry.get_by_name("my_agent")
        candidates = registry.find_by_capability("code.generate")
    """

    HEARTBEAT_TIMEOUT_S = 60.0  # sin heartbeat en 60s → unhealthy.

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry | None = None,
        event_bus: Any | None = None,
        heartbeat_timeout: float = 60.0,
    ) -> None:
        self._records: dict[str, AgentRecord] = {}  # by id
        self._by_name: dict[str, str] = {}  # name -> record_id
        self._cap_registry = capability_registry or CapabilityRegistry()
        self._bus = event_bus
        self._heartbeat_timeout = heartbeat_timeout
        self._lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._auto_discovery_hooks: list = []

        # Métricas.
        self._metrics = {
            "registered_total": 0,
            "unregistered_total": 0,
            "replaced_total": 0,
            "discoveries_total": 0,
            "heartbeat_checks": 0,
            "health_changes": 0,
        }

    @property
    def capability_registry(self) -> CapabilityRegistry:
        return self._cap_registry

    @property
    def event_bus(self) -> Any | None:
        return self._bus

    # ------------------------------------------------------------------
    # Registro
    # ------------------------------------------------------------------

    async def register(
        self,
        agent: BaseAgent,
        *,
        provided_capabilities: list[str] | None = None,
        required_capabilities: list[str] | None = None,
        preferred_capabilities: list[str] | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
        version: str = "0.1.0",
        replace_existing: bool = True,
    ) -> AgentRecord:
        """Registra un agente.

        Args:
            agent:                   BaseAgent a registrar.
            provided_capabilities:   Capabilities que provee.
            required_capabilities:   Capabilities que necesita de otros.
            preferred_capabilities:  Capabilities preferidas.
            priority:                Prioridad (mayor = más prioritario).
            metadata:                Metadatos libres.
            version:                 Versión del agente.
            replace_existing:        Si True y ya existe un agente con el
                                     mismo nombre, lo reemplaza.

        Returns:
            El AgentRecord creado.

        Raises:
            ValueError: Si ya existe un agente con el mismo nombre y
                replace_existing=False.
            TypeError: Si el objeto no parece un BaseAgent.
        """
        # Duck-typing: verificar atributos clave en lugar de isinstance,
        # para ser robusto frente a re-imports del módulo durante los
        # tests (que crean clases BaseAgent distintas con el mismo nombre).
        if not (
            hasattr(agent, "name")
            and hasattr(agent, "status")
            and hasattr(agent, "health")
            and hasattr(agent, "execute")
            and hasattr(agent, "start")
            and hasattr(agent, "stop")
        ):
            raise TypeError(
                f"Expected BaseAgent (or compatible), got {type(agent).__name__}"
            )

        async with self._lock:
            existing_id = self._by_name.get(agent.name)
            if existing_id is not None:
                if not replace_existing:
                    raise ValueError(
                        f"Agent '{agent.name}' already registered "
                        f"(id={existing_id})"
                    )
                # Reemplazar.
                old_record = self._records.pop(existing_id, None)
                self._by_name.pop(agent.name, None)
                self._metrics["replaced_total"] += 1
                if old_record is not None:
                    await self._emit("agent.removed", {
                        "agent_id": old_record.id,
                        "name": old_record.name,
                        "reason": "replaced",
                    })

            record = AgentRecord(
                agent=agent,
                name=agent.name,
                description=agent.description,
                version=version,
                provided_capabilities=list(provided_capabilities or []),
                required_capabilities=list(required_capabilities or []),
                preferred_capabilities=list(preferred_capabilities or []),
                priority=priority,
                metadata=dict(metadata or {}),
            )
            record.update_status()
            record.heartbeat()
            self._records[record.id] = record
            self._by_name[record.name] = record.id
            self._metrics["registered_total"] += 1

        await self._emit("agent.registered", {
            "agent_id": record.id,
            "name": record.name,
            "version": record.version,
            "provided_capabilities": record.provided_capabilities,
            "priority": record.priority,
        })
        logger.info(
            "Registered agent '%s' (id=%s, caps=%d, priority=%d)",
            record.name, record.id[:8],
            len(record.provided_capabilities), record.priority,
        )
        return record

    async def unregister(self, id_or_name: str) -> bool:
        """Elimina un agente por id o nombre."""
        async with self._lock:
            record = self._resolve(id_or_name)
            if record is None:
                return False
            self._records.pop(record.id, None)
            self._by_name.pop(record.name, None)
            self._metrics["unregistered_total"] += 1

        await self._emit("agent.removed", {
            "agent_id": record.id,
            "name": record.name,
            "reason": "unregistered",
        })
        logger.info("Unregistered agent '%s' (id=%s)", record.name, record.id[:8])
        return True

    async def replace(
        self,
        old_id_or_name: str,
        new_agent: BaseAgent,
        **kwargs: Any,
    ) -> AgentRecord:
        """Reemplaza un agente existente por uno nuevo.

        Mantiene el mismo record_id si el nombre coincide; si no,
        crea un nuevo registro.
        """
        async with self._lock:
            old_record = self._resolve(old_id_or_name)
            if old_record is None:
                raise KeyError(f"Agent '{old_id_or_name}' not found")
            # Si el nuevo agente tiene el mismo nombre, reusar el id.
            if new_agent.name == old_record.name:
                kwargs.setdefault("priority", old_record.priority)
                kwargs.setdefault("metadata", old_record.metadata)
                kwargs.setdefault("version", old_record.version)
                # Eliminar el viejo (sin emitir evento de removed).
                self._records.pop(old_record.id, None)
                self._by_name.pop(old_record.name, None)

        # Registrar el nuevo (replace_existing=True para evitar conflictos).
        return await self.register(new_agent, replace_existing=True, **kwargs)

    # ------------------------------------------------------------------
    # Auto-discovery
    # ------------------------------------------------------------------

    def add_discovery_hook(self, hook) -> None:
        """Añade un hook de auto-discovery.

        El hook es un callable async `(registry) -> list[BaseAgent]` que
        devuelve agentes descubiertos. El registry los registrará.
        """
        self._auto_discovery_hooks.append(hook)

    async def discover(self) -> list[AgentRecord]:
        """Ejecuta todos los hooks de discovery y registra los agentes.

        Returns:
            Lista de AgentRecord registrados (puede estar vacía).
        """
        discovered: list[AgentRecord] = []
        for hook in self._auto_discovery_hooks:
            try:
                agents = await hook(self)
            except Exception:  # noqa: BLE001
                logger.exception("Discovery hook failed")
                continue
            for agent in agents:
                if not isinstance(agent, BaseAgent):
                    logger.warning(
                        "Discovery hook returned non-BaseAgent: %s",
                        type(agent).__name__,
                    )
                    continue
                # Auto-detect capabilities from agent's objectives/tools.
                caps = self._infer_capabilities(agent)
                try:
                    record = await self.register(
                        agent,
                        provided_capabilities=caps,
                        replace_existing=True,
                    )
                    discovered.append(record)
                    self._metrics["discoveries_total"] += 1
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to register discovered agent '%s'",
                        agent.name,
                    )
        if discovered:
            logger.info("Discovered and registered %d agents", len(discovered))
        return discovered

    def _infer_capabilities(self, agent: BaseAgent) -> list[str]:
        """Infiere capabilities de un agente a partir de sus objetivos y tools.

        Heurística simple: si el agente tiene `default_objectives` o
        `objectives` que coinciden con categorías de capabilities, las
        deriva. Si tiene `tools`, los mapea a capabilities.
        """
        caps: list[str] = []
        objectives = list(getattr(agent, "objectives", []) or [])
        tools = list(getattr(agent, "tools", []) or [])

        # Mapping heurístico objetivo → capability.
        obj_to_cap = {
            "plan": "planning",
            "planning": "planning",
            "decompose": "planning",
            "workflow": "workflow",
            "code": "code.generate",
            "refactor": "code.refactor",
            "fix": "code.fix",
            "test": "code.test",
            "implement": "code.generate",
            "review": "code.review",
            "audit": "code.review",
            "security": "code.review",
            "git": "git.commit",
            "commit": "git.commit",
            "branch": "git.branch",
            "merge": "git.merge",
            "push": "git.push",
            "terminal": "terminal.execute",
            "execute": "terminal.execute",
            "shell": "terminal.execute",
            "memory": "memory.store",
            "store": "memory.store",
            "retrieve": "memory.retrieve",
            "research": "research",
            "investigate": "research",
            "summarize": "research.summarize",
            "search": "web.search",
        }
        for obj in objectives:
            obj_lower = obj.lower()
            if obj_lower in obj_to_cap:
                cap = obj_to_cap[obj_lower]
                if cap not in caps:
                    caps.append(cap)

        # Mapping heurístico tool → capability.
        tool_to_cap = {
            "file_tool": "filesystem.read",
            "terminal_tool": "terminal.execute",
            "system_tool": "terminal.execute",
            "git": "git.commit",
            "memory_store": "memory.store",
            "vector_index": "memory.search",
            "web_search": "web.search",
            "doc_fetch": "documentation.lookup",
            "planner": "planning",
            "dag_builder": "workflow.create",
            "linter": "code.review",
            "static_analyzer": "code.review",
            "refactor": "code.refactor",
            "fix": "code.fix",
            "test_gen": "code.test",
        }
        for tool in tools:
            tool_lower = tool.lower()
            if tool_lower in tool_to_cap:
                cap = tool_to_cap[tool_lower]
                if cap not in caps:
                    caps.append(cap)

        return caps

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def get(self, id_or_name: str) -> AgentRecord | None:
        """Busca un agente por id o nombre."""
        return self._resolve(id_or_name)

    def get_by_name(self, name: str) -> AgentRecord | None:
        """Busca un agente por nombre."""
        record_id = self._by_name.get(name)
        if record_id is None:
            return None
        return self._records.get(record_id)

    def get_by_id(self, agent_id: str) -> AgentRecord | None:
        """Busca un agente por id."""
        return self._records.get(agent_id)

    def get_all(self) -> list[AgentRecord]:
        """Devuelve todos los registros."""
        return list(self._records.values())

    def exists(self, id_or_name: str) -> bool:
        """True si el agente está registrado."""
        return self._resolve(id_or_name) is not None

    def names(self) -> list[str]:
        """Devuelve los nombres de todos los agentes registrados."""
        return list(self._by_name.keys())

    def find_by_capability(
        self,
        capability: str,
        *,
        only_available: bool = True,
    ) -> list[AgentRecord]:
        """Devuelve agentes que proveen una capability.

        Args:
            capability:      Nombre o patrón de la capability.
            only_available:  Si True, sólo devuelve agentes disponibles
                             (IDLE/CREATED + healthy).
        """
        result: list[AgentRecord] = []
        for record in self._records.values():
            if only_available and not record.is_available:
                continue
            if record.provides(capability):
                result.append(record)
        # Ordenar por prioridad (desc) y luego por usage_count (asc,
        # para balanceo).
        result.sort(key=lambda r: (-r.priority, r.usage_count))
        return result

    def find_by_capability_pattern(
        self,
        pattern: str,
        *,
        only_available: bool = True,
    ) -> list[AgentRecord]:
        """Como find_by_capability pero con soporte de wildcards.

        Ej: "code.*" devuelve agentes que proveen code.generate,
        code.review, etc.
        """
        if pattern == "*":
            return [
                r for r in self._records.values()
                if not only_available or r.is_available
            ]
        # Expandir el patrón contra las provided_capabilities de cada agente.
        result: list[AgentRecord] = []
        for record in self._records.values():
            if only_available and not record.is_available:
                continue
            for cap in record.provided_capabilities:
                cap_obj = Capability(name=cap)
                if cap_obj.matches(pattern) or _pattern_matches_cap(pattern, cap):
                    result.append(record)
                    break
        result.sort(key=lambda r: (-r.priority, r.usage_count))
        return result

    # ------------------------------------------------------------------
    # Health / heartbeat
    # ------------------------------------------------------------------

    def heartbeat(self, id_or_name: str) -> bool:
        """Registra un heartbeat para un agente."""
        record = self._resolve(id_or_name)
        if record is None:
            return False
        record.heartbeat()
        return True

    def update_health(self) -> int:
        """Actualiza el estado de salud de todos los agentes.

        Returns el número de agentes cuyo health_state cambió.
        """
        changes = 0
        now_epoch = _now_epoch()
        for record in self._records.values():
            old_state = record.health_state
            record.update_status()
            # Si no hay heartbeat reciente, marcar como DEAD.
            try:
                last_hb = datetime.fromisoformat(record.last_heartbeat).timestamp()
                if now_epoch - last_hb > self._heartbeat_timeout:
                    if record.health_state != HealthState.DEAD:
                        record.health_state = HealthState.DEAD
            except (ValueError, OSError):
                pass
            if record.health_state != old_state:
                changes += 1
                # Emitir evento de cambio de health (best-effort).
                if self._bus is not None:
                    try:
                        asyncio.create_task(self._emit(
                            "agent.health.changed",
                            {
                                "agent_id": record.id,
                                "name": record.name,
                                "old_state": old_state.value,
                                "new_state": record.health_state.value,
                            },
                        ))
                    except Exception:  # noqa: BLE001
                        pass
        self._metrics["heartbeat_checks"] += 1
        self._metrics["health_changes"] += changes
        return changes

    def health(self) -> dict[str, Any]:
        """Health check de todos los agentes."""
        records = self.get_all()
        summary: dict[str, int] = {s.value: 0 for s in HealthState}
        for r in records:
            summary[r.health_state.value] += 1
        return {
            "total": len(records),
            "summary": summary,
            "healthy": summary.get(HealthState.ALIVE.value, 0)
                       + summary.get(HealthState.IDLE.value, 0),
            "degraded": summary.get(HealthState.BUSY.value, 0)
                        + summary.get(HealthState.ERROR.value, 0),
            "dead": summary.get(HealthState.DEAD.value, 0),
            "agents": {r.name: r.health_state.value for r in records},
        }

    async def start_heartbeat_loop(self, interval: float = 10.0) -> None:
        """Arranca un background task que actualiza health periódicamente."""
        if self._heartbeat_task is not None:
            return

        async def _loop() -> None:
            while True:
                try:
                    self.update_health()
                except Exception:  # noqa: BLE001
                    logger.exception("Heartbeat loop failed")
                await asyncio.sleep(interval)

        self._heartbeat_task = asyncio.create_task(_loop())
        logger.info("AgentRegistry heartbeat loop started (interval=%.1fs)", interval)

    async def stop_heartbeat_loop(self) -> None:
        """Detiene el heartbeat loop."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
            logger.info("AgentRegistry heartbeat loop stopped")

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Métricas del registry."""
        records = self.get_all()
        return {
            **self._metrics,
            "registry_size": len(records),
            "available_count": sum(1 for r in records if r.is_available),
            "healthy_count": sum(1 for r in records if r.is_healthy),
            "total_usage": sum(r.usage_count for r in records),
            "total_successes": sum(r.success_count for r in records),
            "total_failures": sum(r.failure_count for r in records),
            "avg_success_rate": (
                round(
                    sum(r.success_rate for r in records) / len(records), 4
                ) if records else 0.0
            ),
            "capability_registry": self._cap_registry.metrics(),
        }

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _resolve(self, id_or_name: str) -> AgentRecord | None:
        """Resuelve un id o nombre a un AgentRecord."""
        # Por id.
        record = self._records.get(id_or_name)
        if record is not None:
            return record
        # Por nombre.
        record_id = self._by_name.get(id_or_name)
        if record_id is not None:
            return self._records.get(record_id)
        return None

    # ------------------------------------------------------------------
    # Sprint 2.3 — API simplificada + soporte de Manifest
    # ------------------------------------------------------------------

    async def register_simple(
        self,
        agent: BaseAgent,
        *,
        capabilities: list[str] | None = None,
        priority: int = 0,
    ) -> AgentRecord:
        """Registro simplificado (Sprint 2.3).

        Equivalente a::

            await registry.register(
                agent,
                provided_capabilities=capabilities,
                priority=priority,
            )

        Si `capabilities` es None, se infieren automáticamente desde los
        `objectives` y `tools` del agente.
        """
        if capabilities is None:
            capabilities = self._infer_capabilities(agent)
        return await self.register(
            agent,
            provided_capabilities=capabilities,
            priority=priority,
            replace_existing=True,
        )

    async def register_with_manifest(
        self,
        agent: BaseAgent,
        manifest: "AgentManifest",
    ) -> AgentRecord:
        """Registra un agente usando un `AgentManifest` explícito.

        El manifest aporta id, name, version, capabilities, priority y
        metadata de forma declarativa. El agente subyacente debe tener
        el mismo `name` que el manifest (o se le asigna).
        """
        return await self.register(
            agent,
            provided_capabilities=list(manifest.capabilities),
            priority=manifest.priority,
            metadata=dict(manifest.metadata),
            version=manifest.version,
            replace_existing=True,
        )

    def list_agents(self) -> list[AgentRecord]:
        """Alias simplificado de `get_all()` (Sprint 2.3).

        Devuelve todos los registros del registry.
        """
        return self.get_all()

    def list_manifests(self) -> list["AgentManifest"]:
        """Devuelve el manifest de cada agente registrado (Sprint 2.3).

        Si un agente no tiene manifest explícito, se construye uno
        sintético a partir de su AgentRecord.
        """
        from .manifest import AgentManifest
        manifests: list[AgentManifest] = []
        for record in self.get_all():
            # Si el agente expone un método manifest(), usarlo.
            agent = record.agent
            if hasattr(agent, "manifest") and callable(agent.manifest):
                try:
                    m = agent.manifest()
                    if isinstance(m, AgentManifest):
                        manifests.append(m)
                        continue
                except Exception:  # noqa: BLE001
                    pass
            # Sintetizar desde el record.
            manifests.append(AgentManifest(
                id=record.name.lower().replace(" ", "_"),
                name=record.name,
                version=record.version,
                description=record.description,
                capabilities=list(record.provided_capabilities),
                priority=record.priority,
                metadata=dict(record.metadata),
            ))
        return manifests

    def find_by_capability_simple(self, capability: str) -> list[AgentRecord]:
        """Búsqueda simplificada por capability (Sprint 2.3).

        A diferencia de `find_by_capability`, no filtra por disponibilidad:
        devuelve todos los agentes que proveen la capability, ordenados
        por prioridad desc y usage asc.

        Útil para discovery: "¿qué agentes saben de python?"
        """
        results: list[AgentRecord] = []
        for record in self._records.values():
            if record.provides(capability):
                results.append(record)
        results.sort(key=lambda r: (-r.priority, r.usage_count))
        return results

    def find_manifests_by_capability(self, capability: str) -> list["AgentManifest"]:
        """Devuelve manifests de agentes que proveen una capability (Sprint 2.3)."""
        return [
            m for m in self.list_manifests()
            if m.has_capability(capability)
        ]

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emite un evento al EventBus (best-effort)."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(event_type, source="agent_registry", payload=payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event '%s'", event_type)


def _pattern_matches_cap(pattern: str, cap_name: str) -> bool:
    """True si `pattern` (con wildcards) matchea `cap_name`."""
    if pattern == "*":
        return True
    if pattern == cap_name:
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return cap_name == prefix or cap_name.startswith(prefix + ".")
    # Match jerárquico: pattern="code" matches cap_name="code.generate".
    if cap_name.startswith(pattern + "."):
        return True
    return False


__all__ = ["AgentRecord", "AgentRegistry"]
