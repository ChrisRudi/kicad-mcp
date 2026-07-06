# SPDX-License-Identifier: GPL-3.0-or-later
"""Lifecycle manager for the WARM kicad-mcp server (http transport).

In stdio mode claude spawns a fresh server per chat message — every turn pays
the full cold start (import pandas + 183 tools, Windows Defender scanning the
freshly-written ``_deps`` .pyds). In http mode THIS module starts the server
**once per KiCad session** as a persistent local HTTP process; claude then only
connects (``{"type": "http", "url": ...}``) — no spawn, no cold start, no
"MCP nicht verbunden"-Wackler.

Responsibilities:
  * ``ensure_running()`` — reuse a healthy server or start one (health check
    before every chat turn → auto-restart after a crash/hang).
  * Runtime state (pidfile) in a per-user dir so plugin reloads within one
    KiCad session find the same server and ORPHANS from a KiCad crash are
    cleaned up on the next start.
  * Random per-start bearer token: localhost-bind + token = no other local
    process can drive the server.
  * ``shutdown()`` — kill the process tree; also registered via ``atexit`` so
    the server never outlives KiCad.

Pure logic (injectable ``_popen``/health probes); unit-testable headless.
"""

from __future__ import annotations

import atexit
import json
import os
import secrets
import socket
import subprocess
import time
from typing import Any, Optional

from . import deps, mcp_config, server_probe
from .claude_bridge import hidden_console_kwargs

DEFAULT_HOST = "127.0.0.1"  # strictly local — never bind 0.0.0.0
STATE_FILENAME = "kicad_mcp_server.json"
STATE_DIR_ENV = "KICAD_MCP_STATE_DIR"
# Generous: the FIRST start after update/restart imports everything out of a
# freshly-written _deps with Defender scanning each .pyd (same rationale as
# mcp_config.MCP_STARTUP_TIMEOUT_MS). Warm restarts take ~1-2 s.
START_TIMEOUT_S = 300.0
# Per-turn health ping against the RUNNING server — localhost, answers in ms.
PING_TIMEOUT_S = 5.0

_shutdown_registered = False

# The Popen of the server WE spawned (this process is its POSIX parent). Kept
# so shutdown() can wait() the corpse — without it the killed child lingers as
# a zombie and ``pid_alive`` keeps saying True. Survives only within one
# python session; across plugin reloads the pidfile is the source of truth.
_live_proc = None


# -- runtime state (pidfile) ----------------------------------------------------

def state_dir() -> str:
    """Per-user dir for the runtime state (``KICAD_MCP_STATE_DIR`` override)."""
    override = os.environ.get(STATE_DIR_ENV, "").strip()
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "kicad-claude")
    return os.path.join(os.path.expanduser("~"), ".local", "state",
                        "kicad-claude")


def state_path() -> str:
    return os.path.join(state_dir(), STATE_FILENAME)


def read_state() -> dict:
    """The recorded server state ``{pid, port, token, started}``, or ``{}``."""
    try:
        with open(state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_state(state: dict) -> str:
    path = state_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    return path


def clear_state() -> None:
    try:
        os.remove(state_path())
    except Exception:
        pass


# -- small pure helpers ----------------------------------------------------------

def pick_free_port(host: str = DEFAULT_HOST) -> int:
    """Bind ``host:0``, read the port, release it (tiny reuse race accepted —
    ``ensure_running`` verifies the started server actually answers)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def server_url(port: int, host: str = DEFAULT_HOST) -> str:
    """FastMCP's streamable-http endpoint lives under ``/mcp`` (canonical —
    ``/mcp/`` answers with a 307 redirect some clients won't follow on POST).
    """
    return f"http://{host}:{port}/mcp"


def new_token() -> str:
    """A random per-start bearer token (the local-process access gate)."""
    return secrets.token_urlsafe(24)


def _pid_alive_nt(pid: int) -> bool:
    """Windows-Liveness über ``OpenProcess`` — ``os.kill(pid, 0)`` ist unter
    Windows KEIN Existenz-Check: es schlug im Feld mit WinError 6 („Das
    Handle ist ungültig") fehl, unter KiCads eingebettetem Python sogar als
    ``SystemError`` (kein ``OSError``-Subtyp!), der jedem ``except OSError``
    entkam und den Diagnose-Dialog crashte."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited = 0x1000
    still_active = 259
    handle = k32.OpenProcess(process_query_limited, False, int(pid))
    if not handle:
        # ERROR_ACCESS_DENIED (5): Prozess existiert, gehört jemand anderem
        return ctypes.get_last_error() == 5
    try:
        code = wintypes.DWORD()
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == still_active
        return True  # Handle offen, Exit-Code nicht lesbar → als lebend werten
    finally:
        k32.CloseHandle(handle)


def pid_alive(pid: Any) -> bool:
    """Best-effort liveness — POSIX ``os.kill(pid, 0)``, Windows ``OpenProcess``
    (siehe :func:`_pid_alive_nt`). Wirft NIE: der Health-Check läuft in
    Status-/Diagnose-Pfaden, ein Prüf-Fehler darf dort keinen Dialog töten."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            return _pid_alive_nt(pid)
        os.kill(pid, 0)
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    except Exception:
        # Feld-Fall: SystemError aus os.kill im eingebetteten Python — lieber
        # konservativ „nicht lebend" melden als die Diagnose crashen.
        return False
    return True


def port_open(port: int, host: str = DEFAULT_HOST, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def is_healthy(state: dict, _port_open=None) -> bool:
    """Recorded server still alive AND accepting connections?"""
    check = _port_open if _port_open is not None else port_open
    return bool(state.get("pid") and state.get("port")
                and pid_alive(state.get("pid"))
                and check(state.get("port")))


# -- process control --------------------------------------------------------------

def _kill_pid_tree(pid: Any) -> None:
    """Kill a recorded pid + its children (mirror of claude_bridge._kill_tree,
    which needs a Popen object we don't have across plugin reloads)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                timeout=10, check=False, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **hidden_console_kwargs(),
            )
        else:
            import signal
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def default_mcp_root() -> str:
    """Where the kicad-mcp package lives: ``KICAD_MCP_ROOT`` env (if it really
    contains ``kicad_mcp/``), else the copy bundled inside the plugin."""
    env = os.environ.get("KICAD_MCP_ROOT", "").strip()
    if env and os.path.isdir(os.path.join(env, "kicad_mcp")):
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp")


def build_server_cmd(python_exe: str, mcp_root: str,
                     deps_dir: Optional[str], port: int,
                     host: str = DEFAULT_HOST) -> list:
    """argv for the warm server: the SAME in-process sys.path bootstrap the
    stdio config uses (KiCad's Python ignores PYTHONPATH), plus the http args
    (``main()`` parses them after the ``-c`` code)."""
    return [python_exe, "-c",
            mcp_config.server_bootstrap_code(mcp_root, deps_dir),
            "--transport", "streamable-http",
            "--host", host, "--port", str(port)]


def _register_shutdown() -> None:
    global _shutdown_registered
    if not _shutdown_registered:
        atexit.register(shutdown)
        _shutdown_registered = True


def ensure_running(
    mcp_root: Optional[str] = None,
    python_exe: Optional[str] = None,
    deps_dir: Optional[str] = None,
    host: str = DEFAULT_HOST,
    timeout: float = START_TIMEOUT_S,
    _popen=subprocess.Popen,
    _port_open=port_open,
    _sleep=time.sleep,
    _monotonic=time.monotonic,
    _probe=server_probe.probe_http,
) -> dict:
    """Reuse the healthy warm server, or start one. Never raises.

    Returns ``{ok, url, port, pid, token, reused, error}``. Health = recorded
    pid alive + port accepting connections + the server ANSWERS an MCP
    ``initialize`` (``_probe``). Der reine pid+port-Check war im Feld blind:
    ein Prozess kann den Port halten, ohne MCP zu beantworten (wedged, fremder
    Port-Nachnutzer, Token-Drift) — claude meldete dann in JEDEM Turn
    "failed: kicad-mcp", und weil der Server "gesund" aussah, wurde er nie
    ersetzt (E2E-Feld-Report: 34/34 mcp-nicht-verbunden). A dead/hung/mute
    leftover is killed and replaced — no double servers, no orphans.
    """
    global _live_proc
    out = {"ok": False, "url": "", "port": 0, "pid": 0, "token": "",
           "reused": False, "error": ""}
    state = read_state()
    if state:
        healthy = is_healthy(state, _port_open=_port_open)
        if healthy:
            url = server_url(int(state["port"]), host)
            ping = _probe(url, str(state.get("token", "")),
                          timeout=PING_TIMEOUT_S)
            healthy = bool(ping.get("ok"))
        if healthy:
            _register_shutdown()  # a reloaded plugin must still clean up
            out.update(ok=True, reused=True, pid=int(state["pid"]),
                       port=int(state["port"]),
                       token=str(state.get("token", "")),
                       url=server_url(int(state["port"]), host))
            return out
        # stale entry: pid dead, or alive but not answering → clear the ground
        if pid_alive(state.get("pid")):
            _kill_pid_tree(state.get("pid"))
        if _live_proc is not None and _live_proc.pid == state.get("pid"):
            try:
                _live_proc.wait(timeout=10)  # reap the zombie we parented
            except Exception:
                pass
            _live_proc = None
        clear_state()

    python_exe = python_exe or mcp_config.find_kicad_python()
    if not python_exe:
        out["error"] = ("KiCad-Python nicht gefunden — setze "
                        "KICAD_PYTHON_PATH.")
        return out
    mcp_root = mcp_root or default_mcp_root()
    if not os.path.isdir(os.path.join(mcp_root, "kicad_mcp")):
        out["error"] = f"kicad_mcp-Paket fehlt unter: {mcp_root}"
        return out
    if deps_dir is None:
        deps_dir = deps.active_deps_dir()

    port = pick_free_port(host)
    token = new_token()
    env = dict(os.environ)
    env["KICAD_MCP_HTTP_TOKEN"] = token
    env["PYTHONUNBUFFERED"] = "1"
    # Chat läuft in der KiCad-GUI: der warme Server darf NIE einen zweiten
    # Editor auto-spawnen (zwei Instanzen auf dem Bus = alle Links tot).
    env["KICAD_MCP_NO_AUTO_OPEN"] = "1"
    # belt-and-suspenders for pythons that DO honor it (bootstrap is in-process)
    env["PYTHONPATH"] = mcp_root + (os.pathsep + deps_dir if deps_dir else "")
    try:
        proc = _popen(
            build_server_cmd(python_exe, mcp_root, deps_dir, port, host),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=env,
            start_new_session=True,  # POSIX: own group so killpg reaps the tree
            **hidden_console_kwargs(),
        )
    except Exception as exc:
        out["error"] = f"Server-Start fehlgeschlagen: {exc}"
        return out

    deadline = _monotonic() + timeout
    port_ready = False
    while _monotonic() < deadline:
        poll = getattr(proc, "poll", lambda: None)()
        if poll is not None:
            out["error"] = (f"Server beendete sich sofort (exit {poll}) — "
                            "Diagnose-Knopf zeigt den Grund.")
            return out
        if not port_ready:
            port_ready = _port_open(port)
        if port_ready:
            # Port offen ist nur die halbe Wahrheit — erst wenn der Server
            # ein MCP-initialize beantwortet, darf claude auf ihn zeigen.
            ping = _probe(server_url(port, host), token,
                          timeout=PING_TIMEOUT_S)
            if ping.get("ok"):
                break
        _sleep(0.3)
    else:
        _kill_pid_tree(proc.pid)
        what = ("beantwortet kein MCP-initialize auf" if port_ready
                else "öffnete")
        out["error"] = (f"Server {what} Port {port} nicht innerhalb "
                        f"{int(timeout)}s.")
        return out

    _live_proc = proc
    write_state({"pid": proc.pid, "port": port, "token": token,
                 "started": time.time(), "transport": "streamable-http"})
    _register_shutdown()
    out.update(ok=True, pid=proc.pid, port=port, token=token,
               url=server_url(port, host))
    return out


def shutdown() -> bool:
    """Stop the recorded warm server (KiCad close / explicit teardown).

    Idempotent; safe with no server running. Returns True if one was killed.
    """
    global _live_proc
    state = read_state()
    if not state:
        return False
    killed = False
    if pid_alive(state.get("pid")):
        _kill_pid_tree(state.get("pid"))
        killed = True
    # reap our own child, else the killed server lingers as a POSIX zombie
    # (pid_alive would keep reporting it as running)
    if _live_proc is not None:
        try:
            _live_proc.wait(timeout=10)
        except Exception:
            pass
        _live_proc = None
    clear_state()
    return killed


def status() -> dict:
    """Live status for the diagnose report: ``{running, pid, port, url,
    uptime_s, transport}`` — the info this whole debug odyssey was missing."""
    state = read_state()
    running = is_healthy(state)
    started = state.get("started") or 0
    return {
        "running": running,
        "pid": int(state.get("pid") or 0),
        "port": int(state.get("port") or 0),
        "url": server_url(int(state["port"])) if state.get("port") else "",
        "uptime_s": int(time.time() - started) if (running and started) else 0,
        "transport": str(state.get("transport") or ""),
    }
