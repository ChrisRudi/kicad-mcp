@echo off
REM Launch MCP server under KiCad's bundled Python (has pcbnew built-in).
REM
REM SINGLE-LINE statements only (no multi-line "(...)" blocks, no "for", no "^"
REM continuation): cmd.exe mis-parses those when the file has LF line endings,
REM which is what GitHub's source ZIP and the plugin copy deliver. Plain
REM single-line if/goto/set run identically under LF and CRLF.

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"

REM --- Locate KiCad's Python (10+ only; pre-10 lacks the IPC API) ---
set "KICAD_PY="
if defined KICAD_PYTHON_PATH if exist "%KICAD_PYTHON_PATH%" set "KICAD_PY=%KICAD_PYTHON_PATH%"
if not defined KICAD_PY if exist "C:\Program Files\KiCad\10.0\bin\python.exe" set "KICAD_PY=C:\Program Files\KiCad\10.0\bin\python.exe"
if not defined KICAD_PY if exist "D:\Program Files\KiCad\10.0\bin\python.exe" set "KICAD_PY=D:\Program Files\KiCad\10.0\bin\python.exe"
if not defined KICAD_PY goto nopy

REM --- Derive kicad-cli.exe from the Python path (same bin dir) without a
REM "for" loop: substring-replace python.exe -> kicad-cli.exe (LF-safe). ---
set "KICAD_CLI_PATH=!KICAD_PY:python.exe=kicad-cli.exe!"
if not exist "%KICAD_CLI_PATH%" goto nocli

set "_KICAD_MCP_RELAUNCHED=1"
"%KICAD_PY%" -u "%SCRIPT_DIR%main.py"
exit /b %ERRORLEVEL%

:nopy
echo KiCad 10 nicht gefunden - bitte installieren ^(erwartet unter "C:\Program Files\KiCad\10.0\"^). 1>&2
echo Alternativ Env-Variable KICAD_PYTHON_PATH auf python.exe setzen. 1>&2
exit /b 1

:nocli
echo kicad-cli.exe nicht gefunden unter "%KICAD_CLI_PATH%". 1>&2
exit /b 1
