# Convenciones del proyecto — Liz Coder Plus

> Reglas que mantienen el código consistente y colaborable.

## 1. Idiomas

| Contexto                  | Idioma     |
|---------------------------|------------|
| Código (nombres, vars)    | Inglés     |
| Comentarios técnicos      | Inglés     |
| Documentación de usuario  | Español    |
| Mensajes de commit        | Inglés     |
| Issues y PRs              | Español o inglés (según audiencia) |
| Logs del sistema          | Inglés     |

## 2. Estilo de código

### Python (backend y packages)

- **Python 3.11+** obligatorio.
- **Tipado estático** con anotaciones en todas las funciones públicas.
- **Async/await** por defecto para operaciones I/O.
- **Ruff** para linting (`line-length = 100`).
- **mypy --strict** para chequeo de tipos.
- **Pydantic v2** para todos los modelos de API.

### C# (desktop)

- **.NET 8** + **C# 12**.
- **`Nullable enable`** obligatorio.
- **File-scoped namespaces**.
- **`record`** para modelos inmutables cuando aplique.
- **CommunityToolkit.Mvvm** `[ObservableProperty]` y `[RelayCommand]`.

## 3. Estructura de archivos

- Un módulo por archivo cuando sea razonable.
- Nombres de archivo en `snake_case.py` (Python) o `PascalCase.cs` (C#).
- Cada paquete expone su API pública desde `__init__.py`.

## 4. Commits — Conventional Commits

Formato: `type(scope): description`

### Tipos permitidos

| Tipo       | Uso                                              |
|------------|--------------------------------------------------|
| `feat`     | Nueva funcionalidad.                             |
| `fix`      | Corrección de bug.                               |
| `docs`     | Solo documentación.                              |
| `style`    | Formato, sin cambios de código.                  |
| `refactor`| Refactor sin cambio de comportamiento.           |
| `perf`     | Mejora de rendimiento.                           |
| `test`     | Añadir o corregir pruebas.                       |
| `chore`    | Tareas de mantenimiento, dependencias, etc.      |
| `ci`       | Cambios de CI/CD.                                |
| `build`    | Cambios de build o empaquetado.                  |

### Ejemplos

```
feat(permissions): add PermissionService with allow/confirm/deny logic
fix(memory): close SQLite connection on shutdown
docs(architecture): add data flow diagram
chore: initialize Liz Coder Plus architecture v0.1.1
```

## 5. Ramas

| Rama          | Uso                                              |
|---------------|--------------------------------------------------|
| `main`        | Estable, siempre desplegable.                    |
| `develop`     | Integración de features en curso.                |
| `feature/*`   | Desarrollo de una funcionalidad.                 |
| `fix/*`       | Corrección de un bug.                            |
| `release/*`   | Preparación de una release.                      |
| `hotfix/*`    | Parche urgente sobre `main`.                     |

## 6. Pull Requests

- Título en formato Conventional Commit.
- Descripción con: qué cambia, por qué, cómo probarlo.
- Al menos un aprobador para merger a `develop`.
- CI verde obligatorio (cuando exista).

## 7. Pruebas

- **Cobertura mínima objetivo:** 80% en packages y backend.
- **Pirámide:** muchas unit tests, pocas e2e.
- **Nombres:** `test_<unidad>_<escenario>_<resultado_esperado>`.

## 8. Seguridad

- **Nunca** hardcodear secretos en código o config.
- **API keys** en variables de entorno referenciadas por `api_key_env`.
- **`denied_commands`** se revisa en cada PR que toque permisos.
- **Logs** nunca deben contener datos sensibles.

## 9. Versionado — SemVer

Formato: `MAJOR.MINOR.PATCH`

- **MAJOR:** cambios incompatibles.
- **MINOR:** nuevas funcionalidades compatibles.
- **PATCH:** correcciones compatibles.

El archivo `VERSION` en la raíz contiene la versión actual sin prefijo
`v`. Los tags Git usan el prefijo `v` (ej. `v0.1.1`).

## 10. Documentación

- **Cada paquete** tiene su propio `README.md`.
- **Cambios arquitectónicos** se reflejan en `docs/architecture.md`.
- **El roadmap** se actualiza al final de cada prompt.
- **Comentarios en código:** explican *por qué*, no *qué*.
