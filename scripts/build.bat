@echo off
REM ============================================================
REM File: build.bat
REM Project: Liz Coder Plus - Desktop
REM Description: Double-click batch file to build the .exe.
REM              Opens a PowerShell window with the build script.
REM ============================================================

echo.
echo ============================================
echo   Liz Coder Plus - Desktop Builder
echo ============================================
echo.
echo   [1] Compilar (Debug)
echo   [2] Compilar (Release)
echo   [3] Generar .exe auto-contenido
echo   [4] Limpiar + Compilar .exe
echo   [5] Salir
echo.

set /p choice="Selecciona una opcion (1-5): "

if "%choice%"=="1" goto build_debug
if "%choice%"=="2" goto build_release
if "%choice%"=="3" goto publish
if "%choice%"=="4" goto clean_publish
if "%choice%"=="5" goto end

echo Opcion no valida.
goto end

:build_debug
echo.
echo Compilando en modo Debug...
powershell -ExecutionPolicy Bypass -File "%~dp0build-desktop.ps1"
pause
goto end

:build_release
echo.
echo Compilando en modo Release...
powershell -ExecutionPolicy Bypass -File "%~dp0build-desktop.ps1" -Release
pause
goto end

:publish
echo.
echo Generando .exe auto-contenido...
powershell -ExecutionPolicy Bypass -File "%~dp0build-desktop.ps1" -Publish
pause
goto end

:clean_publish
echo.
echo Limpiando y generando .exe...
powershell -ExecutionPolicy Bypass -File "%~dp0build-desktop.ps1" -Clean -Publish
pause
goto end

:end
