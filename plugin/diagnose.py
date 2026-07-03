# SPDX-License-Identifier: GPL-3.0-or-later
"""One-click diagnosis: EVERYTHING needed to debug "MCP läuft nicht" in einem
einzigen kopierbaren Report.

Nach mehreren Update-Runden ohne Treffer war klar: Einzelzeilen aus dem Panel
abzutippen skaliert nicht. Der Report sammelt Pfade (Plugin, mcp_root, _deps,
Python, Claude), Versionen, die Env-Overrides, das Ergebnis der echten
Server-Probe inklusive **vollem** Stderr und ein copy-paste-Rezept, um den
Serverstart manuell in cmd.exe nachzustellen.

Pure logic (injectable runner); unit-testable headless. Der wx-Knopf dazu
lebt in :mod:`setup_dialog`.
"""

from __future__ import annotations

import os
import platform
import subprocess

from . import deps, mcp_config, runtime_env, server_manager, server_probe
from .claude_bridge import hidden_console_kwargs
from .version import __version__


def _run_capture(cmd, timeout: float = 30.0, _run=subprocess.run) -> str:
    try:
        proc = _run(cmd, capture_output=True, text=True, timeout=timeout,
                    check=False, **hidden_console_kwargs())
        text = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return text or f"(leer, exit {getattr(proc, 'returncode', '?')})"
    except Exception as exc:
        return f"(fehlgeschlagen: {exc})"


def _listdir_head(path: str, n: int = 12) -> str:
    try:
        names = sorted(os.listdir(path))
    except Exception as exc:
        return f"(nicht lesbar: {exc})"
    more = f" … (+{len(names) - n} weitere)" if len(names) > n else ""
    return ", ".join(names[:n]) + more


def _transport_section() -> str:
    """The warm-server status block — läuft? PID, Port, Uptime, Transport.

    Genau die Info, die die MCP-Debug-Odyssee gespart hätte: in stdio-Modus
    steht hier, dass claude den Server pro Nachricht selbst startet; im
    http-Modus, ob der persistente Server wirklich lebt (Pidfile + Port +
    echter MCP-Ping).
    """
    mode = runtime_env.transport_mode()
    lines = [f"Transport (KICAD_MCP_TRANSPORT): {mode}"]
    if mode != runtime_env.TRANSPORT_HTTP:
        lines.append("  stdio: claude startet den Server pro Nachricht "
                     "selbst (kein Warm-Server).")
        return "\n".join(lines)
    st = server_manager.status()
    if not st["running"]:
        lines.append("  Warm-Server: LÄUFT NICHT (wird beim nächsten "
                     "Chat-Turn automatisch gestartet).")
        return "\n".join(lines)
    lines.append(f"  Warm-Server: läuft — PID {st['pid']}, Port {st['port']}, "
                 f"Uptime {st['uptime_s']}s, {st['transport']}")
    lines.append(f"  URL: {st['url']}")
    token = server_manager.read_state().get("token", "")
    ping = server_probe.probe_http(st["url"], token)
    if ping["ok"]:
        lines.append(f"  MCP-Ping: OK ({ping['seconds']}s)")
    else:
        lines.append(f"  MCP-Ping: FEHLER — {ping['error']}")
    return "\n".join(lines)


def collect(mcp_root: str, project_dir: str, _run=subprocess.run) -> str:
    """Build the full plain-text diagnosis report (runs the server probe)."""
    py = mcp_config.find_kicad_python()
    deps_dir = deps.active_deps_dir()
    claude = runtime_env.find_claude()
    pythonpath = (mcp_root + (os.pathsep + deps_dir if deps_dir else "")
                  if mcp_root else "")
    pkg = os.path.join(mcp_root, "kicad_mcp")

    lines: list[str] = []
    add = lines.append
    add(f"=== kicad-claude Diagnose (Plugin v{__version__}) ===")
    add(f"OS: {platform.platform()}")
    add(f"Plugin-Ordner: {deps.PLUGIN_DIR}")
    add(f"Projekt:       {project_dir}")
    add("")
    add(f"KiCad-Python:  {py or 'NICHT GEFUNDEN'}")
    if py:
        add(f"  Version: {_run_capture([py, '--version'], _run=_run)}")
    add("Env-Overrides:")
    add("  KICAD_PYTHON_PATH = "
        + (os.environ.get("KICAD_PYTHON_PATH") or "(nicht gesetzt)"))
    add("  KICAD_MCP_ROOT    = "
        + (os.environ.get("KICAD_MCP_ROOT") or "(nicht gesetzt)"))
    add("")
    add(f"mcp_root (Server-Code): {mcp_root}")
    add(f"  kicad_mcp/ vorhanden: {'JA' if os.path.isdir(pkg) else 'NEIN'}")
    if os.path.isdir(pkg):
        add(f"  Inhalt: {_listdir_head(pkg)}")
    add("")
    add(f"_deps-Ordner: {deps.default_target_dir()}")
    add(f"  vorhanden: {'JA' if deps_dir else 'NEIN'}")
    if deps_dir:
        add(f"  Inhalt: {_listdir_head(deps_dir)}")
    add("")
    add(f"Claude Code: {' '.join(claude) if claude else 'NICHT GEFUNDEN'}")
    if claude:
        add("  Version: "
            + _run_capture(list(claude) + ["--version"], _run=_run))
    add("")
    add(_transport_section())
    add("")
    add("--- MCP-Server-Probe (stdio-Startpfad — auch im http-Modus "
        "der Fallback) ---")
    add(f"PYTHONPATH: {pythonpath}")
    res = server_probe.probe_server(py, mcp_root)
    secs = res.get("seconds", 0.0)
    if res.get("ok"):
        add(f"Ergebnis: OK — initialize + tools/list (167) in {secs}s.")
        add(f"  (Claude-Start-Timeout: {mcp_config.MCP_STARTUP_TIMEOUT_MS}ms. "
            "Ist diese Zeit beim 1. Mal viel höher = Kaltstart-/Defender-"
            "Problem → _deps- und mcp-Ordner in Defender ausschließen.)")
    else:
        add(f"Ergebnis: FEHLER (nach {secs}s)")
        add(f"Fehler: {res.get('error', '')}")
        full = (res.get("stderr") or "").strip()
        if full:
            add("Voller Server-Stderr:")
            add(full)
    add("")
    add("--- Manuell nachstellen (cmd.exe) ---")
    bootstrap = mcp_config.server_bootstrap_code(mcp_root, deps_dir)
    add(f'"{py}" -c "{bootstrap}"')
    add("(KiCads Python ignoriert PYTHONPATH — deshalb der sys.path-"
        "Bootstrap. Wartet der Server still auf Eingaben = OK, mit Strg+C "
        "beenden. Ein Fehler erscheint direkt als Traceback.)")
    return "\n".join(lines)
