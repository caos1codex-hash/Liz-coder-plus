# ============================================================
# File: build-desktop.ps1
# Project: Liz Coder Plus - Desktop
# Description: PowerShell build script to publish .exe.
#              Compiles WinUI 3 app as self-contained .exe.
#
# Usage:
#   .\scripts\build-desktop.ps1          # Build debug
#   .\scripts\build-desktop.ps1 -Release # Build release
#   .\scripts\build-desktop.ps1 -Publish  # Publish self-contained .exe
# ============================================================

param(
    [switch]$Release,
    [switch]$Publish,
    [switch]$Clean,
    [string]$Version = "0.13.0",
    [string]$Runtime = "win-x64"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --- Config ---
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$DesktopDir = Join-Path $ProjectRoot "apps" "desktop"
$ProjectFile = Join-Path $DesktopDir "LizCoderPlus.Desktop.csproj"
$OutputDir = Join-Path $ProjectRoot "dist"
$ArtifactsDir = Join-Path $OutputDir "artifacts"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Liz Coder Plus - Desktop Build Script" -ForegroundColor Cyan
Write-Host "  Version: $Version" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# --- Pre-checks ---
$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $dotnet) {
    Write-Host "[ERROR] .NET SDK not found. Install from https://dotnet.microsoft.com/download" -ForegroundColor Red
    exit 1
}

$dotnetVersion = & dotnet --version 2>&1
Write-Host "[INFO] .NET SDK version: $dotnetVersion" -ForegroundColor Green

# Check for Windows App SDK
$winAppSdk = & dotnet --list-sdks 2>&1 | Select-String -Pattern "WindowsAppSDK" -Quiet
if (-not $winAppSdk) {
    Write-Host "[WARN] Windows App SDK workloads may not be installed." -ForegroundColor Yellow
    Write-Host "       Run: dotnet workload install wasm-tools" -ForegroundColor Yellow
}

# --- Clean ---
if ($Clean) {
    Write-Host "[CLEAN] Removing old build artifacts..." -ForegroundColor Yellow
    if (Test-Path $OutputDir) {
        Remove-Item -Recurse -Force $OutputDir
    }
    & dotnet clean $ProjectFile -c Release 2>&1 | Out-Null
    & dotnet clean $ProjectFile -c Debug 2>&1 | Out-Null
    Write-Host "[OK] Clean complete." -ForegroundColor Green
}

# --- Build ---
$configuration = if ($Release -or $Publish) { "Release" } else { "Debug" }

Write-Host ""
Write-Host "[BUILD] Configuration: $configuration" -ForegroundColor Cyan
Write-Host "[BUILD] Project: $ProjectFile" -ForegroundColor Cyan

if ($Publish) {
    Write-Host "[PUBLISH] Building self-contained .exe ($Runtime)..." -ForegroundColor Cyan
    Write-Host ""

    & dotnet publish $ProjectFile `
        -c $configuration `
        -r $Runtime `
        --self-contained true `
        -p:PublishSingleFile=true `
        -p:IncludeNativeLibrariesForSelfExtract=true `
        -p:PublishTrimmed=true `
        -p:TrimUnusedDependencies=true `
        -p:EnableCompressionInSingleFile=true `
        -p:Version=$Version `
        -p:ApplicationVersion=$Version `
        -o (Join-Path $OutputDir "LizCoderPlus-$Version-$Runtime")

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Build failed!" -ForegroundColor Red
        exit 1
    }

    $exePath = Join-Path $OutputDir "LizCoderPlus-$Version-$Runtime" "LizCoderPlus.Desktop.exe"
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  BUILD SUCCESS!" -ForegroundColor Green
    Write-Host "  Output: $exePath" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green

    # Show file size
    if (Test-Path $exePath) {
        $size = (Get-Item $exePath).Length / 1MB
        Write-Host "[INFO] .exe size: $([math]::Round($size, 2)) MB" -ForegroundColor Cyan
    }

} else {
    & dotnet build $ProjectFile -c $configuration

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Build failed!" -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  BUILD SUCCESS!" -ForegroundColor Green
    Write-Host "  Use -Publish flag to generate self-contained .exe" -ForegroundColor Yellow
    Write-Host "============================================" -ForegroundColor Green
}

Write-Host ""
