# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve a consistent runtime plan so the plugin works on Windows AND Linux
KiCad — without silent cross-OS path mismatches.

The plugin always runs inside KiCad's *own* Python, i.e. in KiCad's OS. The only
correctness rule is: Claude Code and the kicad-mcp server must run in the SAME
OS as KiCad, so every path (mcp-config, command, PYTHONPATH, cwd) is in ONE path
style. Three cases:

  * Windows KiCad + Windows Claude  -> native (all ``C:\\`` paths)
  * Linux/mac KiCad + local Claude   -> native (all ``/`` paths)
  * Windows KiCad + WSL Claude        -> bridge: Claude runs in WSL but launches
    the *Windows* ``python.exe`` (via ``/mnt/c/...`` interop) so the MCP server is
    a Windows process that can reach the Windows KiCad IPC. There the only mixed
    bit is on purpose: ``command`` is a ``/mnt/c`` path (WSL execs it) while
    ``PYTHONPATH`` stays ``C:\\`` (the *Windows* python reads it).

Pure logic (no KiCad/wx); unit-testable headless.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from typing import Optional

from . import mcp_config

NATIVE = "native"
BRIDGE = "wsl-bridge"

# Transport between claude and the kicad-mcp server (channel A). ``stdio`` =
# today's behavior: claude spawns a fresh server per chat message. ``http`` =
# warm mode: the plugin keeps ONE persistent local HTTP server running
# (plugin/server_manager.py) and claude merely connects to it — no cold start
# per turn. Default stays ``stdio`` until the http path is validated on real
# Windows setups (docs/warm-server-plan.md, Phase 6). The KiCad IPC channel
# (server <-> kipy, channel B) is independent of this flag.
TRANSPORT_ENV = "KICAD_MCP_TRANSPORT"
TRANSPORT_STDIO = "stdio"
TRANSPORT_HTTP = "http"
DEFAULT_TRANSPORT = TRANSPORT_STDIO

_WIN_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def transport_mode() -> str:
    """``"stdio"`` or ``"http"`` — how claude reaches the kicad-mcp server.

    Reads ``KICAD_MCP_TRANSPORT``; anything unknown falls back to the default
    so a typo can never brick the chat (rollback = one env word).
    """
    raw = os.environ.get(TRANSPORT_ENV, "").strip().lower()
    if raw in (TRANSPORT_STDIO, TRANSPORT_HTTP):
        return raw
    return DEFAULT_TRANSPORT


def kicad_os() -> str:
    """``"windows"`` or ``"posix"`` — the OS this KiCad/plugin runs in."""
    return "windows" if os.name == "nt" else "posix"


def win_to_wsl(path: str) -> str:
    r"""``C:\\Users\\x`` / ``C:/Users/x`` -> ``/mnt/c/Users/x`` (idempotent)."""
    if not path:
        return path
    m = _WIN_DRIVE_RE.match(path)
    if not m:
        return path.replace("\\", "/")  # already posix-ish
    drive, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def _find_native_claude() -> Optional[list]:
    """A Claude on the local OS (no cross-OS ``wsl`` fallback)."""
    for cand in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(cand)
        if found:
            return [found]
    # The official native installer drops it here — found even before the
    # running KiCad's PATH picks it up (so no restart after installing).
    home = os.path.expanduser("~")
    for p in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(home, ".local", "bin", "claude")):
        if os.path.isfile(p):
            return [p]
    return None


def _find_wsl() -> Optional[str]:
    return shutil.which("wsl") or shutil.which("wsl.exe")


def wsl_bridge_enabled() -> bool:
    """The WSL bridge is OFF by default — the product targets NATIVE Claude Code
    in KiCad's own OS, and never asks anyone to install WSL. A developer who
    already runs Claude in WSL can opt in with ``KICAD_CLAUDE_ALLOW_WSL=1``.
    """
    return os.environ.get("KICAD_CLAUDE_ALLOW_WSL", "").strip().lower() in (
        "1", "true", "yes", "on")


def find_claude() -> Optional[list]:
    """The canonical Claude resolver (native first; WSL only if opted in).

    Used by both the preflight and :func:`resolve` so the checklist and the
    actual run agree on what (and whether) Claude was found.
    """
    native = _find_native_claude()
    if native:
        return native
    if kicad_os() == "windows" and wsl_bridge_enabled():
        wsl = _find_wsl()
        if wsl:
            return [wsl, "claude"]
    return None


def project_switch_dir(old_cwd: str, board_path: str) -> Optional[str]:
    """Der NEUE Projektordner, wenn das jetzt offene Board nicht mehr zum
    Plan passt — sonst ``None``.

    Grundlage des Projektwechsel-Detektors im Chat-Panel: der Dock-Pane (und
    damit RunPlan, Session-ID und Link-Ziele) überlebt ein „Datei → Öffnen"
    eines ANDEREN Projekts im selben pcbnew-Fenster. Ohne Detektor lief die
    Unterhaltung des alten Projekts einfach weiter („↺ fortgesetzt") und
    Claude arbeitete am falschen Board."""
    if not board_path:
        return None
    new_dir = os.path.dirname(board_path)
    if not new_dir:
        return None
    same = (os.path.normcase(os.path.normpath(new_dir))
            == os.path.normcase(os.path.normpath(old_cwd or "")))
    return None if same else new_dir


@dataclass
class RunPlan:
    mode: str                  # NATIVE | BRIDGE
    claude_cmd: list           # argv prefix for claude
    config_command: str        # "command" written into the mcp config
    config_pythonpath: str     # "env.PYTHONPATH" written into the mcp config
    config_write_path: str     # where the plugin writes the json (local OS)
    config_arg_path: str       # path passed to --mcp-config (style Claude needs)
    run_cwd: str               # cwd for the claude subprocess
    trust_dir: str             # dir Claude sees as the project (for trust check)


def resolve(
    project_dir: str,
    mcp_root: str,
    config_write_path: str,
    python_exe: Optional[str] = None,
    claude_native: Optional[list] = None,
    wsl: Optional[str] = None,
) -> Optional[RunPlan]:
    """Build a path-consistent :class:`RunPlan`, or ``None`` if Claude/Python
    can't be located (the preflight reports the specifics).

    The ``*_native``/``wsl``/``python_exe`` params are injectable for tests; in
    production they are auto-detected.
    """
    python_exe = python_exe or mcp_config.find_kicad_python()
    if not python_exe:
        return None
    claude_native = (claude_native if claude_native is not None
                     else _find_native_claude())
    wsl = wsl if wsl is not None else _find_wsl()

    # 1) Same-OS native — the portable default (Windows or Linux/mac).
    if claude_native:
        return RunPlan(
            mode=NATIVE,
            claude_cmd=claude_native,
            config_command=python_exe,
            config_pythonpath=mcp_root,
            config_write_path=config_write_path,
            config_arg_path=config_write_path,
            run_cwd=project_dir,
            trust_dir=project_dir,
        )

    # 2) Windows KiCad + Claude only in WSL -> bridge (opt-in only; the product
    #    default never reaches here — natives Claude is the path).
    if kicad_os() == "windows" and wsl and wsl_bridge_enabled():
        return RunPlan(
            mode=BRIDGE,
            claude_cmd=[wsl, "claude"],
            config_command=win_to_wsl(python_exe),   # WSL execs the Win python
            config_pythonpath=mcp_root,               # Win python reads C:\ path
            config_write_path=config_write_path,      # plugin writes Windows path
            config_arg_path=win_to_wsl(config_write_path),
            run_cwd=project_dir,                      # wsl.exe launched from here
            trust_dir=win_to_wsl(project_dir),        # what Claude sees in WSL
        )

    return None
