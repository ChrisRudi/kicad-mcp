#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# One-click installer for the "Claude fuer KiCad" action plugin (Linux/macOS).
# Fetches the repo (git if present, else a ZIP) and copies the plugin into
# KiCad's scripting-plugins dir. Optional arg 1 = KiCad version (default 10.0).
#
# Usage:  ./install_plugin.sh [10.0]
#   or:   curl -fsSL https://raw.githubusercontent.com/ChrisRudi/kicad-mcp/main/install_plugin.sh | bash

set -euo pipefail

REPO="https://github.com/ChrisRudi/kicad-mcp"
BRANCH="main"
PKGNAME="claude_kicad"
VER="${1:-10.0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kicad_claude_install.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# --- Locate the plugin source ------------------------------------------------
SRC=""
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/plugin/claude_action.py" ]; then
    SRC="$SCRIPT_DIR/plugin"          # run from a repo checkout
    echo "Lokale Plugin-Quelle: $SRC"
else
    echo "Lade Plugin von $REPO (Branch $BRANCH) ..."
    if command -v git >/dev/null 2>&1 && \
       git clone --depth 1 -b "$BRANCH" "$REPO.git" "$WORK/src" >/dev/null 2>&1 && \
       [ -f "$WORK/src/plugin/claude_action.py" ]; then
        SRC="$WORK/src/plugin"
    else
        echo "git nicht moeglich - ZIP-Download ..."
        url="$REPO/archive/refs/heads/$BRANCH.tar.gz"
        if command -v curl >/dev/null 2>&1; then
            curl -fsSL "$url" -o "$WORK/repo.tgz"
        elif command -v wget >/dev/null 2>&1; then
            wget -qO "$WORK/repo.tgz" "$url"
        else
            echo "FEHLER: weder git, curl noch wget vorhanden." >&2
            exit 1
        fi
        mkdir -p "$WORK/unz"
        tar -xzf "$WORK/repo.tgz" -C "$WORK/unz"
        SRC="$(find "$WORK/unz" -maxdepth 2 -type d -name plugin | head -n1)"
    fi
fi

if [ -z "$SRC" ] || [ ! -f "$SRC/claude_action.py" ]; then
    echo "FEHLER: Plugin-Quelle nicht gefunden." >&2
    exit 1
fi

# --- Target dir (per OS) -----------------------------------------------------
case "$(uname -s)" in
    Darwin) BASE="$HOME/Library/Application Support/kicad" ;;
    *)      BASE="$HOME/.local/share/kicad" ;;
esac
DEST="$BASE/$VER/scripting/plugins/$PKGNAME"

echo
echo "Installiere nach: $DEST"
rm -rf "$DEST"
mkdir -p "$DEST"
# copy contents of plugin/ into DEST (exclude caches)
( cd "$SRC" && tar --exclude='__pycache__' --exclude='*.pyc' -cf - . ) \
    | ( cd "$DEST" && tar -xf - )

cat <<EOF

============================================================
 Fertig. Plugin installiert (KiCad $VER).

 Naechste Schritte in KiCad (PCB-Editor / pcbnew):
   1) Werkzeuge -> Externe Plugins -> Aktualisieren
      (oder KiCad einmal neu starten)
   2) Den "Claude"-Button in der Toolbar klicken
   3) Das Einrichtungs-Panel fuehrt durch den Rest
      (Claude Code installieren, Login, Abhaengigkeiten, IPC)
============================================================
EOF
