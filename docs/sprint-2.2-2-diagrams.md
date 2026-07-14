# Sprint 2.2/2 — Diagramas

## 1. Arquitectura general

```mermaid
flowchart TB
    USER[Usuario / API REST]

    subgraph S22_2["Sprint 2.2/2 — Registry + Capabilities"]
        REG[AgentRegistry<br/>registro único]
        RESOLV[CapabilityResolver<br/>selección inteligente]
        ADAPT[BackwardCompatibilityAdapter<br/>traduce legacy → capability]
        RSCHED[RegistryAwareScheduler]
        CAPREG[CapabilityRegistry<br/>34 capabilities]
    end

    subgraph S22["Sprint 2.2 — Workflow (coexiste)"]
        ENGINE[WorkflowEngine]
        SCHED[Scheduler classic]
        LB[LoadBalancer]
        WBUS[EventBus]
        CTX[ContextManager]
        METRICS[MetricsCollector]
    end

    subgraph S21["Sprint 2.1 — Agents (coexiste)"]
        AO[AgentOrchestrator]
        MBUS[MessageBus]
        AGENTS[7 Concrete Agents]
    end

    USER --> RSCHED
    RSCHED --> ADAPT
    ADAPT --> RESOLV
    RESOLV --> REG
    REG --> CAPREG
    REG -.registers.-> AGENTS

    ENGINE -.uses.-> RSCHED
    ENGINE -.uses.-> SCHED
    RSCHED -.inherits.-> SCHED

    classDef new fill:#cfe,stroke:#383,stroke-width:2px
    classDef old fill:#eee,stroke:#888
    class S22_2 new
    class S22,S21 old
```

## 2. Flujo: Workflow → Scheduler → Resolver → Registry → Agent

```mermaid
sequenceDiagram
    participant WF as Workflow
    participant SCHED as RegistryAwareScheduler
    participant ADAPT as Adapter
    participant RESOLV as CapabilityResolver
    participant REG as AgentRegistry
    participant CAP as CapabilityRegistry
    participant AGENT as Agent (e.g. CoderAgent)

    WF->>SCHED: schedule(workflow)
    SCHED->>ADAPT: resolve_step_agent(step)
    Note over ADAPT: step.agent="CoderAgent"<br/>step.action="refactor"
    ADAPT->>ADAPT: agent_name_to_capabilities("CoderAgent")
    Note over ADAPT: → ["code.generate", "code.refactor", ...]
    ADAPT->>ADAPT: action_to_capability("refactor")
    Note over ADAPT: → "code.refactor"
    ADAPT->>RESOLV: resolve("code.refactor")
    RESOLV->>REG: find_by_capability("code.refactor")
    REG->>REG: filter available + provides
    REG-->>RESOLV: [AgentRecord(coder)]
    RESOLV->>RESOLV: score(record, "code.refactor")
    Note over RESOLV: specialty=1.0, priority=0.5,<br/>cost=0.9, history=1.0,<br/>load=1.0, latency=1.0
    RESOLV-->>ADAPT: ResolutionResult(agent=coder, score=0.85)
    ADAPT-->>SCHED: result
    SCHED-->>WF: ScheduleDecision(agent_name="coder")
    SCHED->>AGENT: execute(step)
    AGENT-->>SCHED: result
    SCHED->>REG: record.record_usage(success, duration_ms)
```

## 3. Capability hierarchy

```mermaid
flowchart TB
    ROOT[Capability<br/>hierarchy]

    subgraph CODE["code.*"]
        CODE_ROOT[code]
        CODE_GEN[code.generate]
        CODE_REFAC[code.refactor]
        CODE_FIX[code.fix]
        CODE_TEST[code.test]
        CODE_REV[code.review]
        CODE_ROOT --> CODE_GEN
        CODE_ROOT --> CODE_REFAC
        CODE_ROOT --> CODE_FIX
        CODE_ROOT --> CODE_TEST
        CODE_ROOT --> CODE_REV
    end

    subgraph GIT["git.*"]
        GIT_ROOT[git]
        GIT_COMMIT[git.commit]
        GIT_BRANCH[git.branch]
        GIT_MERGE[git.merge]
        GIT_PUSH[git.push]
        GIT_PULL[git.pull]
        GIT_STATUS[git.status]
        GIT_ROOT --> GIT_COMMIT
        GIT_ROOT --> GIT_BRANCH
        GIT_ROOT --> GIT_MERGE
        GIT_ROOT --> GIT_PUSH
        GIT_ROOT --> GIT_PULL
        GIT_ROOT --> GIT_STATUS
    end

    subgraph MEM["memory.*"]
        MEM_ROOT[memory]
        MEM_RET[memory.retrieve]
        MEM_STO[memory.store]
        MEM_SEA[memory.search]
        MEM_ROOT --> MEM_RET
        MEM_ROOT --> MEM_STO
        MEM_ROOT --> MEM_SEA
    end

    ROOT --> CODE
    ROOT --> GIT
    ROOT --> MEM
    ROOT --> PLANNING[planning]
    ROOT --> TERMINAL[terminal.execute]
    ROOT --> FS_READ[filesystem.read]
    ROOT --> WEB[web.search]
```

## 4. AgentRecord lifecycle

```mermaid
stateDiagram-v2
    [*] --> CREATED: register()
    CREATED --> AVAILABLE: start() + healthy
    AVAILABLE --> BUSY: resolver selects
    BUSY --> AVAILABLE: task done (success)
    BUSY --> ERROR: task failed
    ERROR --> AVAILABLE: retry / next task
    AVAILABLE --> UNHEALTHY: no heartbeat (60s)
    UNHEALTHY --> AVAILABLE: heartbeat received
    AVAILABLE --> REMOVED: unregister()
    ERROR --> REMOVED: unregister()
    UNHEALTHY --> REMOVED: unregister()
    REMOVED --> [*]
```

## 5. Scoring algorithm

```mermaid
flowchart LR
    CAND[Candidate AgentRecord]

    CAND --> S1[specialty_score<br/>exact=1.0<br/>wildcard=0.8<br/>hierarchical=0.6]
    CAND --> S2[priority_norm<br/>priority / max_priority]
    CAND --> S3[cost_score<br/>1 - cost/max_cost]
    CAND --> S4[history_score<br/>success_rate]
    CAND --> S5[load_score<br/>1 - usage/max_usage]
    CAND --> S6[latency_score<br/>1 - latency/max_latency]

    S1 --> SUM[weighted sum<br/>w_specialty=0.30<br/>w_history=0.20<br/>w_priority=0.15<br/>w_load=0.15<br/>w_cost=0.10<br/>w_latency=0.10]
    S2 --> SUM
    S3 --> SUM
    S4 --> SUM
    S5 --> SUM
    S6 --> SUM

    SUM --> COMPARE{compare scores}
    COMPARE -->|highest| SELECT[selected]
```

## 6. Auto-discovery flow

```mermaid
flowchart TB
    PLUGIN[Plugin loads]

    subgraph HOOKS["Discovery Hooks"]
        H1[hook 1: scan directory]
        H2[hook 2: import module]
        H3[hook 3: query service]
    end

    PLUGIN --> HOOKS
    HOOKS -->|list of BaseAgent| INFER[_infer_capabilities]
    INFER -->|from objectives| CAP1[code.generate, planning, ...]
    INFER -->|from tools| CAP2[filesystem.read, terminal.execute, ...]
    CAP1 --> REG[AgentRegistry.register]
    CAP2 --> REG
    REG -->|emit| EVENT[agent.registered event]
```

## 7. Backward compatibility

```mermaid
flowchart LR
    subgraph LEGACY["Legacy Workflow (Sprint 2.1/2.2)"]
        STEP1[step.agent = "PlannerAgent"]
        STEP2[step.agent = "CoderAgent"]
        STEP3[step.action = "refactor"]
    end

    ADAPT[BackwardCompatibilityAdapter]

    subgraph NEW["New System (Sprint 2.2/2)"]
        CAP1[planning, workflow.create]
        CAP2[code.generate, code.refactor, ...]
        CAP3[code.refactor]
        RESOLV[CapabilityResolver]
        REG[AgentRegistry]
        AGENT[any agent that<br/>provides the capability]
    end

    STEP1 --> ADAPT
    STEP2 --> ADAPT
    STEP3 --> ADAPT
    ADAPT --> CAP1
    ADAPT --> CAP2
    ADAPT --> CAP3
    CAP1 --> RESOLV
    CAP2 --> RESOLV
    CAP3 --> RESOLV
    RESOLV --> REG
    REG --> AGENT
```

## 8. Health monitoring

```mermaid
flowchart TB
    LOOP[Heartbeat Loop<br/>interval=10s]

    subgraph CHECK["For each AgentRecord"]
        SYNC[update_status<br/>snapshot from agent]
        HB_CHECK{last_heartbeat<br/>< 60s ago?}
        SYNC --> HB_CHECK
        HB_CHECK -->|Yes| ALIVE[health_state = ALIVE/IDLE]
        HB_CHECK -->|No| DEAD[health_state = DEAD]
    end

    ALIVE --> EMIT_OK[no event]
    DEAD --> EMIT_CHANGE[emit agent.health.changed]

    LOOP --> CHECK
```
