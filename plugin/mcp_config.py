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


def server_bootstrap_code(mcp_root: str, deps_dir: Optional[str] = None) -> str:
    """The ``-c`` bootstrap that starts the server with sys.path set
    EXPLICITLY in-process.

    KiCad's bundled Python can IGNORE the ``PYTHONPATH`` env var (isolated
    ``._pth``-style builds do) — field symptom: "Error while finding module
    specification for 'kicad_mcp.server'" despite a correct env. In-process
    ``sys.path`` insertion is immune; it is the same mechanism as the install
    verification and ``start_mcp.bat``'s script launch, both proven working
    on the affected machine.
    """
    paths = ([mcp_root] + ([deps_dir] if deps_dir else [])
             + deps.pywin32_path_entries(deps_dir))
    entries = ", ".join(repr(p) for p in paths)
    return (f"import sys; sys.path[:0] = [{entries}]; "
            + deps.pywin32_dll_setup_code(deps_dir)
            + "from kicad_mcp.server import main; main()")


# Generous startup timeout for the stdio MCP server. Claude Code's default is
# only 30 s; the FIRST cold start on Windows is much slower — importing
# pandas/numpy/pywin32 + 167 tools out of the freshly-written _deps folder,
# with Windows Defender scanning each new .pyd. That one-time scan can blow
# past 30 s (or even 120 s) and makes claude mark the server "failed". A large
# cap gets past the cold start; warm starts are unaffected. Set both the env
# var (claude_bridge) AND this per-server config field.
MCP_STARTUP_TIMEOUT_MS = 300000  # 5 min (Claude Code config max is 600000)


def build_mcp_config(mcp_root: str, python_exe: str,
                     deps_dir: Optional[str] = None) -> dict:
    """Return the Claude-Code MCP config dict for the kicad-mcp stdio server.

    ``deps_dir`` (default: the plugin-local ``_deps`` dir, if present) joins
    ``mcp_root`` on the in-process sys.path bootstrap; PYTHONPATH is set too,
    but only as belt-and-suspenders for pythons that do honor it. A generous
    per-server ``timeout`` survives the slow first cold start (see the
    constant); ``PYTHONUNBUFFERED`` keeps the stdio JSON-RPC stream prompt.
    """
    if deps_dir is None:
        deps_dir = deps.active_deps_dir()
    pythonpath = mcp_root + (os.pathsep + deps_dir if deps_dir else "")
    return {
        "mcpServers": {
            "kicad-mcp": {
                "type": "stdio",
                "command": python_exe,
                "args": ["-c", server_bootstrap_code(mcp_root, deps_dir)],
                # NO_AUTO_OPEN: the chat runs INSIDE the KiCad GUI — the
                # server must never auto-spawn a second (detached) editor;
                # that puts two instances on the IPC bus and kills every
                # cross-probe link with "Kein eindeutiges Board".
                # KICAD_MCP_TRANSPORT wird GEPINNT: im http-Modus trägt der
                # Plugin-Prozess transport=http im Env — ein per stdio-Config
                # gespawnter Server (Fallback!) würde das erben, http auf
                # Port 8331 binden und nie stdio sprechen (Feld-Befund
                # 0.8.2: Errno 10048 in der Diagnose-Probe).
                "env": {"PYTHONPATH": pythonpath, "PYTHONUNBUFFERED": "1",
                        "KICAD_MCP_NO_AUTO_OPEN": "1",
                        "KICAD_MCP_TRANSPORT": "stdio"},
                "timeout": MCP_STARTUP_TIMEOUT_MS,
            }
        }
    }


def build_http_mcp_config(url: str, token: str = "") -> dict:
    """The Claude-Code MCP config for the WARM (persistent) http server.

    Claude does not spawn anything here — it merely connects to the already
    running local server (``plugin/server_manager.py`` owns its lifecycle), so
    the per-message cold start disappears. The bearer token gates the
    localhost endpoint against other local processes.
    """
    server: dict = {"type": "http", "url": url}
    if token:
        server["headers"] = {"Authorization": f"Bearer {token}"}
    return {"mcpServers": {"kicad-mcp": server}}


def write_http_mcp_config(path: str, url: str, token: str = "") -> str:
    """Write the http-mode MCP config to ``path``; returns the path."""
    cfg = build_http_mcp_config(url, token)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return path


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
