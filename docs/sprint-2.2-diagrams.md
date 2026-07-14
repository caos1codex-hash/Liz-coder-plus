# Sprint 2.2 — Diagramas

## 1. Arquitectura general

```mermaid
flowchart TB
    USER[Usuario / API REST]

    subgraph S22["Sprint 2.2 — Collaborative"]
        WFO[WorkflowOrchestrator]
        ENGINE[WorkflowEngine]
        SCHED[Scheduler]
        LB[LoadBalancer]
        WBUS[EventBus]
        CTX[ContextManager]
        METRICS[MetricsCollector]
        API[FastAPI Router]

        subgraph Models["Modelos"]
            WF[Workflow]
            STEP[Step]
            DAG[DAG]
        end
    end

    subgraph S21["Sprint 2.1"]
        AO[AgentOrchestrator]
        MBUS[MessageBus]
        AGENTS[7 Agents]
    end

    USER --> API
    API --> WFO
    WFO --> ENGINE
    WFO --> AO
    ENGINE --> SCHED
    ENGINE --> CTX
    ENGINE --> WBUS
    SCHED --> LB
    SCHED --> AGENTS
    METRICS -.subscribes.-> WBUS
    AO --> AGENTS
    AGENTS <--> MBUS

    classDef new fill:#cfe,stroke:#383,stroke-width:2px
    classDef old fill:#eee,stroke:#888
    class S22,Models new
    class S21 old
```

## 2. Lifecycle de un Workflow

```mermaid
stateDiagram-v2
    [*] --> PENDING: submit()
    PENDING --> RUNNING: schedule()
    RUNNING --> PAUSED: pause()
    PAUSED --> RUNNING: resume()
    RUNNING --> COMPLETED: all steps done
    RUNNING --> FAILED: required step failed
    RUNNING --> CANCELLED: cancel()
    COMPLETED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
```

## 3. Lifecycle de un Step

```mermaid
stateDiagram-v2
    [*] --> PENDING: add_step()
    PENDING --> READY: scheduler picks
    PENDING --> SKIPPED: dep failed
    PENDING --> CANCELLED: workflow cancelled
    READY --> RUNNING: mark_step_started()
    READY --> SKIPPED: dep skipped
    READY --> CANCELLED: cancel
    RUNNING --> COMPLETED: success
    RUNNING --> FAILED: error
    RUNNING --> CANCELLED: cancel
    FAILED --> READY: retry / reassign
    FAILED --> SKIPPED: optional, no more retries
    FAILED --> CANCELLED: cancel
    COMPLETED --> [*]
    SKIPPED --> [*]
    CANCELLED --> [*]
```

## 4. Flujo de ejecución paralela

```mermaid
sequenceDiagram
    participant E as WorkflowEngine
    participant S as Scheduler
    participant LB as LoadBalancer
    participant A1 as Agent 1
    participant A2 as Agent 2
    participant B as EventBus

    E->>S: schedule(workflow, agents)
    S->>LB: select(agents, action)
    LB-->>S: agent=a1
    S->>S: mark_step_started(s1, a1)
    S->>B: emit step.started, agent.busy
    E->>A1: execute(s1) [async task]

    S->>LB: select(agents, action)
    LB-->>S: agent=a2
    S->>S: mark_step_started(s2, a2)
    S->>B: emit step.started, agent.busy
    E->>A2: execute(s2) [async task]

    A1-->>E: result (s1 done)
    E->>S: mark_step_finished(s1, a1, success)
    S->>B: emit step.finished, agent.idle
    E->>E: check ready_steps (deps of s1 now ready)

    A2-->>E: result (s2 done)
    E->>S: mark_step_finished(s2, a2, success)
    S->>B: emit step.finished, agent.idle

    E->>E: workflow complete?
    E->>B: emit workflow.completed
```

## 5. Failover decision tree

```mermaid
flowchart TD
    FAIL[Step failed]
    Q1{retry < max_retries?}
    Q1 -->|Yes| RETRY[RETRY: step → READY]
    Q1 -->|No| Q2{reassign_on_fail AND<br/>reassigns < max?}
    Q2 -->|Yes| REASSIGN[REASSIGN:<br/>exclude agent,<br/>step → READY]
    Q2 -->|No| Q3{criticality == OPTIONAL<br/>AND skip_optional?}
    Q3 -->|Yes| SKIP[SKIP: step → SKIPPED<br/>propagate to dependents]
    Q3 -->|No| Q4{criticality == REQUIRED<br/>AND cancel_on_required?}
    Q4 -->|Yes| CANCEL[CANCEL: workflow → FAILED]
    Q4 -->|No| NONE[NONE: leave FAILED,<br/>propagate SKIP to deps]

    RETRY --> RESET[Reset agent to IDLE]
    REASSIGN --> RESET
```

## 6. DAG validation

```mermaid
flowchart LR
    subgraph Valid["✅ Valid DAG (diamond)"]
        A1[a] --> B1[b]
        A1 --> C1[c]
        B1 --> D1[d]
        C1 --> D1
    end

    subgraph Cycle["❌ Cycle detected"]
        A2[a] --> B2[b]
        B2 --> C2[c]
        C2 --> A2
    end

    subgraph Broken["❌ Broken dependency"]
        A3[a] --> MISS[nonexistent]
    end

    subgraph Orphan["⚠️ Orphan steps"]
        O1[orphan1]
        O2[orphan2]
    end
```

## 7. LoadBalancer scoring

```mermaid
flowchart LR
    AGENT[Agent candidate]
    AGENT --> S1[state_score<br/>IDLE=1.0]
    AGENT --> S2[cpu_score<br/>1 - cpu%]
    AGENT --> S3[ram_score<br/>1 - ram_norm]
    AGENT --> S4[queue_score<br/>1 - queue_norm]
    AGENT --> S5[specialty_score<br/>match action vs objectives]
    AGENT --> S6[history_score<br/>success_rate]

    S1 --> SUM[weighted sum]
    S2 --> SUM
    S3 --> SUM
    S4 --> SUM
    S5 --> SUM
    S6 --> SUM

    SUM --> DECISION{highest score<br/>wins}
```

## 8. EventBus vs MessageBus

```mermaid
flowchart TB
    subgraph MB["MessageBus (Sprint 2.1)"]
        direction LR
        MS1[Agent A]
        MS2[Agent B]
        MS1 -->|REQUEST to B| MBQ[Bus]
        MBQ -->|deliver to B| MS2
        MS2 -->|RESPONSE to A| MBQ
        MBQ -->|deliver to A| MS1
    end

    subgraph EB["EventBus (Sprint 2.2)"]
        direction LR
        SRC[Source<br/>engine/scheduler/agent]
        SRC -->|publish event| EBQ[Bus]
        EBQ -->|broadcast| SUB1[Subscriber 1<br/>MetricsCollector]
        EBQ -->|broadcast| SUB2[Subscriber 2<br/>Logger]
        EBQ -->|broadcast| SUB3[Subscriber 3<br/>API /events]
        EBQ -->|broadcast| SUB4[Subscriber N<br/>WebSocket feed]
    end

    classDef mb fill:#fec,stroke:#a83
    classDef eb fill:#cef,stroke:#369
    class MB mb
    class EB eb
```

## 9. Context Manager

```mermaid
flowchart TB
    subgraph CTX["ContextManager (per workflow)"]
        OBJ[Objectives<br/>list]
        FILES[Files<br/>path → FileSnapshot<br/>with SHA-256]
        SNIP[Snippets<br/>id → CodeSnippet<br/>with tags, shareable]
        VARS[Variables<br/>key → value]
        MSGS[Messages<br/>sender → receiver → content]
        MEM[Memory refs<br/>agent → key → value]
    end

    A1[Agent 1] -->|register_file| FILES
    A1 -->|add_snippet| SNIP
    A1 -->|set_variable| VARS
    A1 -->|share_memory| MEM

    A2[Agent 2] -.->|transfer_context| CTX
    CTX -.->|snapshot| A2

    TRANSFER[transfer_context<br/>from → to] --> CTX
```

## 10. API REST endpoints

```mermaid
flowchart LR
    CLIENT[Client]

    subgraph API["FastAPI"]
        GW[/workflows<br/>GET, POST/]
        GWID[/workflows/{id}<br/>GET/]
        GWST[/workflows/{id}/steps<br/>GET/]
        GWCTL[/workflows/{id}/cancel<br/>pause, resume<br/>POST/]
        GWEV[/workflows/{id}/events<br/>GET/]
        GWM[/workflows/{id}/metrics<br/>GET/]
        ST[/steps<br/>GET/]
        EV[/events<br/>GET/]
        MW[/metrics/workflows<br/>GET/]
        MA[/metrics/agents<br/>GET/]
    end

    WFO[WorkflowOrchestrator]

    CLIENT --> GW
    CLIENT --> GWID
    CLIENT --> GWST
    CLIENT --> GWCTL
    CLIENT --> GWEV
    CLIENT --> GWM
    CLIENT --> ST
    CLIENT --> EV
    CLIENT --> MW
    CLIENT --> MA

    GW --> WFO
    GWID --> WFO
    GWST --> WFO
    GWCTL --> WFO
    GWEV --> WFO
    GWM --> WFO
    ST --> WFO
    EV --> WFO
    MW --> WFO
    MA --> WFO
```
