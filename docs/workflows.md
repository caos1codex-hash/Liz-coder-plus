# Workflow Engine — Guía de Arquitectura (Sprint 2.4)

## Visión General

Sprint 2.4 añade un **motor de workflows basado en capacidades** (`TaskPipelineEngine`) que se integra con el `AgentRegistry` y `CapabilityResolver` existentes. A diferencia del `WorkflowEngine` de Sprint 2.2 (que recibe instancias directas de `BaseAgent`), el nuevo motor resuelve agentes dinámicamente según las capacidades requeridas por cada step.

## Componentes Nuevos

### Pipeline Models (`pipeline_models.py`)

| Modelo | Propósito |
|---|---|
| `TaskRequest` | Solicitud estandarizada enviada al agente. Contiene: `task_id`, `description`, `capability`, `context`, `metadata`, `payload`. |
| `TaskResult` | Resultado de la ejecución. Contiene: `success`, `output`, `error`, `execution_time`, `agent_id`, `agent_name`, `metrics`. |
| `WorkflowStepDef` | Definición de un step. Usa `capability_required` en lugar de `agent_name` para resolución dinámica. |
| `StepRetryPolicy` | Política de reintento con `max_attempts`, `retry_delay`, `retry_on_errors`. |
| `PipelineWorkflow` | Contenedor de un workflow con `name`, `steps`, `description`, `metadata`. |

### TaskPipelineEngine (`task_pipeline_engine.py`)

Motor de ejecución que:

1. **No importa agentes concretos** — usa `AgentRegistry` + `CapabilityResolver`.
2. **Resuelve por capacidad** — cada step declara `capability_required`.
3. **Ejecuta respetando el DAG** — usando la infraestructura `DAG` de Sprint 2.2.
4. **Aplica retry policies** — con soporte para errores específicos.
5. **Propaga contexto** — outputs de steps anteriores se pasan a dependientes.
6. **Propaga fallos** — si un step falla, sus dependientes se marcan como SKIPPED.

## API del TaskPipelineEngine

### Creación y registro

```python
from packages.multiagent.src.multiagent.workflow import (
    TaskPipelineEngine, WorkflowStepDef, StepRetryPolicy
)

engine = TaskPipelineEngine(registry=registry, resolver=resolver)

# Crear workflow
wf = engine.create_workflow("dev-pipeline", [
    WorkflowStepDef(name="plan", capability_required="planning"),
    WorkflowStepDef(
        name="code",
        capability_required="code.generate",
        dependencies=["plan"],
        retry_policy=StepRetryPolicy(max_attempts=3, retry_delay=1.0),
    ),
    WorkflowStepDef(name="test", capability_required="code.test", dependencies=["code"]),
])

# Registrar (valida DAG)
engine.register_workflow(wf)
```

### Ejecución

```python
result = await engine.execute_workflow(wf.id)
print(result.status)        # "completed"
print(result.steps_completed)  # 3
print(result.agents_used)   # ["planner", "coder", "tester"]
```

### Control

```python
await engine.pause_workflow(wf.id)
await engine.resume_workflow(wf.id)
await engine.cancel_workflow(wf.id)
```

### Consultas

```python
status = engine.get_status(wf.id)
metrics = engine.workflow_metrics(wf.id)
engine_metrics = engine.metrics()
```

## Flujo de Ejecución

```
WorkflowStep.capability_required
    ↓
CapabilityResolver.resolve(capability)
    ↓
AgentRegistry → AgentRecord → BaseAgent
    ↓
BaseAgent.execute(TaskRequest.to_agent_task())
    ↓
TaskResult producido
    ↓
Contexto actualizado para steps dependientes
```

## Retry System

El `StepRetryPolicy` permite configurar reintentos a nivel de step:

```python
# Reintentar en cualquier error
StepRetryPolicy(max_attempts=3, retry_delay=1.0)

# Reintentar solo en errores específicos
StepRetryPolicy(
    max_attempts=5,
    retry_delay=2.0,
    retry_on_errors=["timeout", "connection"],
)
```

## Métricas y Observabilidad

El engine expone métricas agregadas:

- `workflows_created`, `workflows_completed`, `workflows_failed`
- `steps_executed`, `steps_retried`
- `total_execution_time_ms`, `total_resolution_time_ms`
- `registered_workflows`, `running_workflows`

Por workflow:

- `steps_total`, `steps_completed`, `steps_failed`
- `total_execution_time_ms`, `agents_used`
- `step_results` con detalle por step

## Compatibilidad

- **No se modifica ningún archivo existente** (solo `__init__.py` para exportar).
- El `WorkflowEngine` de Sprint 2.2 sigue funcionando sin cambios.
- Todos los 791 tests existentes siguen pasando.