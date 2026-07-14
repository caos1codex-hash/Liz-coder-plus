# Plugin System — Liz Coder Plus (Sprint 2.5)

## Visión General

El sistema de plugins permite extender Liz Coder Plus sin modificar el código del núcleo. Los nuevos agentes, herramientas y workflows se instalan como plugins que se descubren, cargan y registran automáticamente.

## Arquitectura

```
plugins/                    ← Directorio de plugins (auto-descubrimiento)
├── my_plugin/
│   ├── manifest.json      ← PluginManifest
│   └── plugin.py          ← Clase Plugin
└── another_plugin/
    ├── manifest.json
    └── plugin.py

Flujo de datos:

  PluginDiscovery → PluginLoader → PluginManager → AgentRegistry
       ↓                                  ↓              ↓
  manifest.json                  PluginEventBus    CapabilityResolver
                                         ↓                    ↓
                                   Observabilidad      WorkflowEngine
```

## Componentes

### PluginManifest

Contrato declarativo inmutable (frozen dataclass). Define la identidad y requisitos del plugin.

```python
from multiagent.plugins import PluginManifest

manifest = PluginManifest(
    id="my-agent-plugin",
    name="My Agent Plugin",
    version="1.0.0",
    author="Liz Team",
    description="Adds a specialized agent",
    api_version="1.0.0",
    capabilities=["specialized.task"],
    entry_point="my_agent_plugin.plugin:MyAgentPlugin",
)
```

Campos obligatorios: `id`, `name`, `version`.
Campos opcionales: `author`, `description`, `api_version`, `license`, `capabilities`, `dependencies`, `entry_point`, `metadata`.

### Plugin

Clase base abstracta con ciclo de vida completo.

```python
from multiagent.plugins import Plugin, PluginManifest

class MyPlugin(Plugin):
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="my-plugin",
            name="My Plugin",
            version="1.0.0",
            capabilities=["my.capability"],
            entry_point="my_plugin.plugin:MyPlugin",
        )

    async def initialize(self) -> None:
        # Setup del plugin
        await super().initialize()

    def get_agents(self) -> list[BaseAgent]:
        # Retornar agentes que el plugin expone
        return [MyAgent()]

    async def shutdown(self) -> None:
        # Limpieza
        await super().shutdown()
```

### Estados del Plugin

```
DISCOVERED → LOADED → INITIALIZED → STARTED → ENABLED ⇄ DISABLED
                                                         ↓
                                                       UNLOADED
Any → ERROR → recovery possible
```

### PluginManager

Gestor central que orquesta todo el sistema.

```python
from multiagent.plugins import PluginManager

manager = PluginManager(
    agent_registry=registry,  # AgentRegistry existente
    system_api_version="1.0.0",
)

# Instalar desde descubrimiento
result = await manager.install_plugin(discovered_plugin)

# Habilitar (inicializa + registra agentes)
await manager.enable_plugin("my-plugin")

# Deshabilitar (desregistra agentes)
await manager.disable_plugin("my-plugin")

# Recargar
await manager.reload_plugin("my-plugin")

# Remover
await manager.remove_plugin("my-plugin")
```

### Descubrimiento Automático

El `PluginDiscovery` escanea directorios buscando `manifest.json`:

```python
from multiagent.plugins import PluginDiscovery

discovery = PluginDiscovery(plugin_dirs=["plugins", "custom_plugins"])
result = await discovery.scan()

for dp in result.discovered:
    await manager.install_plugin(dp)
```

### Registro Automático de Agentes

Cuando un plugin expone agentes vía `get_agents()`, el `PluginManager` los registra automáticamente en el `AgentRegistry` al habilitar el plugin, y los desregistra al deshabilitar:

```
Plugin.enable()
  → PluginManager._register_plugin_agents()
    → AgentRegistry.register(agent, provided_capabilities=manifest.capabilities)
      → CapabilityResolver puede encontrar el agente
        → WorkflowEngine puede usar el agente en steps
```

## Crear un Plugin

### 1. Crear la estructura de directorios

```
plugins/my_agent_plugin/
├── manifest.json
└── plugin.py
```

### 2. Escribir el manifest.json

```json
{
  "id": "my-agent-plugin",
  "name": "My Agent Plugin",
  "version": "1.0.0",
  "author": "Tu Nombre",
  "description": "Un agente especializado",
  "api_version": "1.0.0",
  "license": "MIT",
  "capabilities": ["specialized.task", "specialized.review"],
  "entry_point": "my_agent_plugin.plugin:MyAgentPlugin"
}
```

### 3. Implementar el plugin

```python
# plugins/my_agent_plugin/plugin.py
from multiagent.plugins import Plugin, PluginManifest
from multiagent.base import BaseAgent

class SpecializedAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="specialized-agent")

    async def _execute_impl(self, task: dict) -> dict:
        return {"status": "ok", "result": "done"}

class MyAgentPlugin(Plugin):
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="my-agent-plugin",
            name="My Agent Plugin",
            version="1.0.0",
            capabilities=["specialized.task"],
            entry_point="my_agent_plugin.plugin:MyAgentPlugin",
        )

    def get_agents(self) -> list[BaseAgent]:
        return [SpecializedAgent()]
```

### 4. Registrar el directorio y escanear

```python
discovery = PluginDiscovery(plugin_dirs=["plugins"])
result = await discovery.scan()
for dp in result.discovered:
    await manager.install_plugin(dp)
    await manager.enable_plugin(dp.manifest.id)
```

## Herramientas (Tool Plugins)

Los plugins pueden exponer herramientas además de agentes:

```python
from multiagent.plugins import Plugin, ToolPluginMixin, ToolDefinition, PluginManifest

class MyToolPlugin(Plugin, ToolPluginMixin):
    def __init__(self):
        Plugin.__init__(self)
        ToolPluginMixin.__init__(self)
        self._register_tool(ToolDefinition(
            name="http_request",
            description="Makes HTTP requests",
            category="http",
            parameters={"url": {"type": "string", "required": True}},
        ))

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="http-tool-plugin",
            name="HTTP Tool Plugin",
            version="1.0.0",
            capabilities=["http.request"],
        )

    def get_tools(self) -> list:
        return self.tool_definitions()

    async def execute_tool(self, name: str, params: dict) -> dict:
        if name == "http_request":
            return {"status": "ok", "url": params["url"]}
        return {"status": "error", "error": "Unknown tool"}
```

## Versionado y Compatibilidad

### API Version

El sistema tiene una `system_api_version` (actualmente `"1.0.0"`). Los plugins declaran su `api_version` requerida. La compatibilidad se verifica por major version:

- `1.x.y` es compatible con `1.a.b` ✓
- `2.0.0` NO es compatible con `1.x.y` ✗

### Dependencias

Los plugins pueden declarar dependencias en otros plugins:

```json
{
  "dependencies": [
    {"id": "core-git", "minimum_version": "1.0.0"},
    {"id": "utils", "minimum_version": "0.5.0", "maximum_version": "2.0.0"}
  ]
}
```

### PluginCompatibilityChecker

```python
from multiagent.plugins import PluginCompatibilityChecker

checker = PluginCompatibilityChecker(system_api_version="1.0.0")
result = checker.check(
    manifest,
    available_plugins={"core-git": "1.5.0"},
)
if not result.compatible:
    for error in result.errors:
        print(f"Incompatible: {error}")
```

## Eventos

El sistema emite 15 tipos de eventos:

| Evento | Cuando |
|--------|--------|
| `plugin.discovered` | Plugin encontrado por discovery |
| `plugin.loaded` | Plugin cargado en memoria |
| `plugin.installed` | Plugin instalado en el manager |
| `plugin.initialized` | Plugin inicializado |
| `plugin.started` | Plugin iniciado |
| `plugin.enabled` | Plugin habilitado (listo para usar) |
| `plugin.disabled` | Plugin deshabilitado |
| `plugin.unloaded` | Plugin descargado |
| `plugin.removed` | Plugin removido |
| `plugin.reloaded` | Plugin recargado |
| `plugin.failed` | Error en cualquier fase |
| `plugin.agent.registered` | Agente registrado en registry |
| `plugin.tool.registered` | Herramienta registrada |
| `plugin.workflow.registered` | Workflow registrado |
| `plugin.stopped` | Plugin detenido |

### Suscribir a Eventos

```python
manager = PluginManager()

async def on_plugin_loaded(event: PluginSystemEvent):
    print(f"Plugin loaded: {event.plugin_id}")

await manager.event_bus.subscribe(
    on_plugin_loaded,
    event_type=PluginEvent.PLUGIN_LOADED,
)
```

## Seguridad

El sistema valida:

1. **Manifest obligatorio**: Todo plugin debe tener un PluginManifest válido.
2. **Entry point válido**: Formato `module.path:ClassName`.
3. **Plugin duplicado**: No se permite instalar dos plugins con el mismo ID.
4. **Versión incompatible**: Se verifica `api_version` antes de cargar.
5. **Dependencias faltantes**: Se verifican antes de habilitar.
6. **Errores de carga**: Los errores de import se capturan y no rompen el sistema.
7. **Fallos de inicialización**: Un plugin que falla al inicializar pasa a ERROR.
8. **Aislamiento**: Deshabilitar/descargar un plugin limpia sus recursos.
9. **Timeouts**: La inicialización tiene timeout configurable (default: 30s).

## Métricas

```python
metrics = manager.metrics()
# {
#   "total_plugins": 5,
#   "enabled_plugins": 3,
#   "total_loads": 7,
#   "total_errors": 1,
#   "state_distribution": {"enabled": 3, "disabled": 2},
#   "loader_metrics": {...},
#   "event_bus_metrics": {...},
# }
```