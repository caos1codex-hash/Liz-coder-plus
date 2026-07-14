# Tests

> Pruebas automatizadas de Liz Coder Plus.

## Estructura

```
tests/
├── unit/           → Pruebas unitarias por módulo
├── integration/    → Pruebas de integración entre módulos
└── e2e/            → Pruebas end-to-end (futuro)
```

## Ejecución

### Backend (Python)

```bash
cd apps/backend
pytest -v --cov=src
```

### Desktop (.NET)

```bash
cd apps/desktop
dotnet test
```

## Cobertura

- **Objetivo:** 80% en `packages/` y `apps/backend/`.
- Los stubs de Sprint 1 no requieren cobertura; se excluyen con `# pragma: no cover`
  cuando se implementen.

## Convenciones de nombres

- Archivos: `test_<modulo>.py` (Python), `<Module>Tests.cs` (C#).
- Funciones: `test_<unidad>_<escenario>_<resultado_esperado>`.
