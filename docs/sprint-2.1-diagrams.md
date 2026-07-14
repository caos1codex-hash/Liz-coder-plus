# Sprint 2.1 Multi-Agent — Diagramas

## 1. Arquitectura general

```mermaid
flowchart TB
    USER[Usuario / API]

    subgraph S21["Sprint 2.1 — packages/multiagent"]
        ORCH[AgentOrchestrator]
        BUS[MessageBus]
        Q[PriorityQueue]
        LOG[AgentLogger]
        CFG[MultiAgentConfig]

        subgraph Agents["Agentes (heredan de BaseAgent)"]
            PLAN[PlannerAgent]
            COD[CoderAgent]
            REV[ReviewerAgent]
            RES[ResearchAgent]
            TER[TerminalAgent]
            GIT[GitAgent]
            MEM[MemoryAgent]
        end
    end

    subgraph S1["Sprint 1 — coexiste"]
        S1ORCH[Orchestrator]
        S1AG[BaseAgent v1]
        S1TASK[TaskManager]
        S1BUS[EventBus]
    end

    USER --> ORCH
    ORCH -->|dispatch| Agents
    ORCH -->|enqueue/dequeue| Q
    ORCH -->|publish/subscribe| BUS
    ORCH -->|log| LOG
    ORCH -.reads.-> CFG
    BUS <--> Agents
    Agents -->|owns| AMEM[AgentMemory]

    S1ORCH -.coexists.-> S21

    classDef new fill:#cfe,stroke:#383,stroke-width:2px
    classDef old fill:#eee,stroke:#888,stroke-dasharray:5
    class S21,Agents new
    class S1 old
```

## 2. Lifecycle de un agente

```mermaid
stateDiagram-v2
    [*] --> CREATED: __init__()
    CREATED --> IDLE: start()
    CREATED --> STOPPED: stop()
    CREATED --> ERROR: fallo crítico
    IDLE --> BUSY: execute(task)
    BUSY --> IDLE: task ok
    BUSY --> ERROR: task fail
    IDLE --> PAUSED: pause()
    PAUSED --> IDLE: resume()
    IDLE --> STOPPED: stop()
    BUSY --> STOPPED: stop()
    PAUSED --> STOPPED: stop()
    ERROR --> BUSY: execute() (auto-recuperación)
    ERROR --> STOPPED: stop()
    STOPPED --> [*]
```

## 3. Flujo de dispatch

```mermaid
sequenceDiagram
    participant U as Usuario
    participant O as AgentOrchestrator
    participant Q as PriorityQueue
    participant A as Agent (ej. Coder)
    participant L as AgentLogger

    U->>O: enqueue("refactor", payload={...}, agent_name="coder")
    O->>Q: enqueue(task)
    Q-->>O: task_id

    U->>O: dispatch()
    O->>Q: dequeue_nowait()
    Q-->>O: task (status=RUNNING)
    O->>A: _select_agent(task) → CoderAgent
    O->>A: execute(task_dict)
    A->>L: log_start
    A->>A: _execute_impl(task)
    A->>L: log_end (duration_ms, tokens, tools_used)
    A-->>O: result dict
    alt result.status == "ok"
        O->>Q: complete(task_id, result)
    else result.status == "error"
        O->>Q: fail(task_id, error)
        opt can_retry
            O->>Q: retry(task_id)
        end
    end
    O-->>U: result
```

## 4. Mensajería entre agentes

```mermaid
flowchart LR
    subgraph Senders
        PLAN[Planner]
        ORCH[Orchestrator]
    end

    BUS[MessageBus<br/>pub/sub + broadcast + pending]

    subgraph Receivers
        COD[Coder]
        REV[Reviewer]
        MEM[Memory]
    end

    PLAN -->|publish| BUS
    ORCH -->|broadcast| BUS
    BUS -->|dispatch| COD
    BUS -->|dispatch| REV
    BUS -->|dispatch| MEM

    COD -.response.-> BUS
    BUS -.response.-> PLAN
```

## 5. Cola prioritaria de tareas

```mermaid
stateDiagram-v2
    [*] --> QUEUED: enqueue()
    QUEUED --> RUNNING: dequeue()
    QUEUED --> PAUSED: pause() (preemptive)
    QUEUED --> CANCELLED: cancel()
    QUEUED --> FAILED: error
    RUNNING --> COMPLETED: success
    RUNNING --> FAILED: error
    RUNNING --> PAUSED: pause()
    RUNNING --> CANCELLED: cancel()
    PAUSED --> RUNNING: resume()
    PAUSED --> CANCELLED: cancel()
    FAILED --> QUEUED: retry() si attempts < max
    COMPLETED --> [*]
    CANCELLED --> [*]
```

## 6. Health check

```mermaid
flowchart TD
    H[health\(\)] --> S{agent.status}
    S -->|STOPPED| DEAD[DEAD]
    S -->|ERROR| ERR[ERROR]
    S -->|BUSY| BUSY[BUSY]
    S -->|PAUSED| IDLE[IDLE]
    S -->|IDLE| ALIVE[ALIVE]
    S -->|CREATED| ALIVE
    H --> R[HealthReport]
    R --> RPT[agent, state, status,<br/>uptime, tasks_ok/failed,<br/>last_error, memory_kb, metadata]
```
