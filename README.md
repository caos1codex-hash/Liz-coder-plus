# Liz Coder Plus

> Asistente IA de escritorio nativo para Linux — interfaz en español, codigo en ingles.

**Version:** `v0.19.0`
**Estado:** Desktop Linux (PySide6/Qt6) — App nativa con chat, WebSocket, y control de sistema.

---

## Que es Liz Coder Plus?

Liz Coder Plus es un asistente de inteligencia artificial de escritorio disenado para sistemas Linux. Su objetivo es proporcionar una experiencia conversacional fluida con modelos de lenguaje, manteniendo memoria persistente entre sesiones, ejecutando herramientas externas, automatizando tareas repetitivas y ofreciendo control del sistema mediante un sistema de permisos configurable.

El proyecto esta pensado para crecer de forma modular: cada capacidad (memoria, agentes, herramientas, automatizacion) vive en su propio paquete, lo que permite evolucionar individualmente cada componente sin afectar al resto del sistema. La interfaz de usuario se construye en espanol para maximizar la accesibilidad, mientras que el codigo fuente se mantiene en ingles para asegurar compatibilidad con la comunidad de desarrolladores y las mejores practicas internacionales.

## Objetivo del proyecto

Construir un asistente IA de escritorio profesional que sea:

- **Conversacional:** capaz de mantener dialogos coherentes con modelos IA.
- **Persistente:** con memoria a corto y largo plazo entre sesiones.
- **Capaz:** con ejecucion de herramientas externas y comandos del sistema.
- **Automatizable:** preparado para encadenar tareas y procesos.
- **Seguro:** mediante un sistema de permisos claro (confirmacion / automatico).
- **Extensible:** arquitectura modular preparada para crecer sin reescribir.
- **Nativo:** corre como aplicacion de escritorio de Linux (PySide6/Qt6), no en el navegador.

## Arquitectura general

```
Liz-coder-plus/
├── apps/
│   ├── linux-desktop/  → Cliente Linux (PySide6 / Qt6, Python)
│   └── backend/        → Servidor Python (FastAPI + WebSocket)
├── packages/
│   ├── core/           → Orquestador, pipeline, planner, execution graph, context engine
│   ├── memory/         → Memoria persistente (SQLite, semantica, conocimiento)
│   ├── multiagent/     → Sistema multi-agente (registry, lifecycle, orchestrator)
│   ├── agents/         → Agentes especializados (7 agentes estandar)
│   ├── tools/          → Herramientas externas (6 herramientas + seleccion inteligente)
│   ├── shared/         → Modelos y tipos comunes (WebSocket, utils)
│   └── llm/            → Motor LLM (10 providers, streaming, contexto, prompt)
├── docs/               → Documentacion del proyecto
├── tests/              → Pruebas (unit, integration, e2e, agents, planner)
├── scripts/            → Scripts de utilidad y automatizacion
└── config/             → Configuracion por entorno (development, production)
```

### Flujo de comunicacion

1. El usuario interactua con la **Desktop App** (PySide6/Qt6).
2. La Desktop App se comunica con el **Backend** (FastAPI) por WebSocket.
3. El Backend delega en el **Orchestrator**, que ejecuta el pipeline completo.
4. El **Pipeline** planifica, selecciona contexto, elige agente y ejecuta tareas.
5. Los **Agents** mantienen el dialogo con modelos IA via **LLM Provider**.
6. **Memory** persiste el contexto entre sesiones (con busqueda semantica).
7. **Tools** ejecuta acciones externas segun los permisos configurados (con function calling).

## Proveedores LLM soportados

| Provider | Endpoint | Models | Streaming | Function Calling |
|----------|----------|--------|-----------|-----------------|
| **NVIDIA NIM** | integrate.api.nvidia.com | Llama 3.1 405B/70B/8B, Nemotron, Mixtral, Qwen, Phi-3, Gemma, DeepSeek | SI | SI |
| **OpenAI** | api.openai.com | GPT-4o, GPT-4o-mini, GPT-3.5-turbo | SI | SI |
| **Anthropic** | api.anthropic.com | Claude 3.5 Sonnet, Claude 3 Haiku, Claude 3 Opus | SI | SI |
| **Google** | generativelanguage.googleapis.com | Gemini Pro, Gemini Flash | SI | NO |
| **Ollama** | localhost:11434 | Llama 3, Mistral, CodeLlama (local) | SI | NO |
| **OpenRouter** | openrouter.ai | 100+ modelos (acceso unificado) | SI | SI |
| **DeepSeek** | api.deepseek.com | DeepSeek Chat, DeepSeek Coder, DeepSeek Reasoner | SI | SI |
| **Mistral AI** | api.mistral.ai | Mistral Large, Codestral, Pixtral | SI | SI |

## Tecnologias utilizadas

| Capa            | Tecnologia                          | Uso                                |
|-----------------|-------------------------------------|------------------------------------|
| Desktop         | PySide6 / Qt6 (Python)              | Interfaz nativa de Linux           |
| Backend         | Python 3.11+                        | Logica de servidor                 |
| Backend API     | FastAPI + Uvicorn                   | API REST + WebSocket               |
| Memoria         | SQLite (aiosqlite)                  | Persistencia conversacional       |
| Streaming       | SSE + httpx async                    | Streaming token-by-token real      |
| Function Calling| OpenAI-compatible format            | Invocacion de herramientas por LLM|
| Execution Graph | DAG + topological sort             | Ejecucion paralela de tareas       |
| Context Engine  | Multi-selector + ranking + budget   | Ingenieria de contexto inteligente |
| CI/CD           | GitHub Actions                       | Tests, lint, typecheck            |
| Control version | Git + GitHub                        | Colaboracion y CI/CD               |

## Como ejecutar el proyecto

### Requisitos previos

- **Linux** (cliente desktop) — Debian/Ubuntu recomendado.
- **Python 3.11+** con pip y venv.
- **libegl1, libgles2** (dependencias del sistema para Qt6).
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

### Desktop (Linux — PySide6 / Qt6)

```bash
# Instalar dependencias del sistema (Debian/Ubuntu)
sudo apt install python3 python3-venv libegl1 libgles2

# Instalar la app
cd apps/linux-desktop
bash install.sh

# Ejecutar
./run.sh
```

El script `install.sh` crea automaticamente:
- Un entorno virtual con PySide6 y websocket-client.
- Un script `run.sh` para lanzar la app.
- Una entrada `.desktop` en tu menu de aplicaciones.

### Backend (Python + FastAPI)

```bash
cd apps/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

Endpoints disponibles:
- `GET /health` — Health check del servidor
- `GET /status` — Estado detallado (agentes, modelos, herramientas, memoria)
- `WS /ws/chat` — Chat bidireccional con streaming
- `POST /api/chat` — Chat via REST
- `POST /api/chat/complete` — Completicion directa LLM
- `GET /api/models/discover` — Descubrir modelos disponibles
- `POST /api/plans/execute` — Ejecutar planes de tareas

### Docker

```bash
docker-compose up --build
```

## Funcionalidades del Desktop

- **Chat en tiempo real** via WebSocket con el backend.
- **Modo demo** integrado para probar sin backend.
- **Lista de conversaciones** en sidebar.
- **Selector de modelos** dinámico desde el backend.
- **Modo Confirmacion / Automatico** para control de permisos.
- **System tray** integrado.
- **Interfaz moderna** con estilos Qt6 personalizados.

## Convenciones del proyecto

- **Idioma del codigo:** ingles (nombres, variables, comentarios tecnicos).
- **Idioma de la documentacion de usuario:** espanol.
- **Estilo de codigo:** limpio, tipado, con nombres descriptivos.
- **Commits:** formato *Conventional Commits* (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`).
- **Ramas:** `main` (estable), `feature/*`, `fix/*`, `sprint-*`.

## Proximos pasos

- [ ] Sprint 8 — Generacion de imagenes (DALL-E, Stable Diffusion)
- [ ] Sprint 9 — Voz (TTS / STT) con Whisper
- [ ] Sprint 10 — Control avanzado del PC (comandos del sistema)
- [ ] Sprint 11 — Automatizacion de tareas programadas
- [ ] Sprint 12 — AppImage / Flatpak para distribucion

## Licencia

Este proyecto se distribuye bajo licencia MIT. Consulta el archivo [LICENSE](./LICENSE) para mas detalles.

---

**Liz Coder Plus** — Construido para crecer, modulo a modulo.
