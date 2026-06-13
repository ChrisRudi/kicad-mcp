#!/bin/bash
# Launch MCP server on native Linux / macOS.
# For WSL -> use start_mcp_wsl.sh (different path conventions).
# If KiCad 10 is not installed, emit a clear error and exit.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Locate a Python that can import pcbnew ---
uname_s="$(uname -s)"
CANDIDATES=("${KICAD_PYTHON_PATH:-}")

case "$uname_s" in
    Darwin)
        CANDIDATES+=(
            "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
            "/Applications/KiCad/KiCad.app/Contents/MacOS/python"
        )
        HINT="KiCad 10 unter /Applications installieren oder KICAD_PYTHON_PATH setzen."
        ;;
    Linux)
        # Native Linux: KiCad's pcbnew is installed into system Python via
        # distro packages (e.g. apt 'kicad', 'python3-pcbnew').
        CANDIDATES+=(
            "/usr/bin/python3"
            "/usr/local/bin/python3"
            "/opt/kicad/bin/python3"
        )
        HINT="KiCad 10 installieren ('sudo apt install kicad' o.ae.) oder KICAD_PYTHON_PATH setzen."
        ;;
    *)
        echo "Nicht unterstuetzte Plattform: $uname_s" >&2
        echo "Fuer WSL verwende start_mcp_wsl.sh; fuer Windows start_mcp.bat." >&2
        exit 1
        ;;
esac

KICAD_PY=""
for c in "${CANDIDATES[@]}"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    if "$c" -c 'import pcbnew' 2>/dev/null; then
        KICAD_PY="$c"
        break
    fi
done

if [ -z "$KICAD_PY" ]; then
    echo "Kein Python mit 'pcbnew' gefunden." >&2
    echo "$HINT" >&2
    exit 1
fi

export _KICAD_MCP_RELAUNCHED=1
exec "$KICAD_PY" -u "$SCRIPT_DIR/main.py"
