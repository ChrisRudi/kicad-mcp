# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the ``--mcp-config`` JSON that points Claude Code at the bundled
kicad-mcp server.

The server is launched with **KiCad's** Python (the one that has ``kipy``) so
it can reach the running PCB editor over IPC. For Stufe 1 the kicad-mcp package
is referenced from its repo path (later it gets copied into the plugin).
"""

from __future__ import annotations

import glob
import json
import os
import sys
from typing import Optional

from . import deps


def _sys_python() -> Optional[str]:
    """KiCad's own python — the plugin runs INSIDE KiCad's interpreter, so
    ``sys`` already points at the right python no matter where (which drive),
    which bitness, or which version KiCad is installed. This is the robust path;
    the hardcoded scan below is only a fallback."""
    name = "python.exe" if os.name == "nt" else "python3"
    cands = []
    exe = sys.executable or ""
    if exe and os.path.basename(exe).lower().startswith("python"):
        cands.append(exe)
    cands.append(os.path.join(sys.base_prefix, name))
    cands.append(os.path.join(sys.prefix, name))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _version_key(path: str) -> list:
    """Numeric version of the ``…/KiCad/<ver>/bin/…`` dir, so 10.0 > 9.0."""
    parts = path.replace("\\", "/").split("/")
    low = [p.lower() for p in parts]
    if "kicad" in low:
        ver = parts[low.index("kicad") + 1] if low.index("kicad") + 1 < len(parts) else ""
        return [int(t) if t.isdigit() else -1 for t in ver.split(".")]
    return [-1]


def _scan_python() -> Optional[str]:
    """Fallback: scan install locations across drives / Program Files variants
    (incl. 32-bit ``(x86)`` and per-user) and KiCad versions; pick the newest."""
    pats: list[str] = []
    if os.name == "nt":
        bases = []
        for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)",
                    "LOCALAPPDATA"):
            val = os.environ.get(var)
            if val:
                bases.append(val if var != "LOCALAPPDATA"
                             else os.path.join(val, "Programs"))
        bases += [r"C:\Program Files", r"C:\Program Files (x86)"]
        for base in dict.fromkeys(bases):  # de-dup, keep order
            pats.append(os.path.join(base, "KiCad", "*", "bin", "python.exe"))
    else:
        # WSL view of Windows installs (any drive) + native posix / mac
        pats += ["/mnt/*/Program Files/KiCad/*/bin/python.exe",
                 "/mnt/*/Program Files (x86)/KiCad/*/bin/python.exe",
                 "/Applications/KiCad/KiCad.app/Contents/Frameworks/"
                 "Python.framework/Versions/Current/bin/python3"]
    hits = [h for pat in pats for h in glob.glob(pat) if os.path.isfile(h)]
    if hits:
        return max(hits, key=_version_key)
    if os.name != "nt" and os.path.isfile("/usr/bin/python3"):
        return "/usr/bin/python3"
    return None


def find_kicad_python() -> Optional[str]:
    """Locate the KiCad-bundled Python (the interpreter that has ``kipy``).

    Order: ``KICAD_PYTHON_PATH`` env override → the running KiCad interpreter
    (``sys``, robust to any install path/drive/bitness/version) → a scan of the
    common install locations (incl. 32-bit ``Program Files (x86)`` and per-user).
    """
    override = os.environ.get("KICAD_PYTHON_PATH", "").strip()
    if override and os.path.isfile(override):
        return override
    return _sys_python() or _scan_python()


def build_mcp_config(mcp_root: str, python_exe: str,
                     deps_dir: Optional[str] = None) -> dict:
    """Return the Claude-Code MCP config dict for the kicad-mcp stdio server.

    ``deps_dir`` (default: the plugin-local ``_deps`` dir, if present) is
    appended to PYTHONPATH so the pip-``--target``-installed runtime deps are
    found regardless of user-site quirks in KiCad's bundled Python.
    """
    if deps_dir is None:
        deps_dir = deps.active_deps_dir()
    pythonpath = mcp_root + (os.pathsep + deps_dir if deps_dir else "")
    return {
        "mcpServers": {
            "kicad-mcp": {
                "type": "stdio",
                "command": python_exe,
                "args": ["-m", "kicad_mcp.server"],
                "env": {"PYTHONPATH": pythonpath},
            }
        }
    }


def write_mcp_config(
    path: str, mcp_root: str, python_exe: Optional[str] = None,
    deps_dir: Optional[str] = None,
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
    cfg = build_mcp_config(mcp_root, python_exe, deps_dir=deps_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return path
