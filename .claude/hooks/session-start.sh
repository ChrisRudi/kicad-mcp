#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Installs the project's dev environment so the lint + test matrix from
# .github/workflows/ci.yml runs out of the box, with no manual setup:
#   pylint kicad_mcp tests        (lint job)
#   pytest tests/                 (tests job)
#
# Dependencies go into an isolated project .venv (not the container's
# debian-managed Python) — that sidesteps "Cannot uninstall <pkg>: installed by
# debian" conflicts and keeps the toolchain reproducible. The venv's bin is put
# on PATH for the session via $CLAUDE_ENV_FILE, so `python`, `pylint`, `pytest`
# resolve to it automatically.
#
# Idempotent (safe to re-run; reuses the cached venv) and non-interactive.
# Web/remote sessions only — a no-op on a local checkout.
set -euo pipefail

# Only run in the remote (Claude Code on the web) environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR"

VENV="$PROJECT_DIR/.venv"
LOG="$(mktemp -t kicad-mcp-session-hook.XXXXXX.log)"

# Create the venv once; reuse the cached one on subsequent session starts.
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

# kicad-mcp runtime + dev extras (pytest/ruff/mypy/bandit/...) + pylint (the CI
# linter). Mirrors ci.yml's install step. pcbnew/wx/kipy are KiCad-bundled and
# stay absent here on purpose — the suite self-skips those paths, exactly as CI
# does. Verbose output is captured; only surfaced if the install fails.
{
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/pip" install -e ".[dev]"
  "$VENV/bin/pip" install pylint
} >"$LOG" 2>&1 || {
  echo "session-start hook: dependency install failed — last 25 lines:" >&2
  tail -25 "$LOG" >&2
  exit 1
}

# Make the venv the session's interpreter/toolchain.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$VENV\""
    echo "export PATH=\"$VENV/bin:\$PATH\""
  } >>"$CLAUDE_ENV_FILE"
fi

echo "session-start hook: kicad-mcp dev env ready in $VENV (pylint + pytest available)"
