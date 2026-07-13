# Scripts

> Scripts de utilidad para desarrollo, build y despliegue.

## Contenido

| Script            | Descripción                                              |
|-------------------|----------------------------------------------------------|
| `setup_dev.py`    | Prepara entorno de desarrollo (crea venv, instala deps). |
| `run_backend.sh`  | Inicia el backend en modo desarrollo.                    |
| `run_backend.ps1` | Igual que arriba, para Windows PowerShell.               |
| `lint.sh`         | Ejecuta ruff + mypy sobre el backend y packages.         |

## Uso

### Linux / macOS

```bash
./scripts/setup_dev.py
./scripts/run_backend.sh
```

### Windows (PowerShell)

```powershell
python scripts\setup_dev.py
.\scripts\run_backend.ps1
```
