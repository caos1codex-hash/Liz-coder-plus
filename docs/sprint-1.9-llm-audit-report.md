# Sprint 1.9 — Informe de Auditoría del Motor LLM

> **Fecha:** Sprint 1.9
> **Ámbito:** Backend LLM, adaptadores, providers, streaming, prompts, tokenizer, contexto, configuración

---

## Resumen Ejecutivo

El codebase de Liz Coder Plus tiene **cero código de integración LLM real**. Toda la infraestructura (pipeline, agentes, WebSocket, sesiones, memoria) está diseñada y lista para recibir un motor LLM, pero no existe ningún adaptador de provider, ninguna llamada de inferencia, ningún tokenizer, ni ninguna plantilla de prompt. El único artefacto "LLM-adyacente" es una configuración `AIConfig` que declara campos de provider/modelo pero que **nunca se consume**.

---

## Hallazgos

### 1. Código LLM Existente

| Componente | Estado | Detalle |
|---|---|---|
| Provider/Adaptador LLM | ❌ Ninguno | No hay `packages/llm/` ni ningún módulo de provider |
| Streaming real | ❌ Falso | `orchestrator.stream_message()` divide texto en chunks por palabras — comentario dice "Sprint 5" |
| Prompts/Sistema | ❌ Ninguno | No hay system prompts, templates, ni gestión de prompts |
| Tokenizer | ❌ Ninguno | No hay tiktoken, recuento de tokens, ni presupuesto de contexto |
| Contexto de conversación | ⚠️ Parcial | `SessionManager` gestiona historial (200 turnos max) pero sin recuento de tokens |
| Configuración LLM | ⚠️ Esquema solo | `AIConfig` en config.py tiene provider/model/temperature/max_tokens pero nunca se usa |
| Integración con Agentes | ❌ Solo EchoAgent | El único agente retorna cadenas fijas en español. No hay agente LLM |

### 2. Código Muerto Identificado

| Archivo | Problema |
|---|---|
| `apps/backend/src/core/orchestrator.py` | Stub de Sprint 1 que retorna `not_implemented`. Nunca importado. |
| `apps/backend/src/events/bus.py` | EventBus duplicado del backend. El real está en `packages/core/`. |
| `packages/memory/src/sqlite.py` | Backend SQLAlchemy stub. Todos los métodos son no-ops. |
| `packages/memory/src/models.py` | Modelos SQLAlchemy declarativos no usados (el sistema usa aiosqlite directo). |

### 3. Limitaciones Actuales

- `AIConfig` solo soporta un modelo. No hay selección múltiple ni fallback.
- No hay `base_url` para endpoints personalizados (necesario para Ollama, Azure).
- No hay `context_window` separado de `max_tokens` de generación.
- No hay soporte para function/tool calling.
- El pipeline tiene un `stage_check_permissions` que es un no-op.
- El `stream_message()` del Orchestrator simula streaming dividiendo palabras.

### 4. Deuda Técnica Relevante

| ID | Descripción | Impacto en LLM |
|---|---|---|
| TD-008 | `liz_core.py` manipula `sys.path` | Dificulta importar el nuevo paquete LLM |
| TD-017 | `stage_check_permissions` es no-op | Los tool calls del LLM no tendrán permisos |
| TD-020 | `apps/backend/src/core/orchestrator.py` muerto | Contaminación del namespace |

---

## Plan de Implementación

### Paquete Nuevo: `packages/llm/`

```
packages/llm/
├── pyproject.toml
├── src/
│   ├── __init__.py
│   └── llm/
│       ├── __init__.py
│       ├── protocols.py        # LLMProvider Protocol
│       ├── models.py           # LLMResponse, LLMChunk, ModelInfo, ModelCapabilities
│       ├── manager.py          # ModelManager (registro, activación, selección, cache, healthcheck)
│       ├── providers/
│       │   ├── __init__.py
│       │   ├── base.py         # BaseLLMProvider (clase base con retry/timeout)
│       │   ├── ollama.py       # OllamaProvider
│       │   ├── openai.py       # OpenAIProvider
│       │   ├── anthropic.py    # AnthropicProvider
│       │   ├── google.py       # GoogleProvider
│       │   └── openrouter.py   # OpenRouterProvider
│       ├── streaming/
│       │   ├── __init__.py
│       │   └── manager.py      # StreamingManager
│       ├── context/
│       │   ├── __init__.py
│       │   └── manager.py      # ContextManager
│       └── prompt/
│           ├── __init__.py
│           └── pipeline.py     # PromptPipeline
```

### Integración con Arquitectura Existente

- **Orchestrator**: Recibirá `ModelManager` vía `attach_llm()` (similar a `attach_memory()`).
- **Pipeline**: Nuevo stage `stage_build_prompt` antes de `stage_execute_agent`.
- **EchoAgent**: Reemplazado internamente por `LLMAgent` que usa el ModelManager.
- **stream_message()**: Delegará al `StreamingManager` real.
- **Memory/Planner/Scheduler**: Sin cambios (compatibilidad mantenida).

---

## Conclusión

La auditoría confirma que el sistema está listo arquitectónicamente para recibir el motor LLM. No hay duplicación de funcionalidad LLM que eliminar, pero hay código muerto relevante que debe limpiarse. La implementación procederá creando el paquete `packages/llm/` con los 6 componentes especificados en Sprint 1.9.