# SPDX-License-Identifier: GPL-3.0-or-later
"""Does the bundled kicad-mcp server actually START for Claude? Full dress
rehearsal: launch the server EXACTLY like Claude Code will (``python -m
kicad_mcp.server`` with PYTHONPATH = mcp_root + plugin ``_deps``) and complete
the MCP ``initialize`` handshake over stdio.

``claude -p`` drops a failing MCP server SILENTLY: the chat still answers,
just without any board tools ("kein MCP verbunden"). An import-only probe
proved insufficient in the field — modules can import fine while the server
still dies at startup — so the handshake is the authoritative check: if it
answers ``initialize``, it will answer Claude. On failure the real stderr
tail (traceback) is returned for the preflight panel.

Pure logic (injectable Popen); unit-testable headless.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Optional

from . import deps, mcp_config
from .claude_bridge import hidden_console_kwargs

PROBE_TIMEOUT = 120.0  # cold start imports pandas + 165 tools — be generous


def _popen_kwargs() -> dict:
    # hidden_console_kwargs' stdin=DEVNULL would clash with the stdin PIPE the
    # handshake needs — keep only the no-window flag.
    return {k: v for k, v in hidden_console_kwargs().items() if k != "stdin"}


def init_request() -> str:
    """One MCP ``initialize`` JSON-RPC line, as a stdio client would send."""
    # The FULL handshake claude actually performs: initialize, then tools/list.
    # tools/list runs in the SAME startup-timeout window, so a probe that only
    # tested initialize was too lenient (it passed while claude could still
    # time out enumerating 167 tools).
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "kicad-claude-plugin",
                                   "version": "probe"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    return "".join(json.dumps(m) + "\n" for m in msgs)


def build_probe_cmd(kicad_py: str, mcp_root: str,
                    deps_dir: "str | None" = None) -> list:
    """Launch EXACTLY like the MCP config does: ``-c`` bootstrap with
    in-process sys.path (KiCad's Python proved to ignore PYTHONPATH)."""
    return [kicad_py, "-c",
            mcp_config.server_bootstrap_code(mcp_root, deps_dir)]


def error_tail(stderr: str, lines: int = 3) -> str:
    """The last lines of a traceback — the part worth showing in the panel."""
    rows = [r.strip() for r in (stderr or "").strip().splitlines() if r.strip()]
    return " | ".join(rows[-lines:])


def is_handshake_reply(stdout: str) -> bool:
    """Did the server answer ``initialize``? (serverInfo in a result line)"""
    return '"serverInfo"' in (stdout or "")


def tools_listed(stdout: str) -> bool:
    """Did the server answer ``tools/list``? (a tools array came back)."""
    return '"tools"' in (stdout or "")


def probe_server(kicad_py: Optional[str], mcp_root: str,
                 timeout: float = PROBE_TIMEOUT,
                 _popen: Any = subprocess.Popen,
                 deps_dir: Optional[str] = None) -> dict:
    """Run the handshake probe; returns ``{ok, error, missing_dep}``.

    ``missing_dep`` is True when the failure is a ModuleNotFoundError — then
    the one-click dependency install is the right fix. Never raises.
    """
    out = {"ok": False, "error": "", "missing_dep": False,
           "missing_root": False, "stderr": "", "seconds": 0.0}
    if not kicad_py:
        out["error"] = "KiCad-Python nicht gefunden"
        return out
    # "Error while finding module specification for 'kicad_mcp.server'" means
    # the PACKAGE itself is missing under mcp_root (broken/partial plugin
    # install) — say that precisely instead of a generic module error.
    pkg_dir = os.path.join(mcp_root, "kicad_mcp")
    if not os.path.isdir(pkg_dir):
        out["error"] = (
            f"kicad_mcp-Paket fehlt: {pkg_dir} existiert nicht — "
            "Plugin-Installation unvollständig. In der Einrichtung "
            "'Update prüfen' ausführen (lädt den mcp/-Ordner neu)."
        )
        out["missing_root"] = True
        return out
    env = dict(os.environ)
    if deps_dir is None:
        deps_dir = deps.active_deps_dir()
    # belt-and-suspenders only — the bootstrap sets sys.path in-process
    env["PYTHONPATH"] = mcp_root + (os.pathsep + deps_dir if deps_dir else "")
    t0 = time.perf_counter()
    try:
        proc = _popen(build_probe_cmd(kicad_py, mcp_root, deps_dir),
                      stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      text=True, env=env, **_popen_kwargs())
        try:
            # closing stdin after the requests makes a healthy server exit
            stdout, stderr = proc.communicate(init_request(), timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            out["seconds"] = round(time.perf_counter() - t0, 1)
            out["error"] = (f"Server antwortet nicht (> {int(timeout)}s) — "
                            "Kaltstart zu langsam.")
            return out
    except Exception as exc:
        out["error"] = str(exc)
        return out
    out["seconds"] = round(time.perf_counter() - t0, 1)
    # Both initialize AND tools/list must complete (claude waits for both in the
    # one startup-timeout window). The elapsed time is what claude experiences.
    if is_handshake_reply(stdout) and tools_listed(stdout):
        out["ok"] = True
        return out
    if is_handshake_reply(stdout) and not tools_listed(stdout):
        out["error"] = (f"initialize ok, aber tools/list kam nicht "
                        f"({out['seconds']}s) — Tool-Enumeration zu langsam.")
        out["stderr"] = stderr or ""
        return out
    rc = getattr(proc, "returncode", None)
    tail = error_tail(stderr or stdout) or f"Kein MCP-Handshake (exit {rc})"
    # The used path is half the diagnosis — show it in the red row.
    out["error"] = f"{tail} [PYTHONPATH={env['PYTHONPATH']}]"
    out["missing_dep"] = "ModuleNotFoundError" in (stderr or "")
    out["stderr"] = stderr or ""  # full text for the diagnose report
    return out
