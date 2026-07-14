# Multi-Agent Package (Sprint 2.1)

Arquitectura profesional basada en agentes para Liz Coder Plus.

Cada agente expone:

- `id`, `name`, `description`, `status`
- `permissions`, `memory`, `tools`
- `execute()`, `stop()`, `pause()`, `resume()`, `health()`, `serialize()`

El `AgentOrchestrator` los administra: registro, dispatch, broadcast,
pausa/resume global, health check y métricas.

## Estructura

```
src/multiagent/
├── __init__.py
├── enums.py            → AgentStatus, MessagePriority, TaskStatus, Permission
├── config.py           → MultiAgentConfig
├── base.py             → BaseAgent abstracta
├── messages.py         → Mensajes entre agentes (REQUEST/RESPONSE/EVENT/ERROR/SYSTEM)
├── queue.py            → PriorityQueue de tareas
├── memory.py           → AgentMemory (corta + larga + embeddings)
├── logs.py             → AgentLogger (inicio/fin/errores/tiempo/tokens/herramientas)
├── health.py           → HealthCheck (alive/busy/idle/error)
├── orchestrator.py     → AgentOrchestrator
└── agents/
    ├── __init__.py
    ├── planner.py      → PlannerAgent
    ├── coder.py        → CoderAgent
    ├── reviewer.py     → ReviewerAgent
    ├── research.py     → ResearchAgent
    ├── terminal.py     → TerminalAgent
    ├── git.py          → GitAgent
    └── memory.py       → MemoryAgent
```

## Compatibilidad

Este paquete convive con el `Orchestrator` y `BaseAgent` heredados de
Sprint 1 (en `packages/core` y `packages/agents`). No se reemplaza
código existente; se añade una nueva arquitectura en paralelo, lista
para integrarse con el pipeline de Sprint 1.7 mediante adaptadores.
