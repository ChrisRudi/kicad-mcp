# SPDX-License-Identifier: GPL-3.0-or-later
"""Preflight checks for the Claude-for-KiCad onboarding panel.

Pure logic (no KiCad/wx) so it is unit-testable headless: each detector returns
a :class:`Check`; the wx panel renders them as a green/red checklist with a
one-click fix per failing row. "Board open" is passed in by the KiCad layer
(only it can ask pcbnew).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Optional

from . import deps, ipc_setup, mcp_config, runtime_env

OK = "ok"
FAIL = "fail"
WARN = "warn"


@dataclass
class Check:
    key: str
    label: str
    status: str            # OK | FAIL | WARN
    detail: str = ""
    fix: Optional[str] = None   # fix-action id the panel maps to a button


# --- detectors ---------------------------------------------------------------

def check_claude() -> Check:
    cmd = runtime_env.find_claude()
    if not cmd:
        return Check(
            "claude", "Claude Code nicht gefunden", FAIL,
            "Claude Code für dieses System installieren (Windows-Installer bzw. "
            "npm) — kein WSL nötig.", "install_claude",
        )
    # Native is the default; the WSL bridge only appears if explicitly opted in.
    is_bridge = (runtime_env.kicad_os() == "windows"
                 and os.path.basename(cmd[0]).lower().startswith("wsl"))
    suffix = " — WSL-Brücke (opt-in)" if is_bridge else " — nativ"
    return Check("claude", "Claude Code gefunden", OK, " ".join(cmd) + suffix)


def check_kicad_python() -> Check:
    py = mcp_config.find_kicad_python()
    if py:
        return Check("python", "KiCad-Python (kipy) gefunden", OK, py)
    return Check(
        "python", "KiCad-Python nicht gefunden", FAIL,
        "Setze KICAD_PYTHON_PATH auf <KiCad>/bin/python.exe.", "env_help",
    )


def check_mcp_root(mcp_root: str) -> Check:
    if mcp_root and os.path.isdir(os.path.join(mcp_root, "kicad_mcp")):
        return Check("mcp", "kicad-mcp bereit", OK, mcp_root)
    return Check(
        "mcp", "kicad-mcp nicht gefunden", FAIL,
        f"Pfad ohne kicad_mcp/: {mcp_root!r}. Setze KICAD_MCP_ROOT.", "env_help",
    )


def check_deps() -> Check:
    """Are the bundled MCP server's runtime deps importable in KiCad's Python?

    WARN (not FAIL): without them the MCP server can't start, so the board tools
    go dark — but Claude still answers text, so we don't hard-block the chat. The
    one-click fix pip-installs them (``--user``, no admin).
    """
    py = mcp_config.find_kicad_python()
    res = deps.check_deps(py)
    if res.get("ok"):
        return Check("deps", "MCP-Abhängigkeiten vorhanden", OK)
    if not py:
        return Check("deps", "MCP-Abhängigkeiten", WARN,
                     "Erst KiCad-Python nötig.")
    detail = ", ".join(res.get("missing") or []) or res.get("error") or "?"
    return Check("deps", "MCP-Abhängigkeiten fehlen", WARN,
                 f"Fehlt: {detail} — ein Klick installiert sie (pip --user).",
                 "install_deps")


def check_ipc(common_path: Optional[str], restart_hint: bool = False) -> Check:
    """Is KiCad's IPC API server on? (``api.enable_server`` in kicad_common.json)

    The plugin auto-enables it on first run, so this normally reads True; the
    one-click fix is the fallback when the auto-write couldn't happen (e.g. file
    perms). Never FAIL — file-based tools work without IPC; only the *live*
    board needs it. After a fresh enable, ``restart_hint`` flags the one restart.
    """
    enabled = ipc_setup.read_ipc_enabled(common_path)
    if enabled:
        if restart_hint:
            return Check("ipc", "KiCad-API aktiviert", OK,
                         "Bitte KiCad einmal neu starten — dann arbeitet "
                         "Claude live am Board.")
        return Check("ipc", "KiCad-API aktiv", OK)
    return Check("ipc", "KiCad-API aus", WARN,
                 "Für Live-Arbeit am Board nötig — ein Klick aktiviert sie "
                 "(danach KiCad neu starten).", "enable_ipc")


def check_board(board_open: bool, board_name: str = "") -> Check:
    if board_open:
        return Check("board", "Board offen", OK, board_name or "")
    return Check("board", "Kein Board offen", FAIL,
                 "Öffne zuerst ein .kicad_pcb im PCB-Editor.")


def _claude_config_paths() -> list[str]:
    home = os.path.expanduser("~")
    out = [os.path.join(home, ".claude.json"), os.path.join(home, ".claude")]
    # WSL: a Windows KiCad may use a claude that lives in a Linux home.
    out += [
        f"/home/{os.environ.get('USER', '')}/.claude.json",
    ]
    return [p for p in out if p]


def _is_trusted(cfg_path: str, project_dir: str) -> bool:
    try:
        if not cfg_path.endswith(".json") or not os.path.isfile(cfg_path):
            return False
        with open(cfg_path, encoding="utf-8") as fh:
            data = json.load(fh)
        projects = data.get("projects", {})
        if isinstance(projects, dict):
            norm = os.path.normpath(project_dir)
            for key in projects:
                if os.path.normpath(key) == norm:
                    return True
    except Exception:
        pass
    return False


def check_login(project_dir: str) -> Check:
    """Best-effort: did Claude Code run before, and is this dir trusted?

    Never returns FAIL (detection across native/WSL installs is imperfect) — at
    worst WARN with a one-click setup, so it never wrongly blocks the chat.
    """
    found_cfg = next((p for p in _claude_config_paths() if os.path.exists(p)), None)
    if not found_cfg:
        return Check("login", "Login/Vertrauen nötig", WARN,
                     "Melde dich einmal an (claude login) — ein Klick.", "login")
    cfg_json = found_cfg if found_cfg.endswith(".json") else os.path.join(
        found_cfg, "..", ".claude.json")
    if _is_trusted(cfg_json, project_dir):
        return Check("login", "Angemeldet & Ordner vertraut", OK)
    return Check("login", "Ordner evtl. nicht vertraut", WARN,
                 "Einmalig Setup im Projektordner ausführen.", "login")


# --- aggregate ---------------------------------------------------------------

def run_preflight(
    mcp_root: str, project_dir: str, board_open: bool, board_name: str = "",
    common_path: Optional[str] = None, ipc_restart_hint: bool = False,
) -> list[Check]:
    return [
        check_claude(),
        check_kicad_python(),
        check_mcp_root(mcp_root),
        check_deps(),
        check_login(project_dir),
        check_ipc(common_path, ipc_restart_hint),
        check_board(board_open, board_name),
    ]


def hard_ok(checks: list[Check]) -> bool:
    """Chat may start when no check is a hard FAIL (WARNs are advisory)."""
    return all(c.status != FAIL for c in checks)


# --- fix-action command builders (pure; the panel runs them) -----------------

def build_login_terminal_cmd(project_dir: str, claude_cmd: list[str]) -> list[str]:
    """A terminal command that opens an interactive ``claude`` in the project
    dir — doing the OAuth login AND the per-directory trust in one go."""
    login = subprocess.list2cmdline(list(claude_cmd) + ["login"])
    if os.name == "nt":
        inner = (
            f'cd /d "{project_dir}" && {login} && '
            f'echo. && echo Fertig - Fenster kann zu. && pause'
        )
        return ["cmd.exe", "/c", "start", "Claude Login", "cmd", "/k", inner]
    # posix fallback (dev): just run it
    return list(claude_cmd) + ["login"]
