# Sprint 2 Roadmap — Liz Coder Plus

> Generado en Sprint 1.10 — Fase 7
> Versión objetivo: v0.4.0

## Prioridades Sprint 2

### 1. Interfaz Desktop (WinUI 3)
- Completar la UI de chat con streaming real
- Integrar con el backend WebSocket
- Agregar historial de conversaciones local
- **Paquete:** `apps/desktop/`
- **Depende de:** Pipeline→LLM connection (TD-036)

### 2. Sistema de Plugins
- Diseñar interfaz de plugin (descubrimiento, carga, ciclo de vida)
- Sandbox de ejecución para plugins
- API de registro de plugins
- **Paquete:** `packages/plugins/` (nuevo)
- **Depende de:** Arquitectura consolidada de Sprint 1

### 3. Marketplace
- Catálogo de plugins descargables
- Sistema de rating y revisiones
- Autenticación de desarrolladores
- **Paquete:** apps/backend + nuevo servicio
- **Depende de:** Sistema de Plugins

### 4. Voice (Voz)
- Integración STT/TTS
- Comandos de voz
- Respuestas habladas
- **Skills:** ASR, TTS
- **Depende de:** Streaming LLM connection

### 5. Vision
- Análisis de imágenes
- Captura de pantalla
- Descripción visual
- **Skill:** VLM
- **Depende de:** Pipeline→LLM connection

### 6. RAG (Retrieval-Augmented Generation)
- Embeddings y vector store
- Búsqueda semántica
- Ingesta de documentos
- **Paquete:** `packages/rag/` (nuevo)
- **Depende de:** Memory system + LLM providers

### 7. Multi-Agent
- Agentes especializados (coder, reviewer, planner)
- Comunicación inter-agente
- Delegación de tareas
- **Paquete:** `packages/agents/` (extensión)
- **Depende de:** Pipeline→LLM, Agent→Tool connections

### 8. Automation
- Workflows programables por el usuario
- Disparadores basados en eventos
- Ejecución programada avanzada
- **Paquete:** `packages/core/` (extensión)
- **Depende de:** Workflow engine, Scheduler

### 9. Cloud Sync
- Sincronización de configuraciones
- Backup de conversaciones
- Multi-dispositivo
- **Paquete:** `apps/backend/` (extensión)
- **Depende de:** Memory persistence

### 10. Extensiones
- Soporte para extensiones VS Code
- LSP (Language Server Protocol)
- Snippets y plantillas
- **Paquete:** nuevo
- **Depende de:** Interfaz Desktop

---

## Separación de Trabajo

### Lo Terminado (Sprint 1)
- ✅ Arquitectura base monorepo
- ✅ EventBus, SessionManager, WebSocketManager
- ✅ AgentRouter con agentes echo
- ✅ Planner heurístico (rule-based)
- ✅ TaskManager con máquina de estados
- ✅ WorkflowManager con DAG y ejecución
- ✅ Scheduler con persistencia SQLite
- ✅ ToolExecutor con permisos y timeout
- ✅ MemoryManager con SQLite
- ✅ RecoveryManager con checkpoints
- ✅ Observability (MetricsCollector, StructuredLogger, AuditRecorder)
- ✅ Pipeline de ejecución unificado (11 etapas)
- ✅ LLM Engine: 5 providers (OpenAI, Anthropic, Google, Ollama, OpenRouter)
- ✅ LLM: ModelManager, ContextManager, PromptPipeline, StreamingManager
- ✅ PermissionService con niveles y auditoría
- ✅ Backend FastAPI con WebSocket
- ✅ Desktop WinUI 3 (scaffolding)

### Lo Pendiente (Sprint 2)
- ❌ Conectar Pipeline → ModelManager (TD-036.B-3)
- ❌ Conectar Planner → TaskManager (TD-036.B-1)
- ❌ Conectar Agent → ToolExecutor (TD-036.B-4)
- ❌ Conectar Pipeline → StreamingManager (TD-036.B-5)
- ❌ Conectar Planner → WorkflowManager (TD-036.B-2)
- ❌ Crear agentes LLM-backed (reemplazar EchoAgent)
- ❌ pyproject.toml con installs editables (TD-008)
- ❌ Reemplazar sys.path manipulation (TD-018)

### Lo Futuro (Sprint 3+)
- Cloud deployment
- Multi-tenant
- Colaboración en tiempo real
- Marketplace de plugins
- Integración con IDEs