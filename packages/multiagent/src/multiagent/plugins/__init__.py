"""Plugin system subpackage (Sprint 2.5).

Public API:

- `PluginManifest` — contrato declarativo de un plugin.
- `Plugin` — clase base abstracta para plugins.
- `PluginHealth` — resultado de health_check().
- `PluginState` — estados del ciclo de vida.
- `PluginEvent` — tipos de eventos de plugins.
- `PluginManager` — gestor central de plugins.
- `PluginRecord` — registro interno de un plugin.
- `PluginLoader` — carga dinamica de plugins.
- `LoadResult` — resultado de la carga.
- `PluginDiscovery` — descubrimiento automatico.
- `DiscoveredPlugin` — plugin descubierto.
- `DiscoveryResult` — resultado del escaneo.
- `PluginCompatibilityChecker` — verificacion de versiones.
- `CompatibilityResult` — resultado de compatibilidad.
- `PluginEventBus` — bus de eventos de plugins.
- `PluginSystemEvent` — evento del sistema de plugins.
- `ToolDefinition` — definicion de herramienta de plugin.
- `ToolPlugin` — protocolo para plugins con herramientas.
- `ToolPluginMixin` — mixin para facilitar herramientas.
"""

from __future__ import annotations

from .base import Plugin, PluginHealth
from .discovery import DiscoveredPlugin, DiscoveryResult, PluginDiscovery
from .enums import PluginEvent, PluginState, can_transition_plugin
from .events import (
    PluginEventBus,
    PluginEventHandler,
    PluginEventFilter,
    PluginSystemEvent,
)
from .loader import LoadResult, PluginLoader
from .manager import PluginManager, PluginRecord
from .manifest import PluginManifest
from .tools import ToolDefinition, ToolPlugin, ToolPluginMixin
from .versioning import CompatibilityResult, PluginCompatibilityChecker

__all__ = [
    # Manifest
    "PluginManifest",
    # Base
    "Plugin",
    "PluginHealth",
    # Enums
    "PluginState",
    "PluginEvent",
    "can_transition_plugin",
    # Manager
    "PluginManager",
    "PluginRecord",
    # Loader
    "PluginLoader",
    "LoadResult",
    # Discovery
    "PluginDiscovery",
    "DiscoveredPlugin",
    "DiscoveryResult",
    # Versioning
    "PluginCompatibilityChecker",
    "CompatibilityResult",
    # Events
    "PluginEventBus",
    "PluginSystemEvent",
    "PluginEventHandler",
    "PluginEventFilter",
    # Tools
    "ToolDefinition",
    "ToolPlugin",
    "ToolPluginMixin",
]