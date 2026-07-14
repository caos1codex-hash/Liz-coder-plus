# Config

> Configuración por entorno del proyecto Liz Coder Plus.

## Archivos

| Archivo              | Entorno      | Uso                                  |
|----------------------|--------------|--------------------------------------|
| `development.json`   | Desarrollo   | Desarrollo local, logs verbosos      |
| `production.json`    | Producción   | Despliegue final, logs restrictivos  |

## Selección de entorno

El backend carga el archivo según la variable de entorno `LIZ_ENV`:

```bash
# Windows (PowerShell)
$env:LIZ_ENV = "development"

# Linux / macOS
export LIZ_ENV=production
```

Si `LIZ_ENV` no está definida, se usa `development` por defecto.

## Esquema

Cada archivo JSON contiene:

| Campo            | Descripción                                                  |
|------------------|--------------------------------------------------------------|
| `version`        | Versión del proyecto (debe coincidir con `VERSION`).         |
| `environment`    | Nombre del entorno (`development` / `production`).           |
| `ai`             | Proveedor, modelo y parámetros del LLM.                      |
| `permissions`    | Modo de permisos y listas blancas / negras de comandos.      |
| `logging`        | Nivel y destino de logs.                                     |
| `backend`        | Host, puerto y CORS del servidor FastAPI.                    |
| `memory`         | Backend de memoria y URL de la base de datos.                |

## Permisos

El campo `permissions.mode` admite dos valores:

- **`Confirmation`** — pregunta al usuario antes de cualquier acción.
- **`Automatic`** — ejecuta automáticamente los comandos de
  `allowed_commands`; todo lo demás requiere confirmación.

Los comandos de `denied_commands` siempre se bloquean, sin importar el modo.

## Seguridad

- **Nunca** almacenes secretos (API keys, contraseñas) en estos archivos.
- Las API keys se leen de variables de entorno indicadas por
  `ai.api_key_env`.
- Añade `config/secrets.json` a `.gitignore` (ya incluido).
