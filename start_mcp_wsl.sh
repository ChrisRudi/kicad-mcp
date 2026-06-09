#!/bin/bash
# Launch MCP server under KiCad's bundled Python (has pcbnew built-in).
# If KiCad 10 is not installed, emit a clear error and exit.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Locate KiCad's Python ---
# KiCad 10+ only — pre-10 lacks the IPC API the server depends on.
KICAD_PY=""
CANDIDATES=(
    "${KICAD_PYTHON_PATH:-}"
    "/mnt/c/Program Files/KiCad/10.0/bin/python.exe"
    "/mnt/d/Program Files/KiCad/10.0/bin/python.exe"
)
for c in "${CANDIDATES[@]}"; do
    [ -n "$c" ] && [ -x "$c" ] && KICAD_PY="$c" && break
done

if [ -z "$KICAD_PY" ]; then
    echo "KiCad 10 nicht gefunden - bitte installieren (erwartet unter 'C:\\Program Files\\KiCad\\10.0\\')." >&2
    echo "Alternativ Env-Variable KICAD_PYTHON_PATH auf python.exe setzen." >&2
    exit 1
fi

# --- Derive matching kicad-cli.exe from Python location ---
KICAD_BIN_DIR="$(dirname "$KICAD_PY")"
KICAD_CLI_WSL="$KICAD_BIN_DIR/kicad-cli.exe"
if [ ! -x "$KICAD_CLI_WSL" ]; then
    echo "kicad-cli.exe nicht gefunden unter '$KICAD_CLI_WSL'." >&2
    exit 1
fi

# Convert WSL path -> Windows path (Python.exe needs Windows paths for its args)
wsl_to_win() {
    local p="$1"
    # /mnt/c/... -> C:\...
    if [[ "$p" =~ ^/mnt/([a-z])/(.*)$ ]]; then
        local drive="${BASH_REMATCH[1]^^}"
        local rest="${BASH_REMATCH[2]//\//\\}"
        echo "${drive}:\\${rest}"
    else
        echo "$p"
    fi
}

export KICAD_CLI_PATH="$(wsl_to_win "$KICAD_CLI_WSL")"
export _KICAD_MCP_RELAUNCHED=1
export WSLENV="KICAD_CLI_PATH:_KICAD_MCP_RELAUNCHED"

MAIN_WIN="$(wsl_to_win "$SCRIPT_DIR/main.py")"

exec "$KICAD_PY" -u "$MAIN_WIN"
