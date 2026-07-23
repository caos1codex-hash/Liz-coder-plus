# Sprint 1.5 — Informe de Auditoría del Agent System

## Estado Actual

- **Rama:** main
- **Tests unitarios:** 88 pasando
- **Tests integración:** fallan por import path (conocido desde Sprint 1.4)
- **Enfoque:** Paquetes `agents/`, `core/` (agent_router, orchestrator, protocols), `tools/`

## 1. Hallazgos por Severidad

| Severidad | Cantidad |
|-----------|----------|
| Críticos  | 4        |
| Altos     | 5        |
| Medios    | 8        |
| Bajos     | 6        |
| **Total** | **23**   |

## 2. Problemas Críticos

### C1. `packages/agents/` está desconectado del sistema activo
- **Detalle:** `BaseAgent` (ABC) y `AgentRegistry` en `packages/agents/` nunca son importados por el Orchestrator ni el AgentRouter. El contrato activo es el `Agent` Protocol en `packages/core/src/protocols.py`.
- **Riesgo:** Sprint 2 creará agentes en `packages/agents/` que no cumplirán el Protocol del core, o viceversa. Doble estándar de agentes.
- **Impacto:** Arquitectura dividida, confusión para desarrolladores.

### C2. Sin ciclo de vida de agentes
- **Detalle:** Los agentes no tienen estados (IDLE, RUNNING, FAILED, etc.). No hay inicialización, cleanup, ni health checks. `handle()` se llama directamente sin lifecycle.
- **Riesgo:** Un agente en estado corrupto puede procesar mensajes indefinidamente. No hay forma de cancelar tareas ni destruir agentes de forma segura.
- **Impacto:** Sin fundamentos para agentes autónomos futuros.

### C3. Sin sistema de permisos conectado al flujo de agentes
- **Detalle:** El `PermissionService` existe en `apps/backend/src/permissions/` y el `Orchestrator` tiene `self._tools` dict, pero no hay conexión entre ellos. Los agentes no pueden solicitar permisos, ni ejecutar herramientas con supervisión.
- **Riesgo:** Cuando Sprint 2 agregue herramientas reales, no habrá validación de permisos en el flujo activo.
- **Impacto:** Ejecución de código sin supervisión en producción.

### C4. EventBus nunca se instancia ni se usa
- **Detalle:** `EventBus` existe en `apps/backend/src/events/bus.py`. Las constantes de eventos existen en `packages/core/src/events.py`. El `SessionManager` acepta `event_bus` pero nunca se pasa. Los eventos AGENT_INVOKED, AGENT_COMPLETED, AGENT_FAILED nunca se emiten.
- **Riesgo:** Sin observabilidad del sistema de agentes. No se puede auditar, loggear ni reaccionar a eventos del pipeline.
- **Impacto:** Sistema opaco, sin capacidad de monitoreo.

## 3. Problemas Altos

### A1. Duplicación: AgentRegistry vs AgentRouter
- `AgentRegistry` (packages/agents/) y `AgentRouter._agents` dict (packages/core/) hacen lo mismo.
- El Orchestrator mantiene su propio `self._agents` dict además del router.
- Tres lugares diferentes almacenan referencias a agentes.

### A2. Sin validación de entrada en agent registration
- `AgentRouter.register_agent()` no verifica que el agente implemente el Protocol `Agent`.
- `Orchestrator.register_agent()` tampoco valida.
- Un objeto sin método `handle()` causará AttributeError en runtime.

### A3. EchoAgent no usa el contexto
- `EchoAgent.handle()` ignora el historial, idioma, y modo de permisos.
- No es un buen ejemplo para futuros desarrolladores de agentes.

### A4. Herramientas registradas pero nunca ejecutadas
- `Orchestrator._tools` existe pero no hay método `execute_tool()`.
- `ToolRegistry` (packages/tools/) existe pero no se conecta al Orchestrator.
- No hay flujo: agente solicita herramienta → permiso → ejecución → resultado.

### A5. Sin manejo de timeout en ejecución de agentes
- `agent.handle()` se llama con `await` sin `asyncio.wait_for()`.
- Un agente que se cuelga bloqueará el Orchestrator indefinidamente.
- No hay timeout configurable por agente.

## 4. Problemas Medios

### M1. `BaseAgent` es un ABC pero el sistema usa Protocol
- Diseño mixto: ABC con herencia vs Protocol estructural.
- Sprint 2 debe elegir uno y eliminar el otro.

### M2. Sin logging estructurado en routing
- El AgentRouter loggea pero no incluye metadata útil como session_id, message_hash, o timing.

### M3. `_build_context()` es síncrono pero accede a estado mutable
- Podría dar contexto inconsistente si se llama durante una operación async concurrente.

### M4. `message_id` usa `len(history)` como índice
- Frágil: si se eliminan turnos por trimming, el message_id no será secuencial.

### M5. Sin métricas de rendimiento
- No hay timing en `handle_message()`, `route()`, ni `agent.handle()`.

### M6. `stream_message` no es streaming real
- Genera toda la respuesta primero, luego la trocea. Para LLMs reales (Sprint 5) esto no sirve.

### M7. Duplicación de PermissionMode
- Definido en `packages/shared/src/enums.py` y `apps/backend/src/permissions/modes.py`.
- El Orchestrator usa strings ("confirmation", "automatic") en vez del enum.

### M8. Tests de integración rotos por import path
- `test_websocket_chat.py` falla al importar por el shim `liz_core.py`.

## 5. Problemas Bajos

### B1. `packages/agents/README.md` describe funcionalidad no implementada
### B2. `packages/tools/README.md` describe funcionalidad no implementada
### B3. Falta `__all__` en módulos de agents y tools
### B4. Sin type hints en algunos métodos del SessionManager (event_bus: Any)
### B5. Tests de agent_router son básicos — no cubren agentes concurrentes ni边境 casos
### B6. Documentación de arquitectura menciona EventBus como activo cuando no lo está

## 6. Deuda Técnica

| Ítem | Descripción | Acción Recomendada |
|------|-------------|-------------------|
| Dual registry | AgentRegistry + AgentRouter + Orchestrator._agents | Unificar en Sprint 2 |
| Dual contract | BaseAgent ABC + Agent Protocol | Elegir Protocol, ABC como helper |
| Sin lifecycle | Sin estados, sin cleanup, sin cancelación | Implementar máquina de estados |
| Sin permisos conectados | PermissionService desconectado del flujo | Conectar en este sprint |
| EventBus muerto | Existe pero nunca se usa | Conectar o eliminar |
| Herramientas sin flujo | Registradas pero no ejecutables | Preparar pipeline tool |

## 7. Plan de Acción por Fase

| Fase | Objetivo | Entregable |
|------|----------|------------|
| 2 | Estabilizar core de agentes | BaseAgent mejorado, validación de registro, logging |
| 3 | Integrar Agent System + Orchestrator | Conectar permisos, eventos, herramienta pipeline |
| 4 | Sistema de estados y lifecycle | AgentState enum, máquina de estados, cancelación |
| 5 | Herramientas y permisos | ToolExecutor, PermissionService en flujo activo |
| 6 | Testing | Tests de creación, ejecución, errores, comunicación, estados |
| 7 | Documentación | Arquitectura actualizada con diseño del Agent System |
| 8 | Validación final | Informe completo, todos los tests pasando |