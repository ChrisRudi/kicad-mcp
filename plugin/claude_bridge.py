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


# Claude must NEVER text-edit project files or run a shell itself: it would
# otherwise patch .kicad_pcb/.kicad_sch directly or shell out — KiCad nags about
# external edits on open documents, and hand-patched geometry is exactly what
# the MCP server exists to prevent. Board changes go through MCP tools only;
# reading stays allowed (Read/Grep/Glob are useful and harmless).
#
# Two correctness facts the earlier version got wrong and burned a whole
# session on:
#  * ``--disallowedTools`` takes ONE tool name per argv value (space-separated
#    items). A single comma-joined string ("Bash,Edit,…") matches NOTHING and
#    blocks nothing — that is how Write/PowerShell slipped through.
#  * On Windows the shell tool is ``PowerShell`` when Git-for-Windows is absent
#    and ``Bash`` when present — deny BOTH.
# Deny rules are enforced even under --dangerously-skip-permissions (deny has
# the highest precedence), so this list is the real security boundary.
FORBIDDEN_BUILTIN_TOOLS = [
    "Bash", "PowerShell", "Edit", "Write", "MultiEdit", "NotebookEdit",
]

# ``claude -p`` loads CLAUDE.md from its cwd (the user's board folder), NOT from
# this repo — so the project's anti-toolcall-explosion rules never reach the
# agent. Inject the essentials per turn via --append-system-prompt. The "no
# tools → say so, don't guess/flail" rule is what stops a dropped MCP from
# turning into a 30-minute runaway.
BEHAVIOR_SYSTEM_PROMPT = (
    "Du bist ein erfahrener Senior-PCB-/Platinen-Entwickler und arbeitest an "
    "einem in KiCad GEÖFFNETEN PCB ausschließlich über die kicad-mcp-Tools. "
    "Strikte Regeln: "
    "(1) Board-Änderungen NUR über MCP-Tools — niemals Datei-Schreiben oder "
    "Shell. Das Board ist offen: mutiere ausschließlich über die Live-Tools "
    "(ipc_*/live_*); die Text-Patcher (*_text, pcb_batch) sind bei offenem "
    "Board hart geblockt (BoardOpenError) — gar nicht erst versuchen. "
    "(2) Fehlen die MCP/Board-Tools (kein 'mcp__'-Tool verfügbar), sage das "
    "in EINEM Satz und höre auf — nicht raten, nicht behelfsweise per Shell/"
    "Datei arbeiten. "
    "(3) pcb_render ist der teuerste Call: nie nach einer Einzelmutation, nur "
    "am Abschluss aller Mutationen oder auf ausdrückliche Aufforderung. "
    "(4) Korrektheit per check_connectivity prüfen, NICHT per Render; lies das "
    "Ergebnis eines Mutations-Tools statt den State zurückzulesen. "
    "(5) Gleichartige Mutationen bündeln (z. B. add_vias_to_pcb statt N× "
    "add_via_to_pcb), dann EINMAL füllen und EINMAL verifizieren. "
    "(6) Kein Fortschritt nach wenigen Versuchen? Abbrechen und kurz "
    "berichten/fragen — kein Probieren über Dutzende Calls. "
    "(7) Erst das passende Tool suchen, nicht selbst aus der Datei parsen: "
    "Board lesen → list_pcb_footprints/analyze_pcb_nets; Pad-/Pin-Welt-"
    "koordinaten → compute_pad_world_positions/ipc_get_pad_world_pos; "
    "Konnektivität bzw. 'ist dieses Via load-bearing?' → check_connectivity; "
    "Layout sehen → pcb_render; mehrere Live-Mutationen → die ipc_*-Tools. "
    "(8) Benenne Board-Elemente AUSSCHLIESSLICH mit ihrem kanonischen "
    "KiCad-Token, damit sie im Chat klickbar werden: Footprints als bare "
    "Reference (R12, U8); Netze mit dem EXAKTEN Netznamen aus der Tool-Ausgabe "
    "(nicht paraphrasieren, nicht übersetzen, kein führender Slash); Layer "
    "kanonisch (F.Cu, B.Cu, In1.Cu); Pins als <ref>.<pin> (U1.33); Koordinaten "
    "als (x, y) in mm. Übernimm Namen aus Tool-Ergebnissen WÖRTLICH."
)

# Hard cap on agentic turns so a stuck task can't loop for the whole idle
# budget (the failed session ran ~60 calls to the 30-min wall). Generous by
# default (real board work needs many turns); override via env, 0 = off.
_MAX_TURNS_ENV = "KICAD_MCP_MAX_TURNS"
DEFAULT_MAX_TURNS = 80


def max_turns() -> int:
    """Agentic-turn cap (``KICAD_MCP_MAX_TURNS`` or the default; 0 = off)."""
    raw = os.environ.get(_MAX_TURNS_ENV, "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_MAX_TURNS


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
        # one tool name PER value (NOT comma-joined) — see the constant's note
        "--disallowedTools", *FORBIDDEN_BUILTIN_TOOLS,
        "--append-system-prompt", BEHAVIOR_SYSTEM_PROMPT,
        "--output-format", "stream-json",
        "--verbose",                      # claude requires it for stream-json
    ]
    turns = max_turns()
    if turns > 0:
        cmd += ["--max-turns", str(turns)]
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
                return describe_tool(_tool_short_name(b.get("name", "")),
                                     b.get("input")) + " …"
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


def tool_calls(ev: dict) -> list:
    """``[(short_name, input_dict), …]`` for every tool the assistant invokes
    in this event — the input lets the panel narrate in board language and
    collect what changed (see :func:`describe_tool` / :func:`changed_targets`)."""
    if ev.get("type") != "assistant":
        return []
    blocks = (ev.get("message") or {}).get("content") or []
    out = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            inp = b.get("input")
            out.append((_tool_short_name(b.get("name", "")),
                        inp if isinstance(inp, dict) else {}))
    return out


def _as_list(val) -> list:
    """A tool arg that is a list, or a JSON-string list, else []. MCP tools take
    JSON-string args, so ``"[{...}]"`` and ``[{...}]`` must both count."""
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val.strip().startswith("["):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


# What the agent DID, in the board owner's language — keyed by MCP tool.
# ``(template, count_key)``: a ``{n}`` in the template is filled from the length
# of the ``count_key`` input list (defaulting to 1); ``None`` = no count.
_TOOL_VERBS = {
    "add_vias_to_pcb": ("{n}× Via gesetzt", "vias"),
    "add_via_to_pcb": ("Via gesetzt", None),
    "move_components": ("{n}× Bauteil verschoben", "moves"),
    "ipc_move_items": ("{n}× Element verschoben", "uuids"),
    "live_move_footprint": ("Bauteil verschoben", None),
    "ipc_set_footprint_pose": ("Bauteil neu platziert", None),
    "ipc_route_pin_to_pin": ("Leiterbahn verlegt", None),
    "add_track_to_pcb": ("Leiterbahn verlegt", None),
    "add_arc_to_pcb": ("Bogen verlegt", None),
    "ipc_route_power_ring": ("Power-Ring verlegt", None),
    "ipc_markup_to_tracks": ("Markup in Kupfer umgesetzt", None),
    "add_zone_pour_to_pcb": ("Kupferfläche (Zone) gelegt", None),
    "ipc_add_zone_pour": ("Kupferfläche (Zone) gelegt", None),
    "ipc_remove_items": ("{n}× Element entfernt", "uuids"),
    "ipc_set_track_width": ("Track-Breite gesetzt", None),
    "set_properties": ("Eigenschaft gesetzt", None),
    "via_promote": ("Vias optimiert", None),
}
# Read/verify tools — a single calm "reads/checks" line, no alarm.
_TOOL_READS = {
    "list_pcb_footprints": "liest die Bauteile",
    "analyze_pcb_nets": "liest die Netze",
    "find_tracks_by_net": "liest die Leiterbahnen",
    "check_connectivity": "prüft die Konnektivität",
    "run_drc_check": "prüft die Design-Regeln (DRC)",
    "ipc_run_drc": "prüft die Design-Regeln (DRC)",
    "run_erc": "prüft den Schaltplan (ERC)",
    "pcb_render": "rendert das Layout",
    "ipc_get_selection": "liest deine Auswahl",
    "ipc_inspect_item": "inspiziert ein Element",
}


def describe_tool(name: str, tool_input=None) -> str:
    """One short German line in board language for a tool call — what the agent
    is doing, not the raw tool name. Falls back to a humanised tool name for
    tools not in the tables, so nothing ever shows a bare ``mcp__…`` slug."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    if name in _TOOL_VERBS:
        template, count_key = _TOOL_VERBS[name]
        if "{n}" in template:
            n = len(_as_list(inp.get(count_key))) or 1
            return template.format(n=n)
        return template
    if name in _TOOL_READS:
        return _TOOL_READS[name]
    return name.replace("_", " ")


def changed_targets(name: str, tool_input=None) -> list:
    """Clickable board targets a *mutation* tool touched, as ``(kind, value)``
    tuples in the panel's link format (``("ref","R12")`` / ``("net","GND")`` /
    ``("coord",(x,y))`` / ``("pin",(ref,pin))``). Feeds the "[zeigen]" affordance
    on a change receipt so the user can see exactly what the agent changed.
    Read-only tools contribute nothing (not in ``_TOOL_VERBS``)."""
    if name not in _TOOL_VERBS:
        return []
    inp = tool_input if isinstance(tool_input, dict) else {}
    out: list = []

    def add(t):
        if t not in out:
            out.append(t)

    def from_obj(o):
        if not isinstance(o, dict):
            return
        for k in ("ref", "reference", "component"):
            if isinstance(o.get(k), str) and o[k]:
                add(("ref", o[k]))
        for k in ("net_name", "net"):
            if isinstance(o.get(k), str) and o[k]:
                add(("net", o[k]))
        if o.get("x_mm") is not None and o.get("y_mm") is not None:
            try:
                add(("coord", (float(o["x_mm"]), float(o["y_mm"]))))
            except (TypeError, ValueError):
                pass

    from_obj(inp)
    for key in ("vias", "moves", "items", "components"):
        for o in _as_list(inp.get(key)):
            from_obj(o)
    for ref_k, pin_k in (("from_ref", "from_pin"), ("to_ref", "to_pin")):
        if isinstance(inp.get(ref_k), str) and inp.get(pin_k) is not None:
            add(("pin", (inp[ref_k], str(inp[pin_k]))))
    return out


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


# MUST match kicad_mcp/utils/spawned_registry.py::REGISTRY_FILENAME — the MCP
# server writes the file, we read it here (we cannot import that package: its
# __init__ pulls the whole server).
_SPAWNED_REGISTRY_FILENAME = "kicad_mcp_spawned_editors.json"


def _reap_spawned_editors() -> int:
    """Kill GUI editors the MCP server spawned via ``ipc_open_kicad`` and
    recorded. Those are launched DETACHED, so ``_kill_tree``'s ``/T`` does not
    reach them — without this they orphan as a board-less ``pcbnew`` that squats
    the IPC socket and breaks every chat link ("kein eindeutiges Board"). Reads
    the registry file the server writes; best-effort, never raises."""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), _SPAWNED_REGISTRY_FILENAME)
    try:
        with open(path, encoding="utf-8") as fh:
            ids = [int(p) for p in json.load(fh)
                   if str(p).strip().lstrip("-").isdigit()]
    except Exception:
        return 0
    killed = 0
    for pid in ids:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)], timeout=10,
                    check=False, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, **hidden_console_kwargs())
            else:
                import signal
                os.kill(pid, signal.SIGTERM)
            killed += 1
        except Exception:
            pass
    try:
        os.remove(path)
    except Exception:
        pass
    return killed


def terminate_all() -> int:
    """Kill every in-flight claude turn + its MCP child. Returns how many.

    Called on chat-panel close and (via atexit) on KiCad shutdown, so neither
    claude nor the MCP server — nor any editor the MCP spawned — outlive KiCad.
    Idempotent and safe to call with nothing running.
    """
    with _LIVE_LOCK:
        procs = list(_LIVE)
        _LIVE.clear()
    for proc in procs:
        _kill_tree(proc)
    _reap_spawned_editors()  # detached MCP-spawned editors aren't in the tree
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
    on_tool: Optional[Callable[[str, dict], None]] = None,
    on_proc: Optional[Callable[[Any], None]] = None,
    _popen=subprocess.Popen,
) -> dict[str, Any]:
    """Run one chat turn, streaming progress via ``on_status(text)`` and each
    tool call via ``on_tool(name, tool_input)`` (input lets the panel narrate in
    board language and collect what changed).

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
    # The FIRST cold start (167 tools + pandas/numpy out of a freshly-written
    # _deps, with Windows Defender scanning each new .pyd) can blow past
    # claude's 30 s MCP-startup default — the server is then dropped SILENTLY
    # ("failed: kicad-mcp", chat without board tools). 5 min headroom gets past
    # the one-time cold start; warm starts are unaffected. User override wins.
    env.setdefault("MCP_TIMEOUT", "300000")  # ms — matches the config timeout
    env.setdefault("PYTHONUNBUFFERED", "1")
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
            for name, inp in tool_calls(ev):
                try:
                    on_tool(name, inp)
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
        subtype = result_ev.get("subtype", "success")
        if "max_turns" in str(subtype):  # --max-turns hit → friendly message
            limit = max_turns()
            note = (f"⏹ Schritt-Limit ({limit}) erreicht — die Aufgabe brauchte "
                    "zu viele Tool-Calls. Verkleinere sie, oder erhöhe "
                    "KICAD_MCP_MAX_TURNS (0 = aus).")
            out["text"] = (out["text"] + "\n\n" + note) if out["text"] else note
            out["ok"] = True
            return out
        if subtype != "success" and not out["text"]:
            out["error"] = result_ev.get("error") or str(subtype)
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
