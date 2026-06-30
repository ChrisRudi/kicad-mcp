#!/usr/bin/env sh
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Einmaliges Dev-Setup: aktiviert die getrackten Git-Hooks unter .githooks/.
# Danach synchronisiert ein pre-commit-Hook plugin/mcp/kicad_mcp/ automatisch
# aus dem kanonischen kicad_mcp/ (siehe .githooks/pre-commit, scripts/sync_bundle.py).
#
# Aufruf:  sh scripts/setup-hooks.sh

set -e
repo_root=$(git rev-parse --show-toplevel)
git -C "$repo_root" config core.hooksPath .githooks
echo "Git-Hooks aktiviert: core.hooksPath -> .githooks"
echo "pre-commit spiegelt jetzt den Bundle (plugin/mcp/kicad_mcp) bei jedem Commit."
