# SPDX-License-Identifier: GPL-3.0-or-later
"""Does the bundled kicad-mcp server actually START in KiCad's Python?

``claude -p`` drops a failing MCP server SILENTLY: the chat still answers,
just without any board tools ("kein MCP verbunden"). The fast find_spec
dependency check can't catch everything (version conflicts, broken installs,
import-time errors), so this probe runs the real thing: KiCad's Python
imports ``kicad_mcp.server`` with the same PYTHONPATH the MCP config uses.
Import time is where the server dies in practice (top-level ``from fastmcp
import FastMCP``); when the import succeeds, ``python -m kicad_mcp.server``
will start.

Pure logic (injectable runner); unit-testable headless.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from . import deps
from .claude_bridge import hidden_console_kwargs

PROBE_CODE = "import kicad_mcp.server"


def build_probe_cmd(kicad_py: str) -> list:
    return [kicad_py, "-c", PROBE_CODE]


def error_tail(stderr: str, lines: int = 3) -> str:
    """The last lines of a traceback — the part worth showing in the panel."""
    rows = [r.strip() for r in (stderr or "").strip().splitlines() if r.strip()]
    return " | ".join(rows[-lines:])


def probe_server(kicad_py: Optional[str], mcp_root: str,
                 timeout: float = 90.0, _run=subprocess.run,
                 deps_dir: Optional[str] = None) -> dict:
    """Run the import probe; returns ``{ok, error, missing_dep}``.

    ``missing_dep`` is True when the failure is a ModuleNotFoundError — then
    the one-click dependency install is the right fix. Never raises.
    """
    out = {"ok": False, "error": "", "missing_dep": False}
    if not kicad_py:
        out["error"] = "KiCad-Python nicht gefunden"
        return out
    env = dict(os.environ)
    if deps_dir is None:
        deps_dir = deps.active_deps_dir()
    # exactly what the MCP config will use
    env["PYTHONPATH"] = mcp_root + (os.pathsep + deps_dir if deps_dir else "")
    try:
        proc = _run(build_probe_cmd(kicad_py), capture_output=True, text=True,
                    timeout=timeout, check=False, env=env,
                    **hidden_console_kwargs())
    except subprocess.TimeoutExpired:
        out["error"] = f"Import-Probe hängt (> {int(timeout)}s)"
        return out
    except Exception as exc:
        out["error"] = str(exc)
        return out
    if getattr(proc, "returncode", 1) == 0:
        out["ok"] = True
        return out
    stderr = proc.stderr or ""
    out["error"] = (error_tail(stderr or proc.stdout)
                    or "Import fehlgeschlagen (ohne Meldung)")
    out["missing_dep"] = "ModuleNotFoundError" in stderr
    return out
