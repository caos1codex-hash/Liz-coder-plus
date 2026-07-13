# Liz Coder Plus

> Asistente IA de escritorio para Windows — interfaz en español, código en inglés.

**Versión:** `v0.2.0`
**Estado:** Sprint 1.7 — Orchestrator Avanzado

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
│   ├── desktop/   → Cliente Windows (WinUI 3 / .NET, MVVM)
│   └── backend/   → Servidor Python (FastAPI + WebSocket)
├── packages/
│   ├── core/      → Orquestador principal y sistema de eventos
│   ├── memory/    → Memoria persistente (SQLite inicial)
│   ├── agents/    → Agentes especializados
│   ├── tools/     → Herramientas externas y comandos
│   └── shared/    → Modelos y tipos comunes
├── docs/          → Documentación del proyecto
├── tests/         → Pruebas (unit, integration, e2e)
├── scripts/       → Scripts de utilidad y automatización
└── config/        → Configuración por entorno
```

### Flujo de comunicación

1. El usuario interactúa con la **Desktop App** (WinUI 3).
2. La Desktop App se comunica con el **Backend** (FastAPI) por WebSocket.
3. El Backend delega en el **Core**, que orquesta agentes y herramientas.
4. Los **Agents** mantienen el diálogo con modelos IA.
5. **Memory** persiste el contexto entre sesiones.
6. **Tools** ejecuta acciones externas según los permisos configurados.

## Tecnologías utilizadas

| Capa            | Tecnología                          | Uso                                |
|-----------------|-------------------------------------|------------------------------------|
| Desktop         | WinUI 3 / .NET 8                    | Interfaz nativa de Windows         |
| Desktop patron  | MVVM (CommunityToolkit.Mvvm)        | Separación UI / lógica             |
| Backend         | Python 3.11+                        | Lógica de servidor                 |
| Backend API     | FastAPI                             | API REST + WebSocket               |
| Memoria         | SQLite (SQLAlchemy)                 | Persistencia inicial               |
| Empaquetado     | Monorepo (carpetas independientes)  | Modularidad y crecimiento          |
| Control versión | Git + GitHub                        | Colaboración y CI/CD futuro        |

## Cómo ejecutar el proyecto

> Esta versión `v0.1.1` corresponde al Sprint 1 — Prompt 1.
> Solo se establecen los cimientos. Aún no hay UI final ni modelos IA conectados.

### Requisitos previos

- **Windows 10/11** (cliente desktop) — o Windows/Linux/macOS para desarrollo del backend.
- **.NET 8 SDK** para la app desktop.
- **Python 3.11+** para el backend.
- **Git** para control de versiones.

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

### Desktop (WinUI 3 / .NET)

```bash
cd apps/desktop
dotnet restore
dotnet build
dotnet run
```

> La app desktop está en fase de cimientos. La UI final se implementará en siguientes sprints.

## Roadmap inicial

### Sprint 1 — Cimientos (Prompt 1) ✅
- Estructura monorepo creada.
- Arquitectura base definida.
- Sistema de permisos inicial.
- Configuración por entorno.
- Documentación inicial.

### Sprint 1 — Capa de comunicación WebSocket (Prompt 2) ✅
- Backend FastAPI funcional con endpoints /health y /status.
- WebSocket bidireccional con streaming de respuestas.
- Orquestador conectado con SessionManager y AgentRouter.
- Sistema de permisos inicial.

### Sprint 1 — Memoria Persistente (Prompt 3) ✅
- Sistema de memoria conversacional con SQLite (aiosqlite).
- MemoryManager como API pública del sistema de memoria.
- ConversationRepository para acceso a datos asíncrono.
- Integración completa: Orchestrator → SessionManager → MemoryManager → SQLite.
- Cache RAM para lecturas rápidas con persistencia en disco.
- Restauración de sesiones después de reinicio del proceso.

### Sprint 1.5 — Agent Stabilization ✅
- BaseAgent con lifecycle completo (CREATED → READY → RUNNING → COMPLETED/FAILED → SHUTDOWN).
- AgentRegistry con validación del Protocol Agent.
- ToolExecutor con gating de permisos y emisión de eventos.
- Integración agentes-orchestrator con timeout y métricas.

### Sprint 1.6 — Tools System ✅
- BaseTool con lifecycle, validación de entrada/salida, y métricas.
- Niveles de permiso (LOW/MEDIUM/HIGH) en herramientas.
- ToolRegistry con fuentes, activación/desactivación y filtrado.
- ToolExecutor con timeout, cancelación, contexto y tracking de agente.
- PermissionService con evaluate_tool() level-aware y audit log.
- 3 herramientas base: FileTool, SystemTool, TerminalTool.
- 225 tests unitarios pasando.

### Sprint 2 — Agente Conversacional
- Primer agente conversacional con IA real.

### Sprint 3 — Permisos en Producción y Plugins
- Sistema de permisos operativo en producción.
- Plugins de herramientas externas.
- Registro de auditoría persistente.

### Sprint 4 — Interfaz Desktop
- UI WinUI 3 funcional.
- Chat conversacional.
- Panel de configuración.

### Sprint 5 — Modelos IA
- Integración con proveedores de LLM.
- Gestión de contexto y tokens.

### Sprint 6+ — Capacidades avanzadas
- Generación de imágenes.
- Voz (TTS / STT).
- Control avanzado del PC.
- Automatización de tareas.

## Convenciones del proyecto

- **Idioma del código:** inglés (nombres, variables, comentarios técnicos).
- **Idioma de la documentación de usuario:** español.
- **Estilo de código:** limpio, tipado, con nombres descriptivos.
- **Commits:** formato *Conventional Commits* (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`).
- **Ramas:** `main` (estable), `develop` (integración), `feature/*`, `fix/*`.

## Licencia

Este proyecto se distribuye bajo licencia MIT. Consulta el archivo [LICENSE](./LICENSE) para más detalles.

---

**Liz Coder Plus** — Construido para crecer, módulo a módulo.
