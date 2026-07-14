# Agent Architecture Contract — Liz Coder Plus

**Sprint 2.10 — Final Architecture Audit**  
**Status**: Canonical — all new code MUST follow this contract.  
**Last updated**: 2026-07-14

This document defines the **canonical contract** for creating, registering,
communicating with, and delegating to agents in Liz Coder Plus. It is the
single source of truth for agent architecture decisions.

---

## 1. Agent Creation Contract

### 1.1 Two BaseAgent hierarchies (conscious duplication)

Liz Coder Plus has **two** `BaseAgent` classes. This is intentional and
documented; they serve different layers:

| Layer | File | Purpose |
|-------|------|---------|
| **Core** | `packages/agents/src/base.py` | Lightweight `Agent` Protocol (`handle(message, context) -> str`). Used by `core.Orchestrator` for simple message routing. |
| **Multiagent** | `packages/multiagent/src/multiagent/base.py` | Full-featured `BaseAgent` with `execute(task) -> dict`, lifecycle states, health checks, MessageBus subscription, AgentMemory. Used by `AgentOrchestrator`, `WorkflowOrchestrator`, `AgentRegistry`. |

### 1.2 Which one should I use?

- **New agents** → subclass `multiagent.base.BaseAgent`. It is the
  production-grade base class with lifecycle, health, and communication
  support.
- **Simple echo/test agents** → implement the `agents` Protocol
  (`name: str` + `async handle(message, context) -> str`). This is
  sufficient for the core `Orchestrator`.
- **Never mix** → do not subclass one and pass it to the other's
  orchestrator. Use the `BackwardCompatibilityAdapter`
  (`multiagent.registry.adapter`) if you must bridge.

### 1.3 Creating a multiagent BaseAgent

```python
from multiagent.base import BaseAgent
from multiagent.enums import Permission

class MyAgent(BaseAgent):
    def __init__(self, name: str = "my_agent") -> None:
        super().__init__(
            name=name,
            description="Does something useful",
            permissions=frozenset({Permission.READ_FILES, Permission.WRITE_FILES}),
            objectives=["my_objective"],
            tools=["file_tool"],
        )

    async def _execute_impl(self, task: dict) -> dict:
        # Your logic here.
        return {"result": "done"}
```

### 1.4 Agent lifecycle states

```
CREATED → IDLE ⇄ BUSY → STOPPED
              ↓
            PAUSED
              ↓
            STOPPED
            ERROR → STOPPED
```

- `CREATED` → agent instantiated but not started.
- `IDLE` → ready to accept tasks.
- `BUSY` → executing a task.
- `PAUSED` → temporarily not accepting tasks (use `pause()` / `resume()`).
- `ERROR` → encountered an error (may recover to `IDLE`).
- `STOPPED` → terminal; cannot be restarted.

**Contract**: agents must not transition from `STOPPED` to any other state.
The `AgentRegistry` enforces this.

---

## 2. Agent Registration Contract

### 2.1 Which registry?

| Registry | File | Use when |
|----------|------|----------|
| `AgentRegistry` | `packages/multiagent/src/multiagent/registry/agent_registry.py` | **Default** — full lifecycle, capabilities, health, metrics. |
| `agents.registry.AgentRegistry` | `packages/agents/src/registry.py` | Only for the core `Orchestrator` (simple name→agent dict). |

### 2.2 Registering an agent

```python
from multiagent.registry import AgentRegistry, AgentManifest

registry = AgentRegistry()

# Option A: register with explicit capabilities.
record = await registry.register(
    agent=my_agent,
    provided_capabilities=["code.generate", "filesystem.read"],
    priority=10,
    metadata={"cost": 0.5},
)

# Option B: register from a manifest.
manifest = AgentManifest(
    id="coder",
    name="Coder Agent",
    version="1.0.0",
    capabilities=["code.generate", "code.refactor"],
    priority=10,
)
# Manifests can be created independently and used for discovery.
```

### 2.3 AgentManifest contract

An `AgentManifest` is the **declarative identity** of an agent:
- `id`: lowercase, `[a-z0-9_-]+`, must match the agent's `name`.
- `name`: human-readable.
- `version`: semver (`MAJOR.MINOR.PATCH`).
- `capabilities`: list of hierarchical strings (e.g. `"code.generate"`).
- `priority`: int ≥ 0 (higher = more prioritary).
- `metadata`: free-form (cost, latency hints, tags).

**Contract**: the manifest is immutable and serializable. It does NOT
reference the agent instance — it can be published/discovered without
instantiating the agent.

### 2.4 Capability matching

Capabilities use hierarchical matching:
- Exact: `"code.generate" == "code.generate"`
- Wildcard: `"code.*"` matches `"code.generate"`, `"code.review"`, etc.
- Parent: `"code"` matches `"code.generate"` (parent covers children).

The `CapabilityResolver` (Sprint 2.2-2) scores agents by:
- specialty (exact > wildcard > parent)
- availability (healthy + idle)
- priority
- cost
- success rate
- workload (usage count)
- latency

---

## 3. Agent Communication Contract

### 3.1 Two communication layers

| Layer | File | Use when |
|-------|------|----------|
| `MessageBus` | `packages/multiagent/src/multiagent/messages.py` | Low-level point-to-point messaging with `Message` objects. |
| `AgentMessageBus` | `packages/communication/src/communication/bus.py` | **Preferred** — facade with persistence, TTL, tracing, collaboration tracking. |

### 3.2 AgentMessage protocol (Sprint 2.9)

The canonical message format. Every inter-agent message MUST be an
`AgentMessage` with:

- `message_id`: unique id (auto-generated).
- `sender_agent`: agent name (or `"orchestrator"`).
- `receiver_agent`: agent name (or `"*"` for broadcast).
- `task_id`: optional, for task-scoped messages.
- `timestamp`: ISO-8601 UTC.
- `message_type`: one of `TASK_REQUEST`, `TASK_RESPONSE`, `STATUS_UPDATE`,
  `ERROR_REPORT`, `CONTEXT_TRANSFER`, `HANDOFF`.
- `payload`: JSON-compatible dict.
- `metadata`: free-form tracing metadata.
- `correlation_id`: for end-to-end tracing of a conversation.
- `priority`: `CRITICAL` / `HIGH` / `NORMAL` / `LOW`.
- `ttl`: seconds (0 = no expiration).
- `in_reply_to`: message_id of the message this replies to.

### 3.3 Sending messages

```python
from communication import AgentMessageBus, MessageType

bus = AgentMessageBus()

# Option A: build + publish.
msg = AgentMessage.task_request("planner", "coder", payload={"action": "write"})
await bus.publish(msg)

# Option B: convenience sender.
msg = await bus.send_task_request("planner", "coder", payload={"action": "write"})
```

### 3.4 Receiving messages

```python
async def coder_handler(msg: AgentMessage) -> None:
    if msg.message_type == MessageType.TASK_REQUEST:
        # Do work...
        response = msg.reply(
            MessageType.TASK_RESPONSE,
            payload={"result": "done", "done": True},
        )
        await bus.publish(response)

await bus.subscribe("coder", coder_handler)
```

### 3.5 Collaboration (high-level coordination)

```python
from communication import CollaborationManager

mgr = CollaborationManager(bus=bus, default_timeout=30.0)

# Delegate a task and wait for the response.
collab = await mgr.delegate("planner", "coder", task_id="t1", payload={...})
response = await mgr.wait_for_response(collab.initial_message.message_id, timeout=10.0)

# Broadcast a help request.
collab = await mgr.request_help("planner", topic="need help with auth")

# Transfer context.
collab = await mgr.transfer_context("planner", "coder", context={"files": [...]})

# Hand off a task (fire-and-forget).
collab = await mgr.handoff("planner", "coder", task_id="t1", reason="specialized")
```

**Contract**: the `CollaborationManager` auto-subscribes to the bus and
auto-routes responses. Callers do NOT need to call `route_message`
manually.

---

## 4. Task Delegation Contract

### 4.1 Three delegation paths (choose ONE)

| Path | When to use |
|------|-------------|
| `CollaborationManager.delegate()` | **Preferred** — sends an `AgentMessage` TASK_REQUEST, tracks the collaboration, supports `wait_for_response`. |
| `WorkflowOrchestrator.delegate()` | When you also need the task enqueued in the `PriorityQueue` (for `dispatch()` / `run_once()` execution). |
| `AgentOrchestrator.send_to()` | Low-level — sends a raw `Message` without collaboration tracking. |

### 4.2 Delegation flow (canonical)

```
Agent A (initiator)
  │
  │ mgr.delegate(A, B, task_id=t1, payload={...})
  ▼
CollaborationManager
  │
  │ bus.send_task_request(A, B, correlation_id=collab.correlation_id)
  ▼
AgentMessageBus ──► MessageBus ──► Agent B
  │                                     │
  │ (auto-route via broadcast tap)      │ msg.reply(TASK_RESPONSE)
  ▼                                     ▼
CollaborationManager ◄──────────── AgentMessageBus
  │
  │ collab.add_message(response)
  │ resolve wait_for_response future
  ▼
Agent A receives response via await mgr.wait_for_response(...)
```

### 4.3 Timeout contract

- `delegate(timeout=X)` sets the message TTL to `2X` (generous).
- `wait_for_response(timeout=Y)` blocks for up to `Y` seconds.
- If `Y` is `None`, uses `default_timeout` (default: 30s).
- On timeout, raises `CollaborationTimeoutError` and marks the
  collaboration `TIMED_OUT`.

### 4.4 Error handling contract

- If the responder sends an `ERROR_REPORT`, the collaboration is marked
  `FAILED` and `wait_for_response` raises an exception.
- If the responder doesn't respond, `wait_for_response` raises
  `CollaborationTimeoutError`.
- If the responder doesn't exist, the message is silently dropped
  (`dropped_no_subscriber` metric increments). Use
  `RegistryAdapter.agent_exists()` to validate before delegating.

---

## 5. Integration Contract

### 5.1 Building an integrated system

```python
from communication import build_integrated_manager
from multiagent.messages import MessageBus
from multiagent.registry import AgentRegistry

# 1. Create the agent registry.
registry = AgentRegistry()
# ... register agents ...

# 2. Build the integrated communication + collaboration stack.
bus, mgr = build_integrated_manager(
    message_bus=MessageBus(),       # wraps the existing MessageBus
    default_timeout=30.0,
)

# 3. Agents subscribe to the bus.
async def coder_handler(msg):
    ...
await bus.subscribe("coder", coder_handler)

# 4. Delegate and coordinate.
collab = await mgr.delegate("planner", "coder", payload={...})
```

### 5.2 Planner integration

```python
from planner import build_integrated_planner

planner = build_integrated_planner(registry=registry)
plan = planner.create_plan("Create an AI code editor")
# plan.tasks, plan.graph, plan.execution_order
```

The planner's `MultiAgentRegistryAdapter` (in
`planner/integration.py`) bridges the `AgentRegistry` to the planner's
`CapabilityMatcher`. The planner does NOT import `multiagent` at module
load time — the integration is lazy.

### 5.3 Workflow integration

The planner's `WorkflowEngineAdapter.to_workflow(plan)` converts a
planner `ExecutionPlan` into a multiagent `Workflow` for execution by
the existing `WorkflowEngine`. **Step IDs are set to task IDs** so that
dependencies resolve correctly in the workflow DAG.

---

## 6. What NOT to do

1. **Do not** create a third `BaseAgent` class. Use `multiagent.base.BaseAgent`.
2. **Do not** create a third `MessageBus`. Use `AgentMessageBus` (which
   wraps the existing `MessageBus`).
3. **Do not** create a third `Task` class. Use `planner.task.Task` for
   planned tasks, `multiagent.queue.Task` for queued tasks, or
   `core.task.Task` for core-orchestrator tasks. Document which one you
   mean.
4. **Do not** import `multiagent` at module load time from `planner`,
   `communication`, or `core`. Use lazy imports (`TYPE_CHECKING` or
   function-local imports).
5. **Do not** call `WorkflowOrchestrator.delegate()` AND
   `CollaborationManager.delegate()` for the same delegation — pick one.
   The `OrchestratorAdapter` in `communication/integration.py` handles
   this by preferring the manager when configured.
6. **Do not** transition an agent from `STOPPED` to any other state.
7. **Do not** publish messages without a `correlation_id` if they are
   part of a multi-message conversation.

---

## 7. Versioning

This contract is versioned with the project. Changes that break the
contract require a major version bump. The current contract version is
**2.10.0** (Sprint 2.10).
