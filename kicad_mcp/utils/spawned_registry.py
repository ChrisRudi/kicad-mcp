# SPDX-License-Identifier: GPL-3.0-or-later
"""Track GUI editor processes the MCP server spawns, so they can be reaped
instead of orphaning as socket-squatting ghosts.

``ipc_open_kicad`` launches ``pcbnew``/``eeschema`` DETACHED
(``creationflags=DETACHED_PROCESS``) so the editor survives the one-shot call.
The flip side: nothing then owns its lifetime. When KiCad closes, the plugin
force-kills the claude+MCP tree (``taskkill /F /T``) — but a DETACHED child sits
OUTSIDE that tree, so the spawned editor SURVIVES. It keeps an IPC API server on
the socket with **no board loaded**, and every later ``ipc_*`` / chat-link call
hits that ghost and fails with "no handler for GetOpenDocuments" /
"kein eindeutiges Board". (Observed live 2026-06-19: a 7 MB ``pcbnew.exe`` that
outlived KiCad and broke every link.)

This registry records each spawned PID to a fixed JSON file in the temp dir.
Two independent reapers consume it: the MCP server (``ipc_close_kicad``) and the
plugin's shutdown handler. The plugin reaper reads the SAME file *directly* — it
cannot import this package (``kicad_mcp/__init__`` pulls the whole server), so
``plugin/claude_bridge.py`` mirrors :data:`REGISTRY_FILENAME` by literal; keep
the two in sync (there is no shared import to enforce it).

Pure stdlib; injectable killer / aliveness → unit-testable headless.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Callable, List, Optional

# MUST stay in sync with plugin/claude_bridge.py::_SPAWNED_REGISTRY_FILENAME.
REGISTRY_FILENAME = "kicad_mcp_spawned_editors.json"

# Hide the console window the tasklist/taskkill helpers would flash on Windows.
_NT_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def registry_path() -> str:
    """Fixed path of the spawned-editor registry (temp dir)."""
    return os.path.join(tempfile.gettempdir(), REGISTRY_FILENAME)


def _read(path: Optional[str] = None) -> List[int]:
    try:
        with open(path or registry_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return [int(p) for p in data if str(p).strip().lstrip("-").isdigit()]
    except Exception:
        return []


def _write(pids, path: Optional[str] = None) -> None:
    try:
        with open(path or registry_path(), "w", encoding="utf-8") as fh:
            json.dump(sorted({int(p) for p in pids}), fh)
    except Exception:
        # best effort: Registry-Write darf den Editor-Start nicht brechen
        pass


def record(pid: int, path: Optional[str] = None) -> None:
    """Remember a freshly spawned editor PID (idempotent)."""
    if not pid:
        return
    pids = _read(path)
    if int(pid) not in pids:
        pids.append(int(pid))
        _write(pids, path)


def forget(pid: int, path: Optional[str] = None) -> None:
    """Drop ``pid`` from the registry (e.g. after a targeted kill)."""
    _write([p for p in _read(path) if p != int(pid)], path)


def pids(path: Optional[str] = None) -> List[int]:
    """The currently recorded PIDs."""
    return _read(path)


def _alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=10, check=False,
                creationflags=_NT_NO_WINDOW)
            return str(pid) in (out.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _default_killer(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, text=True, timeout=10,
                           check=False, creationflags=_NT_NO_WINDOW)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        # Prozess bereits beendet oder kill nicht erlaubt — best effort
        pass


def reap(path: Optional[str] = None,
         killer: Optional[Callable[[int], None]] = None,
         alive: Optional[Callable[[int], bool]] = None) -> List[int]:
    """Kill every recorded PID still alive, clear the registry, return the PIDs
    reaped. Safe to call repeatedly. ``killer``/``alive`` are injectable for
    tests; defaults use ``taskkill``/``tasklist`` (nt) or ``os.kill`` (posix)."""
    kill = killer or _default_killer
    is_alive = alive or _alive
    reaped = []
    for pid in _read(path):
        if is_alive(pid):
            kill(pid)
            reaped.append(pid)
    _write([], path)
    return reaped
