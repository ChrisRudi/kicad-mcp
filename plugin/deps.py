# SPDX-License-Identifier: GPL-3.0-or-later
"""Check / install the bundled kicad-mcp server's RUNTIME dependencies.

The server imports ``fastmcp`` / ``mcp`` / ``pandas`` / ``yaml`` / ``defusedxml``
/ ``jsonschema`` — none of which ship in KiCad's bundled Python. We probe them
fast with ``importlib.util.find_spec`` in a subprocess (KiCad's Python), and
install with ``pip --user`` (no admin needed) in a visible terminal.

Pure logic (command builders + an injectable runner); unit-testable headless.
"""

from __future__ import annotations

import subprocess
from typing import Optional

# Import names to probe (note: pyyaml imports as ``yaml``).
IMPORT_NAMES = ["fastmcp", "mcp", "pandas", "yaml", "defusedxml", "jsonschema"]
# pip specs to install (no brackets -> no cross-shell quoting headaches;
# fastmcp pulls mcp transitively, mcp listed too to be safe).
PIP_SPECS = ["fastmcp", "mcp", "pandas", "pyyaml", "defusedxml", "jsonschema"]

_CHECK_CODE = (
    "import importlib.util,sys;"
    f"req={IMPORT_NAMES!r};"
    "miss=[m for m in req if importlib.util.find_spec(m) is None];"
    "print(','.join(miss));"
    "sys.exit(1 if miss else 0)"
)


def build_check_cmd(kicad_py: str) -> list:
    return [kicad_py, "-c", _CHECK_CODE]


def check_deps(kicad_py: Optional[str], _run=subprocess.run) -> dict:
    """Return ``{ok, missing, error}`` — which runtime deps KiCad's Python lacks.
    Fast (find_spec, no full import). Never raises."""
    out = {"ok": False, "missing": [], "error": ""}
    if not kicad_py:
        out["error"] = "KiCad-Python nicht gefunden"
        return out
    try:
        proc = _run(build_check_cmd(kicad_py), capture_output=True, text=True,
                    timeout=30, check=False)
    except Exception as exc:
        out["error"] = str(exc)
        return out
    miss = [m for m in (proc.stdout or "").strip().split(",") if m]
    out["missing"] = miss
    out["ok"] = getattr(proc, "returncode", 1) == 0 and not miss
    return out


def pip_install_commands(kicad_py: str) -> list:
    """The ``pip install --user`` command line to run in a visible terminal
    (see plugin.terminal)."""
    pkgs = " ".join(PIP_SPECS)
    return [f'"{kicad_py}" -m pip install --user {pkgs}']
