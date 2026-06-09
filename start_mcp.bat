@echo off
REM Launch MCP server under KiCad's bundled Python (has pcbnew built-in).
REM If KiCad 10 is not installed, emit a clear error and exit.

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"

REM --- Locate KiCad's Python ---
set "KICAD_PY="

if defined KICAD_PYTHON_PATH (
    if exist "%KICAD_PYTHON_PATH%" set "KICAD_PY=%KICAD_PYTHON_PATH%"
)
REM KiCad 10+ only — pre-10 lacks the IPC API the server depends on.
if not defined KICAD_PY if exist "C:\Program Files\KiCad\10.0\bin\python.exe" set "KICAD_PY=C:\Program Files\KiCad\10.0\bin\python.exe"
if not defined KICAD_PY if exist "D:\Program Files\KiCad\10.0\bin\python.exe" set "KICAD_PY=D:\Program Files\KiCad\10.0\bin\python.exe"

if not defined KICAD_PY (
    echo KiCad 10 nicht gefunden - bitte installieren ^(erwartet unter "C:\Program Files\KiCad\10.0\"^). 1>&2
    echo Alternativ Env-Variable KICAD_PYTHON_PATH auf python.exe setzen. 1>&2
    exit /b 1
)

REM --- Derive kicad-cli.exe from Python location ---
for %%I in ("%KICAD_PY%") do set "KICAD_BIN_DIR=%%~dpI"
set "KICAD_CLI_PATH=%KICAD_BIN_DIR%kicad-cli.exe"

if not exist "%KICAD_CLI_PATH%" (
    echo kicad-cli.exe nicht gefunden unter "%KICAD_CLI_PATH%". 1>&2
    exit /b 1
)

set "_KICAD_MCP_RELAUNCHED=1"

"%KICAD_PY%" -u "%SCRIPT_DIR%main.py"
