"""Plugin enums (Sprint 2.5).

States and event types for the plugin system.
"""

from __future__ import annotations

from enum import Enum


class PluginState(str, Enum):
    """Plugin lifecycle states.

    Lifecycle::

        DISCOVERED → LOADED → INITIALIZED → STARTED ⇄ ENABLED
                                                  ↓
                                                DISABLED
        Any → ERROR → (can recover to previous state)
        Any → UNLOADED (terminal)
    """

    DISCOVERED = "discovered"
    LOADED = "loaded"
    INITIALIZED = "initialized"
    STARTED = "started"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"
    UNLOADED = "unloaded"


# Transiciones permitidas para plugins.
_ALLOWED_PLUGIN_TRANSITIONS: dict[PluginState, frozenset[PluginState]] = {
    PluginState.DISCOVERED: frozenset({
        PluginState.LOADED, PluginState.UNLOADED, PluginState.ERROR,
    }),
    PluginState.LOADED: frozenset({
        PluginState.INITIALIZED, PluginState.UNLOADED, PluginState.ERROR,
    }),
    PluginState.INITIALIZED: frozenset({
        PluginState.STARTED, PluginState.UNLOADED, PluginState.ERROR,
    }),
    PluginState.STARTED: frozenset({
        PluginState.ENABLED, PluginState.DISABLED, PluginState.UNLOADED,
        PluginState.ERROR, PluginState.INITIALIZED,
    }),
    PluginState.ENABLED: frozenset({
        PluginState.DISABLED, PluginState.UNLOADED, PluginState.ERROR,
    }),
    PluginState.DISABLED: frozenset({
        PluginState.ENABLED, PluginState.UNLOADED, PluginState.ERROR,
    }),
    PluginState.ERROR: frozenset({
        PluginState.LOADED, PluginState.UNLOADED, PluginState.DISABLED,
    }),
    PluginState.UNLOADED: frozenset(),  # terminal
}


def can_transition_plugin(
    current: PluginState, target: PluginState,
) -> bool:
    """Devuelve True si el plugin puede transicionar de current a target."""
    return target in _ALLOWED_PLUGIN_TRANSITIONS.get(current, frozenset())


class PluginEvent(str, Enum):
    """Eventos canonicos del sistema de plugins."""

    PLUGIN_DISCOVERED = "plugin.discovered"
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_UNLOADED = "plugin.unloaded"
    PLUGIN_ENABLED = "plugin.enabled"
    PLUGIN_DISABLED = "plugin.disabled"
    PLUGIN_FAILED = "plugin.failed"
    PLUGIN_INITIALIZED = "plugin.initialized"
    PLUGIN_STARTED = "plugin.started"
    PLUGIN_STOPPED = "plugin.stopped"
    PLUGIN_RELOADED = "plugin.reloaded"
    PLUGIN_INSTALLED = "plugin.installed"
    PLUGIN_REMOVED = "plugin.removed"
    AGENT_REGISTERED = "plugin.agent.registered"
    TOOL_REGISTERED = "plugin.tool.registered"
    WORKFLOW_REGISTERED = "plugin.workflow.registered"


__all__ = [
    "PluginEvent",
    "PluginState",
    "can_transition_plugin",
]