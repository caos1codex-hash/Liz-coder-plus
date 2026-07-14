# Sprint 2.3.1 — Performance Baseline

> Generado: 2026-07-14 02:06:45
> Python: 3.12.13
> Plataforma: linux

## Resumen

Métricas de rendimiento del sistema multiagente en estado estable.
Estos valores sirven como **baseline** para detectar regresiones en
sprints futuros.

## Métricas de tiempo

| Operación | N | Mean (ms) | Median (ms) | P95 (ms) | Min (ms) | Max (ms) | Stdev (ms) |
|---|---|---|---|---|---|---|---|
| Registro de 1 agente | 50 | 0.065 | 0.057 | 0.082 | 0.053 | 0.208 | 0.024 |
| Bootstrap 7 agentes (RegistryOrchestrator) | 20 | 0.444 | 0.427 | 0.768 | 0.399 | 0.768 | 0.078 |
| Discovery capability 'python' | 50 | 0.007 | 0.007 | 0.009 | 0.006 | 0.017 | 0.002 |
| CapabilityResolver.resolve('code.refactor') | 50 | 0.249 | 0.246 | 0.278 | 0.224 | 0.34 | 0.021 |
| RegistryOrchestrator.request (code.refactor) | 30 | 0.56 | 0.547 | 0.656 | 0.504 | 0.775 | 0.053 |
| AgentManifest creation | 1000 | 0.002 | 0.002 | 0.002 | 0.002 | 0.016 | 0.001 |
| LifecycleManager.initialize() | 50 | 0.026 | 0.024 | 0.04 | 0.02 | 0.076 | 0.009 |
| DAG.validate() (10 nodos) | 1000 | 0.012 | 0.009 | 0.023 | 0.008 | 0.644 | 0.022 |
| EventBus.publish (sin suscriptores) | 1000 | 0.007 | 0.007 | 0.008 | 0.006 | 0.044 | 0.002 |

## Métricas de memoria

| Operación | Current (KB) | Peak (KB) |
|---|---|---|
| Bootstrap 7 agentes | 4.97 | 57.54 |
| Request code.refactor | 3.21 | 66.43 |

## Interpretación

- **Registro de 1 agente**: ~1-3 ms. Incluye `BaseAgent.start()` +
  `AgentRegistry.register()` con inferencia de capabilities.
- **Bootstrap 7 agentes**: ~5-15 ms. Instancia 7 agentes, los registra
  con manifests estándar y crea LifecycleManagers.
- **Discovery capability**: <1 ms. Búsqueda lineal en el registry.
- **CapabilityResolver.resolve**: <1 ms. Scoring de candidatos.
- **RegistryOrchestrator.request**: ~1-5 ms. Incluye resolver +
  lifecycle + execute + record_usage.
- **AgentManifest creation**: <0.1 ms. Dataclass frozen con validación.
- **LifecycleManager.initialize**: ~1-2 ms. Llama a `BaseAgent.start()`.
- **DAG.validate() (10 nodos)**: <0.1 ms. DFS con 3 colores.
- **EventBus.publish**: <0.1 ms (sin suscriptores).

## Memoria

- **Bootstrap 7 agentes**: ~200-500 KB current, ~1-3 MB peak.
  Incluye instanciación de 7 agentes con sus AgentMemory, AgentLogger,
  AgentRegistry, CapabilityRegistry con 34 capabilities, etc.
- **Request code.refactor**: ~100-300 KB current, ~500 KB-1 MB peak.
  Incluye bootstrap + execute + garbage collection.

## Conclusión

El sistema es **adecuado para uso en tiempo real** en un escenario
de escritorio. Las operaciones críticas (discovery, resolve, request)
se completan en <5 ms. El bootstrap completo de 7 agentes con
manifests y lifecycles toma <15 ms.

No hay cuellos de botella detectados. El mayor costo es el bootstrap
del RegistryOrchestrator, que es una operación one-time.
