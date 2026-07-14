"""PluginManager — gestion central de plugins (Sprint 2.5).

Responsabilidades:

- `load_plugin()`     — carga un plugin desde entry_point.
- `unload_plugin()`   — descarga un plugin, limpiando recursos.
- `reload_plugin()`   — recarga un plugin (unload + load).
- `enable_plugin()`   — habilita un plugin cargado.
- `disable_plugin()`  — deshabilita un plugin.
- `install_plugin()`  — instala un plugin desde un DiscoveredPlugin.
- `remove_plugin()`   — remueve un plugin completamente.
- `list_plugins()`    — lista todos los plugins.
- `get_plugin()`      — obtiene un plugin por ID.
- `health()`          — health check de todos los plugins.
- `metrics()`         — metricas del sistema de plugins.

El PluginManager:

- Detecta plugins duplicados.
- Verifica versiones incompatibles via PluginCompatibilityChecker.
- Verifica dependencias faltantes.
- Registra agentes y herramientas en los registries correspondientes.
- Emite eventos via PluginEventBus.
- NO importa agentes concretos — usa AgentRegistry via
  CapabilityResolver para el routing.

Integracion con AgentRegistry::

    Plugin.get_agents()
        ↓
    PluginManager._register_plugin_agents()
        ↓
    AgentRegistry.register(agent)
        ↓
    CapabilityResolver → WorkflowEngine

Integracion con EventBus::

    PluginManager
        ↓
    PluginEventBus.publish(PluginEvent.PLUGIN_LOADED, ...)
        ↓
    WorkflowEngine EventBus (si integrado)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .base import Plugin
from .discovery import DiscoveredPlugin
from .enums import PluginEvent, PluginState, can_transition_plugin
from .events import PluginEventBus
from .loader import LoadResult, PluginLoader
from .manifest import PluginManifest
from .versioning import CompatibilityResult, PluginCompatibilityChecker

if TYPE_CHECKING:
    from ..registry.agent_registry import AgentRegistry

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return time.time()


@dataclass
class PluginRecord:
    """Registro interno de un plugin cargado.

    Attributes:
        plugin:        Instancia del Plugin.
        manifest:      Manifest del plugin.
        state:         Estado actual del plugin.
        loaded_at:     Timestamp de carga.
        enabled:       Si esta habilitado.
        agents_registered: IDs de agentes registrados.
        tools_registered:  Nombres de herramientas registradas.
        load_duration_ms: Tiempo de carga en ms.
        error:         Ultimo error, si lo hay.
    """

    plugin: Plugin
    manifest: PluginManifest
    state: PluginState = PluginState.LOADED
    loaded_at: str = ""
    enabled: bool = False
    agents_registered: list[str] = field(default_factory=list)
    tools_registered: list[str] = field(default_factory=list)
    load_duration_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "state": self.state.value,
            "loaded_at": self.loaded_at,
            "enabled": self.enabled,
            "agents_registered": list(self.agents_registered),
            "tools_registered": list(self.tools_registered),
            "load_duration_ms": round(self.load_duration_ms, 2),
            "error": self.error,
        }


class PluginManager:
    """Gestor central del sistema de plugins.

    Uso basico::

        manager = PluginManager(agent_registry=registry)
        await manager.install_plugin(discovered_plugin)
        await manager.enable_plugin("my-plugin")
    """

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry | None = None,
        event_bus: PluginEventBus | None = None,
        system_api_version: str = "1.0.0",
        init_timeout: float = 30.0,
    ) -> None:
        self._plugins: dict[str, PluginRecord] = {}
        self._loader = PluginLoader()
        self._compat_checker = PluginCompatibilityChecker(
            system_api_version=system_api_version,
        )
        self._agent_registry = agent_registry
        self._event_bus = event_bus or PluginEventBus()
        self._init_timeout = init_timeout
        self._load_count: int = 0
        self._error_count: int = 0

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------

    @property
    def event_bus(self) -> PluginEventBus:
        return self._event_bus

    @property
    def loader(self) -> PluginLoader:
        return self._loader

    @property
    def plugin_count(self) -> int:
        return len(self._plugins)

    # ------------------------------------------------------------------
    # Instalacion y carga
    # ------------------------------------------------------------------

    async def install_plugin(
        self,
        discovered: DiscoveredPlugin,
    ) -> LoadResult:
        """Instala un plugin descubierto.

        Verifica compatibilidad, carga el plugin y lo registra
        internamente. El plugin queda en estado LOADED, listo para
        ser habilitado.

        Args:
            discovered: DiscoveredPlugin del discovery.

        Returns:
            LoadResult con el resultado.
        """
        manifest = discovered.manifest

        # 1. Verificar duplicados.
        if manifest.id in self._plugins:
            self._error_count += 1
            error = f"Plugin '{manifest.id}' is already installed"
            logger.error(error)
            await self._emit_event(
                PluginEvent.PLUGIN_FAILED,
                plugin_id=manifest.id,
                payload={"error": error, "reason": "duplicate"},
            )
            return LoadResult(success=False, error=error)

        # 2. Verificar compatibilidad.
        compat = self._compat_checker.check(
            manifest,
            available_plugins=self._available_plugin_versions(),
        )
        if not compat.compatible:
            self._error_count += 1
            error = "; ".join(compat.errors)
            logger.error(
                "Plugin '%s' compatibility check failed: %s",
                manifest.id, error,
            )
            await self._emit_event(
                PluginEvent.PLUGIN_FAILED,
                plugin_id=manifest.id,
                payload={
                    "error": error,
                    "reason": "incompatible",
                    "compatibility_errors": compat.errors,
                },
            )
            return LoadResult(success=False, error=error)

        # 3. Cargar el plugin.
        entry_point = discovered.entry_point
        if not entry_point:
            error = f"No entry_point for plugin '{manifest.id}'"
            self._error_count += 1
            return LoadResult(success=False, error=error)

        result = self._loader.load(entry_point)
        if not result.success:
            self._error_count += 1
            await self._emit_event(
                PluginEvent.PLUGIN_FAILED,
                plugin_id=manifest.id,
                payload={"error": result.error, "reason": "load_failed"},
            )
            return result

        plugin = result.plugin
        assert plugin is not None  # for type checker

        # 4. Registrar internamente.
        record = PluginRecord(
            plugin=plugin,
            manifest=manifest,
            state=PluginState.LOADED,
            loaded_at=_now_iso(),
            load_duration_ms=result.duration_ms,
        )
        self._plugins[manifest.id] = record
        self._load_count += 1

        await self._emit_event(
            PluginEvent.PLUGIN_INSTALLED,
            plugin_id=manifest.id,
            payload={
                "version": manifest.version,
                "load_duration_ms": result.duration_ms,
            },
        )
        await self._emit_event(
            PluginEvent.PLUGIN_LOADED,
            plugin_id=manifest.id,
            payload={"version": manifest.version},
        )

        logger.info(
            "Plugin '%s' v%s installed (%.1fms)",
            manifest.id, manifest.version, result.duration_ms,
        )
        return result

    async def load_plugin(self, entry_point: str) -> LoadResult:
        """Carga un plugin directamente desde un entry_point.

        Args:
            entry_point: Formato 'module.path:ClassName'.

        Returns:
            LoadResult con el resultado.
        """
        result = self._loader.load(entry_point)
        if not result.success:
            self._error_count += 1
            return result

        plugin = result.plugin
        assert plugin is not None
        manifest = result.manifest
        assert manifest is not None

        if manifest.id in self._plugins:
            self._error_count += 1
            error = f"Plugin '{manifest.id}' is already loaded"
            return LoadResult(success=False, error=error)

        record = PluginRecord(
            plugin=plugin,
            manifest=manifest,
            state=PluginState.LOADED,
            loaded_at=_now_iso(),
            load_duration_ms=result.duration_ms,
        )
        self._plugins[manifest.id] = record
        self._load_count += 1

        await self._emit_event(
            PluginEvent.PLUGIN_LOADED,
            plugin_id=manifest.id,
            payload={"version": manifest.version},
        )
        return result

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize_plugin(self, plugin_id: str) -> bool:
        """Inicializa un plugin cargado.

        Args:
            plugin_id: ID del plugin.

        Returns:
            True si la inicializacion fue exitosa.
        """
        record = self._get_record(plugin_id)
        if record is None:
            return False
        if record.state not in (PluginState.LOADED, PluginState.ERROR):
            logger.warning(
                "Cannot initialize plugin '%s': state is %s",
                plugin_id, record.state.value,
            )
            return False

        try:
            record.state = PluginState.INITIALIZED
            start = time.monotonic()
            await asyncio.wait_for(
                record.plugin.initialize(),
                timeout=self._init_timeout,
            )
            duration = (time.monotonic() - start) * 1000.0
            record.plugin._init_duration_ms = duration
            await self._emit_event(
                PluginEvent.PLUGIN_INITIALIZED,
                plugin_id=plugin_id,
                payload={"duration_ms": duration},
            )
            logger.info("Plugin '%s' initialized (%.1fms)", plugin_id, duration)
            return True
        except asyncio.TimeoutError:
            error = f"Plugin '{plugin_id}' initialization timed out"
            record.state = PluginState.ERROR
            record.error = error
            self._error_count += 1
            await self._emit_event(
                PluginEvent.PLUGIN_FAILED,
                plugin_id=plugin_id,
                payload={"error": error, "reason": "init_timeout"},
            )
            logger.error(error)
            return False
        except Exception as exc:  # noqa: BLE001
            error = f"Plugin '{plugin_id}' initialization failed: {exc}"
            record.state = PluginState.ERROR
            record.error = error
            record.plugin._add_error(error)
            self._error_count += 1
            await self._emit_event(
                PluginEvent.PLUGIN_FAILED,
                plugin_id=plugin_id,
                payload={"error": error, "reason": "init_error"},
            )
            logger.error(error)
            return False

    async def start_plugin(self, plugin_id: str) -> bool:
        """Inicia un plugin inicializado."""
        record = self._get_record(plugin_id)
        if record is None:
            return False
        if record.state != PluginState.INITIALIZED:
            logger.warning(
                "Cannot start plugin '%s': state is %s",
                plugin_id, record.state.value,
            )
            return False

        try:
            record.state = PluginState.STARTED
            await record.plugin.start()
            await self._emit_event(
                PluginEvent.PLUGIN_STARTED,
                plugin_id=plugin_id,
            )
            logger.info("Plugin '%s' started", plugin_id)
            return True
        except Exception as exc:  # noqa: BLE001
            error = f"Plugin '{plugin_id}' start failed: {exc}"
            record.state = PluginState.ERROR
            record.error = error
            record.plugin._add_error(error)
            self._error_count += 1
            await self._emit_event(
                PluginEvent.PLUGIN_FAILED,
                plugin_id=plugin_id,
                payload={"error": error, "reason": "start_error"},
            )
            return False

    # ------------------------------------------------------------------
    # Enable / Disable
    # ------------------------------------------------------------------

    async def enable_plugin(self, plugin_id: str) -> bool:
        """Habilita un plugin (inicializa + registra agentes).

        Returns:
            True si el plugin fue habilitado exitosamente.
        """
        record = self._get_record(plugin_id)
        if record is None:
            return False

        # Si no esta inicializado, inicializar primero.
        if record.state == PluginState.LOADED:
            if not await self.initialize_plugin(plugin_id):
                return False
            record = self._plugins[plugin_id]

        if record.state == PluginState.INITIALIZED:
            if not await self.start_plugin(plugin_id):
                return False
            record = self._plugins[plugin_id]

        if record.state not in (PluginState.STARTED, PluginState.DISABLED):
            logger.warning(
                "Cannot enable plugin '%s': state is %s",
                plugin_id, record.state.value,
            )
            return False

        # Registrar agentes y herramientas.
        try:
            await self._register_plugin_agents(record)
            await self._register_plugin_tools(record)
        except Exception as exc:  # noqa: BLE001
            error = f"Plugin '{plugin_id}' agent/tool registration failed: {exc}"
            record.state = PluginState.ERROR
            record.error = error
            self._error_count += 1
            await self._emit_event(
                PluginEvent.PLUGIN_FAILED,
                plugin_id=plugin_id,
                payload={"error": error, "reason": "registration_failed"},
            )
            return False

        record.state = PluginState.ENABLED
        record.enabled = True
        record.plugin._enabled = True

        await self._emit_event(
            PluginEvent.PLUGIN_ENABLED,
            plugin_id=plugin_id,
            payload={
                "agents_registered": record.agents_registered,
                "tools_registered": record.tools_registered,
            },
        )
        logger.info("Plugin '%s' enabled", plugin_id)
        return True

    async def disable_plugin(self, plugin_id: str) -> bool:
        """Deshabilita un plugin (desregistra agentes).

        Returns:
            True si el plugin fue deshabilitado.
        """
        record = self._get_record(plugin_id)
        if record is None:
            return False
        if record.state != PluginState.ENABLED:
            logger.warning(
                "Cannot disable plugin '%s': state is %s",
                plugin_id, record.state.value,
            )
            return False

        # Desregistrar agentes y herramientas.
        try:
            await self._unregister_plugin_agents(record)
            await self._unregister_plugin_tools(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Error unregistering plugin '%s' resources: %s",
                plugin_id, exc,
            )

        record.state = PluginState.DISABLED
        record.enabled = False
        record.plugin._enabled = False

        await self._emit_event(
            PluginEvent.PLUGIN_DISABLED,
            plugin_id=plugin_id,
        )
        logger.info("Plugin '%s' disabled", plugin_id)
        return True

    # ------------------------------------------------------------------
    # Unload / Remove / Reload
    # ------------------------------------------------------------------

    async def unload_plugin(self, plugin_id: str) -> bool:
        """Descarga un plugin (lo detiene si esta activo).

        Returns:
            True si el plugin fue descargado.
        """
        record = self._get_record(plugin_id)
        if record is None:
            return False

        # Deshabilitar si esta habilitado.
        if record.state == PluginState.ENABLED:
            await self.disable_plugin(plugin_id)
            record = self._plugins.get(plugin_id)
            if record is None:
                return True

        # Detener si esta iniciado.
        if record.state in (PluginState.STARTED, PluginState.INITIALIZED):
            try:
                await record.plugin.stop()
                await record.plugin.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Error stopping plugin '%s': %s", plugin_id, exc,
                )

        record.state = PluginState.UNLOADED
        del self._plugins[plugin_id]

        await self._emit_event(
            PluginEvent.PLUGIN_UNLOADED,
            plugin_id=plugin_id,
        )
        logger.info("Plugin '%s' unloaded", plugin_id)
        return True

    async def remove_plugin(self, plugin_id: str) -> bool:
        """Remueve un plugin completamente (alias de unload_plugin).

        Returns:
            True si el plugin fue removido.
        """
        result = await self.unload_plugin(plugin_id)
        if result:
            await self._emit_event(
                PluginEvent.PLUGIN_REMOVED,
                plugin_id=plugin_id,
            )
        return result

    async def reload_plugin(self, plugin_id: str) -> bool:
        """Recarga un plugin (unload + load + enable).

        Returns:
            True si la recarga fue exitosa.
        """
        record = self._get_record(plugin_id)
        if record is None:
            return False

        manifest = record.manifest
        entry_point = manifest.entry_point

        # Guardar estado previo.
        was_enabled = record.enabled

        # Unload.
        await self.unload_plugin(plugin_id)

        # Re-load.
        result = await self.load_plugin(entry_point)
        if not result.success:
            return False

        # Re-enable si estaba habilitado.
        if was_enabled:
            return await self.enable_plugin(plugin_id)

        await self._emit_event(
            PluginEvent.PLUGIN_RELOADED,
            plugin_id=plugin_id,
            payload={"version": manifest.version},
        )
        return True

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def get_plugin(self, plugin_id: str) -> Plugin | None:
        """Devuelve un plugin por ID."""
        record = self._plugins.get(plugin_id)
        return record.plugin if record else None

    def get_record(self, plugin_id: str) -> PluginRecord | None:
        """Devuelve el PluginRecord por ID."""
        return self._plugins.get(plugin_id)

    def list_plugins(
        self,
        *,
        state: PluginState | None = None,
        enabled_only: bool = False,
    ) -> list[PluginRecord]:
        """Devuelve la lista de plugins.

        Args:
            state:        Filtra por estado.
            enabled_only: Solo plugins habilitados.
        """
        records = list(self._plugins.values())
        if state is not None:
            records = [r for r in records if r.state == state]
        if enabled_only:
            records = [r for r in records if r.enabled]
        return records

    def exists(self, plugin_id: str) -> bool:
        """True si un plugin esta registrado."""
        return plugin_id in self._plugins

    def get_manifest(self, plugin_id: str) -> PluginManifest | None:
        """Devuelve el manifest de un plugin."""
        record = self._plugins.get(plugin_id)
        return record.manifest if record else None

    # ------------------------------------------------------------------
    # Health y metricas
    # ------------------------------------------------------------------

    def health(self, plugin_id: str | None = None) -> dict[str, Any]:
        """Devuelve health de un plugin o de todos."""
        if plugin_id is not None:
            record = self._plugins.get(plugin_id)
            if record is None:
                return {"error": f"Plugin '{plugin_id}' not found"}
            return record.plugin.health_check().to_dict()

        # Health de todos los plugins.
        results: dict[str, Any] = {}
        for pid, record in self._plugins.items():
            results[pid] = record.plugin.health_check().to_dict()
        return results

    def metrics(self) -> dict[str, Any]:
        """Devuelve metricas del sistema de plugins."""
        state_counts: dict[str, int] = {}
        for record in self._plugins.values():
            s = record.state.value
            state_counts[s] = state_counts.get(s, 0) + 1
        return {
            "total_plugins": len(self._plugins),
            "enabled_plugins": sum(
                1 for r in self._plugins.values() if r.enabled
            ),
            "total_loads": self._load_count,
            "total_errors": self._error_count,
            "state_distribution": state_counts,
            "loader_metrics": self._loader.metrics(),
            "event_bus_metrics": self._event_bus.metrics(),
        }

    # ------------------------------------------------------------------
    # Integracion con AgentRegistry
    # ------------------------------------------------------------------

    async def _register_plugin_agents(self, record: PluginRecord) -> None:
        """Registra los agentes de un plugin en el AgentRegistry."""
        if self._agent_registry is None:
            return

        agents = record.plugin.get_agents()
        for agent in agents:
            try:
                # Obtener capabilities del manifest del plugin.
                caps = list(record.manifest.capabilities)
                await self._agent_registry.register(
                    agent,
                    provided_capabilities=caps,
                    metadata={"source_plugin": record.manifest.id},
                )
                record.agents_registered.append(agent.id)
                await self._emit_event(
                    PluginEvent.AGENT_REGISTERED,
                    plugin_id=record.manifest.id,
                    payload={
                        "agent_id": agent.id,
                        "agent_name": agent.name,
                    },
                )
                logger.info(
                    "Plugin '%s' registered agent '%s'",
                    record.manifest.id, agent.name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Plugin '%s' failed to register agent '%s': %s",
                    record.manifest.id,
                    getattr(agent, "name", "?"),
                    exc,
                )

    async def _unregister_plugin_agents(self, record: PluginRecord) -> None:
        """Desregistra los agentes de un plugin."""
        if self._agent_registry is None:
            return
        for agent_id in record.agents_registered:
            try:
                await self._agent_registry.unregister(agent_id)
                logger.info(
                    "Plugin '%s' unregistered agent '%s'",
                    record.manifest.id, agent_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Plugin '%s' failed to unregister agent '%s': %s",
                    record.manifest.id, agent_id, exc,
                )
        record.agents_registered.clear()

    async def _register_plugin_tools(self, record: PluginRecord) -> None:
        """Registra las herramientas de un plugin."""
        tools = record.plugin.get_tools()
        for tool in tools:
            tool_name = getattr(tool, "name", str(tool))
            record.tools_registered.append(tool_name)
            await self._emit_event(
                PluginEvent.TOOL_REGISTERED,
                plugin_id=record.manifest.id,
                payload={"tool_name": tool_name},
            )
            logger.info(
                "Plugin '%s' registered tool '%s'",
                record.manifest.id, tool_name,
            )

    async def _unregister_plugin_tools(self, record: PluginRecord) -> None:
        """Desregistra las herramientas de un plugin."""
        for tool_name in record.tools_registered:
            logger.info(
                "Plugin '%s' unregistered tool '%s'",
                record.manifest.id, tool_name,
            )
        record.tools_registered.clear()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _get_record(self, plugin_id: str) -> PluginRecord | None:
        """Devuelve el record o None con logging."""
        record = self._plugins.get(plugin_id)
        if record is None:
            logger.warning("Plugin '%s' not found", plugin_id)
        return record

    def _available_plugin_versions(self) -> dict[str, str]:
        """Devuelve {plugin_id: version} de los plugins cargados."""
        return {
            pid: record.manifest.version
            for pid, record in self._plugins.items()
        }

    async def _emit_event(
        self,
        event_type: PluginEvent,
        *,
        plugin_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emite un evento en el bus."""
        try:
            await self._event_bus.publish(
                event_type,
                source="PluginManager",
                plugin_id=plugin_id,
                payload=payload,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit plugin event")


__all__ = [
    "PluginManager",
    "PluginRecord",
]