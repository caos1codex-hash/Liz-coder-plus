# Liz Coder Plus

> Asistente IA de escritorio para Windows — interfaz en español, código en inglés.

**Versión:** `v0.13.0`
**Estado:** Sprint 7 completado — Production Ready con 8 proveedores LLM, 7 agentes, function calling, streaming real, execution graph, y memoria semántica.

---

## ¿Qué es Liz Coder Plus?

Liz Coder Plus es un asistente de inteligencia artificial de escritorio diseñado para funcionar en sistemas operativos Windows. Su objetivo es proporcionar una experiencia conversacional fluida con modelos de lenguaje, manteniendo memoria persistente entre sesiones, ejecutando herramientas externas, automatizando tareas repetitivas y ofreciendo control del sistema mediante un sistema de permisos configurable.

El proyecto está pensado para crecer de forma modular: cada capacidad (memoria, agentes, herramientas, automatización) vive en su propio paquete, lo que permite evolucionar individualmente cada componente sin afectar al resto del sistema. La interfaz de usuario se construye en español para maximizar la accesibilidad, mientras que el código fuente se mantiene en inglés para asegurar compatibilidad con la comunidad de desarrolladores y las mejores prácticas internacionales.

## Objetivo del proyecto

Construir un asistente IA de escritorio profesional que sea:

- **Conversacional:** capaz de mantener diálogos coherentes con modelos IA.
- **Persistente:** con memoria a corto y largo plazo entre sesiones.
- **Capaz:** con ejecución de herramientas externas y comandos del sistema.
- **Automatizable:** preparado para encadenar tareas y procesos.
- **Seguro:** mediante un sistema de permisos claro (confirmación / automático).
- **Extensible:** arquitectura modular preparada para crecer sin reescribir.

## Arquitectura general

El proyecto sigue una arquitectura monorepo con separación clara de responsabilidades:

```
Liz-coder-plus/
├── apps/
│   ├── desktop/   → Cliente Windows (WinUI 3 / .NET 8, MVVM)
│   └── backend/   → Servidor Python (FastAPI + WebSocket)
├── packages/
│   ├── core/      → Orquestador, pipeline, planner, execution graph, context engine
│   ├── memory/    → Memoria persistente (SQLite, semántica, conocimiento)
│   ├── multiagent/→ Sistema multi-agente (registry, lifecycle, orchestrator)
│   ├── agents/    → Agentes especializados (7 agentes estándar)
│   ├── tools/     → Herramientas externas (6 herramientas + selección inteligente)
│   ├── shared/    → Modelos y tipos comunes (WebSocket, utils)
│   └── llm/       → Motor LLM (10 providers, streaming, contexto, prompt)
├── docs/          → Documentación del proyecto (arquitectura, planning, context engine)
├── tests/         → Pruebas (unit, integration, e2e, agents, planner)
├── scripts/       → Scripts de utilidad y automatización
└── config/        → Configuración por entorno (development, production)
```

### Flujo de comunicación

1. El usuario interactúa con la **Desktop App** (WinUI 3).
2. La Desktop App se comunica con el **Backend** (FastAPI) por WebSocket.
3. El Backend delega en el **Orchestrator**, que ejecuta el pipeline completo.
4. El **Pipeline** planifica, selecciona contexto, elige agente y ejecuta tareas.
5. Los **Agents** mantienen el diálogo con modelos IA via **LLM Provider**.
6. **Memory** persiste el contexto entre sesiones (con búsqueda semántica).
7. **Tools** ejecuta acciones externas según los permisos configurados (con function calling).

## Proveedores LLM soportados

| Provider | Endpoint | Models | Streaming | Function Calling |
|----------|----------|--------|-----------|-----------------|
| **NVIDIA NIM** | integrate.api.nvidia.com | Llama 3.1 405B/70B/8B, Nemotron, Mixtral, Qwen, Phi-3, Gemma, DeepSeek | ✅ | ✅ |
| **OpenAI** | api.openai.com | GPT-4o, GPT-4o-mini, GPT-3.5-turbo | ✅ | ✅ |
| **Anthropic** | api.anthropic.com | Claude 3.5 Sonnet, Claude 3 Haiku, Claude 3 Opus | ✅ | ✅ |
| **Google** | generativelanguage.googleapis.com | Gemini Pro, Gemini Flash | ✅ | ❌ |
| **Ollama** | localhost:11434 | Llama 3, Mistral, CodeLlama (local) | ✅ | ❌ |
| **OpenRouter** | openrouter.ai | 100+ modelos (acceso unificado) | ✅ | ✅ |
| **DeepSeek** | api.deepseek.com | DeepSeek Chat, DeepSeek Coder, DeepSeek Reasoner | ✅ | ✅ |
| **Mistral AI** | api.mistral.ai | Mistral Large, Codestral, Pixtral | ✅ | ✅ |

## Tecnologías utilizadas

| Capa            | Tecnología                          | Uso                                |
|-----------------|-------------------------------------|------------------------------------|
| Desktop         | WinUI 3 / .NET 8                    | Interfaz nativa de Windows         |
| Desktop patron  | MVVM (CommunityToolkit.Mvvm)        | Separación UI / lógica             |
| Backend         | Python 3.11+                        | Lógica de servidor                 |
| Backend API     | FastAPI + Uvicorn                   | API REST + WebSocket               |
| Memoria         | SQLite (aiosqlite)                  | Persistencia conversacional        |
| Streaming       | SSE + httpx async                    | Streaming token-by-token real     |
| Function Calling| OpenAI-compatible format            | Invocación de herramientas por LLM |
| Execution Graph | DAG + topological sort             | Ejecución paralela de tareas       |
| Context Engine  | Multi-selector + ranking + budget   | Ingeniería de contexto inteligente |
| Empaquetado     | Monorepo (carpetas independientes)  | Modularidad y crecimiento          |
| CI/CD           | GitHub Actions + Docker             | Tests, lint, typecheck, deploy    |
| Control versión | Git + GitHub                        | Colaboración y CI/CD               |

## Cómo ejecutar el proyecto

### Requisitos previos

- **Windows 10/11** (cliente desktop) — o Windows/Linux/macOS para desarrollo del backend.
- **.NET 8 SDK** para la app desktop.
- **Python 3.11+** para el backend.
- **Git** para control de versiones.
- **API Key** de al menos un proveedor LLM (NVIDIA free recomendado).

### Variables de entorno

```bash
# NVIDIA NIM (free tier, recomendado)
NVIDIA_API_KEY=nvapi-tu-clave-aqui

# Opcional: otros proveedores
OPENAI_API_KEY=sk-tu-clave-openai
ANTHROPIC_API_KEY=sk-ant-tu-clave
DEEPSEEK_API_KEY=tu-clave-deepseek
MISTRAL_API_KEY=tu-clave-mistral
```

### Clonar el repositorio

```bash
git clone https://github.com/caos1codex-hash/Liz-coder-plus.git
cd Liz-coder-plus
```

### Backend (Python + FastAPI)

```bash
cd apps/backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

Endpoints disponibles:
- `GET /health` — Health check del servidor
- `GET /status` — Estado detallado (agentes, modelos, herramientas, memoria)
- `WS /ws/chat` — Chat bidireccional con streaming
- `POST /api/chat` — Chat vía REST
- `POST /api/chat/complete` — Completión directa LLM
- `GET /api/models/discover` — Descubrir modelos disponibles
- `POST /api/plans/execute` — Ejecutar planes de tareas

### Desktop (WinUI 3 / .NET)

```bash
cd apps/desktop
dotnet restore
dotnet build
dotnet run
```

### Docker

```bash
docker-compose up --build
```

## Historial de Sprints

### Sprint 1 — Cimientos ✅ (v0.1.0 → v0.2.1)
- Estructura monorepo, arquitectura base, sistema de permisos.
- Backend FastAPI con WebSocket bidireccional.
- Memoria persistente SQLite con cache RAM.
- Agent stabilization con lifecycle completo.
- Tools system con 3 herramientas base (File, System, Terminal).
- Motor LLM: 5 providers, streaming, contexto, prompt pipeline.

### Sprint 2 — Multi-Agent System ✅ (v0.3.0 → v0.5.0)
- 7 agentes especializados: Planner, Memory, Coding, Research, Execution, Tool, Validation.
- Agent Registry con manifests y capability matching.
- Agent Lifecycle Manager (CREATED → READY → RUNNING → STOPPED).
- Registry Orchestrator desacoplado.
- Agent communication via EventBus.

### Sprint 3 — Planning & Execution Engine ✅ (v0.6.0 → v0.9.0)
- Planner heurístico + LLM-based task decomposition.
- Execution Graph con DAG y topological sort.
- Parallel execution de tareas independientes.
- Context Engineering Engine (multi-selector, ranking, budget).
- Plan tracking con snapshots y detección de bloqueos.
- Recovery con checkpoints y re-planificación.

### Sprint 4 — Tool Intelligence ✅ (v0.10.0)
- Tool Selection Engine con 8 filtros independientes.
- Multi-factor ranking con pesos configurables.
- Tool Execution Pipeline con permisos integrados.
- 133 nuevos tests.

### Sprint 5 — LLM Integration & Desktop ✅ (v0.11.0)
- 8 proveedores LLM: NVIDIA, OpenAI, Anthropic, Google, Ollama, OpenRouter, DeepSeek, Mistral.
- Auto-descubrimiento de modelos NVIDIA NIM free.
- REST API: /api/chat, /api/chat/complete, /api/models/discover.
- Streaming token-by-token real vía WebSocket.
- Desktop WinUI 3 profesional con MVVM completo.
- Nuevas herramientas: WebSearch, CodeExecution.

### Sprint 6 — Tool-Use & Semantic Memory ✅ (v0.12.0)
- Function calling loop con herramientas (max 10 iteraciones).
- Planner basado en LLM para descomposición de tareas.
- Execution Graph integrado con planes.
- Búsqueda semántica en memoria con cosine similarity.
- Permisos implementados en pipeline.

### Sprint 7 — Production Readiness ✅ (v0.13.0)
- Dockerfile multi-stage + docker-compose.
- GitHub Actions CI/CD (lint, typecheck, test).
- Makefile unificado.
- Unificación de versiones.
- /status endpoint con información detallada en tiempo real.
- Bug fixes críticos de build.
- KnowledgeMemory para almacenamiento a largo plazo.

## Convenciones del proyecto

- **Idioma del código:** inglés (nombres, variables, comentarios técnicos).
- **Idioma de la documentación de usuario:** español.
- **Estilo de código:** limpio, tipado, con nombres descriptivos.
- **Commits:** formato *Conventional Commits* (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`).
- **Ramas:** `main` (estable), `feature/*`, `fix/*`, `sprint-*`.

## Próximos pasos

- [ ] Sprint 8 — Generación de imágenes (DALL-E, Stable Diffusion)
- [ ] Sprint 9 — Voz (TTS / STT) con Whisper
- [ ] Sprint 10 — Control avanzado del PC
- [ ] Sprint 11 — Automatización de tareas programadas
- [ ] Sprint 12 — Empaquetado MSIX para distribución

## Licencia

Este proyecto se distribuye bajo licencia MIT. Consulta el archivo [LICENSE](./LICENSE) para más detalles.

---

**Liz Coder Plus** — Construido para crecer, módulo a módulo.
