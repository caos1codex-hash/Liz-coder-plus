# Liz Communication — Agent Communication & Collaboration Layer

Sprint 2.9 — Agent Communication & Collaboration Layer for Liz Coder Plus.

## Overview

The `liz-communication` package provides a formal communication layer
between agents, built **on top of** the existing Sprint 2.1-2.8
infrastructure. It does not replace the existing `MessageBus`,
`Message`, or `WorkflowOrchestrator` APIs; it wraps them with a
higher-level facade that adds:

- A canonical **`AgentMessage`** protocol with the 6 message types
  required by the sprint spec (`TASK_REQUEST`, `TASK_RESPONSE`,
  `STATUS_UPDATE`, `ERROR_REPORT`, `CONTEXT_TRANSFER`, `HANDOFF`).
- An **`AgentMessageBus`** facade that bridges the existing
  `multiagent.messages.MessageBus` with persistence and tracing.
- A **`CollaborationManager`** that coordinates multi-agent work:
  tracks interactions, enforces timeouts, handles failures, and
  records metrics.
- A **`MessageRepository`** that persists inter-agent messages via
  the existing SQLite memory architecture (no new schema required —
  uses `ExecutionRepository.record(event_type="agent.message", ...)`).
- Integration adapters for `AgentRegistry`, `CapabilityResolver`,
  `WorkflowOrchestrator`, and the planner's `Scheduler`.

## Architecture

```
Agent A
  │
  │ AgentMessage.task_request(...)
  ▼
AgentMessageBus ──► MessageBus (existing)  ──► Agent B
  │                       │
  │                       ▼
  │                 MessageBusPersister
  │                       │
  ▼                       ▼
CollaborationManager  MessageRepository (SQLite)
  │
  ├── tracks interactions
  ├── enforces timeouts
  ├── handles failures
  └── records metrics
```

## Public API

```python
from communication import (
    # Protocol
    AgentMessage, MessageType, MessagePriority,
    # Bus
    AgentMessageBus, BusConfig,
    # Collaboration
    CollaborationManager, Collaboration, CollaborationStatus,
    # Persistence
    MessageRepository, InMemoryMessageRepository,
    # Integration
    build_integrated_bus,
    # Exceptions
    CommunicationError, MessageDeliveryError, CollaborationTimeoutError,
)
```

## Integration

The package integrates with existing modules through lazy adapters:

- **`multiagent.messages.MessageBus`** (Sprint 2.1) — wrapped, not replaced.
- **`multiagent.messages.Message`** (Sprint 2.1) — reused as the wire format.
- **`multiagent.registry.AgentRegistry`** (Sprint 2.2-2) — agent lookup.
- **`multiagent.registry.CapabilityResolver`** (Sprint 2.2-2) — agent selection.
- **`multiagent.workflow.EventBus`** (Sprint 2.2) — observability bridge.
- **`memory.repositories.ExecutionRepository`** (Sprint 1.8) — persistence.

The integration is **optional**: the package works standalone with
in-memory data structures. When adapters are provided, it uses them
transparently.

## Compatibility

- Python 3.11+ (target 3.12 per project standard).
- No breaking changes to existing modules.
- New package: `packages/communication/`.
- Tests live in `tests/communication/`.
