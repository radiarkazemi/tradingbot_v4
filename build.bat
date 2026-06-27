@echo off
setlocal enabledelayedexpansion
title TraderBot v4 — Build Script
color 0A

echo.
echo  ============================================================
echo    TraderBot v4 — Build Script
echo  ============================================================
echo.
echo  Choose build type:
echo.
echo    [1] Standard build     (faster, less protected)
echo    [2] Obfuscated build   (slower, source encrypted — USE FOR DISTRIBUTION)
echo.
set /p CHOICE= Enter 1 or 2: 

echo.

:: ── Check Python ─────────────────────────────────────────────────
echo  [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install Python 3.11 from python.org
    pause & exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Found Python %PYVER%
echo.

:: ── Step 1: Inject secret ─────────────────────────────────────────
echo  [2/5] Injecting license secret...
if not exist inject_secret.py (
    echo  ERROR: inject_secret.py not found!
    echo  This file must exist on your developer machine.
    echo  It is gitignored and never committed to the repo.
    pause & exit /b 1
)
python inject_secret.py
if errorlevel 1 (
    echo  ERROR: Secret injection failed.
    pause & exit /b 1
)
echo.

:: ── Step 2: Install dependencies ─────────────────────────────────
echo  [3/5] Installing dependencies...
python -m pip install pyinstaller --upgrade --quiet --no-warn-script-location
python -m pip install -r requirements.txt --quiet --no-warn-script-location
echo  Done.
echo.

:: ── Step 3: Clean ────────────────────────────────────────────────
echo  [4/5] Cleaning previous build...
if exist build         rmdir /s /q build
if exist dist          rmdir /s /q dist
if exist _obf_build    rmdir /s /q _obf_build
echo  Done.
echo.

:: ── Step 4: Build ────────────────────────────────────────────────
echo  [5/5] Building...
echo.

if "%CHOICE%"=="2" (
    echo  Mode: OBFUSCATED ^(source encrypted^)
    echo  This takes 5-10 minutes...
    echo.
    python obfuscate_build.py
) else (
    echo  Mode: STANDARD
    python -m PyInstaller traderbotv4.spec --noconfirm --clean
)

if errorlevel 1 (
    echo.
    echo  ============================================================
    echo  ERROR: Build failed. See output above.
    echo  ============================================================
    pause & exit /b 1
)

:: ── Done ─────────────────────────────────────────────────────────
if not exist "dist\TraderBotV4\TraderBotV4.exe" (
    echo  ERROR: EXE not found in dist\TraderBotV4\
    pause & exit /b 1
)

echo.
echo  ============================================================
echo  BUILD COMPLETE
echo  ============================================================
echo.
echo  EXE:  dist\TraderBotV4\TraderBotV4.exe
echo.
echo  Next: Open setup_installer.iss in Inno Setup and press F9.
echo.
pause