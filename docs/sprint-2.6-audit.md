# Sprint 2.6 — Memory & Context Engine Audit

## Executive Summary

Liz Coder Plus has **two completely separate memory systems** that are not integrated:

1. **`packages/memory/`** (v0.1.2) — Persistent SQLite-based conversation memory (Sprint 1). Provides `MemoryManager`, `DatabaseManager`, `ConversationRepository`, plus task/workflow/execution repos.
2. **`packages/multiagent/src/multiagent/memory.py`** (Sprint 2.1) — In-memory per-agent `AgentMemory` with short-term buffer, long-term KV store, and embedding similarity search. Volatile, session-scoped.

The `ContextManager` (Sprint 2.2) bridges agents within a single workflow but has no connection to the persistent `MemoryManager`. No `packages/multiagent/src/multiagent/memory/` directory exists — memory is a single file.

## Architecture Gaps

| # | Gap | Severity |
|---|---|---|
| G1 | `AgentMemory` and `MemoryManager` have zero integration | Critical |
| G2 | `ContextManager._memory_refs` stores flat dict copies, not live references | High |
| G3 | `Workflow.context` dict and engine `ContextManager` never synchronized | High |
| G4 | `WorkflowEvent.MEMORY_UPDATED` defined but never emitted | Medium |
| G5 | `MemoryBackend` protocol defined but nothing implements it | Medium |
| G6 | multiagent pyproject has no dependency on liz-memory | Medium |

## Existing Foundation

| Asset | Reusability |
|---|---|
| `MemoryBackend` protocol (`packages/memory/src/base.py`) | High — formal backend contract |
| `AgentMemory` (multiagent/memory.py) | High — used by all agents |
| `ContextManager` (workflow/context.py) | High — has event emission |
| `MemoryManager` (memory/manager.py) | High — stable, async API |
| `DatabaseManager` (memory/database.py) | High — migration-ready |
| `WorkflowEvent.MEMORY_UPDATED` (workflow/enums.py) | Medium — needs emitter |
| `RegistryOrchestrator` (registry/orchestrator.py) | High — capability-based dispatch |

## Sprint 2.6 Strategy

Create a **new `packages/multiagent/src/multiagent/memory/` package** that provides:
- Memory models (decoupled from core)
- Pluggable storage API (InMemory + File as built-in backends)
- MemoryEngine (backend-agnostic CRUD + search)
- ContextEngine (context construction for agents/workflows)
- Memory policies (TTL, expiration, archival)
- Search index (tag-based, extensible to vectorial)
- Memory event bus
- Observability metrics
- Integration adapters for Workflow Engine, AgentRegistry, PluginManager

This approach **does not modify** existing memory modules and provides a clean, extensible foundation for future semantic memory and vector databases.