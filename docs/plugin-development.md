# Plugin Development Guide

## Quick Start

### Archivo: `manifest.json`

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "author": "Tu Nombre <email@example.com>",
  "description": "Descripcion de lo que hace el plugin",
  "api_version": "1.0.0",
  "license": "MIT",
  "capabilities": ["category.action"],
  "entry_point": "my_plugin.plugin:MyPlugin",
  "dependencies": [],
  "metadata": {}
}
```

### Archivo: `plugin.py`

```python
from multiagent.plugins import Plugin, PluginManifest
from multiagent.base import BaseAgent

class MyAgent(BaseAgent):
    """Agente expuesto por el plugin."""

    def __init__(self):
        super().__init__(
            name="my-agent",
            description="Agente de ejemplo",
        )

    async def _execute_impl(self, task: dict) -> dict:
        # Logica del agente
        return {"status": "ok", "result": task}

class MyPlugin(Plugin):
    """Plugin que registra MyAgent."""

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="my-plugin",
            name="My Plugin",
            version="1.0.0",
            capabilities=["my.action"],
            entry_point="my_plugin.plugin:MyPlugin",
        )

    def get_agents(self) -> list[BaseAgent]:
        return [MyAgent()]
```

## Reglas

1. El `id` del plugin debe ser **lowercase**, empezar con letra, y usar solo `[a-z0-9_-]`.
2. La `version` debe seguir **semver** (`MAJOR.MINOR.PATCH`).
3. `api_version` debe coincidir en major con la version del sistema (`1.0.0`).
4. El `entry_point` debe ser `module.path:ClassName`.
5. Las `capabilities` deben ser strings jerárquicos con dots (`code.generate`).
6. El método `manifest()` debe retornar siempre el mismo PluginManifest.

## Ciclo de Vida

1. **DISCOVERED**: El discovery escanea y encuentra el manifest.json.
2. **LOADED**: El PluginLoader importa e instancia la clase Plugin.
3. **INITIALIZED**: Se llama `initialize()` (setup de recursos).
4. **STARTED**: Se llama `start()` (el plugin está activo).
5. **ENABLED**: Los agentes/herramientas se registran en los registries.
6. **DISABLED**: Los agentes/herramientas se desregistran.
7. **UNLOADED**: El plugin se elimina del sistema.

## Integración con el Sistema

### Registro Automático

Cuando `get_agents()` retorna agentes, el `PluginManager` los registra automáticamente en el `AgentRegistry` con las capabilities del manifest del plugin. Esto significa que:

- El `CapabilityResolver` puede encontrarlos por capability.
- El `WorkflowEngine` puede asignarles steps de workflows.
- El `RegistryOrchestrator` puede orquestarlos.

### Eventos

Suscribirse a eventos del sistema:

```python
await manager.event_bus.subscribe(
    my_handler,
    event_type=PluginEvent.PLUGIN_ENABLED,
    filter_fn=lambda e: "agent" in e.plugin_id,
)
```

## Herramientas

Para exponer herramientas:

```python
from multiagent.plugins import Plugin, ToolPluginMixin, ToolDefinition

class MyToolPlugin(Plugin, ToolPluginMixin):
    def __init__(self):
        Plugin.__init__(self)
        ToolPluginMixin.__init__(self)
        self._register_tool(ToolDefinition(
            name="my_tool",
            description="Hace algo útil",
            category="custom",
        ))

    async def execute_tool(self, name: str, params: dict) -> dict:
        if name == "my_tool":
            # Lógica de la herramienta
            return {"status": "ok", "result": params}
        return {"status": "error", "error": f"Unknown tool: {name}"}
```

## Testing

```python
import pytest
from multiagent.plugins import Plugin, PluginManifest

class MyTestPlugin(Plugin):
    def manifest(self) -> PluginManifest:
        return PluginManifest(id="test", name="T", version="1.0.0")

def test_plugin_creation():
    plugin = MyTestPlugin()
    assert plugin.manifest().id == "test"
    assert plugin.state.value == "discovered"

@pytest.mark.asyncio
async def test_plugin_lifecycle():
    plugin = MyTestPlugin()
    await plugin.initialize()
    assert plugin.is_initialized
    await plugin.start()
    await plugin.shutdown()
```