# Roadmap — Liz Coder Plus

> Hoja de ruta del proyecto. Sujeta a cambios según el avance.

## Sprint 1 — Cimientos

### Prompt 1 — v0.1.1
- [x] Estructura monorepo creada.
- [x] Arquitectura base definida.
- [x] Sistema de permisos inicial (enum + servicio).
- [x] Configuración por entorno (`development.json`, `production.json`).
- [x] Documentación inicial (`README.md`, `docs/`).
- [x] Repositorio Git inicializado y primer commit.

### Prompt 2 (actual) — v0.1.2
- [x] Backend FastAPI funcional.
- [x] Endpoints `/health` y `/status` operativos (con métricas WS).
- [x] WebSocket endpoint `/ws/chat` con streaming.
- [x] WebSocketManager (conexiones, broadcast, eventos, desconexiones).
- [x] SessionManager (session_id, historial temporal, estado de usuario).
- [x] AgentRouter con EchoAgent por defecto.
- [x] Orchestrator integrado con WS + Session + Router.
- [x] ChatService.cs en desktop (Connect/Send/Receive async).
- [x] UI mínima: textbox + botón + área de mensajes.
- [x] 56 tests pasando (48 unitarios + 8 integración WS).

## Sprint 2 — Memoria y agentes
- [ ] SQLite + SQLAlchemy funcionando.
- [ ] Memoria a corto plazo (contexto de sesión).
- [ ] Memoria a largo plazo (hechos persistentes).
- [ ] Primer agente conversacional.
- [ ] Pruebas unitarias de memoria y agentes.

## Sprint 3 — Herramientas y permisos
- [ ] ToolRegistry operativo.
- [ ] Herramienta Shell (con permisos).
- [ ] Herramienta File (leer / escribir).
- [ ] Diálogo de confirmación en desktop.
- [ ] Auditoría de permisos.

## Sprint 4 — Interfaz desktop
- [ ] Ventana principal con chat.
- [ ] Listado de conversaciones.
- [ ] Panel de configuración.
- [ ] Indicador de estado de conexión.
- [ ] Empaquetado MSIX inicial.

## Sprint 5 — Modelos IA
- [ ] Integración con proveedores (OpenAI, etc.).
- [ ] Gestión de contexto y tokens.
- [ ] Streaming de respuestas por WebSocket.
- [ ] Selección de modelo desde la UI.

## Sprint 6+ — Capacidades avanzadas
- [ ] Generación de imágenes.
- [ ] Voz (TTS / STT).
- [ ] Control avanzado del PC.
- [ ] Automatización de tareas programadas.
- [ ] Plugins externos.
- [ ] Sincronización entre dispositivos.

## Principios del roadmap

1. **Vertical primero:** cada sprint entrega algo ejecutable.
2. **Sin deuda oculta:** los stubs se marcan claramente con TODOs.
3. **Seguridad antes que potencia:** los permisos llegan antes que las
   herramientas peligrosas.
4. **UX al final:** la UI se construye una vez el backend es sólido.
