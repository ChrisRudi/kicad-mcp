@echo off
REM SPDX-License-Identifier: GPL-3.0-or-later
REM One-click installer for the "Claude fuer KiCad" action plugin (Windows).
REM Downloads the repo (git if present, else a ZIP) and copies the plugin into
REM KiCad's scripting-plugins dir. Optional arg 1 = KiCad version (default 10.0).
REM
REM Usage: double-click, or:  install_plugin.bat [10.0]

setlocal EnableDelayedExpansion
chcp 65001 >nul
title Claude fuer KiCad - Installer

set "REPO=https://github.com/ChrisRudi/kicad-mcp"
set "BRANCH=main"
set "PKGNAME=claude_kicad"
set "VER=%~1"
if "%VER%"=="" set "VER=10.0"

set "WORK=%TEMP%\kicad_claude_install"
rmdir /S /Q "%WORK%" 2>nul
mkdir "%WORK%" 2>nul

set "SRC="

REM --- If run from inside a repo checkout, use the local plugin\ directly ----
if exist "%~dp0plugin\claude_action.py" (
    set "SRC=%~dp0plugin"
    echo Lokale Plugin-Quelle gefunden: !SRC!
    goto :havesrc
)

REM --- Otherwise fetch the repo: git first, ZIP fallback ---------------------
echo Lade Plugin von %REPO% (Branch %BRANCH%) ...
where git >nul 2>nul
if %ERRORLEVEL%==0 (
    git clone --depth 1 -b %BRANCH% "%REPO%.git" "%WORK%\src" >nul 2>nul
    if exist "%WORK%\src\plugin\claude_action.py" (
        set "SRC=%WORK%\src\plugin"
        goto :havesrc
    )
    echo git-Clone nicht moeglich - versuche ZIP-Download ...
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Invoke-WebRequest -Uri '%REPO%/archive/refs/heads/%BRANCH%.zip' -OutFile '%WORK%\repo.zip' -UseBasicParsing; Expand-Archive -Path '%WORK%\repo.zip' -DestinationPath '%WORK%\unz' -Force; exit 0 } catch { exit 1 }"
if not exist "%WORK%\unz" (
    echo.
    echo FEHLER: Download fehlgeschlagen. Internet/Proxy pruefen oder Repo als
    echo ZIP manuell herunterladen und install_plugin.bat aus dem Ordner starten.
    pause & exit /b 1
)
for /d %%D in ("%WORK%\unz\*") do set "SRC=%%D\plugin"

:havesrc
if not exist "%SRC%\claude_action.py" (
    echo FEHLER: Plugin-Quelle nicht gefunden (%SRC%). & pause & exit /b 1
)

REM --- Target: %APPDATA%\kicad\<ver>\scripting\plugins\claude_kicad ----------
set "DEST=%APPDATA%\kicad\%VER%\scripting\plugins\%PKGNAME%"
echo.
echo Installiere nach: %DEST%
if exist "%DEST%" (
    echo Vorherige Installation wird ersetzt ...
    rmdir /S /Q "%DEST%"
)
mkdir "%DEST%" 2>nul
xcopy /E /I /Y /Q "%SRC%" "%DEST%" >nul
if %ERRORLEVEL% neq 0 (
    echo FEHLER: Kopieren fehlgeschlagen. Laeuft KiCad noch? Bitte schliessen
    echo und erneut versuchen. & pause & exit /b 1
)

rmdir /S /Q "%WORK%" 2>nul

echo.
echo ============================================================
echo  Fertig. Plugin installiert (KiCad %VER%).
echo.
echo  Naechste Schritte in KiCad (PCB-Editor / pcbnew):
echo    1) Werkzeuge -^> Externe Plugins -^> Aktualisieren
echo       (oder KiCad einmal neu starten)
echo    2) Den "Claude"-Button in der Toolbar klicken
echo    3) Das Einrichtungs-Panel fuehrt durch den Rest
echo       (Claude Code installieren, Login, Abhaengigkeiten, IPC)
echo ============================================================
pause
endlocal
