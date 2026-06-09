#!/bin/bash
# One-shot installer for the KiCad MCP server (Linux / WSL / macOS).
# - Detects platform, picks the right launcher
# - Verifies KiCad 10 is reachable
# - Registers the server with Claude Code (if `claude` CLI is available)
# - Prints ready-to-paste snippets for all other MCP clients

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- 1. Detect platform ---
uname_s="$(uname -s)"
is_wsl=0
if [[ "$uname_s" == "Linux" ]] && [[ -d /mnt/c/Windows ]]; then
    is_wsl=1
fi

case "$uname_s" in
    Darwin)
        PLATFORM="macOS"
        LAUNCHER="$SCRIPT_DIR/start_mcp.sh"
        CANDIDATES=(
            "${KICAD_PYTHON_PATH:-}"
            "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
            "/Applications/KiCad/KiCad.app/Contents/MacOS/python"
        )
        HINT="KiCad 10 unter /Applications installieren oder KICAD_PYTHON_PATH setzen."
        ;;
    Linux)
        if [[ $is_wsl -eq 1 ]]; then
            PLATFORM="WSL"
            LAUNCHER="$SCRIPT_DIR/start_mcp_wsl.sh"
            CANDIDATES=(
                "${KICAD_PYTHON_PATH:-}"
                "/mnt/c/Program Files/KiCad/10.0/bin/python.exe"
                "/mnt/c/Program Files/KiCad/9.0/bin/python.exe"
                "/mnt/d/Program Files/KiCad/10.0/bin/python.exe"
            )
            HINT="KiCad 10 unter Windows installieren (erwartet: 'C:\\Program Files\\KiCad\\10.0\\') oder KICAD_PYTHON_PATH setzen."
        else
            PLATFORM="Linux"
            LAUNCHER="$SCRIPT_DIR/start_mcp.sh"
            CANDIDATES=(
                "${KICAD_PYTHON_PATH:-}"
                "/usr/bin/python3"
                "/usr/local/bin/python3"
                "/opt/kicad/bin/python3"
            )
            HINT="KiCad 10 installieren ('sudo apt install kicad' o.ae.) oder KICAD_PYTHON_PATH setzen."
        fi
        ;;
    *)
        echo "FEHLER: Nicht unterstuetzte Plattform: $uname_s" >&2
        exit 1
        ;;
esac

echo ">> Platform: $PLATFORM"

# --- 2. Verify KiCad 10 is reachable ---
echo ">> Checking KiCad 10 installation..."
KICAD_PY=""
for c in "${CANDIDATES[@]}"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    # For Windows Python (WSL case), skip the pcbnew import check —
    # WSL calling Windows python.exe just for -c "import pcbnew" is slow
    # and sometimes races; the server's own lazy import will surface issues.
    if [[ "$c" == *.exe ]]; then
        KICAD_PY="$c"
        break
    fi
    if "$c" -c 'import pcbnew' 2>/dev/null; then
        KICAD_PY="$c"
        break
    fi
done

if [ -z "$KICAD_PY" ]; then
    echo "   FEHLER: Kein KiCad-Python gefunden." >&2
    echo "   $HINT" >&2
    exit 1
fi
echo "   OK: $KICAD_PY"

# --- 3. Install Python deps into KiCad's Python (editable, --user) ---
echo ">> Installing Python dependencies into KiCad's Python (--user)..."

# WSL case: Windows Python needs Windows path for the install target
pip_target="$SCRIPT_DIR"
if [[ "$KICAD_PY" == *.exe ]]; then
    wsl_to_win() {
        local p="$1"
        if [[ "$p" =~ ^/mnt/([a-z])/(.*)$ ]]; then
            local drive="${BASH_REMATCH[1]^^}"
            local rest="${BASH_REMATCH[2]//\//\\}"
            echo "${drive}:\\${rest}"
        else
            echo "$p"
        fi
    }
    pip_target="$(wsl_to_win "$SCRIPT_DIR")"
fi

pip_log="${TMPDIR:-/tmp}/kicad-mcp-pip.log"
if "$KICAD_PY" -m pip install --user --upgrade -e "$pip_target" >"$pip_log" 2>&1; then
    echo "   OK"
else
    echo "   FEHLER beim pip-install. Siehe $pip_log" >&2
    tail -20 "$pip_log" >&2
    exit 1
fi

# --- 4. Register with Claude Code ---
if command -v claude >/dev/null 2>&1; then
    echo ">> Registering with Claude Code (user scope)..."
    claude mcp remove kicad -s user >/dev/null 2>&1 || true
    claude mcp add kicad -s user -- bash "$LAUNCHER"
    echo "   OK"
else
    echo ">> Claude Code CLI (\`claude\`) not found — skipping auto-register."
fi

# --- 5. Print snippets for all other clients ---
cat <<EOF

=========================================================================
 For other MCP clients, paste into the respective config file:
 (see docs/MCP_CLIENTS.md for file locations)
=========================================================================

--- Claude Desktop / Cursor / Windsurf / Claude Code (project-scope) ---
{
  "mcpServers": {
    "kicad": {
      "command": "bash",
      "args": ["$LAUNCHER"]
    }
  }
}

--- VS Code (.vscode/mcp.json) ---
{
  "servers": {
    "kicad": {
      "type": "stdio",
      "command": "bash",
      "args": ["$LAUNCHER"]
    }
  }
}

Done.
EOF
