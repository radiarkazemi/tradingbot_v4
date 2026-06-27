@echo off
setlocal enabledelayedexpansion
title TraderBot v4 — Release Builder
color 0A

echo.
echo  ============================================================
echo    TraderBot v4 — Full Release Builder
echo  ============================================================
echo.
echo  This will automatically:
echo    1. Inject license secret
echo    2. Build the EXE
echo    3. Create the installer
echo.
echo  Press any key to start, or Ctrl+C to cancel.
pause >nul
echo.

:: ── Check Python ─────────────────────────────────────────────────
echo  [1/4] Checking requirements...

python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found.
    echo  Install Python 3.11 from python.org ^(tick "Add to PATH"^)
    echo.
    pause & exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo        Python %PYVER% ... OK

if not exist inject_secret.py (
    echo.
    echo  ERROR: inject_secret.py not found.
    echo  This file contains your secret key and must exist on this machine.
    echo.
    pause & exit /b 1
)
echo        inject_secret.py ... OK

if not exist traderbotv4.spec (
    echo.
    echo  ERROR: traderbotv4.spec not found.
    echo.
    pause & exit /b 1
)
echo        traderbotv4.spec ... OK

:: Check Inno Setup — search all common locations
set INNO=
set SKIP_INSTALLER=1

for %%P in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
    "C:\Program Files (x86)\Inno Setup 5\ISCC.exe"
    "C:\Program Files\Inno Setup 5\ISCC.exe"
    "D:\Inno Setup 6\ISCC.exe"
    "D:\Inno Setup 5\ISCC.exe"
    "C:\Inno Setup 6\ISCC.exe"
) do (
    if exist %%P (
        set INNO=%%P
        set SKIP_INSTALLER=0
    )
)

:: Also try registry lookup
if not defined INNO (
    for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" /s /f "Inno Setup" /k 2^>nul ^| findstr /i "InstallLocation" 2^>nul') do (
        if exist "%%b\ISCC.exe" (
            set INNO="%%b\ISCC.exe"
            set SKIP_INSTALLER=0
        )
    )
)

:: Try PATH
if not defined INNO (
    where ISCC.exe >nul 2>&1
    if not errorlevel 1 (
        set INNO=ISCC.exe
        set SKIP_INSTALLER=0
    )
)

if "%SKIP_INSTALLER%"=="1" (
    echo        Inno Setup ... NOT FOUND ^(installer step will be skipped^)
    echo        Download from: https://jrsoftware.org/isinfo.php
) else (
    echo        Inno Setup ... OK  ^(%INNO%^)
)
echo.

:: ── Step 1: Inject secret ─────────────────────────────────────────
echo  [2/4] Injecting license secret...
python inject_secret.py
if errorlevel 1 (
    echo.
    echo  ERROR: Secret injection failed.
    pause & exit /b 1
)
echo.

:: ── Step 2: Install deps + build EXE ─────────────────────────────
echo  [3/4] Building EXE...
echo.

python -m pip install pyinstaller --upgrade --quiet --no-warn-script-location
python -m pip install -r requirements.txt --quiet --no-warn-script-location

if exist build      rmdir /s /q build
if exist dist       rmdir /s /q dist

python -m PyInstaller traderbotv4.spec --noconfirm --clean

if errorlevel 1 (
    echo.
    echo  ERROR: EXE build failed. See output above.
    pause & exit /b 1
)

if not exist "dist\TraderBotV4\TraderBotV4.exe" (
    echo  ERROR: EXE not found after build.
    pause & exit /b 1
)
echo.
echo        EXE created OK
echo.

:: ── Step 3: Create installer ──────────────────────────────────────
:: Delete old installer first to avoid "file in use" error
if exist "installer_output\TraderBotV4_Setup_v4.0.0.exe" (
    del /f /q "installer_output\TraderBotV4_Setup_v4.0.0.exe" >nul 2>&1
)

if "%SKIP_INSTALLER%"=="1" (
    echo  [4/4] Skipping installer ^(Inno Setup not installed^)
    echo.
    echo  ============================================================
    echo  PARTIAL COMPLETE — EXE only
    echo  ============================================================
    echo.
    echo  EXE:  dist\TraderBotV4\TraderBotV4.exe
    echo.
    echo  To create the installer, install Inno Setup from:
    echo  https://jrsoftware.org/isinfo.php
    echo  Then run release.bat again.
    echo.
    pause & exit /b 0
)

echo  [4/4] Creating installer...
echo.

if exist installer_output rmdir /s /q installer_output

%INNO% setup_installer.iss

if errorlevel 1 (
    echo.
    echo  ERROR: Installer creation failed.
    pause & exit /b 1
)

:: ── Find the installer file ───────────────────────────────────────
for %%f in (installer_output\*.exe) do set INSTALLER=%%f

echo.
echo  ============================================================
echo  RELEASE COMPLETE
echo  ============================================================
echo.
echo  EXE:        dist\TraderBotV4\TraderBotV4.exe
echo  Installer:  %INSTALLER%
echo.
echo  Send the installer file to your users.
echo  ============================================================
echo.
pause