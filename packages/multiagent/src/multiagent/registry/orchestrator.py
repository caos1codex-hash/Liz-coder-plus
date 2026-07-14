"""RegistryOrchestrator — orchestrator desacoplado (Sprint 2.3).

Este orchestrator **no conoce clases concretas de agentes**. En lugar de
instanciar `CoderAgent()` o `PlannerAgent()`, pide capacidades al
`AgentRegistry` y obtiene el agente adecuado.

Flujo::

    Usuario pide "necesito un agente que sepa python"
        ↓
    RegistryOrchestrator.request(capability="python", task={...})
        ↓
    AgentRegistry.find_by_capability_simple("python")
        ↓
    Devuelve AgentRecord del CoderAgent (que declara "python" en su manifest)
        ↓
    LifecycleManager.start() → RUNNING
        ↓
    Agent.execute(task)
        ↓
    LifecycleManager.stop() (vuelve a READY)

Compatibilidad:

- El `AgentOrchestrator` de Sprint 2.1 sigue existiendo y se puede usar
  directamente (mantiene su propia lista de agentes).
- Este `RegistryOrchestrator` es una **alternativa desacoplada** que
  delega al registry. Se puede usar en paralelo o como reemplazo.
- No modifica el `AgentOrchestrator` existente; lo extiende vía
  composición (recibe uno opcionalmente para reutilizar MessageBus,
  logger, etc.).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..base import BaseAgent
from ..logs import AgentLogger
from ..messages import MessageBus
from .agent_registry import AgentRecord, AgentRegistry
from .capability import CapabilityRegistry, register_standard_capabilities
from .lifecycle import LifecycleManager
from .manifest import AgentManifest, standard_manifests
from .resolver import CapabilityResolver, ResolutionResult

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Resultado de ejecutar una task vía el RegistryOrchestrator.

    Attributes:
        task_id:          UUID de la task.
        capability:       Capability solicitada.
        agent_name:       Agente que la ejecutó (None si no se encontró).
        agent_id:         ID del AgentRecord.
        success:          True si la task se ejecutó con éxito.
        result:           Salida del agente (dict).
        error:            Mensaje de error si falló.
        duration_ms:      Duración total.
        lifecycle_before: Estado del lifecycle antes de ejecutar.
        lifecycle_after:  Estado del lifecycle después de ejecutar.
    """

    task_id: str = ""
    capability: str = ""
    agent_name: str | None = None
    agent_id: str | None = None
    success: bool = False
    result: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    lifecycle_before: str = ""
    lifecycle_after: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "capability": self.capability,
            "agent_name": self.agent_name,
            "agent_id": self.agent_id,
            "success": self.success,
            "result": self.result if _jsonable(self.result) else str(self.result),
            "error": self.error,
            "duration_ms": round(self.duration_ms, 2),
            "lifecycle_before": self.lifecycle_before,
            "lifecycle_after": self.lifecycle_after,
        }


def _jsonable(v: Any) -> bool:
    import json
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


class RegistryOrchestrator:
    """Orchestrator desacoplado basado en registry y capabilities.

    Uso::

        orchestrator = RegistryOrchestrator()
        await orchestrator.bootstrap()  # registra 7 agentes estándar

        result = await orchestrator.request(
            capability="python",
            task={"name": "write", "payload": {"op": "refactor", ...}},
        )
        if result.success:
            print(result.result)
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry | None = None,
        resolver: CapabilityResolver | None = None,
        message_bus: MessageBus | None = None,
        agent_logger: AgentLogger | None = None,
        auto_register_standard: bool = False,
    ) -> None:
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        self._registry = registry or AgentRegistry(capability_registry=cap_reg)
        self._resolver = resolver or CapabilityResolver(registry=self._registry)
        self._bus = message_bus or MessageBus()
        self._logger = agent_logger or AgentLogger()
        # LifecycleManagers por agent_name.
        self._lifecycles: dict[str, LifecycleManager] = {}
        if auto_register_standard:
            # Bootstrap se llama explícitamente para ser async.
            pass

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @property
    def resolver(self) -> CapabilityResolver:
        return self._resolver

    @property
    def message_bus(self) -> MessageBus:
        return self._bus

    @property
    def logger(self) -> AgentLogger:
        return self._logger

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    async def bootstrap(
        self,
        agents: list[BaseAgent] | None = None,
        *,
        use_standard_manifests: bool = True,
    ) -> int:
        """Inicializa el orchestrator registrando agentes.

        Si `agents` es None y `use_standard_manifests=True`, instancia los
        7 agentes concretos de Sprint 2.1 y los registra con sus manifests
        estándar.

        Returns el número de agentes registrados.
        """
        if agents is None and use_standard_manifests:
            agents = self._instantiate_standard_agents()

        manifests = standard_manifests() if use_standard_manifests else []
        manifest_by_id = {m.id: m for m in manifests}

        count = 0
        for agent in agents:
            # Buscar manifest por nombre (agent.name coincide con manifest.id).
            manifest = manifest_by_id.get(agent.name)
            if manifest is not None:
                await self.register(agent, manifest=manifest)
            else:
                await self.register(agent)
            count += 1
        return count

    def _instantiate_standard_agents(self) -> list[BaseAgent]:
        """Instancia los 7 agentes concretos de Sprint 2.1.

        Import diferido para evitar ciclos.
        """
        from ..agents.coder import CoderAgent
        from ..agents.git import GitAgent
        from ..agents.memory import MemoryAgent
        from ..agents.planner import PlannerAgent
        from ..agents.research import ResearchAgent
        from ..agents.reviewer import ReviewerAgent
        from ..agents.terminal import TerminalAgent

        return [
            PlannerAgent(),
            CoderAgent(),
            ReviewerAgent(),
            ResearchAgent(),
            TerminalAgent(),
            GitAgent(),
            MemoryAgent(),
        ]

    # ------------------------------------------------------------------
    # Registro
    # ------------------------------------------------------------------

    async def register(
        self,
        agent: BaseAgent,
        *,
        manifest: AgentManifest | None = None,
        capabilities: list[str] | None = None,
        priority: int = 0,
    ) -> AgentRecord:
        """Registra un agente con su lifecycle.

        - Crea un `LifecycleManager` para el agente.
        - Llama a `lifecycle.initialize()` (CREATED → READY).
        - Registra en el `AgentRegistry` (con manifest o capabilities).
        """
        # Lifecycle.
        lifecycle = LifecycleManager(agent)
        await lifecycle.initialize()
        self._lifecycles[agent.name] = lifecycle

        # Registry.
        if manifest is not None:
            record = await self._registry.register_with_manifest(agent, manifest)
        else:
            record = await self._registry.register_simple(
                agent, capabilities=capabilities, priority=priority,
            )
        logger.info(
            "Registered agent '%s' with lifecycle (state=%s, caps=%d)",
            agent.name, lifecycle.state.value, len(record.provided_capabilities),
        )
        return record

    async def unregister(self, name: str) -> bool:
        """Desregistra un agente y detiene su lifecycle."""
        lifecycle = self._lifecycles.pop(name, None)
        if lifecycle is not None and not lifecycle.is_terminal:
            await lifecycle.stop()
        return await self._registry.unregister(name)

    def get_lifecycle(self, name: str) -> LifecycleManager | None:
        """Devuelve el LifecycleManager de un agente."""
        return self._lifecycles.get(name)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, capability: str) -> list[AgentRecord]:
        """Descubre agentes que proveen una capability.

        No filtra por disponibilidad; usa `find_by_capability_simple`.
        """
        return self._registry.find_by_capability_simple(capability)

    def discover_manifests(self, capability: str) -> list[AgentManifest]:
        """Descubre manifests de agentes que proveen una capability."""
        return self._registry.find_manifests_by_capability(capability)

    def list_agents(self) -> list[AgentRecord]:
        """Lista todos los agentes registrados."""
        return self._registry.list_agents()

    def list_manifests(self) -> list[AgentManifest]:
        """Lista todos los manifests."""
        return self._registry.list_manifests()

    # ------------------------------------------------------------------
    # Request (API principal desacoplada)
    # ------------------------------------------------------------------

    async def request(
        self,
        *,
        capability: str,
        task: dict[str, Any],
        exclude: set[str] | None = None,
    ) -> ExecutionResult:
        """Pide un agente con una capability y ejecuta una task.

        Flujo:
        1. Resolver: encuentra el mejor agente con la capability.
        2. Lifecycle: READY → RUNNING.
        3. Execute: agent.execute(task).
        4. Lifecycle: RUNNING → READY (o ERROR si falla).
        5. Registry: record.record_usage(success, duration_ms).
        """
        task_id = task.get("id") or str(uuid.uuid4())
        start = time.monotonic()
        result = ExecutionResult(task_id=task_id, capability=capability)

        # 1. Resolver.
        resolution: ResolutionResult = await self._resolver.resolve(
            capability, exclude=exclude,
        )
        if not resolution.resolved or resolution.agent_record is None:
            result.success = False
            result.error = resolution.reason
            result.duration_ms = (time.monotonic() - start) * 1000.0
            return result

        record = resolution.agent_record
        agent = record.agent
        result.agent_name = record.name
        result.agent_id = record.id

        # 2. Lifecycle: READY → RUNNING.
        lifecycle = self._lifecycles.get(record.name)
        if lifecycle is not None:
            result.lifecycle_before = lifecycle.state.value
            # Sincronizar el lifecycle desde el agent.status (puede haber
            # vuelto a IDLE tras una ejecución anterior).
            lifecycle.health_check()  # sincroniza
            try:
                await lifecycle.start()
            except RuntimeError as exc:
                result.success = False
                result.error = f"lifecycle start failed: {exc}"
                result.duration_ms = (time.monotonic() - start) * 1000.0
                return result

        # 3. Execute.
        try:
            exec_result = await agent.execute(task)
            duration_ms = (time.monotonic() - start) * 1000.0
            if exec_result.get("status") == "ok":
                result.success = True
                result.result = exec_result.get("result")
                # 4. Lifecycle: RUNNING → READY (auto-sync desde agent.status).
                if lifecycle is not None:
                    lifecycle.recover()  # no-op si no está en ERROR
                    result.lifecycle_after = lifecycle.state.value
            else:
                result.success = False
                result.error = exec_result.get("error", "unknown error")
                if lifecycle is not None:
                    lifecycle.mark_error(result.error)
                    result.lifecycle_after = lifecycle.state.value
            # 5. Record usage.
            record.record_usage(
                success=result.success, duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.monotonic() - start) * 1000.0
            result.success = False
            result.error = str(exc)
            if lifecycle is not None:
                lifecycle.mark_error(str(exc))
                result.lifecycle_after = lifecycle.state.value
            record.record_usage(success=False, duration_ms=duration_ms)

        result.duration_ms = duration_ms
        return result

    async def request_batch(
        self,
        *,
        capability: str,
        tasks: list[dict[str, Any]],
    ) -> list[ExecutionResult]:
        """Ejecuta varias tasks con la misma capability (secuencial)."""
        results: list[ExecutionResult] = []
        for task in tasks:
            r = await self.request(capability=capability, task=task)
            results.append(r)
        return results

    # ------------------------------------------------------------------
    # Health & metrics
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Health de todos los agentes con su lifecycle."""
        agents_health: dict[str, Any] = {}
        for name, lifecycle in self._lifecycles.items():
            agents_health[name] = lifecycle.health_check().to_dict()
        return {
            "agents": agents_health,
            "registry": self._registry.health(),
            "total_agents": len(self._lifecycles),
        }

    def metrics(self) -> dict[str, Any]:
        """Métricas combinadas."""
        return {
            "registry": self._registry.metrics(),
            "resolver": self._resolver.metrics(),
            "lifecycles": {
                name: lm.health_check().to_dict()
                for name, lm in self._lifecycles.items()
            },
        }

    # ------------------------------------------------------------------
    # Lifecycle global
    # ------------------------------------------------------------------

    async def stop_all(self) -> int:
        """Detiene todos los lifecycles."""
        count = 0
        for lifecycle in self._lifecycles.values():
            if not lifecycle.is_terminal:
                await lifecycle.stop()
                count += 1
        return count

    async def shutdown(self) -> None:
        """Apaga el orchestrator: stop_all + cleanup."""
        await self.stop_all()
        self._lifecycles.clear()


__all__ = ["ExecutionResult", "RegistryOrchestrator"]
