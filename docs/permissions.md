# Sistema de permisos — Liz Coder Plus

> Define cómo Liz Coder Plus decide si una acción puede ejecutarse
> automáticamente o requiere confirmación explícita del usuario.

## 1. Motivación

Un asistente que puede ejecutar comandos del sistema es poderoso, pero
también peligroso. Liz Coder Plus implementa un sistema de permisos que
**falla hacia la seguridad**: ante la duda, siempre pregunta.

## 2. Modos de permiso

El sistema define dos modos en el enumerado `PermissionMode`:

| Modo           | Comportamiento                                                              |
|----------------|-----------------------------------------------------------------------------|
| `Confirmation` | Pregunta al usuario antes de **cualquier** acción.                          |
| `Automatic`    | Ejecuta automáticamente los comandos de `allowed_commands`; el resto pregunta. |

El modo se configura en `config/{environment}.json` bajo
`permissions.mode`, y se carga al arrancar el backend.

## 3. Listas de comandos

Cada entorno define dos listas:

- **`allowed_commands`**: comandos seguros que pueden ejecutarse
  automáticamente cuando el modo es `Automatic`. En modo `Confirmation`
  esta lista se ignora (todo pregunta).
- **`denied_commands`**: comandos siempre bloqueados, **incluso si
  aparecen en `allowed_commands`**. Útil para listar destructores
  globales como `rm -rf`, `format`, `shutdown`, etc.

## 4. Algoritmo de decisión

El `PermissionService.evaluate(command)` sigue este orden:

```
1. ¿Comando está en denied_commands?
   → DENY

2. ¿Modo actual es Automatic?
   → Sí: ¿Comando está en allowed_commands?
        → ALLOW
        → si no: CONFIRM
   → No (Confirmation):
        → CONFIRM
```

El resultado es un `PermissionDecision` con:

- `decision`: `"allow"` | `"confirm"` | `"deny"`.
- `reason`: texto explicativo.
- `mode`: modo activo cuando se evaluó.

## 5. Flujo con el usuario

1. Un agente decide que necesita ejecutar una herramienta.
2. El orquestador consulta `PermissionService.evaluate(tool_name)`.
3. Si `allow` → ejecuta.
4. Si `confirm` → el backend publica un evento `permission.prompted`,
   el desktop muestra un diálogo, y el usuario acepta o cancela.
5. Si `deny` → no se ejecuta y se registra en el log.

## 6. Auditoría

Cada decisión se publica en el `EventBus`:

- `permission.prompted` — se pidió confirmación.
- `permission.granted` — el usuario aceptó.
- `permission.revoked` — el usuario canceló.

Estos eventos quedan registrados para auditoría futura.

## 7. Implementación

- **Backend:** `apps/backend/src/permissions/modes.py` (enum) y
  `apps/backend/src/permissions/service.py` (lógica).
- **Shared:** `packages/shared/src/enums.py` (enum duplicado para
  uso cruzado entre paquetes).
- **Desktop:** leerá el modo desde `AppSettings.Permissions.Mode` y
  mostrará los diálogos correspondientes (Sprint 4).

## 8. Buenas prácticas para el usuario

- Mantén el modo `Confirmation` salvo que confíes plenamente en
  tus comandos whitelistados.
- Revisa `denied_commands` antes de añadir nada a `allowed_commands`.
- En producción, deja `allowed_commands` casi vacío; en desarrollo
  puedes ser más permisivo para iterar rápido.
