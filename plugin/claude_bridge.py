# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge: drive the Claude Code CLI headlessly from the KiCad plugin.

Each chat message becomes one ``claude -p`` invocation that loads the bundled
kicad-mcp server, runs against the open board, and returns the final text.
The session id from the first reply is reused (``--resume``) so the separate
invocations form ONE conversation. No Anthropic API key — this uses the
user's Claude Code subscription.

Output is consumed as **stream-json**: the panel sees live progress (which
tool runs right now, whether the MCP connected) and the timeout is based on
INACTIVITY, not total duration — long-but-alive board work (OneDrive cold
reads can take 80s+ per file) is never killed mid-flight anymore.

Pure logic (subprocess + JSON parsing); no KiCad/wx imports, so it is unit
testable headless.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Optional

# Abort only when claude produced NO event for this long (a working turn
# streams continuously); the hard cap is a safety net against zombies.
IDLE_TIMEOUT_S = 180.0
MAX_TURN_S = 1800.0


def find_claude() -> Optional[list[str]]:
    """Locate the Claude Code CLI. Returns the command prefix (list) or None.

    Tries a native ``claude`` on PATH first; falls back to ``wsl claude`` so a
    Windows KiCad can reach a Claude Code installed inside WSL.
    """
    for cand in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(cand)
        if found:
            return [found]
    wsl = shutil.which("wsl") or shutil.which("wsl.exe")
    if wsl:
        return [wsl, "claude"]
    return None


def hidden_console_kwargs(os_name: str = os.name) -> dict[str, Any]:
    """Extra ``subprocess`` kwargs so the ``claude`` child stays invisible.

    KiCad is a GUI process; without this, Windows pops up a black console
    window for every chat turn (also for the ``wsl claude`` fallback) that
    flashes open and shut while the reply is computed. ``CREATE_NO_WINDOW``
    suppresses it; the output already flows into the chat panel via pipes.
    """
    if os_name == "nt":
        return {
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        }
    return {}


# Claude must NEVER text-edit project files itself: without (or even with)
# the MCP it would otherwise "helpfully" patch .kicad_pcb/.kicad_sch/.kicad_pro
# directly — KiCad sees external edits on open documents and nags about
# unsaved changes; and hand-patched geometry is exactly what the MCP server
# exists to prevent. Mutations go through MCP tools only; reading stays
# allowed (Read/Grep/Glob are useful and harmless).
FORBIDDEN_BUILTIN_TOOLS = "Bash,Edit,Write,MultiEdit,NotebookEdit"


def build_command(
    claude: list[str],
    prompt: str,
    mcp_config_path: str,
    session_id: Optional[str],
    extra_args: Optional[list[str]] = None,
) -> list[str]:
    """Build the ``claude`` argv for one chat turn (stream-json output).

    ``extra_args`` are raw Claude Code CLI switches the user supplied (e.g.
    ``["--model", "sonnet"]``); appended last so they can extend the turn.
    """
    cmd = list(claude) + [
        "-p", prompt,
        "--mcp-config", mcp_config_path,
        "--strict-mcp-config",            # ONLY the bundled kicad-mcp
        "--dangerously-skip-permissions",  # headless: no TTY to approve tools
        "--disallowedTools", FORBIDDEN_BUILTIN_TOOLS,
        "--output-format", "stream-json",
        "--verbose",                      # claude requires it for stream-json
    ]
    if session_id:
        cmd += ["--resume", session_id]   # continue the same conversation
    if extra_args:
        cmd += list(extra_args)
    return cmd


# -- stream-json parsing -------------------------------------------------------

def parse_stream_event(line: str) -> Optional[dict]:
    """One stream-json line -> event dict, or None for non-JSON noise."""
    line = (line or "").strip()
    if not line.startswith("{"):
        return None
    try:
        ev = json.loads(line)
    except Exception:
        return None
    return ev if isinstance(ev, dict) else None


def _tool_short_name(name: str) -> str:
    """``mcp__kicad-mcp__list_pcb_footprints`` -> ``list_pcb_footprints``."""
    return name.split("__")[-1] if name else "?"


def mcp_status_from_init(ev: dict) -> Optional[str]:
    """``"connected"`` / ``"failed: <name>"`` from a system-init event."""
    if ev.get("type") != "system" or ev.get("subtype") != "init":
        return None
    servers = ev.get("mcp_servers") or []
    bad = [s.get("name", "?") for s in servers
           if s.get("status") not in ("connected", "ok")]
    if bad:
        return "failed: " + ", ".join(bad)
    return "connected" if servers else "none"


def describe_event(ev: dict) -> Optional[str]:
    """A short German activity line for the status bar, or None."""
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        status = mcp_status_from_init(ev)
        if status == "connected":
            return "MCP verbunden — Claude liest dein Board …"
        if status and status.startswith("failed"):
            return "⚠ MCP NICHT verbunden!"
        return "gestartet …"
    if t == "assistant":
        blocks = (ev.get("message") or {}).get("content") or []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                return f"Tool {_tool_short_name(b.get('name', ''))} …"
        if any(isinstance(b, dict) and b.get("type") == "text"
               for b in blocks):
            return "formuliert die Antwort …"
    if t == "user":
        return "Tool-Ergebnis erhalten …"
    return None


def extract_text(ev: dict) -> str:
    """Concatenated text blocks of an assistant event (fallback collector)."""
    if ev.get("type") != "assistant":
        return ""
    blocks = (ev.get("message") or {}).get("content") or []
    return "".join(b.get("text", "") for b in blocks
                   if isinstance(b, dict) and b.get("type") == "text")


def tool_names(ev: dict) -> list[str]:
    """Short names of every tool the assistant invokes in this event
    (``mcp__kicad-mcp__list_pcb_footprints`` → ``list_pcb_footprints``)."""
    if ev.get("type") != "assistant":
        return []
    blocks = (ev.get("message") or {}).get("content") or []
    return [_tool_short_name(b.get("name", ""))
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "tool_use"]


# -- child-process lifecycle ---------------------------------------------------
# The claude child (and its MCP grandchild) are spawned from inside KiCad. If
# KiCad closes mid-turn, Windows does NOT auto-kill them — they'd orphan. We
# track every live turn and tear the whole tree down on panel-close / KiCad
# exit (atexit), so nothing survives KiCad.

_LIVE_LOCK = threading.Lock()
_LIVE: set = set()


def _register(proc) -> None:
    with _LIVE_LOCK:
        _LIVE.add(proc)


def _unregister(proc) -> None:
    with _LIVE_LOCK:
        _LIVE.discard(proc)


def _kill_tree(proc) -> None:
    """Kill ``proc`` AND its children (the MCP server is claude's child)."""
    if proc is None:
        return
    poll = getattr(proc, "poll", None)
    if callable(poll) and poll() is not None:
        return  # already exited
    try:
        if os.name == "nt":
            # /T kills the whole tree (claude + python MCP), /F forces it.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                timeout=10, check=False, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **hidden_console_kwargs(),
            )
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def terminate_all() -> int:
    """Kill every in-flight claude turn + its MCP child. Returns how many.

    Called on chat-panel close and (via atexit) on KiCad shutdown, so neither
    claude nor the MCP server outlive KiCad. Idempotent and safe to call with
    nothing running.
    """
    with _LIVE_LOCK:
        procs = list(_LIVE)
        _LIVE.clear()
    for proc in procs:
        _kill_tree(proc)
    return len(procs)


def stop(proc) -> None:
    """Stop ONE in-flight turn (the user pressed Stopp): kill its tree +
    untrack it. The worker's ``ask`` then returns as the stream ends."""
    if proc is None:
        return
    _kill_tree(proc)
    _unregister(proc)


atexit.register(terminate_all)


# -- the turn ------------------------------------------------------------------

def _pump(stream, q: "queue.Queue") -> None:
    try:
        for line in stream:
            q.put(line)
    except Exception:
        pass
    q.put(None)  # EOF marker


def ask(
    prompt: str,
    project_dir: str,
    mcp_config_path: str,
    session_id: Optional[str] = None,
    idle_timeout: float = IDLE_TIMEOUT_S,
    max_seconds: float = MAX_TURN_S,
    claude_cmd: Optional[list[str]] = None,
    extra_args: Optional[list[str]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    on_tool: Optional[Callable[[str], None]] = None,
    on_proc: Optional[Callable[[Any], None]] = None,
    _popen=subprocess.Popen,
) -> dict[str, Any]:
    """Run one chat turn, streaming progress via ``on_status(text)`` and each
    tool call via ``on_tool(name)``.

    Returns ``{ok, text, session_id, error, mcp_status}``. The turn is only
    aborted when claude is SILENT for ``idle_timeout`` seconds (or exceeds the
    ``max_seconds`` safety cap) — a working turn streams events continuously,
    so honest long board work survives. ``extra_args`` are raw Claude CLI
    switches; ``on_proc(proc)`` hands the live process to the caller so a
    Stopp button can kill it. ``_popen`` is injectable for tests.
    """
    out: dict[str, Any] = {"ok": False, "text": "", "session_id": session_id,
                           "error": "", "mcp_status": ""}
    claude = claude_cmd or find_claude()
    if claude is None:
        out["error"] = ("Claude Code (claude) nicht gefunden. Installiere "
                        "Claude Code und melde dich einmal an (claude login).")
        return out
    cmd = build_command(claude, prompt, mcp_config_path, session_id, extra_args)
    env = dict(os.environ)
    # A cold KiCad-Python start (165 tools, synced disks) can exceed claude's
    # default MCP startup timeout — and a too-slow server is dropped SILENTLY
    # (chat without board tools). Generous headroom, user override wins.
    env.setdefault("MCP_TIMEOUT", "120000")  # ms
    try:
        proc = _popen(
            cmd, cwd=project_dir, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env,
            # POSIX: own session/group so _kill_tree can killpg the MCP child;
            # on Windows this is a no-op (the tree is killed via taskkill /T).
            start_new_session=True,
            **hidden_console_kwargs(),
        )
    except Exception as exc:
        out["error"] = f"Start fehlgeschlagen: {exc}"
        return out

    _register(proc)  # tracked so KiCad-close / atexit can tear the tree down
    if on_proc:
        try:
            on_proc(proc)  # hand the live process to the caller (Stopp button)
        except Exception:
            pass
    try:
        return _run_turn(proc, out, idle_timeout, max_seconds, on_status,
                         on_tool)
    finally:
        _unregister(proc)


def _run_turn(proc, out, idle_timeout, max_seconds, on_status, on_tool=None):
    """Drive one started turn to completion (split out so ``ask`` can wrap it
    in register/unregister)."""
    q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_pump, args=(proc.stdout, q), daemon=True).start()

    started = time.time()
    texts: list[str] = []
    result_ev: Optional[dict] = None
    while True:
        if time.time() - started > max_seconds:
            proc.kill()
            out["error"] = (f"Abbruch: Limit von {int(max_seconds)}s "
                            "überschritten.")
            return out
        try:
            line = q.get(timeout=idle_timeout)
        except queue.Empty:
            proc.kill()
            out["error"] = (
                f"Abbruch: {int(idle_timeout)}s ohne Lebenszeichen von "
                "Claude. Häufigste Ursachen: Projektordner nicht vertraut "
                "(einmal 'claude' interaktiv im Projektordner starten) oder "
                "Login abgelaufen ('claude login')."
            )
            return out
        if line is None:
            break
        ev = parse_stream_event(line)
        if ev is None:
            continue
        status = mcp_status_from_init(ev)
        if status is not None:
            out["mcp_status"] = status
        desc = describe_event(ev)
        if desc and on_status:
            try:
                on_status(desc)
            except Exception:
                pass
        if on_tool:
            for name in tool_names(ev):
                try:
                    on_tool(name)
                except Exception:
                    pass
        if ev.get("type") == "assistant":
            t = extract_text(ev)
            if t:
                texts.append(t)
        if ev.get("type") == "result":
            result_ev = ev
        sid = ev.get("session_id")
        if sid:
            out["session_id"] = sid

    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()

    if result_ev is not None:
        if isinstance(result_ev.get("result"), str) and result_ev["result"]:
            out["text"] = result_ev["result"]
        elif texts:
            out["text"] = "\n".join(texts)
        if result_ev.get("subtype", "success") != "success" and not out["text"]:
            out["error"] = result_ev.get("error") or str(result_ev.get(
                "subtype"))
            return out
        out["ok"] = True
        return out
    if texts:  # stream ended without result event — use what we saw
        out["text"] = "\n".join(texts)
        out["ok"] = True
        return out
    stderr = ""
    try:
        stderr = (proc.stderr.read() or "").strip()
    except Exception:
        pass
    out["error"] = (stderr or "claude beendete ohne Antwort")[:800]
    return out
