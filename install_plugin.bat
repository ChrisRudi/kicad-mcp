@echo off
REM SPDX-License-Identifier: GPL-3.0-or-later
REM One-click installer for the "Claude fuer KiCad" action plugin (Windows).
REM Downloads the repo (git if present, else a ZIP) and copies the plugin into
REM KiCad's scripting-plugins dir. Optional arg 1 = KiCad version (default 10.0).
REM
REM Usage: double-click, or:  install_plugin.bat [10.0]
REM
REM IMPORTANT: every statement is SINGLE-LINE (no multi-line "( ... )" blocks,
REM no "^" line-continuation). cmd.exe mis-parses those when the file has LF
REM line endings -- which is exactly what GitHub's source ZIP delivers (the
REM .gitattributes eol=crlf is NOT applied to zip archives). Single-line flow
REM via goto/labels runs identically under LF and CRLF.

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
if exist "%~dp0plugin\claude_action.py" set "SRC=%~dp0plugin"
if defined SRC echo Lokale Plugin-Quelle gefunden: !SRC!
if defined SRC goto havesrc

REM --- Otherwise fetch the repo: git first, ZIP fallback --------------------
echo Lade Plugin von %REPO% (Branch %BRANCH%) ...
where git >nul 2>nul && git clone --depth 1 -b %BRANCH% "%REPO%.git" "%WORK%\src" >nul 2>nul
if exist "%WORK%\src\plugin\claude_action.py" set "SRC=%WORK%\src\plugin"
if defined SRC goto havesrc

REM ZIP fallback. %WORK% holds the user-temp path (C:\Users\ueser\... with an
REM umlaut). PowerShell reads it from the inherited environment ($env:WORK,
REM passed UTF-16) instead of an inlined %WORK% that cmd's OEM codepage folds.
echo git-Clone nicht moeglich - versuche ZIP-Download ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $w=$env:WORK; Invoke-WebRequest -Uri '%REPO%/archive/refs/heads/%BRANCH%.zip' -OutFile (Join-Path $w 'repo.zip') -UseBasicParsing; Expand-Archive -Path (Join-Path $w 'repo.zip') -DestinationPath (Join-Path $w 'unz') -Force; exit 0 } catch { exit 1 }"
if not exist "%WORK%\unz" goto dlfail
REM GitHub's zipball extracts to a deterministic "<repo>-<branch>" folder, so
REM set SRC directly -- a "for /d" loop is NOT LF-safe (cmd mis-parses it when
REM the .bat has LF endings, same failure class as multi-line blocks).
set "SRC=%WORK%\unz\kicad-mcp-%BRANCH%\plugin"
goto havesrc

:dlfail
echo.
echo FEHLER: Download fehlgeschlagen. Internet/Proxy pruefen oder Repo als
echo ZIP manuell herunterladen und install_plugin.bat aus dem Ordner starten.
pause
exit /b 1

:havesrc
if not exist "%SRC%\claude_action.py" goto nosrc

REM --- Target: %APPDATA%\kicad\<ver>\scripting\plugins\claude_kicad ----------
set "DEST=%APPDATA%\kicad\%VER%\scripting\plugins\%PKGNAME%"
echo.
echo Installiere nach: %DEST%
if exist "%DEST%" echo Vorherige Installation wird ersetzt ...
if exist "%DEST%" rmdir /S /Q "%DEST%"
mkdir "%DEST%" 2>nul
xcopy /E /I /Y /Q "%SRC%" "%DEST%" >nul
if errorlevel 1 goto copyfail

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
goto end

:nosrc
echo FEHLER: Plugin-Quelle nicht gefunden (%SRC%).
pause
exit /b 1

:copyfail
echo FEHLER: Kopieren fehlgeschlagen. Laeuft KiCad noch? Bitte schliessen
echo und erneut versuchen.
pause
exit /b 1

:end
endlocal
