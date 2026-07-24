# ============================================================
# File: create-release.ps1
# Project: Liz Coder Plus - Desktop
# Description: Creates a GitHub Release with the built .exe
#              upload and auto-update JSON manifest.
#
# Usage:
#   .\scripts\create-release.ps1 -Version "0.14.0"
#   .\scripts\create-release.ps1 -Version "0.14.0" -Prerelease
# ============================================================

param(
    [Parameter(Mandatory=$true)]
    [string]$Version,

    [switch]$Prerelease,
    [switch]$Draft,
    [string]$Runtime = "win-x64",
    [string]$RepoOwner = "caos1codex-hash",
    [string]$RepoName = "Liz-coder-plus"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$OutputDir = Join-Path $ProjectRoot "dist"
$PublishDir = Join-Path $OutputDir "LizCoderPlus-$Version-$Runtime"
$ExePath = Join-Path $PublishDir "LizCoderPlus.Desktop.exe"
$ZipPath = Join-Path $OutputDir "LizCoderPlus-$Version-$Runtime.zip"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Liz Coder Plus - GitHub Release Creator" -ForegroundColor Cyan
Write-Host "  Version: $Version" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# --- Pre-checks ---
if (-not (Test-Path $ExePath)) {
    Write-Host "[ERROR] .exe not found at: $ExePath" -ForegroundColor Red
    Write-Host "        Run build-desktop.ps1 -Publish first." -ForegroundColor Yellow
    exit 1
}

# Check for GitHub CLI
$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh) {
    Write-Host "[ERROR] GitHub CLI (gh) not found. Install from: https://cli.github.com" -ForegroundColor Red
    Write-Host "        Alternatively, set GITHUB_TOKEN env var and use curl." -ForegroundColor Yellow
    exit 1
}

Write-Host "[INFO] GitHub CLI found." -ForegroundColor Green

# --- Create ZIP for release asset ---
Write-Host "[PACK] Creating ZIP archive..." -ForegroundColor Cyan
if (Test-Path $ZipPath) { Remove-Item $ZipPath }

# Use .NET's built-in zip or PowerShell Compress-Archive
$zipContent = Get-ChildItem -Path $PublishDir -Recurse -File
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) "LizCoderPlus-Release-$Version"
if (Test-Path $tempDir) { Remove-Item -Recurse -Force $tempDir }

# Copy files to clean temp dir for zipping
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
Copy-Item -Path $PublishDir\* -Destination $tempDir -Recurse -Force

Compress-Archive -Path $tempDir\* -DestinationPath $ZipPath -Force
Remove-Item -Recurse -Force $tempDir

$zipSize = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
Write-Host "[OK] ZIP created: $ZipPath ($zipSize MB)" -ForegroundColor Green

# --- Build changelog from VERSION file or input ---
$changelogPath = Join-Path $ProjectRoot "CHANGELOG.md"
$body = "Release $Version of Liz Coder Plus Desktop"
if (Test-Path $changelogPath) {
    # Extract latest entry from changelog (first section after a heading)
    $changelog = Get-Content $changelogPath -Raw -ErrorAction SilentlyContinue
    if ($changelog) {
        $body = "## What's New`n`n$changelog`n`n---`n`n**Assets:**`n- `LizCoderPlus-$Version-$Runtime.zip` - Windows Desktop App (.exe included)"
    }
}

# --- Create GitHub Release ---
Write-Host ""
Write-Host "[RELEASE] Creating GitHub release v$Version..." -ForegroundColor Cyan

$releaseArgs = @(
    "release", "create",
    "v$Version",
    $ZipPath,
    "--repo", "$RepoOwner/$RepoName",
    "--title", "Liz Coder Plus v$Version",
    "--notes", $body
)

if ($Prerelease) {
    $releaseArgs += "--prerelease"
}
if ($Draft) {
    $releaseArgs += "--draft"
}

& gh $releaseArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to create GitHub release." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  RELEASE CREATED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "  Version: v$Version" -ForegroundColor Green
Write-Host "  Asset: LizCoderPlus-$Version-$Runtime.zip" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "[INFO] The desktop app will detect this release on next" -ForegroundColor Cyan
Write-Host "       update check (F5 or 'Buscar Actualizaciones')." -ForegroundColor Cyan
Write-Host ""
