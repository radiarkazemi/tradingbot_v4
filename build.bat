@echo off
setlocal enabledelayedexpansion
title TraderBot v4 — Build Script
color 0A

echo.
echo  ============================================================
echo    TraderBot v4 — EXE Build Script
echo  ============================================================
echo.

:: ── Step 1: Check Python ────────────────────────────────────────
echo  [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found.
    echo  Install Python 3.11 from https://python.org
    echo  Tick "Add Python to PATH" during install.
    pause & exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Found Python %PYVER%

:: Warn if Python 3.13 (MetaTrader5 does not support it yet)
echo %PYVER% | findstr /b "3.13" >nul
if not errorlevel 1 (
    echo.
    echo  WARNING: Python 3.13 detected.
    echo  MetaTrader5 Python library does NOT support Python 3.13 yet.
    echo  The EXE will build but may fail at runtime when connecting to MT5.
    echo  Recommended: install Python 3.11 from python.org alongside 3.13.
    echo  Then run:  py -3.11 -m pip install ... and use py -3.11 in this script.
    echo.
    echo  Continuing anyway — press Ctrl+C to cancel, or any key to continue.
    pause
)
echo.

:: ── Step 2: Install dependencies ────────────────────────────────
echo  [2/5] Installing/updating dependencies...
python -m pip install --upgrade pip --quiet --no-warn-script-location
python -m pip install pyinstaller --upgrade --quiet --no-warn-script-location
python -m pip install -r requirements.txt --quiet --no-warn-script-location
if errorlevel 1 (
    echo.
    echo  ERROR: pip install failed.
    echo  Check requirements.txt and your internet connection.
    pause & exit /b 1
)
echo  Done.
echo.

:: ── Step 3: Clean previous build ────────────────────────────────
echo  [3/5] Cleaning previous build...
if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist
echo  Done.
echo.

:: ── Step 4: Build EXE ───────────────────────────────────────────
echo  [4/5] Building EXE (2-5 minutes, please wait)...
echo.

:: Use "python -m PyInstaller" — works even when pyinstaller.exe is not on PATH
python -m PyInstaller traderbotv4.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo  ============================================================
    echo  ERROR: PyInstaller build failed.
    echo  Common fixes:
    echo    1. Missing package  → pip install ^<name^>
    echo    2. Antivirus block  → disable real-time protection, retry
    echo    3. Python 3.13      → switch to Python 3.11 (see above)
    echo  ============================================================
    pause & exit /b 1
)
echo.

:: ── Step 5: Verify ───────────────────────────────────────────────
echo  [5/5] Verifying output...
if not exist "dist\TraderBotV4\TraderBotV4.exe" (
    echo  ERROR: EXE not found in dist\TraderBotV4\
    pause & exit /b 1
)
echo  Done.
echo.
echo  ============================================================
echo  BUILD COMPLETE
echo  ============================================================
echo.
echo  EXE:  dist\TraderBotV4\TraderBotV4.exe
echo.
echo  Test it: double-click the EXE above before making installer.
echo.
echo  ── Make installer ──────────────────────────────────────────
echo  1. Install Inno Setup 6: https://jrsoftware.org/isinfo.php
echo  2. Open setup_installer.iss in Inno Setup
echo  3. Edit AppVersion + AppPublisher at top of that file
echo  4. Press F9 to compile
echo  5. Installer saved to: installer_output\
echo  ────────────────────────────────────────────────────────────
echo.
pause