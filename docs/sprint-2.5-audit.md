# Sprint 2.5 — Plugin System Architecture Audit

**Date**: 2026-07-14
**Branch**: `sprint-2.5-plugin-system`
**Base Version**: v0.7.0

---

## 1. Current State

### 1.1 Package Structure

```
packages/multiagent/src/multiagent/
├── __init__.py          # 67 exported symbols
├── base.py              # BaseAgent (ABC), HealthReport
├── config.py            # MultiAgentConfig, RetryPolicy
├── enums.py             # AgentStatus, TaskStatus, Priority, etc.
├── messages.py          # Message, MessageBus (point-to-point)
├── queue.py             # Task, PriorityQueue
├── memory.py            # AgentMemory, MemoryEntry
├── logs.py              # AgentLogger, LogEntry
├── orchestrator.py      # AgentOrchestrator
├── agents/              # 7 concrete agents (coder, git, memory, etc.)
│   └── __init__.py      # Exports all concrete agents
├── registry/            # Sprint 2.2/2.3
│   ├── agent_registry.py # AgentRegistry, AgentRecord
│   ├── capability.py    # Capability, CapabilityRegistry
│   ├── resolver.py      # CapabilityResolver
│   ├── adapter.py       # BackwardCompatibilityAdapter
│   ├── manifest.py      # AgentManifest (frozen dataclass)
│   ├── lifecycle.py     # LifecycleManager, LifecycleState
│   └── orchestrator.py  # RegistryOrchestrator
└── workflow/            # Sprint 2.4
    ├── models.py        # Step, Workflow
    ├── dag.py           # DAG, ValidationResult
    ├── engine.py        # WorkflowEngine
    ├── event_bus.py     # EventBus (pub/sub), Event
    ├── scheduler.py     # Scheduler
    ├── load_balancer.py # LoadBalancer
    └── ...
```

### 1.2 Key Extension Points

| Extension Point | Location | Mechanism |
|---|---|---|
| Agent creation | `agents/*.py` | Subclass `BaseAgent`, implement `_execute_impl()` |
| Agent registration | `AgentRegistry.register()` | Wraps in `AgentRecord`, emits events |
| Capability discovery | `CapabilityResolver` | Pattern matching on `AgentManifest.capabilities` |
| Tool registration | `packages/tools/` | `ToolRegistry.register()` (separate package) |
| Workflow events | `EventBus` | Pub/sub with `WorkflowEvent` enum |
| Lifecycle | `LifecycleManager` | Wraps `BaseAgent` with state machine |

### 1.3 Current Limitations

1. **No plugin system**: Agents are hardcoded imports in `__init__.py`
2. **No dynamic loading**: All agents imported statically at package load
3. **No tool plugin API**: Tools have their own registry but no plugin mechanism
4. **No version checking**: No API version compatibility for extensions
5. **No discovery**: Agents must be manually imported and registered

### 1.4 Integration Points for Plugins

- `AgentRegistry.register(agent)` — accepts `BaseAgent` instances
- `CapabilityRegistry.register(capability)` — registers capabilities
- `EventBus.publish()` — system-wide event bus
- `LifecycleManager(agent)` — lifecycle wrapper

### 1.5 Design Constraints

1. **NEVER import concrete agents in the plugin system** — use `AgentRegistry` via `CapabilityResolver`
2. **Maintain backward compatibility** — all 67 exported symbols must remain
3. **Frozen dataclasses for manifests** — follow `AgentManifest` pattern
4. **asyncio-safe** — follow existing patterns
5. **Event-driven** — use `EventBus` for plugin lifecycle events
6. **No circular imports** — use `TYPE_CHECKING` guards

---

## 2. Plugin System Architecture

### 2.1 Component Map

```
plugins/
├── manifest.py      # PluginManifest (frozen dataclass)
├── base.py          # Plugin (ABC), PluginState
├── manager.py       # PluginManager
├── loader.py        # PluginLoader
├── discovery.py     # PluginDiscovery
├── versioning.py    # PluginCompatibilityChecker
├── events.py        # PluginEvent enum + event helpers
├── tools.py         # ToolPluginMixin
├── __init__.py      # Public API
```

### 2.2 Data Flow

```
Plugin (with manifest)
    ↓
PluginDiscovery (scans plugins/ directory)
    ↓
PluginLoader (imports and instantiates)
    ↓
PluginManager (validates, enables, tracks)
    ↓
AgentRegistry.register() [for agent plugins]
ToolRegistry.register()  [for tool plugins]
    ↓
CapabilityResolver → WorkflowEngine
```

### 2.3 API Version

- Current system API version: `"1.0.0"`
- Plugin manifests must declare `api_version` compatible with system
- Semver comparison for compatibility checking

---

## 3. Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Circular imports | Medium | TYPE_CHECKING guards, lazy imports |
| Plugin breaking core | High | Sandboxed initialization, rollback on failure |
| Duplicate plugins | Medium | ID uniqueness check in PluginManager |
| Incompatible plugins | Medium | PluginCompatibilityChecker before load |
| Test breakage | Low | All new code in new `plugins/` subpackage |