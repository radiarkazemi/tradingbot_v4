@echo off
setlocal enabledelayedexpansion
title TraderBot v4 — Publish Update
color 0A

echo.
echo  ============================================================
echo    TraderBot v4 — Publish Update to Existing Users
echo  ============================================================
echo.
echo  This assumes you already ran release.bat and have a fresh
echo  installer in installer_output\
echo.

set /p NEWVER= Enter the new version number (e.g. 4.1.0): 
if "%NEWVER%"=="" (
    echo  ERROR: Version number required.
    pause & exit /b 1
)

set /p NOTES= Enter a short release note (e.g. "Fixed R2 SL bug"): 

echo.
echo  [1/3] Checking installer exists...
if not exist "installer_output\TraderBotV4_Setup_v4.0.0.exe" (
    echo  Looking for any installer in installer_output\...
    for %%f in (installer_output\*.exe) do set FOUND=%%f
    if not defined FOUND (
        echo  ERROR: No installer found in installer_output\
        echo  Run release.bat first.
        pause & exit /b 1
    )
)

REM Rename installer to match new version
set NEWNAME=TraderBotV4_Setup_v%NEWVER%.exe
echo  Renaming installer to %NEWNAME%...
for %%f in (installer_output\*.exe) do (
    copy /y "%%f" "installer_output\%NEWNAME%" >nul
)

echo.
echo  [2/3] Updating version.json...

REM Write new version.json
(
echo {
echo   "version": "%NEWVER%",
echo   "download_url": "https://github.com/radiarkazemi/tradingbot_v4/releases/download/v%NEWVER%/%NEWNAME%",
echo   "release_notes": "%NOTES%",
echo   "min_version": "4.0.0"
echo }
) > version.json

echo  version.json updated.
echo.

echo  [3/3] Next steps — do these manually on GitHub:
echo.
echo    1. Go to: https://github.com/radiarkazemi/tradingbot_v4/releases/new
echo    2. Tag version: v%NEWVER%
echo    3. Upload file: installer_output\%NEWNAME%
echo    4. Publish the release
echo    5. Commit and push version.json:
echo         git add version.json
echo         git commit -m "Release v%NEWVER%"
echo         git push
echo.
echo  Once pushed, EVERY existing installation will automatically
echo  detect the update within a few hours and prompt the user to
echo  click "Update Now" — their settings, license, and trade
echo  history are all preserved (stored in %%APPDATA%%, untouched
echo  by the installer).
echo.
pause