# Sprint 2.2/2 — Refinamiento de la Arquitectura Multiagente

> **Estado:** ✅ Completo · **Rama:** `feature/sprint-2-2-2-agent-registry`
> **Tests:** 334 pasando (248 Sprint 2.1+2.2 + 86 nuevos) · **Compat:** ✅

## Resumen

Elimina dependencias rígidas entre Scheduler, Workflow Engine y agentes.
Los agentes dejan de identificarse únicamente por nombre y pasan a ser
**descubiertos, registrados y seleccionados automáticamente** según sus
**capabilities**.

### Cambios clave

1. **`AgentRegistry`** — único responsable de registrar agentes.
2. **`Capability` / `CapabilityRegistry`** — catálogo de habilidades declarativas.
3. **`CapabilityResolver`** — selección inteligente por scoring ponderado.
4. **`BackwardCompatibilityAdapter`** — traduce nombres legacy (PlannerAgent, etc.) a capabilities.
5. **`RegistryAwareScheduler`** — scheduler que usa el resolver en lugar de nombres.
6. **Auto-discovery** — registro automático al crear agentes y cargar plugins.
7. **Health system** — heartbeat, last_seen, uptime, usage, errores.
8. **Observabilidad** — registry_size, capability_hits/misses, agent_selection_time, etc.
9. **Eventos nuevos** — agent.registered, agent.removed, capability.resolved, scheduler.selected_agent.

## Diagrama: Workflow → Scheduler → Resolver → Registry → Agent

```mermaid
flowchart TB
    WF[Workflow<br/>con steps y dependencias]

    subgraph SCHED["RegistryAwareScheduler"]
        SCHED_S[schedule]
        ADAPT[BackwardCompatibilityAdapter<br/>traduce agent/action a capability]
    end

    subgraph RESOLV["CapabilityResolver"]
        SCORE[Scoring ponderado<br/>specialty + priority + cost<br/>+ history + load + latency]
    end

    subgraph REG["AgentRegistry"]
        REG_MGR[registro único<br/>por id y por nombre]
        REC1[AgentRecord 1<br/>capabilities + health]
        REC2[AgentRecord 2<br/>capabilities + health]
        REC3[AgentRecord N<br/>capabilities + health]
        DISC[Auto-Discovery<br/>hooks + infer_capabilities]
    end

    subgraph CAP["CapabilityRegistry"]
        CAP_CAT[34 capabilities<br/>en 10 categorías]
    end

    AGENT1[Agent 1<br/>e.g. CoderAgent]
    AGENT2[Agent 2<br/>e.g. ReviewerAgent]
    AGENT3[Agent N<br/>custom agent]

    WF -->|step.agent=CoderAgent<br/>step.action=refactor| SCHED_S
    SCHED_S --> ADAPT
    ADAPT -->|code.refactor| SCORE
    SCORE -->|find_by_capability| REG_MGR
    REG_MGR --> REC1
    REG_MGR --> REC2
    REG_MGR --> REC3
    CAP_CAT -.reference.> SCORE
    REG_MGR -.register.-> AGENT1
    REG_MGR -.register.-> AGENT2
    REG_MGR -.register.-> AGENT3
    DISC -.auto-register.-> REG_MGR

    SCORE -->|resolved| SCHED_S
    SCHED_S -->|dispatch| AGENT1

    classDef new fill:#cfe,stroke:#383,stroke-width:2px
    class SCHED,RESOLV,REG,CAP new
```

## Componentes

### `Capability` (inmutable)

```python
@dataclass(frozen=True)
class Capability:
    name: str          # "code.generate" (jerárquico con dots)
    description: str   # "Generate code"
    category: str      # "code" (auto-derivada)
    version: str       # "1.0.0"
    metadata: dict     # libre
```

- **Jerárquica**: `code.generate` es sub-capability de `code`.
- **Wildcards**: `code.*` matchea `code.generate`, `code.review`, etc.
- **Match jerárquico**: un agente que provee `code` puede manejar `code.generate`.

### `CapabilityRegistry`

Catálogo de capabilities conocidas. 34 estándar en 10 categorías:

| Categoría | Capabilities |
|---|---|
| planning | planning |
| workflow | workflow, workflow.create, workflow.execute, workflow.cancel |
| code | code, code.generate, code.refactor, code.fix, code.test, code.review |
| git | git, git.commit, git.branch, git.merge, git.push, git.pull, git.status |
| terminal | terminal, terminal.execute |
| filesystem | filesystem, filesystem.read, filesystem.write, filesystem.list |
| memory | memory, memory.retrieve, memory.store, memory.search |
| web | web, web.search |
| documentation | documentation, documentation.lookup |
| research | research, research.summarize |

Métodos: `register`, `unregister`, `get`, `exists`, `all`, `by_category`,
`search` (con wildcards), `categories`, `metrics` (hits/misses/hit_rate).

### `AgentRecord`

Wrapper sobre `BaseAgent` con metadatos de registro:

| Campo | Descripción |
|---|---|
| `id` | UUID único |
| `name` | Nombre (único en el registry) |
| `version` | Versión del agente |
| `description` | Descripción humana |
| `provided_capabilities` | Capabilities que provee |
| `required_capabilities` | Capabilities que necesita de otros |
| `preferred_capabilities` | Capabilities preferidas |
| `priority` | Prioridad (mayor = más prioritario) |
| `status` | AgentStatus snapshot |
| `metadata` | Metadatos libres (incl. `cost`) |
| `created_at` | ISO timestamp |
| `last_seen` | Última actividad |
| `last_heartbeat` | Último heartbeat |
| `health_state` | HealthState (alive/busy/idle/error/dead) |
| `errors` | Historial de errores (cap 20) |
| `usage_count` | Veces seleccionado |
| `success_count` / `failure_count` | Conteos de éxito/fallo |
| `total_duration_ms` | Tiempo total ocupado |
| `avg_latency_ms` | Latencia media (rolling 100) |

### `AgentRegistry`

Registro único de agentes. **Ningún otro componente debe mantener su
propia lista de agentes.**

| Método | Descripción |
|---|---|
| `register(agent, provided_capabilities, ...)` | Registra (con replace opcional) |
| `unregister(id_or_name)` | Elimina |
| `replace(old, new)` | Reemplaza manteniendo metadatos |
| `discover()` | Ejecuta hooks de auto-discovery |
| `get(id_or_name)` / `get_by_name` / `get_by_id` | Consultas |
| `get_all()` / `names()` / `exists()` | Consultas |
| `find_by_capability(cap)` | Agentes que proveen una capability |
| `find_by_capability_pattern(pattern)` | Con wildcards |
| `heartbeat(id_or_name)` | Registra heartbeat |
| `update_health()` | Sincroniza estado de todos |
| `health()` | Summary con conteos por estado |
| `start_heartbeat_loop(interval)` | Background health check |
| `metrics()` | Métricas del registry |

**Auto-discovery**:

- `add_discovery_hook(hook)`: hook async `(registry) -> list[BaseAgent]`.
- `discover()`: ejecuta todos los hooks, registra los agentes encontrados.
- `_infer_capabilities(agent)`: deriva capabilities de `objectives` y `tools`.

### `CapabilityResolver`

Resuelve un objetivo (string o Capability) a un agente capaz.

**Algoritmo de scoring ponderado** (pesos normalizables):

```
score = w_specialty * specialty_score      (0..1, exacto=1.0, wildcard=0.8, jerárquico=0.6)
      + w_priority  * priority_norm         (0..1)
      + w_cost      * (1 - cost_norm)       (0..1, menor costo = mejor)
      + w_history   * success_rate          (0..1)
      + w_load      * (1 - load_norm)       (0..1, menor usage = mejor)
      + w_latency   * (1 - latency_norm)    (0..1, menor latencia = mejor)
```

Pesos por defecto: `specialty=0.30, history=0.20, priority=0.15, load=0.15,
cost=0.10, latency=0.10`.

**Eventos emitidos**:

- `capability.resolved` (con agent, score, candidates, duration_ms).
- `capability.miss` (cuando no hay agentes capaces).
- `scheduler.selected_agent` (alias del enunciado).

### `BackwardCompatibilityAdapter`

Traduce workflows antiguos (que referencian agentes por nombre) al nuevo
sistema basado en capabilities.

**Mapeo de nombres legacy → capabilities**:

| Nombre legacy | Capabilities |
|---|---|
| PlannerAgent / planner | planning, workflow.create |
| CoderAgent / coder | code.generate, code.refactor, code.fix, code.test, filesystem.read, filesystem.write |
| ReviewerAgent / reviewer | code.review |
| ResearchAgent / research | research, web.search, documentation.lookup |
| TerminalAgent / terminal | terminal.execute |
| GitAgent / git | git.commit, git.branch, git.merge, git.push, git.pull, git.status |
| MemoryAgent / memory | memory.retrieve, memory.store, memory.search |

**Mapeo de actions → capability**:

| Action | Capability |
|---|---|
| refactor | code.refactor |
| review | code.review |
| test | code.test |
| fix | code.fix |
| write | code.generate |
| plan / plan_workflow | planning |
| commit | git.commit |
| branch / checkout | git.branch |
| merge | git.merge |
| push | git.push |
| pull | git.pull |
| store | memory.store |
| retrieve | memory.retrieve |
| search | memory.search |
| execute / cmd | terminal.execute |
| gather / analyze | research |
| summarize | research.summarize |

**Resolución** (`resolve_step_agent(step)`):

1. Si `step.agent` es un nombre legacy mapeado, traducir a capabilities.
2. Si `step.action` está mapeado, usar esa capability (más específica).
3. Si `step.agent` está registrado por nombre custom, usarlo directamente.
4. Fallback: resolver con capability `planning`.

### `RegistryAwareScheduler`

Scheduler que usa el `CapabilityResolver` en lugar del LoadBalancer
directo. Hereda de `Scheduler` (Sprint 2.2) para reutilizar
`mark_step_started/finished` y la emisión de eventos.

- `schedule(workflow, agents=None, exclude=None)`: el parámetro `agents`
  se ignora (se obtienen del registry). Para cada step listo, usa
  `adapter.resolve_step_agent(step)` para encontrar el agente.
- Si el resolver no encuentra agente, **fallback** al LoadBalancer sobre
  los agentes disponibles del registry.
- `mark_step_finished()` registra usage en el `AgentRecord`
  (`record_usage(success, duration_ms)`).

## Compatibilidad

| Componente anterior | Nuevo componente | Relación |
|---|---|---|
| `AgentOrchestrator` (Sprint 2.1) | `AgentRegistry` (Sprint 2.2/2) | Coexisten; el registry es la fuente de verdad |
| `Scheduler` (Sprint 2.2) | `RegistryAwareScheduler` | Hereda; se puede usar cualquiera |
| `LoadBalancer` (Sprint 2.2) | `CapabilityResolver` | Coexisten; el resolver usa scoring más rico |
| Workflows con `step.agent = "CoderAgent"` | `BackwardCompatibilityAdapter` | Traduce automáticamente |
| Workflows con `step.action = "refactor"` | `BackwardCompatibilityAdapter` | Traduce automáticamente |

**Ningún workflow existente se rompe**: el adapter traduce nombres y
actions legacy a capabilities, y el resolver encuentra agentes capaces.

Los **248 tests de Sprint 2.1+2.2** siguen pasando sin cambios.

## Eventos nuevos

| Evento | Origen | Payload |
|---|---|---|
| `agent.registered` | AgentRegistry | agent_id, name, version, provided_capabilities, priority |
| `agent.removed` | AgentRegistry | agent_id, name, reason |
| `agent.health.changed` | AgentRegistry | agent_id, name, old_state, new_state |
| `capability.resolved` | CapabilityResolver | capability, agent, score, candidates, duration_ms |
| `capability.miss` | CapabilityResolver | capability, duration_ms |
| `scheduler.selected_agent` | CapabilityResolver | capability, agent, score, duration_ms |

## Observabilidad

### `AgentRegistry.metrics()`

```python
{
    "registered_total": 3,
    "unregistered_total": 0,
    "replaced_total": 1,
    "discoveries_total": 2,
    "heartbeat_checks": 15,
    "health_changes": 1,
    "registry_size": 3,
    "available_count": 2,
    "healthy_count": 3,
    "total_usage": 10,
    "total_successes": 9,
    "total_failures": 1,
    "avg_success_rate": 0.9,
    "capability_registry": {
        "registered": 34,
        "lookups": 15,
        "hits": 12,
        "misses": 3,
        "hit_rate": 0.8,
        "categories": 10
    }
}
```

### `CapabilityResolver.metrics()`

```python
{
    "resolutions_total": 20,
    "resolved_count": 18,
    "miss_count": 2,
    "total_duration_ms": 1.5,
    "avg_resolution_time_ms": 0.075,
    "success_rate": 0.9
}
```

## Verificación: nada depende de clases concretas

El sistema está diseñado para que **ningún componente del workflow
engine dependa de una clase concreta de agente**:

| Componente | Antes (Sprint 2.1/2.2) | Ahora (Sprint 2.2/2) |
|---|---|---|
| `Scheduler` | Usaba `step.agent` (nombre concreto) | `RegistryAwareScheduler` usa `CapabilityResolver` |
| `WorkflowEngine` | Recibía `agents: list[BaseAgent]` | Puede usar `RegistryAwareScheduler` (agents del registry) |
| `LoadBalancer` | Recibía `candidates: list[BaseAgent]` | Fallback del resolver; agentes vienen del registry |
| `AgentOrchestrator` | Mantenía `_agents: dict[name, Agent]` | Coexiste; el registry es la fuente de verdad nueva |

El `BackwardCompatibilityAdapter` asegura que workflows antiguos que
referencian `PlannerAgent`, `CoderAgent`, etc. sigan funcionando: el
adapter traduce el nombre a capabilities y el resolver encuentra
cualquier agente que las provea.

## Roadmap

- **Sprint 2.3**: Integrar `AgentRegistry` con el `AgentOrchestrator`
  de Sprint 2.1 para que el orchestrator consulte al registry en lugar
  de mantener su propia lista.
- **Sprint 2.4**: Plugin system que cargue agentes dinámicamente y los
  registre vía `add_discovery_hook`.
- **Sprint 2.5**: Persistencia del `AgentRegistry` en SQLite para
  recovery tras crash.
- **Sprint 2.6**: Hot-reload de agentes (unregister + register sin
  interrumpir workflows en curso).
