# Run the Liz Coder Plus backend in development mode (Windows PowerShell).
$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location (Join-Path $Root "apps\backend")

if (-not (Test-Path ".venv")) {
    Write-Error "Virtual environment not found. Run scripts\setup_dev.py first."
    exit 1
}

& .\.venv\Scripts\Activate.ps1

if (-not $env:LIZ_ENV) {
    $env:LIZ_ENV = "development"
}

Write-Host "Starting backend (env=$env:LIZ_ENV)..."
uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
