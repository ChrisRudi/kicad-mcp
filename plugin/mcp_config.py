# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the ``--mcp-config`` JSON that points Claude Code at the bundled
kicad-mcp server.

The server is launched with **KiCad's** Python (the one that has ``kipy``) so
it can reach the running PCB editor over IPC. For Stufe 1 the kicad-mcp package
is referenced from its repo path (later it gets copied into the plugin).
"""

from __future__ import annotations

import json
import os
from typing import Optional


def find_kicad_python() -> Optional[str]:
    """Locate the KiCad-bundled Python (the interpreter that has ``kipy``).

    KiCad ships it at ``<install>/bin/python(.exe)``. Checks an env override,
    then the common KiCad 10/9 install locations on Windows / WSL / Linux / mac.
    """
    override = os.environ.get("KICAD_PYTHON_PATH", "").strip()
    if override and os.path.isfile(override):
        return override
    cands = []
    for ver in ("10.0", "9.0"):
        cands += [
            rf"C:\Program Files\KiCad\{ver}\bin\python.exe",
            f"/mnt/c/Program Files/KiCad/{ver}/bin/python.exe",
            "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/"
            "Versions/Current/bin/python3",
        ]
    cands.append("/usr/bin/python3")
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def build_mcp_config(mcp_root: str, python_exe: str) -> dict:
    """Return the Claude-Code MCP config dict for the kicad-mcp stdio server."""
    return {
        "mcpServers": {
            "kicad-mcp": {
                "type": "stdio",
                "command": python_exe,
                "args": ["-m", "kicad_mcp.server"],
                "env": {"PYTHONPATH": mcp_root},
            }
        }
    }


def write_mcp_config(
    path: str, mcp_root: str, python_exe: Optional[str] = None
) -> str:
    """Write the MCP config to ``path``; returns the path.

    Raises ``RuntimeError`` if KiCad's Python can't be found.
    """
    python_exe = python_exe or find_kicad_python()
    if not python_exe:
        raise RuntimeError(
            "KiCad-Python (mit kipy) nicht gefunden — setze KICAD_PYTHON_PATH."
        )
    if not os.path.isdir(mcp_root):
        raise RuntimeError(f"kicad-mcp-Pfad existiert nicht: {mcp_root}")
    cfg = build_mcp_config(mcp_root, python_exe)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return path
