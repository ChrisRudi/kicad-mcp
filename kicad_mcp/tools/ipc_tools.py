# SPDX-License-Identifier: GPL-3.0-or-later
"""
IPC-API tools — talk to a running KiCad GUI via the new Protobuf/NNG API.

Since KiCad 9.0 (April 2025) and stable in 10.0.x, KiCad ships an Inter-Process
Communication API that replaces the legacy SWIG ``pcbnew`` Python bindings.
Communication goes through NNG sockets (UNIX socket on Linux/macOS, named
pipe on Windows) using Protocol Buffers — language-agnostic, thread-safe and
free of the SwigPyObject memory-ownership quirks that haunt scripted use of
``PCB_IO_KICAD_SEXPR().FootprintLoad()``.

**Preconditions:**
  1. KiCad is running and a board is open.
  2. ``Preferences → Plugins → IPC API`` is enabled inside KiCad.
  3. The ``kicad-python`` (``kipy``) Python package is installed in the same
     interpreter that runs the MCP server. Install via
     :func:`ipc_install_kipy` if missing.

These tools are the **preferred routing path** when a KiCad GUI session is
available — they sidestep the SWIG quirks completely. For headless / CI
workflows, use the ``pcb_patch_tools`` module instead, which works on
``.kicad_pcb`` files directly.

Tools registered:
  * ``ipc_check_status`` — Diagnose: kipy installed? KiCad reachable? Board
    open?
  * ``ipc_install_kipy`` — ``pip install kicad-python`` (with platform-aware
    pip invocation) plus operator hint to enable the API in KiCad preferences.
  * ``ipc_get_pad_world_pos`` — Read the absolute world coordinates of any
    pad (correctly accounting for footprint rotation and ``B.Cu`` flip — the
    one thing the text-patcher cannot reliably compute).
  * ``ipc_route_pin_to_pin`` — Add a track segment from one pad to another,
    optionally inserting a layer-change via.
  * ``ipc_add_zone_pour`` — Add a copper-pour zone bound to a net on a layer
    over a polygon outline.
  * ``ipc_route_power_ring`` — Convenience: run a wide power track through a
    sequence of components in order (one segment per consecutive pair).
"""

# kipy.proto.* are generated Protobuf modules — pylint cannot resolve their
# attributes via static analysis. Suppress no-name-in-module file-wide.
# pylint: disable=no-name-in-module

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

# Central IPC timeout (Env KICAD_MCP_IPC_TIMEOUT_MS, default 15000 ms) — every
# inline KiCad(timeout_ms=_ipc_timeout_ms()) below is constructed with it instead of kipy's 2000 ms.
from kicad_mcp.utils.ipc_session import timeout_ms as _ipc_timeout_ms
# Optimistic-concurrency primitives shared with the live layer (live collab):
# refuse to clobber a footprint the user moved since the agent planned the move.
from kicad_mcp.tools.ipc_live_diff import cas_conflict, fp_signature


# ---------------------------------------------------------------------------
# Lazy KiCad-IPC client wrapper. We never import kipy at module load time so
# the MCP server can register these tools even when kipy / KiCad are not yet
# present (the user can install them via ``ipc_install_kipy`` first).
# ---------------------------------------------------------------------------


def _kipy_available() -> bool:
    """Return ``True`` if the ``kipy`` Python package is importable."""
    return importlib.util.find_spec("kipy") is not None


# ---------------------------------------------------------------------------
# Auto-open helper for editor-specific IPC tools.
#
# Many of these tools require either Eeschema or Pcbnew to be running with a
# document open — `RunAction`, `SaveDocument`, footprint-pose changes, schematic
# job exports, etc. When the wrong editor is open (or none), KiCad's IPC server
# answers with the cryptic "no handler available for request of type ...". The
# helper below removes that footgun: it auto-launches the missing editor with
# the correct project file, polls the IPC bus until the editor registers, and
# returns ``None`` once the requested doc_type is reachable.
#
# The *first* such auto-open in a tool call is recorded in
# ``_AUTO_OPEN_LAST`` so the calling tool can splice the
# ``auto_opened="schematic"|"pcb"`` field into its response — that way the LLM
# (and the user reading its summary) always knows when an extra editor window
# has just been spawned.
# ---------------------------------------------------------------------------


_AUTO_OPEN_LAST: dict[str, Any] = {"doc_type": None, "binary": "", "project_file": ""}


def _editor_binary_path(doc_type: str) -> str:
    """Resolve the absolute path to ``eeschema`` / ``pcbnew``.

    Reuses the KiCad-CLI discovery in ``path_env`` — the editors live as
    siblings to ``kicad-cli`` in the install's ``bin/`` directory.
    """
    from kicad_mcp.utils.path_env import kicad_cli  # local import to avoid cycle

    cli = kicad_cli()
    if not cli:
        return ""
    bin_dir = os.path.dirname(cli)
    suffix = ".exe" if cli.lower().endswith(".exe") else ""
    name = "eeschema" if doc_type == "schematic" else "pcbnew"
    candidate = os.path.join(bin_dir, name + suffix)
    return candidate if os.path.isfile(candidate) else ""


def _editor_process_running(doc_type: str) -> bool:
    """True if ``eeschema``/``pcbnew`` already runs as an OS process.

    Why: ``client.get_open_documents(DOCTYPE_SCHEMATIC)`` returns empty / raises
    when Eeschema's IPC handler is not (yet) responsive — but the GUI window
    may already exist. Launching a second instance opens a duplicate window.
    Use the OS process list as the authoritative "is it running" check.
    """
    name = "eeschema" if doc_type == "schematic" else "pcbnew"
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}.exe", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                timeout=5, check=False,
            )
            return f"{name}.exe".lower() in out.stdout.lower()
        out = subprocess.run(
            ["pgrep", "-x", name],
            capture_output=True,
            text=True,
            timeout=5, check=False,
        )
        return out.returncode == 0
    except Exception:
        return False


def _kicad_manager_running() -> bool:
    """True if the KiCad **project manager** (``kicad``/``kicad.exe``) runs.

    Distinct from :func:`_editor_process_running`, which only looks for the
    pcbnew/eeschema editors. This matters for launch decisions: the project
    manager hosts the IPC API server for itself **and** its child editors.
    Spawning a *standalone* editor (launched directly, not from the manager)
    while a manager is already running creates a SECOND API server on the
    same socket — they conflict and ``GetOpenDocuments`` stops resolving
    ("no handler"). So callers must not double-launch when this returns True.
    """
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq kicad.exe", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return "kicad.exe" in out.stdout.lower()
        out = subprocess.run(
            ["pgrep", "-x", "kicad"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.returncode == 0
    except Exception:
        return False


def _kill_editor_process(doc_type: str) -> bool:
    """Force-terminate the ``eeschema``/``pcbnew`` OS process and verify
    it is gone. Returns True when the process is confirmed not running.

    KiCad's IPC layer has no clean 'quit application' command — the
    ``CloseDocument`` command closes a document tab but leaves the
    editor process alive. A scripted file-patch workflow needs the
    process genuinely terminated, or the next disk write races KiCad's
    in-memory copy. This is the OS-level hammer used after a graceful
    close did not make the process exit.
    """
    name = "eeschema" if doc_type == "schematic" else "pcbnew"
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/IM", f"{name}.exe"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        else:
            subprocess.run(
                ["pkill", "-x", name],
                capture_output=True, text=True, timeout=10, check=False,
            )
    except Exception:
        pass
    deadline = time.time() + 5
    while time.time() < deadline and _editor_process_running(doc_type):
        time.sleep(0.4)
    return not _editor_process_running(doc_type)


def _project_path_from_other_editor(client, want_doc_type: str) -> Optional[str]:
    """If the *other* editor already has a document open, derive the path
    of the corresponding ``.kicad_sch`` / ``.kicad_pcb`` from its
    DocumentSpecifier.project field.
    """
    try:
        from kipy.proto.common.types.base_types_pb2 import DocumentType  # type: ignore
    except Exception:
        return None
    other_const = (
        DocumentType.DOCTYPE_PCB
        if want_doc_type == "schematic"
        else DocumentType.DOCTYPE_SCHEMATIC
    )
    try:
        docs = client.get_open_documents(other_const)
    except Exception:
        return None
    if not docs:
        return None
    d = docs[0]
    proj_dir = ""
    proj_name = ""
    try:
        if d.HasField("project"):
            proj_dir = d.project.path
            proj_name = d.project.name
    except Exception:
        pass
    if not proj_dir or not proj_name:
        return None
    suffix = ".kicad_sch" if want_doc_type == "schematic" else ".kicad_pcb"
    return os.path.join(proj_dir, proj_name + suffix)


def _require_editor(
    doc_type: str,
    *,
    timeout_s: float = 10.0,
    project_path: str = "",
) -> Optional[dict[str, Any]]:
    """Ensure the requested KiCad editor has a document open.

    Args:
        doc_type: ``"schematic"`` or ``"pcb"``.
        timeout_s: how long to wait for an auto-launched editor to
            register on the IPC bus.
        project_path: optional cold-start anchor. When provided and
            no editor of ``doc_type`` is running, the matching
            ``.kicad_pcb`` / ``.kicad_sch`` is derived from this path
            and the editor is launched against it. Without
            ``project_path`` the function falls back to deriving the
            project from whichever OTHER editor is already running
            (legacy behavior).

    Returns:
        ``None`` on success — the editor is reachable and the calling
        tool may proceed.
        A ready-to-return error-dict on failure (e.g. KiCad unreachable,
        no project active, editor binary missing, launch did not
        register in time).

    Side effect:
        On a successful auto-launch, populates the module-level
        ``_AUTO_OPEN_LAST`` dict; the calling tool should call
        :func:`_consume_auto_open` and merge the result into its
        response so the user sees that an editor window was opened.
    """
    _AUTO_OPEN_LAST["doc_type"] = None
    _AUTO_OPEN_LAST["binary"] = ""
    _AUTO_OPEN_LAST["project_file"] = ""

    if doc_type not in ("schematic", "pcb"):
        return {"success": False, "error": f"unknown editor doc_type {doc_type!r}"}
    if not _kipy_available():
        return {"success": False, "error": "kipy not installed."}
    try:
        from kipy.proto.common.types.base_types_pb2 import DocumentType  # type: ignore
    except Exception as exc:
        return {"success": False, "error": f"kipy import failed: {exc}"}
    from kicad_mcp.utils.ipc_session import call_with_retry, get_client

    doc_const = (
        DocumentType.DOCTYPE_SCHEMATIC
        if doc_type == "schematic"
        else DocumentType.DOCTYPE_PCB
    )

    # Reuse the central, health-checked, auto-reconnecting client instead of a
    # fresh per-call connection: this pre-flight gate runs before almost every
    # IPC tool, so a fresh-connect-without-retry here was the single biggest
    # source of spurious "Cannot reach KiCad" aborts when the GUI was briefly
    # busy (zone fill / DRC / redraw). get_client() pings + reconnects a stale
    # socket; call_with_retry() rides out transient "busy".
    try:
        client = get_client()
    except Exception as exc:
        return {"success": False, "error": f"Cannot reach KiCad: {exc}"}

    try:
        if call_with_retry(lambda: client.get_open_documents(doc_const),
                           "require_editor"):
            return None  # already open — no auto-launch needed
    except Exception as exc:
        # Differentiate "the bus is down" from "the editor's IPC handler
        # is not registered". The latter is exactly the case where
        # auto-launch helps; the former means we have nothing to talk to.
        msg = str(exc).lower()
        if "no handler" in msg or "ras_invalid" in msg or "not open" in msg:
            # SCH handler dead / not yet registered — fall through to launch.
            pass
        else:
            return {
                "success": False,
                "error": f"KiCad IPC bus is not reachable: {exc}",
            }

    project_file = ""
    if project_path:
        # Explicit cold-start: derive .kicad_pcb / .kicad_sch from
        # whatever the user passed (.kicad_pro / .kicad_pcb / .kicad_sch).
        from kicad_mcp.utils.path_env import to_local_path  # local import
        local = to_local_path(project_path)
        if os.path.isfile(local):
            base, ext = os.path.splitext(local)
            suffix = ".kicad_sch" if doc_type == "schematic" else ".kicad_pcb"
            if ext == suffix:
                project_file = local
            elif ext in (".kicad_pro", ".kicad_pcb", ".kicad_sch"):
                project_file = base + suffix
    if not project_file:
        project_file = _project_path_from_other_editor(client, doc_type) or ""
    if not project_file:
        return {
            "success": False,
            "error": (
                "No KiCad project active in either editor — pass "
                "project_path=, open a project in KiCad first, "
                f"or open the {doc_type} editor manually."
            ),
        }
    if not os.path.isfile(project_file):
        return {
            "success": False,
            "error": (
                f"Derived {doc_type} file does not exist on disk: {project_file}"
            ),
        }

    binary = _editor_binary_path(doc_type)
    if not binary:
        return {
            "success": False,
            "error": (
                f"Could not locate the {doc_type} editor binary "
                f"({'eeschema' if doc_type == 'schematic' else 'pcbnew'}). "
                "Set KICAD_BIN to your KiCad bin/ directory."
            ),
        }

    already_running = _editor_process_running(doc_type)
    if not already_running:
        try:
            if os.name == "nt" or binary.lower().endswith(".exe"):
                DETACHED_PROCESS = 0x00000008  # noqa: N806 — Win32 constant
                subprocess.Popen(
                    [binary, project_file],
                    creationflags=DETACHED_PROCESS,
                    close_fds=True,
                )
            else:
                subprocess.Popen(
                    [binary, project_file],
                    start_new_session=True,
                    close_fds=True,
                )
        except Exception as exc:
            return {
                "success": False,
                "error": f"Failed to launch {os.path.basename(binary)}: {exc}",
            }

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if client.get_open_documents(doc_const):
                _AUTO_OPEN_LAST["doc_type"] = doc_type
                _AUTO_OPEN_LAST["binary"] = os.path.basename(binary)
                _AUTO_OPEN_LAST["project_file"] = project_file
                return None
        except Exception:
            pass
        time.sleep(0.3)

    if already_running:
        return {
            "success": False,
            "error": (
                f"{os.path.basename(binary)} is already running but its IPC "
                f"handler did not respond within {timeout_s:.0f}s. Verify "
                "Preferences → Plugins → Enable KiCad API is on, and that the "
                f"{doc_type} window has a project loaded."
            ),
            "binary": binary,
            "project_file": project_file,
            "already_running": True,
        }
    return {
        "success": False,
        "error": (
            f"Launched {os.path.basename(binary)} but it did not register on "
            f"the IPC bus within {timeout_s:.0f}s. Verify Preferences → "
            "Plugins → Enable KiCad API is on."
        ),
        "binary": binary,
        "project_file": project_file,
    }


def _consume_auto_open() -> Optional[dict[str, str]]:
    """Pop the most recent auto-open record (if any). Returns a dict with
    ``doc_type``, ``binary``, ``project_file`` to splice into the tool's
    response, or ``None`` if no editor was auto-launched.
    """
    if not _AUTO_OPEN_LAST["doc_type"]:
        return None
    record = {
        "doc_type": _AUTO_OPEN_LAST["doc_type"],
        "binary": _AUTO_OPEN_LAST["binary"],
        "project_file": _AUTO_OPEN_LAST["project_file"],
    }
    _AUTO_OPEN_LAST["doc_type"] = None
    _AUTO_OPEN_LAST["binary"] = ""
    _AUTO_OPEN_LAST["project_file"] = ""
    return record


def _attach_auto_open(response: dict[str, Any]) -> dict[str, Any]:
    """Splice the auto_opened record (if any) into a response dict."""
    rec = _consume_auto_open()
    if rec is not None:
        response = {**response, "auto_opened": rec}
    return response


def _connect_kicad():
    """Reused KiCad IPC client + its board (central session layer).

    Returns a ``(client, board)`` tuple on success or raises ``RuntimeError``
    with a clear message on failure (kipy missing, KiCad not running, no
    board open). The client is process-wide REUSED (``ipc_session``) with the
    configurable timeout, busy-retry/backoff and reconnect-on-stale — so
    repeated tool calls don't pay a fresh-connect each time.
    """
    if not _kipy_available():
        raise RuntimeError(
            "kicad-python (kipy) is not installed. Run ipc_install_kipy first."
        )
    from kicad_mcp.utils import ipc_session
    client, board = ipc_session.connect_board()

    # First-board-contact presence beacon: light up the MCP.Skizze layer so the
    # user sees the MCP is active here. Once per process, best-effort, and
    # disablable via KICAD_MCP_SKETCH_PRESENCE=0. Lazy import avoids a circular
    # import at module load (ipc_interact_tools imports from this module).
    try:
        from .ipc_interact_tools import ensure_mcp_presence  # local import
        ensure_mcp_presence(board)
    except Exception:
        pass

    return client, board


def _close_editor_silent(doc_type: str) -> dict[str, Any]:
    """Close all open documents of ``doc_type`` via the proper IPC
    CloseDocument command. Returns ``{closed_count, errors}``.

    Used by tools that take ``close_after=True`` — module-level helper
    so the same logic doesn't have to be duplicated per tool.
    """
    if not _kipy_available():
        return {"closed_count": 0, "errors": ["kipy not installed"]}
    try:
        from kipy import KiCad  # type: ignore
        from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
            DocumentType,
        )
    except Exception as exc:
        return {"closed_count": 0, "errors": [f"kipy import failed: {exc}"]}
    try:
        client = KiCad(timeout_ms=_ipc_timeout_ms())
    except Exception as exc:
        return {"closed_count": 0, "errors": [f"Cannot reach KiCad: {exc}"]}
    doc_const = (
        DocumentType.DOCTYPE_SCHEMATIC
        if doc_type == "schematic"
        else DocumentType.DOCTYPE_PCB
    )
    try:
        docs = client.get_open_documents(doc_const)
    except Exception:
        docs = []
    if not docs:
        return {"closed_count": 0, "errors": []}
    # Best-effort graceful CloseDocument (closes the document tab only).
    try:
        from google.protobuf.empty_pb2 import Empty  # type: ignore
        from kipy.proto.common.commands.editor_commands_pb2 import (  # type: ignore
            CloseDocument,
        )

        for d in docs:
            try:
                cmd = CloseDocument()
                if hasattr(cmd, "document"):
                    cmd.document.CopyFrom(d)
                # send() REQUIRES a response type — omitting it raises TypeError
                # (silently swallowed here), so the graceful close never ran.
                client._client.send(cmd, Empty)  # noqa: SLF001
            except Exception:
                pass
    except ImportError:
        try:
            client.run_action("common.Control.close")
        except Exception:
            pass
    # CloseDocument leaves the editor PROCESS alive — verify it actually
    # exited, and OS-terminate it if not, so close_after truly closes.
    deadline = time.time() + 4
    while time.time() < deadline and _editor_process_running(doc_type):
        time.sleep(0.5)
    if _editor_process_running(doc_type):
        _kill_editor_process(doc_type)
    running = _editor_process_running(doc_type)
    return {
        "closed_count": 0 if running else len(docs),
        "running": running,
        "errors": [],
    }


def _kicad_version_string(client) -> str:
    """Best-effort retrieval of the connected KiCad's version string."""
    for attr in ("get_version", "version", "kicad_version"):
        v = getattr(client, attr, None)
        if v is None:
            continue
        try:
            result = v() if callable(v) else v
            return str(result)
        except Exception:
            continue
    return "unknown"


# ---------------------------------------------------------------------------
# Pip-install helper (used by ipc_install_kipy)
# ---------------------------------------------------------------------------


def _pip_install_kipy(target_python: str | None = None) -> tuple[bool, str]:
    """Run ``pip install kicad-python`` against the current interpreter (or
    a different one if ``target_python`` is supplied).

    Returns ``(success, combined_stdout_stderr)``.
    """
    py = target_python or sys.executable
    cmd = [py, "-m", "pip", "install", "--upgrade", "kicad-python"]
    try:
        proc = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=300
        )
    except FileNotFoundError as exc:
        return False, f"pip launch failed: {exc}"
    except subprocess.TimeoutExpired as exc:
        return False, f"pip install timed out after 300s: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out


# ---------------------------------------------------------------------------
# Pad lookup + small geometric helpers (independent of which kipy version is
# installed — we probe a few attribute names).
# ---------------------------------------------------------------------------


def _find_footprint_by_ref(board, ref: str):
    """Return the kipy footprint object for ``ref`` or ``None``."""
    # kipy's API surface has been moving; try the common spellings.
    for getter in ("get_footprints", "footprints", "GetFootprints"):
        f = getattr(board, getter, None)
        if f is None:
            continue
        try:
            iterable = f() if callable(f) else f
        except Exception:
            continue
        for fp in iterable:
            # kipy 0.7.x: fp.reference_field.text.value
            rf = getattr(fp, "reference_field", None)
            if rf is not None:
                txt = getattr(rf, "text", None)
                rv = getattr(txt, "value", None) if txt is not None else None
                if rv == ref:
                    return fp
            # Fallbacks for older / alternative kipy versions
            for ra in ("reference", "Reference", "ref", "get_reference"):
                rv = getattr(fp, ra, None)
                if rv is None:
                    continue
                rv = rv() if callable(rv) else rv
                if rv == ref:
                    return fp
    return None


def _find_pad(board, footprint, pad_name: str):
    """Return the pad object whose name/number matches ``pad_name``.

    kipy 0.7.x: footprint instance only exposes template pads via
    ``footprint.definition.pads`` (relative coords). The board-level
    ``board.get_pads()`` returns instance pads with world coords. The two
    lists share UUIDs, so we filter board pads by the footprint's pad-id
    set, then match on ``pad.number``.
    """
    # Preferred path: kipy 0.7.x via UUID intersection with board pads.
    defi = getattr(footprint, "definition", None)
    if defi is not None and board is not None:
        def_pads = getattr(defi, "pads", None)
        try:
            def_pads = list(def_pads) if def_pads is not None else []
        except Exception:
            def_pads = []
        if def_pads:
            try:
                def_ids = {p.id.value for p in def_pads if getattr(p, "id", None) is not None}
            except Exception:
                def_ids = set()
            if def_ids:
                try:
                    board_pads = board.get_pads()
                except Exception:
                    board_pads = []
                for pad in board_pads:
                    pid = getattr(pad, "id", None)
                    if pid is None or getattr(pid, "value", None) not in def_ids:
                        continue
                    if str(getattr(pad, "number", "")) == str(pad_name):
                        return pad

    # Fallback: older kipy / mock objects exposing pads directly on footprint.
    for getter in ("get_pads", "pads", "GetPads"):
        f = getattr(footprint, getter, None)
        if f is None:
            continue
        try:
            iterable = f() if callable(f) else f
        except Exception:
            continue
        for pad in iterable:
            for pa in ("name", "number", "pad_number", "get_name"):
                pv = getattr(pad, pa, None)
                if pv is None:
                    continue
                pv = pv() if callable(pv) else pv
                if str(pv) == str(pad_name):
                    return pad
    return None


def _layer_to_enum(layer_str: str):
    """Map a KiCad layer name like ``"F.Cu"`` to the kipy ``BoardLayer`` enum int.

    Returns the enum int value, or ``None`` if the layer cannot be resolved.
    Accepts both human form (``F.Cu``, ``In1.Cu``) and the proto form
    (``BL_F_Cu``).
    """
    try:
        from kipy.proto.board.board_types_pb2 import BoardLayer  # type: ignore
    except Exception:
        return None
    if not layer_str:
        return None
    name = layer_str if layer_str.startswith("BL_") else "BL_" + layer_str.replace(".", "_")
    try:
        return BoardLayer.Value(name)
    except Exception:
        return None


def _find_net(board, name: str):
    """Return the kipy ``Net`` wrapper whose ``name`` matches, else ``None``."""
    if not name:
        return None
    try:
        nets = board.get_nets()
    except Exception:
        return None
    for n in nets:
        if getattr(n, "name", None) == name:
            return n
    return None


def _board_default_via_nm(board) -> tuple[int, int]:
    """Return the board's default ``(via_diameter, via_drill)`` in nm.

    A freshly-constructed kipy ``Via()`` has diameter/drill **0** and KiCad
    keeps it at 0 on create — a degenerate via. Callers that create vias must
    fall back to the Default net class's via size. Falls back to 0.4/0.2 mm if
    the net classes can't be read.
    """
    try:
        for nc in board.get_project().get_net_classes():
            if getattr(nc, "name", None) in ("Default", "default"):
                d = int(getattr(nc, "via_diameter", 0) or 0)
                k = int(getattr(nc, "via_drill", 0) or 0)
                if d > 0 and k > 0:
                    return d, k
    except Exception:
        pass
    return 400_000, 200_000


def _pad_primary_layer_enum(pad) -> int | None:
    """Return the first copper-layer enum value of a pad, or ``None``."""
    try:
        cls = pad.proto.pad_stack.copper_layers
    except Exception:
        return None
    for cl in cls:
        return getattr(cl, "layer", None)
    return None


def _pad_world_xy_mm(pad) -> tuple[float, float]:
    """Best-effort extraction of the pad's world position in millimetres.
    kipy normalises the rotation/flip math internally, so this is the value
    the text-patcher cannot reliably compute on its own.
    """
    for attr in ("position", "world_position", "get_position"):
        v = getattr(pad, attr, None)
        if v is None:
            continue
        try:
            pos = v() if callable(v) else v
        except Exception:
            continue
        # kipy positions usually expose .x / .y in nm or mm depending on type.
        x = getattr(pos, "x", None)
        y = getattr(pos, "y", None)
        if x is not None and y is not None:
            # Heuristic: nm if absolute value is huge.
            if abs(x) > 10_000 or abs(y) > 10_000:
                return float(x) / 1_000_000.0, float(y) / 1_000_000.0
            return float(x), float(y)
    raise RuntimeError("Could not read pad position from kipy object.")


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register_ipc_tools(mcp: FastMCP) -> None:
    """Register IPC-based KiCad tools with the MCP server."""

    @mcp.tool()
    def ipc_check_status() -> dict[str, Any]:
        """Diagnose the IPC-API setup.

        Returns a structured report with three booleans (``kipy_installed``,
        ``kicad_reachable``, ``board_open``) plus the ``kicad_version`` if the
        connection succeeded. Use as the first call before invoking any other
        ``ipc_*`` tool.
        """
        report: dict[str, Any] = {
            "kipy_installed": _kipy_available(),
            "kicad_reachable": False,
            "board_open": False,
            "kicad_version": None,
            "ready": False,
        }
        if not report["kipy_installed"]:
            report["hint"] = "Run ipc_install_kipy to install the client."
            return report
        try:
            client, _board = _connect_kicad()
        except RuntimeError as exc:
            report["error"] = str(exc)
            if "Cannot reach" in str(exc):
                report["hint"] = (
                    "Start KiCad and enable Preferences → Plugins → IPC API."
                )
            elif "No board accessible" in str(exc):
                report["kicad_reachable"] = True
                report["hint"] = "Open a .kicad_pcb in the PCB Editor."
            return report
        report["kicad_reachable"] = True
        report["board_open"] = True
        report["kicad_version"] = _kicad_version_string(client)
        report["ready"] = True
        return report

    @mcp.tool()
    def ipc_get_open_documents() -> dict[str, Any]:
        """List the schematics and PCBs currently open in KiCad.

        Useful before any patching workflow to confirm which file the user
        is looking at — the agent can then target that exact path.

        Returns ``{success, schematics: [...], pcbs: [...], project_name}``
        where each list entry is ``{filename, project}``.
        """
        if not _kipy_available():
            return {
                "success": False,
                "error": "kipy not installed. Run ipc_install_kipy first.",
            }
        try:
            from kipy import KiCad  # type: ignore
            from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
                DocumentType,
            )
        except Exception as exc:
            return {"success": False, "error": f"kipy import failed: {exc}"}
        try:
            client = KiCad(timeout_ms=_ipc_timeout_ms())
        except Exception as exc:
            return {
                "success": False,
                "error": f"Cannot reach KiCad: {exc}",
                "hint": (
                    "Start KiCad and enable Preferences → Plugins → IPC API."
                ),
            }

        def _entries(doc_type_name: str) -> list[dict[str, str]]:
            try:
                docs = client.get_open_documents(
                    DocumentType.Value(doc_type_name)
                )
            except Exception:
                return []
            out: list[dict[str, str]] = []
            for d in docs:
                proj = ""
                try:
                    if d.HasField("project"):
                        proj = d.project.name
                except Exception:
                    pass
                out.append(
                    {
                        "filename": getattr(d, "board_filename", "") or "",
                        "project": proj,
                    }
                )
            return out

        schematics = _entries("DOCTYPE_SCHEMATIC")
        pcbs = _entries("DOCTYPE_PCB")
        project_name = ""
        for src in (schematics, pcbs):
            for entry in src:
                if entry.get("project"):
                    project_name = entry["project"]
                    break
            if project_name:
                break
        return {
            "success": True,
            "schematics": schematics,
            "pcbs": pcbs,
            "project_name": project_name,
        }

    def _run_kicad_action(action: str) -> dict[str, Any]:
        """Internal helper: dispatch a kipy ``run_action`` and convert the
        common failure modes into a uniform MCP-tool dict.
        """
        if not _kipy_available():
            return {"success": False, "error": "kipy not installed."}
        try:
            from kipy import KiCad  # type: ignore
        except Exception as exc:
            return {"success": False, "error": f"kipy import failed: {exc}"}
        try:
            client = KiCad(timeout_ms=_ipc_timeout_ms())
        except Exception as exc:
            return {"success": False, "error": f"Cannot reach KiCad: {exc}"}
        try:
            result = client.run_action(action)
        except Exception as exc:
            msg = str(exc)
            if "Timed out" in msg or "timed out" in msg.lower():
                return {
                    "success": True,
                    "action": action,
                    "ipc_timed_out": True,
                    "note": (
                        "IPC reply timed out — usually because KiCad popped "
                        "a confirmation dialog. The action did fire."
                    ),
                }
            if "no handler" in msg.lower():
                return {
                    "success": False,
                    "error": (
                        "KiCad refused the action — usually because no "
                        "editor / document is currently active."
                    ),
                    "action": action,
                }
            return {"success": False, "error": msg, "action": action}
        status = str(result).strip()
        # RAS_INVALID = KiCad accepted the call but the action handler
        # rejected it (wrong editor focused, action not registered for the
        # current document type, …). Treat that as a failure rather than
        # silent success so callers don't think the request landed.
        if "RAS_INVALID" in status:
            return {
                "success": False,
                "action": action,
                "action_status": status,
                "error": (
                    "KiCad reported RAS_INVALID — the action exists but is "
                    "not applicable in the current editor / document state. "
                    "Common causes: the wrong editor window is focused, or "
                    "the action is not implemented for the active document "
                    "type (a known limitation of Eeschema's IPC surface)."
                ),
            }
        return {"success": True, "action": action, "action_status": status}

    def _save_or_revert_via_proper_command(
        kind: str, doc_type_name: str
    ) -> dict[str, Any]:
        """Issue a proper ``SaveDocument`` / ``RevertDocument`` IPC command
        for the first open document of the given type.

        This is the API path KiCad actually maintains, in contrast to
        ``RunAction("common.Control.save")`` which is documented as
        unstable. PCB's ``RevertDocument`` handler in particular clears
        the modified flag *before* reloading from disk → no
        "Save changes?" dialog. Eeschema does **not** register
        Save/Revert command handlers in KiCad 10.0.x, so the fall-back
        to RunAction is preserved for SCH.

        Args:
            kind: ``"save"`` or ``"revert"``.
            doc_type_name: ``"DOCTYPE_PCB"`` or ``"DOCTYPE_SCHEMATIC"``.
        """
        if kind not in ("save", "revert"):
            return {"success": False, "error": f"unknown kind {kind!r}"}
        if not _kipy_available():
            return {"success": False, "error": "kipy not installed."}
        try:
            from kipy import KiCad  # type: ignore
            from kipy.proto.common.commands.editor_commands_pb2 import (  # type: ignore
                RevertDocument,
                SaveDocument,
            )
            from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
                DocumentType,
            )
            from google.protobuf.empty_pb2 import Empty  # type: ignore
        except Exception as exc:
            return {"success": False, "error": f"kipy import failed: {exc}"}
        try:
            client = KiCad(timeout_ms=_ipc_timeout_ms())
        except Exception as exc:
            return {"success": False, "error": f"Cannot reach KiCad: {exc}"}
        try:
            docs = client.get_open_documents(DocumentType.Value(doc_type_name))
        except Exception as exc:
            return {
                "success": False,
                "error": f"get_open_documents failed: {exc}",
            }
        if not docs:
            return {
                "success": False,
                "error": f"No {doc_type_name} document open in KiCad.",
            }
        cmd = (SaveDocument if kind == "save" else RevertDocument)()
        cmd.document.CopyFrom(docs[0])
        try:
            client._client.send(cmd, Empty)
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "fallback_hint": (
                    "If KiCad reports 'the requested document … is not open',"
                    " the document type's API handler does not implement"
                    f" {kind.title()}Document — Eeschema is the known case in"
                    " KiCad 10.0.x. Use ipc_save_via_action / ipc_revert_via_action"
                    " for the legacy RunAction fallback (which pops a confirm dialog)."
                ),
            }
        return {
            "success": True,
            "kind": kind,
            "document": docs[0].board_filename or doc_type_name,
            "silent": True,
        }

    @mcp.tool()
    def ipc_save(
        doc_type: str = "pcb",
        project_path: str = "",
        close_after: bool = False,
    ) -> dict[str, Any]:
        """Save the live KiCad document silently via the proper ``SaveDocument`` IPC command.

        Use this after a sequence of ``ipc_set_footprint_pose`` /
        ``ipc_route_*`` / ``ipc_add_zone_pour`` calls to persist the
        live edits. Silent — no "Save changes?" dialog, no Save-As
        dialog (the document is already on disk by virtue of being
        open). If the requested editor isn't running, the auto-open
        hook starts it (using ``project_path`` if given).

        Don't fall back to ``ipc_save_via_action`` for PCB saves —
        ``SaveDocument`` is the proper, dialog-free path.

        Args:
            doc_type: ``"pcb"`` (default) or ``"schematic"``. Eeschema
                does not implement ``SaveDocument`` in KiCad 10.0.x —
                pass ``"schematic"`` only on KiCad 11+ once the
                Eeschema handler ships (see KiCad #2077). For 10.0.x
                SCH workflows use the Phase-S text-patcher and let the
                user press Ctrl+S.
            project_path: Optional cold-start anchor.
            close_after: If True, close editor after save. Default
                False.

        Returns:
            ``{success, action: "save", doc_type, silent: True,
            auto_opened?, auto_closed?}``.
        """
        from kicad_mcp.utils.path_env import to_local_path
        if project_path:
            project_path = to_local_path(project_path)
        if err := _require_editor(doc_type.lower(), project_path=project_path):
            return err
        type_name = (
            "DOCTYPE_PCB" if doc_type.lower() == "pcb" else "DOCTYPE_SCHEMATIC"
        )
        resp = _save_or_revert_via_proper_command("save", type_name)
        if close_after and resp.get("success"):
            cl = _close_editor_silent(doc_type.lower())
            resp["auto_closed"] = {
                "closed_count": cl.get("closed_count", 0),
            }
        return _attach_auto_open(resp)

    @mcp.tool()
    def ipc_save_via_action() -> dict[str, Any]:
        """Legacy save via ``common.Control.save`` (RunAction).

        ``RunAction`` is registered **only by the PCB handler** in KiCad 10.0.x
        (verified against master ``pcbnew/api/api_handler_pcb.cpp``;
        ``API_HANDLER_COMMON`` and ``API_HANDLER_SCH`` do not register it).
        Effective behaviour:

        * Pcbnew open → action runs against the PCB tool manager → PCB saved.
        * Only Eeschema open → server returns ``no handler``.
        * Pcbnew + Eeschema open → action runs against the PCB tool manager
          regardless of which window is focused → **PCB saved, schematic
          untouched**.

        For schematics use the Phase-S text patcher and ask the user to press
        Ctrl+S, or wait for KiCad #2077. ``ipc_save(doc_type="pcb")`` is the
        right call when you really want a PCB save.
        """
        return _run_kicad_action("common.Control.save")

    @mcp.tool()
    def ipc_save_all() -> dict[str, Any]:
        """Save every open document in KiCad (schematic + PCB) via
        ``common.Control.saveAll`` — single round-trip for combined patches.

        Use this after a series of mixed PCB + SCH edits (e.g. patching a
        schematic via the Phase-S text-patcher and immediately moving the
        matching footprint via ``ipc_set_footprint_pose``) — one call
        flushes both editors. Don't loop ``ipc_save("pcb")`` +
        ``ipc_save("schematic")``: ``saveAll`` is one IPC round-trip.

        Caveat: the underlying ``RunAction`` dispatch is registered only
        by the PCB handler in KiCad 10.0.x — if **only** Eeschema is open
        the call falls through with ``no handler``. With Pcbnew open it
        does save both editors via KiCad's internal saveAll routine.

        Args:
            (none)

        Returns:
            ``{success, status}`` from the KiCad tool manager.
        """
        return _run_kicad_action("common.Control.saveAll")

    @mcp.tool()
    def ipc_run_drc(
        project_path: str = "",
        close_after: bool = False,
    ) -> dict[str, Any]:
        """Trigger ``Tools → DRC`` in the active KiCad PCB editor.

        Wraps ``pcbnew.DRCTool.runDRC`` — opens the DRC dialog and runs
        rule checks against the **live in-memory PCB**. If Pcbnew is not
        running, this tool launches it (using ``project_path`` if given,
        else derived from whichever editor is open).

        Args:
            project_path: Optional cold-start anchor (``.kicad_pro`` etc.).
            close_after: If True, close PCB editor after DRC. Default
                False.

        Results live in the dialog; for programmatic output use the
        disk-based ``run_drc_check`` (after saving, e.g. via ``ipc_save``).
        """
        from kicad_mcp.utils.path_env import to_local_path
        if project_path:
            project_path = to_local_path(project_path)
            if not os.path.isfile(project_path):
                return {
                    "success": False,
                    "error": f"Project file not found: {project_path}",
                }
        if err := _require_editor("pcb", project_path=project_path):
            return err
        resp = _run_kicad_action("pcbnew.DRCTool.runDRC")
        if close_after:
            cl = _close_editor_silent("pcb")
            resp["auto_closed"] = {
                "closed_count": cl.get("closed_count", 0),
            }
        return _attach_auto_open(resp)

    @mcp.tool()
    def kicad_mcp_doctor() -> dict[str, Any]:
        """Aggregated environment health check.

        Reports presence/version of every external dependency the MCP
        server can talk to:

          * ``platform`` / ``python_executable``
          * ``kicad_cli`` (path + version)
          * ``kipy`` (installed?)
          * ``kicad_running`` + ``kicad_version``
          * ``open_documents`` (schematics + PCBs the GUI has loaded)
          * ``cairo_dll`` + ``cairo_mirror`` (the cairo-2 → libcairo-2 fix)
          * ``cairosvg`` (installed?)
          * ``powershell`` (Windows-only)

        Returns a single ``ok`` flag (True only when every essential
        component is wired up) plus a verbose ``components`` dict for
        triage.
        """
        from kicad_mcp.utils.path_env import (
            detect_environment,
            kicad_cli,
        )

        components: dict[str, Any] = {}

        # Platform basics
        components["platform"] = detect_environment()
        components["python_executable"] = sys.executable

        # KiCad CLI
        cli = kicad_cli()
        cli_ok = bool(cli) and os.path.isfile(cli)
        cli_version = ""
        if cli_ok:
            try:
                p = subprocess.run(
                    [cli, "version"], capture_output=True, text=True, timeout=10, check=False,
                )
                cli_version = (p.stdout or p.stderr or "").strip().splitlines()[0] if p.returncode == 0 else ""
            except Exception:
                cli_version = ""
        components["kicad_cli"] = {"path": cli, "ok": cli_ok, "version": cli_version}

        # kipy
        components["kipy"] = {"installed": _kipy_available()}

        # KiCad running + open docs
        kicad_running = False
        kicad_version = ""
        open_docs: dict[str, Any] = {"schematics": [], "pcbs": []}
        if _kipy_available():
            try:
                from kipy import KiCad  # type: ignore
                from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
                    DocumentType,
                )

                client = KiCad(timeout_ms=_ipc_timeout_ms())
                kicad_running = True
                try:
                    kicad_version = _kicad_version_string(client)
                except Exception:
                    kicad_version = ""
                for type_name, key in (
                    ("DOCTYPE_SCHEMATIC", "schematics"),
                    ("DOCTYPE_PCB", "pcbs"),
                ):
                    try:
                        docs = client.get_open_documents(
                            DocumentType.Value(type_name)
                        )
                        for d in docs:
                            open_docs[key].append(
                                getattr(d, "board_filename", "") or ""
                            )
                    except Exception:
                        pass
            except Exception:
                kicad_running = False
        components["kicad_running"] = kicad_running
        components["kicad_version"] = kicad_version
        components["open_documents"] = open_docs

        # Cairo native DLL + cairosvg
        bin_dir = os.path.dirname(cli) if cli else ""
        cairo_src = os.path.join(bin_dir, "cairo-2.dll") if bin_dir else ""
        cairo_mirror_dir = os.path.join(
            os.path.expanduser("~"), ".kicad-mcp", "native_libs"
        )
        cairo_mirror = os.path.join(cairo_mirror_dir, "libcairo-2.dll")
        components["cairo_dll"] = {
            "src": cairo_src,
            "src_present": bool(cairo_src) and os.path.isfile(cairo_src),
            "mirror": cairo_mirror,
            "mirror_present": os.path.isfile(cairo_mirror),
        }
        try:

            components["cairosvg"] = {"installed": True}
        except Exception:
            components["cairosvg"] = {"installed": False}

        # PowerShell (Windows-only relevance)
        ps = shutil.which("powershell.exe")
        components["powershell"] = {"path": ps or "", "ok": bool(ps)}

        # Aggregate ok: kicad_cli + kipy installed are the bare minimum.
        ok = bool(components["kicad_cli"]["ok"] and components["kipy"]["installed"])
        return {"ok": ok, "components": components}

    @mcp.tool()
    def ipc_run_erc() -> dict[str, Any]:
        """Stub. Returns a structured "use ``run_erc`` instead" message.

        ``RunAction`` is not registered for Eeschema in KiCad 10.0.x (only
        ``API_HANDLER_PCB`` registers it; ``API_HANDLER_COMMON`` and
        ``API_HANDLER_SCH`` do not). Asking the IPC server for
        ``eeschema.InspectionTool.runERC`` either returns ``no handler`` (if
        only Eeschema is open) or routes to the PCB tool manager (if Pcbnew
        is also open) — neither runs ERC on the schematic. Tracking-Issue:
        `KiCad #2077 <https://gitlab.com/kicad/code/kicad/-/issues/2077>`_.

        Use ``run_erc(schematic_path=…)`` instead — that wraps
        ``kicad-cli sch erc`` and produces JSON output without IPC. If the
        live editor has unsaved changes, export the live state via
        ``ipc_export_schematic(format="netlist")`` first (Phase 3) and run
        ERC against the exported file.
        """
        return {
            "success": False,
            "stub": True,
            "reason": "Eeschema does not register RunAction in KiCad 10.0.x.",
            "tracking_issue": "https://gitlab.com/kicad/code/kicad/-/issues/2077",
            "use_instead": "run_erc",
        }

    def _revert_sch_via_close_open() -> dict[str, Any]:
        """Eeschema-only revert via ``CloseDocument`` + ``OpenDocument``.

        Both commands are registered globally by ``API_HANDLER_COMMON``.
        ``HandleApiCloseDocument`` in ``eeschema/eeschema.cpp`` calls
        ``closeCurrentDocument()`` *without* checking the modified flag and
        without popping a save dialog, which is what makes this path
        viable as a silent revert — the in-memory state is dropped, then
        the on-disk state is re-loaded by ``OpenDocument``.

        Returns the standard ``ipc_revert`` shape. Loses the editor's
        zoom / cursor position because the document is closed and
        re-opened; that's the price for not popping a dialog.
        """
        if not _kipy_available():
            return {"success": False, "error": "kipy not installed."}
        try:
            from kipy import KiCad  # type: ignore
            from kipy.proto.common.commands.project_commands_pb2 import (  # type: ignore  # noqa: F401
                CloseDocument,
                OpenDocument,
            )
            from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
                DocumentSpecifier,
                DocumentType,
            )
            from google.protobuf.empty_pb2 import Empty  # type: ignore
        except Exception as exc:
            # The project_commands_pb2 path is the modern location; older
            # kipy distributions kept Open/Close in base_commands_pb2.
            try:
                from kipy.proto.common.commands.base_commands_pb2 import (  # type: ignore
                    CloseDocument,
                    OpenDocument,
                )
            except Exception:
                return {
                    "success": False,
                    "error": (
                        f"kipy does not expose Open/Close document commands: {exc}"
                    ),
                }
        try:
            client = KiCad(timeout_ms=_ipc_timeout_ms())
        except Exception as exc:
            return {"success": False, "error": f"Cannot reach KiCad: {exc}"}
        try:
            docs = client.get_open_documents(DocumentType.DOCTYPE_SCHEMATIC)
        except Exception as exc:
            return {"success": False, "error": f"get_open_documents failed: {exc}"}
        if not docs:
            return {"success": False, "error": "No schematic open in KiCad."}
        path = docs[0].board_filename or ""
        if not path:
            return {
                "success": False,
                "error": "Open schematic has no on-disk filename — cannot reopen.",
            }
        try:
            close_cmd = CloseDocument()
            close_cmd.document.CopyFrom(docs[0])
            client._client.send(close_cmd, Empty)
        except Exception as exc:
            return {"success": False, "error": f"CloseDocument failed: {exc}"}
        try:
            open_cmd = OpenDocument()
            open_cmd.type = DocumentType.DOCTYPE_SCHEMATIC
            open_cmd.path = path
            client._client.send(open_cmd, DocumentSpecifier)
        except Exception as exc:
            return {
                "success": False,
                "error": f"OpenDocument failed after close: {exc}",
                "warning": "Schematic was closed but reopen failed — open it manually in KiCad.",
            }
        return {
            "success": True,
            "kind": "revert",
            "document": path,
            "silent": True,
            "method": "close+open",
        }

    @mcp.tool()
    def ipc_revert(doc_type: str = "pcb") -> dict[str, Any]:
        """Reload the open document from disk silently — no confirm dialog.

        * ``doc_type="pcb"`` → uses the proper ``RevertDocument`` IPC command;
          KiCad's PCB handler clears the in-memory modified flag before
          reloading, so the call is silent.
        * ``doc_type="schematic"`` → ``RevertDocument`` is not registered for
          Eeschema in KiCad 10.0.x. Falls back to a ``CloseDocument`` +
          ``OpenDocument`` pair routed through ``API_HANDLER_COMMON``;
          ``eeschema/eeschema.cpp::HandleApiCloseDocument`` skips the
          save-prompt dialog, so this is also silent. Side-effect: editor
          zoom / cursor position are lost because the document is closed
          and reopened.

        Args:
            doc_type: ``"pcb"`` (default) or ``"schematic"`` — which open
                editor document to reload from disk.
        """
        if err := _require_editor(doc_type.lower()):
            return err
        if doc_type.lower() == "pcb":
            return _attach_auto_open(
                _save_or_revert_via_proper_command("revert", "DOCTYPE_PCB")
            )
        return _attach_auto_open(_revert_sch_via_close_open())

    @mcp.tool()
    def ipc_export_schematic(  # pylint: disable=redefined-builtin  # `format` is API-stable
        output_path: str,
        format: str = "svg",
        netlist_format: str = "kicad_sexpr",
        variant: str = "",
        plot_drawing_sheet: bool = True,
    ) -> dict[str, Any]:
        """Export the live schematic to disk via the IPC ``RunSchematicJobExport*``
        commands — bypasses the on-disk file, so the **unsaved in-memory
        state** is what gets written. Counterpart to ``kicad-cli sch export``,
        but consumes the editor's current buffer instead of the saved file.

        Use this when you need an SVG/PDF/netlist/BOM that reflects unsaved
        schematic edits in the running Eeschema (e.g. to run live ERC on a
        netlist) without first saving the project to disk.

        Args:
            output_path: absolute path where the file should land. The host
                running KiCad must be able to write to this path (in WSL
                setups, pass a Windows path).
            format: one of ``"svg"``, ``"pdf"``, ``"dxf"``, ``"ps"``,
                ``"netlist"``, ``"bom"``.
            netlist_format: when ``format="netlist"``, one of
                ``"kicad_sexpr"`` (default), ``"kicad_xml"``, ``"spice"``,
                ``"spice_model"``, ``"orcad_pcb2"``, ``"cadstar"``,
                ``"pads"``, ``"allegro"``.
            variant: design variant name (passed through; empty = default).
            plot_drawing_sheet: include the title block / drawing sheet in
                graphical exports (svg/pdf/dxf/ps).

        Use this in combination with ``run_erc`` for live ERC against
        unsaved-edit schematics:

        >>> ipc_export_schematic("/tmp/live.net", format="netlist")
        >>> run_erc(schematic_path="/tmp/live.net")

        Auto-opens Eeschema if it isn't running yet.
        """
        from kicad_mcp.utils.path_env import to_local_path  # local import to avoid cycle
        output_path = to_local_path(output_path)
        if err := _require_editor("schematic"):
            return err
        if not _kipy_available():
            return {"success": False, "error": "kipy not installed."}
        try:
            from kipy import KiCad  # type: ignore
            from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
                DocumentType,
            )
            from kipy.proto.common.types.jobs_pb2 import (  # type: ignore
                RunJobResponse,
                RunJobSettings,
            )
            from kipy.proto.schematic.schematic_jobs_pb2 import (  # type: ignore
                RunSchematicJobExportBOM,
                RunSchematicJobExportDxf,
                RunSchematicJobExportNetlist,
                RunSchematicJobExportPdf,
                RunSchematicJobExportPs,
                RunSchematicJobExportSvg,
                SchematicNetlistFormat,
                SchematicPlotSettings,
            )
        except Exception as exc:
            return {"success": False, "error": f"kipy import failed: {exc}"}

        format_lower = format.lower().strip()
        if format_lower not in {"svg", "pdf", "dxf", "ps", "netlist", "bom"}:
            return {
                "success": False,
                "error": (
                    f"unknown format {format!r} — expected one of svg/pdf/dxf/"
                    "ps/netlist/bom."
                ),
            }
        try:
            client = KiCad(timeout_ms=_ipc_timeout_ms())
        except Exception as exc:
            return {"success": False, "error": f"Cannot reach KiCad: {exc}"}
        try:
            docs = client.get_open_documents(DocumentType.DOCTYPE_SCHEMATIC)
        except Exception as exc:
            return {"success": False, "error": f"get_open_documents failed: {exc}"}
        if not docs:
            return {"success": False, "error": "No schematic open in KiCad."}

        settings = RunJobSettings()
        settings.document.CopyFrom(docs[0])
        settings.output_path = output_path

        if format_lower == "netlist":
            netlist_map = {
                "kicad_xml": SchematicNetlistFormat.SNF_KICAD_XML,
                "kicad_sexpr": SchematicNetlistFormat.SNF_KICAD_SEXPR,
                "orcad_pcb2": SchematicNetlistFormat.SNF_ORCAD_PCB2,
                "cadstar": SchematicNetlistFormat.SNF_CADSTAR,
                "spice": SchematicNetlistFormat.SNF_SPICE,
                "spice_model": SchematicNetlistFormat.SNF_SPICE_MODEL,
                "pads": SchematicNetlistFormat.SNF_PADS,
                "allegro": SchematicNetlistFormat.SNF_ALLEGRO,
            }
            nf_key = netlist_format.lower().strip()
            if nf_key not in netlist_map:
                return {
                    "success": False,
                    "error": (
                        f"unknown netlist_format {netlist_format!r} — expected "
                        "one of " + ", ".join(sorted(netlist_map))
                    ),
                }
            cmd = RunSchematicJobExportNetlist()
            cmd.job_settings.CopyFrom(settings)
            cmd.format = netlist_map[nf_key]
            if variant:
                cmd.variant_name = variant
        elif format_lower == "bom":
            cmd = RunSchematicJobExportBOM()
            cmd.job_settings.CopyFrom(settings)
            if variant:
                cmd.variant_name = variant
        else:
            plot_settings = SchematicPlotSettings()
            plot_settings.plot_all = True
            plot_settings.plot_drawing_sheet = plot_drawing_sheet
            if variant:
                plot_settings.variant = variant
            cmd_cls = {
                "svg": RunSchematicJobExportSvg,
                "pdf": RunSchematicJobExportPdf,
                "dxf": RunSchematicJobExportDxf,
                "ps": RunSchematicJobExportPs,
            }[format_lower]
            cmd = cmd_cls()
            cmd.job_settings.CopyFrom(settings)
            cmd.plot_settings.CopyFrom(plot_settings)

        try:
            response = client._client.send(cmd, RunJobResponse)
        except Exception as exc:
            err_text = str(exc)
            if "no handler" in err_text.lower():
                return {
                    "success": False,
                    "error": err_text,
                    "format": format_lower,
                    "kicad_10_0_x_limitation": True,
                    "hint": (
                        "KiCad 10.0.x's Eeschema does not register the "
                        "RunSchematicJobExport* handlers (only the proto "
                        "messages exist client-side; the server handler "
                        "ships in 10.1+). Tracking: KiCad #2077.\n"
                        "Fallback: save the schematic (Ctrl+S in Eeschema, "
                        "or ipc_save_via_action), then call "
                        "kicad-cli sch export <fmt> against the on-disk "
                        ".kicad_sch — that bypasses the live buffer but "
                        "produces the same output for saved state."
                    ),
                }
            return {
                "success": False,
                "error": err_text,
                "format": format_lower,
                "hint": (
                    "ipc_export_schematic requires Eeschema running with a "
                    "document loaded. If Eeschema is open and you still get "
                    "this, check Preferences → Plugins → IPC API."
                ),
            }

        # JS_SUCCESS=1, JS_WARNING=2, JS_ERROR=3 in jobs_pb2.JobStatus
        status = int(response.status)
        ok = status in (1, 2)
        return _attach_auto_open(
            {
                "success": ok,
                "format": format_lower,
                "output_path": response.output_path or output_path,
                "status_code": status,
                "message": response.message,
                "warning": status == 2,
            }
        )

    @mcp.tool()
    def ipc_revert_via_action() -> dict[str, Any]:
        """Legacy revert via ``common.Control.revert`` (RunAction). Same
        action as ``File → Revert`` in the GUI menus. KiCad pops a
        "Save changes?" confirmation; default is **Save**, which would
        overwrite any disk patches with the GUI's stale memory. Click
        Discard to actually load the on-disk version. Use this only on
        Eeschema or as a manual fallback when ``ipc_revert`` rejects the
        document type.
        """
        if not _kipy_available():
            return {"success": False, "error": "kipy not installed."}
        try:
            from kipy import KiCad  # type: ignore
        except Exception as exc:
            return {"success": False, "error": f"kipy import failed: {exc}"}
        try:
            client = KiCad(timeout_ms=_ipc_timeout_ms())
        except Exception as exc:
            return {"success": False, "error": f"Cannot reach KiCad: {exc}"}
        action = "common.Control.revert"
        try:
            result = client.run_action(action)
        except Exception as exc:
            msg = str(exc)
            if "Timed out" in msg or "timed out" in msg.lower():
                return {
                    "success": True,
                    "action": action,
                    "note": "Confirmation dialog open in KiCad — click Discard.",
                }
            return {"success": False, "error": msg, "action": action}
        return {"success": True, "action": action, "action_status": str(result).strip()}

    @mcp.tool()
    def ipc_install_kipy(python_executable: str = "") -> dict[str, Any]:
        """Bootstrap the ``kicad-python`` (kipy) client so the IPC bridge can run.

        Use this once on a fresh setup when ``ipc_check_status`` reports
        ``kipy_available: False``. The tool runs
        ``pip install kicad-python`` against the bundled KiCad Python
        (or the interpreter you pass) so subsequent ``ipc_*`` tools can
        import the protobuf modules. Don't ``pip install`` from a
        terminal yourself — KiCad's bundled Python isn't on ``PATH`` on
        Windows, and an installed-elsewhere kipy is invisible to the
        server.

        After install the user must enable Preferences → Plugins →
        IPC API in KiCad and have a board open before
        ``ipc_check_status`` reports green.

        Args:
            python_executable: Override the Python interpreter that
                ``pip`` installs into. Default = current interpreter
                (``sys.executable`` — i.e. the bundled KiCad Python
                when launched via ``start_mcp.bat`` /
                ``start_mcp_wsl.sh``).

        Returns:
            ``{success, output, next_step}`` — ``output`` is the
            combined pip stdout/stderr, ``next_step`` a human-readable
            hint to enable the IPC API in Preferences.
        """
        from kicad_mcp.utils.path_env import to_local_path  # local import to avoid cycle
        normalised = to_local_path(python_executable) if python_executable else None
        ok, out = _pip_install_kipy(normalised)
        return {
            "success": ok,
            "output": out,
            "next_step": (
                "Now enable Preferences → Plugins → IPC API in a running "
                "KiCad instance, open a board, and call ipc_check_status."
            ),
        }

    @mcp.tool()
    def ipc_get_pad_world_pos(ref: str, pin: str) -> dict[str, Any]:
        """Return the world-coordinate position of a pad (millimetres).

        This is the operation that ``pcb_patch_tools`` cannot reliably do via
        text parsing: when a footprint has been flipped to ``B.Cu``, the pad
        offsets in the file are *not* simply mirrored, and the math diverges
        from the cosmetic rotation. kipy delegates the math to KiCad itself
        and returns the canonical position.

        Args:
            ref: Component reference (e.g. ``"U8"``).
            pin: Pad name/number (e.g. ``"1"``, ``"23"``).
        """
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        fp = _find_footprint_by_ref(board, ref)
        if fp is None:
            return {"success": False, "error": f"Footprint {ref} not found."}
        pad = _find_pad(board, fp, pin)
        if pad is None:
            return {
                "success": False,
                "error": f"Pad {pin} of {ref} not found.",
            }
        try:
            x, y = _pad_world_xy_mm(pad)
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        return _attach_auto_open(
            {"success": True, "ref": ref, "pin": pin, "x_mm": x, "y_mm": y}
        )

    @mcp.tool()
    def ipc_set_footprint_pose(
        ref: str,
        x_mm: Optional[float] = None,
        y_mm: Optional[float] = None,
        dx_mm: float = 0.0,
        dy_mm: float = 0.0,
        angle_deg: Optional[float] = None,
        delta_angle_deg: float = 0.0,
        dry_run: bool = False,
        expect_sig: Optional[list] = None,
    ) -> dict[str, Any]:
        """Live-translate / rotate a footprint in the running KiCad PCB
        editor — visible **immediately**, no F5, no dialog.

        Use this when you want to reposition or rotate a placed component in
        the open PCB editor and have the change appear instantly as a single
        undoable step (optionally guarded against clobbering a user's
        concurrent edit via the dry_run/expect_sig confirm gate).

        Uses the proper ``UpdateItems`` IPC command path: KiCad mutates the
        in-memory footprint, the GUI re-renders, and a single
        ``push_commit`` registers the change as one undo-step (Ctrl+Z
        reverts cleanly).

        Args:
            ref: Component reference (e.g. ``"U1"``, ``"LED9"``).
            x_mm: Absolute target X position in millimetres (board coords); ``None`` keeps the current X (shift only via ``dx_mm``).
            y_mm: Absolute target Y position in millimetres (board coords); ``None`` keeps the current Y (shift only via ``dy_mm``).
            dx_mm: Relative X offset in millimetres added to the current position (default ``0``).
            dy_mm: Relative Y offset in millimetres added to the current position (default ``0``).
            angle_deg: Absolute target rotation in degrees.
            delta_angle_deg: Delta added to the current rotation.

        At least one of position or angle must be supplied (otherwise the
        call is a no-op and rejected).

        Live-collaboration safety (compare-and-swap): KiCad has no item lock,
        so pass ``dry_run=True`` first to read the footprint's current ``sig``
        (its plan baseline), then confirm the move with ``expect_sig=sig``. If
        the footprint changed since (the user moved it) and it is not the
        agent's own write, the move is REFUSED
        (``{success: False, conflict: True, who: "user", ...}``) rather than
        clobbering the user. Without ``expect_sig`` the move applies as before.

        Args:
            dry_run: if True, report before/after pose + ``sig`` WITHOUT
                writing (the plan half of a confirm gate).
            expect_sig: the ``sig`` from a prior dry_run; the write is refused
                if the live signature no longer matches it.

        Returns ``{success, ref, before:{x_mm,y_mm,angle_deg},
        after:{x_mm,y_mm,angle_deg}, sig}`` (``sig`` = the footprint signature
        after the change, or current pose under ``dry_run``); on a user clash
        ``{success: False, conflict: True, who, baseline_sig, current_sig}``.
        """
        if (
            x_mm is None
            and y_mm is None
            and dx_mm == 0.0
            and dy_mm == 0.0
            and angle_deg is None
            and delta_angle_deg == 0.0
        ):
            return {
                "success": False,
                "error": "Specify at least one of x_mm/y_mm/dx_mm/dy_mm/angle_deg/delta_angle_deg.",
            }
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        try:
            from kipy.geometry import Angle, Vector2  # type: ignore
        except Exception as exc:
            return {"success": False, "error": f"kipy types not available: {exc}"}

        fp = _find_footprint_by_ref(board, ref)
        if fp is None:
            return {"success": False, "error": f"Footprint {ref!r} not found."}

        before = {
            "x_mm": fp.position.x / 1_000_000.0,
            "y_mm": fp.position.y / 1_000_000.0,
            "angle_deg": fp.orientation.degrees,
        }

        # Compare-and-swap: refuse to clobber a concurrent user move.
        cur_sig = fp_signature(fp.position.x, fp.position.y,
                               fp.orientation.degrees, fp.layer)
        if expect_sig is not None and cas_conflict(cur_sig, expect_sig, None):
            return {
                "success": False, "conflict": True, "who": "user", "ref": ref,
                "baseline_sig": list(expect_sig), "current_sig": list(cur_sig),
                "error": (f"{ref} wurde seit deinem Plan im Editor geaendert "
                          "(vermutlich vom User) — NICHT verschoben. Pose per "
                          "dry_run neu lesen und neu planen."),
            }

        # Compute new pose.
        new_x_mm = (
            float(x_mm) if x_mm is not None else before["x_mm"]
        ) + float(dx_mm)
        new_y_mm = (
            float(y_mm) if y_mm is not None else before["y_mm"]
        ) + float(dy_mm)
        new_angle = (
            float(angle_deg)
            if angle_deg is not None
            else before["angle_deg"]
        ) + float(delta_angle_deg)
        new_angle = ((new_angle % 360.0) + 360.0) % 360.0

        if dry_run:
            return {
                "success": True, "dry_run": True, "ref": ref, "before": before,
                "after": {"x_mm": new_x_mm, "y_mm": new_y_mm,
                          "angle_deg": new_angle},
                "sig": list(cur_sig),
            }

        try:
            fp.position = Vector2.from_xy_mm(new_x_mm, new_y_mm)
            fp.orientation = Angle.from_degrees(new_angle)
            commit = board.begin_commit()
            board.update_items(fp)
            board.push_commit(commit, f"kicad-mcp set_footprint_pose {ref}")
        except Exception as exc:
            return {"success": False, "error": f"update failed: {exc}"}

        # Re-fetch to confirm persisted values.
        fp2 = _find_footprint_by_ref(board, ref)
        after = {
            "x_mm": fp2.position.x / 1_000_000.0 if fp2 else new_x_mm,
            "y_mm": fp2.position.y / 1_000_000.0 if fp2 else new_y_mm,
            "angle_deg": fp2.orientation.degrees if fp2 else new_angle,
        }
        new_sig = (fp_signature(fp2.position.x, fp2.position.y,
                                fp2.orientation.degrees, fp2.layer)
                   if fp2 else cur_sig)
        return _attach_auto_open(
            {
                "success": True,
                "ref": ref,
                "before": before,
                "after": after,
                "sig": list(new_sig),
            }
        )

    @mcp.tool()
    def ipc_route_pin_to_pin(
        ref1: str, pin1: str,
        ref2: str, pin2: str,
        layer: str = "F.Cu",
        width_mm: float = 0.25,
        with_via: bool = True,
    ) -> dict[str, Any]:
        """Add a track from pad ``(ref1, pin1)`` to pad ``(ref2, pin2)``.

        If the two pads sit on different copper layers and ``with_via`` is
        true, the tool adds a through-via at the destination point so the
        track lands on a valid layer.

        Args:
            ref1: Source footprint reference (e.g. ``"U1"``).
            pin1: Source pad number/name on ``ref1`` (e.g. ``"1"``).
            ref2: Destination footprint reference (e.g. ``"R5"``).
            pin2: Destination pad number/name on ``ref2`` (e.g. ``"2"``).
            layer: Track layer (``"F.Cu"`` / ``"B.Cu"`` / ``"In1.Cu"`` …).
            width_mm: Track width in millimetres.
            with_via: Insert a layer-change via at the endpoint if needed.
        """
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        fp1 = _find_footprint_by_ref(board, ref1)
        fp2 = _find_footprint_by_ref(board, ref2)
        if not (fp1 and fp2):
            missing = []
            if not fp1:
                missing.append(ref1)
            if not fp2:
                missing.append(ref2)
            return {
                "success": False,
                "error": f"Footprint(s) not found: {', '.join(missing)}.",
            }
        pad1 = _find_pad(board, fp1, pin1)
        pad2 = _find_pad(board, fp2, pin2)
        if not (pad1 and pad2):
            return {
                "success": False,
                "error": (
                    f"Pad lookup failed: {ref1}.{pin1} → "
                    f"{'OK' if pad1 else 'MISS'}, "
                    f"{ref2}.{pin2} → {'OK' if pad2 else 'MISS'}"
                ),
            }
        try:
            from kipy.board import Track, Via  # type: ignore
            from kipy.geometry import Vector2  # type: ignore
        except Exception as exc:
            return {
                "success": False,
                "error": f"kipy types not available: {exc}",
            }
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {
                "success": False,
                "error": f"Unknown layer: {layer!r}",
            }
        try:
            x1, y1 = _pad_world_xy_mm(pad1)
            x2, y2 = _pad_world_xy_mm(pad2)
            track = Track()
            track.start = Vector2.from_xy_mm(x1, y1)
            track.end = Vector2.from_xy_mm(x2, y2)
            track.width = int(round(width_mm * 1_000_000))
            track.layer = layer_enum
            # Inherit net from source pad (Track wrapper accepts pad.net directly).
            try:
                track.net = pad1.net
            except Exception:
                pass
            commit = board.begin_commit()
            board.create_items(track)
            via_added = False
            l1 = _pad_primary_layer_enum(pad1)
            l2 = _pad_primary_layer_enum(pad2)
            if with_via and l1 is not None and l2 is not None and l1 != l2:
                via = Via()
                via.position = Vector2.from_xy_mm(x2, y2)
                # A default Via() has diameter/drill 0 (degenerate). Set the
                # board default so the via is real.
                _vd, _vk = _board_default_via_nm(board)
                via.diameter = _vd
                via.drill_diameter = _vk
                try:
                    via.net = pad1.net
                except Exception:
                    pass
                board.create_items(via)
                via_added = True
            board.push_commit(commit, "kicad-mcp ipc_route_pin_to_pin")
            return _attach_auto_open(
                {
                    "success": True,
                    "from": {"ref": ref1, "pin": pin1, "x_mm": x1, "y_mm": y1},
                    "to": {"ref": ref2, "pin": pin2, "x_mm": x2, "y_mm": y2},
                    "layer": layer,
                    "width_mm": width_mm,
                    "via_added": via_added,
                    "net": getattr(getattr(pad1, "net", None), "name", None),
                }
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def ipc_add_zone_pour(
        net_name: str,
        layer: str,
        polygon_xy_mm: list[list[float]],
    ) -> dict[str, Any]:
        """Add a filled zone (copper pour) bound to ``net_name`` on ``layer``.

        Use this when you want to drop a ground/power plane or shielding pour
        over a region of the live board by giving its outline polygon and the
        net it should connect to.

        Args:
            net_name: Net name to bind the pour to (e.g. ``"GND"``).
            layer: KiCad copper layer name.
            polygon_xy_mm: Outline as a list of ``[x_mm, y_mm]`` pairs (>=3
                points). Closed implicitly.
        """
        if len(polygon_xy_mm) < 3:
            return {
                "success": False,
                "error": "Polygon needs at least 3 points.",
            }
        if err := _require_editor("pcb"):
            return err
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        try:
            from kipy.board import Zone  # type: ignore
            from kipy.geometry import PolyLineNode  # type: ignore
        except Exception as exc:
            return {
                "success": False,
                "error": f"kipy types not available: {exc}",
            }
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {
                "success": False,
                "error": f"Unknown layer: {layer!r}",
            }
        net = _find_net(board, net_name)
        if net is None:
            return {
                "success": False,
                "error": (
                    f"Net {net_name!r} does not exist on this board "
                    f"— create it via the schematic / netlist first."
                ),
            }
        try:
            zone = Zone()
            # PolySet has 'polygons' repeated; add one outline-polygon to it,
            # then append PolyLineNode-points to the wrapper's outline list.
            zone._proto.outline.polygons.add()
            outline = zone.outline.outline
            for x, y in polygon_xy_mm:
                outline.append(
                    PolyLineNode.from_xy(
                        int(round(x * 1_000_000)),
                        int(round(y * 1_000_000)),
                    )
                )
            outline.closed = True
            zone.layers = [layer_enum]
            zone.net = net
            commit = board.begin_commit()
            board.create_items(zone)
            board.push_commit(commit, "kicad-mcp ipc_add_zone_pour")
            return _attach_auto_open(
                {
                    "success": True,
                    "net": net_name,
                    "layer": layer,
                    "vertices": len(polygon_xy_mm),
                }
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def ipc_route_power_ring(
        net_name: str,
        nodes: list[list[str]],
        layer: str = "B.Cu",
        width_mm: float = 0.5,
    ) -> dict[str, Any]:
        """Route a wide power track that visits a sequence of pads in order.

        Use this when you need to daisy-chain a power/ground rail through an
        ordered list of pads (e.g. VBUS across several decoupling caps) in one
        call rather than placing each segment individually.

        Each consecutive pair of pads becomes one track segment. Useful for
        chaining a power rail through several decoupling-pad attachment
        points. Use a width >= 0.5 mm for VBUS rails.

        Args:
            net_name: Power net name (used in the response only; the actual
                net assignment relies on the pads already carrying that net).
            nodes: List of ``[ref, pin]`` pairs in routing order.
            layer: Track layer.
            width_mm: Track width.
        """
        if len(nodes) < 2:
            return {
                "success": False,
                "error": "Need at least 2 nodes to form a segment.",
            }
        if err := _require_editor("pcb"):
            return err
        segments_added = 0
        last_xy: tuple[float, float] | None = None
        results = []
        try:
            _, board = _connect_kicad()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        try:
            from kipy.board import Track  # type: ignore
            from kipy.geometry import Vector2  # type: ignore
        except Exception as exc:
            return {
                "success": False,
                "error": f"kipy types not available: {exc}",
            }
        layer_enum = _layer_to_enum(layer)
        if layer_enum is None:
            return {
                "success": False,
                "error": f"Unknown layer: {layer!r}",
            }
        net = _find_net(board, net_name)
        if net is None:
            # Without a resolved net the ring tracks would be created with no
            # net (unconnected copper) while the tool reported success — fail
            # loudly instead (mirrors ipc_add_zone_pour).
            return {
                "success": False,
                "error": f"net {net_name!r} not found on board.",
                "segments_added": 0,
            }
        commit = board.begin_commit()
        tracks: list = []
        for ref, pin in nodes:
            fp = _find_footprint_by_ref(board, ref)
            if fp is None:
                board.drop_commit(commit)
                return {
                    "success": False,
                    "error": f"Footprint {ref} not found.",
                    "segments_added": 0,
                }
            pad = _find_pad(board, fp, pin)
            if pad is None:
                board.drop_commit(commit)
                return {
                    "success": False,
                    "error": f"Pad {pin} of {ref} not found.",
                    "segments_added": 0,
                }
            try:
                x, y = _pad_world_xy_mm(pad)
            except RuntimeError as exc:
                board.drop_commit(commit)
                return {"success": False, "error": str(exc)}
            if last_xy is not None:
                track = Track()
                track.start = Vector2.from_xy_mm(*last_xy)
                track.end = Vector2.from_xy_mm(x, y)
                track.width = int(round(width_mm * 1_000_000))
                track.layer = layer_enum
                if net is not None:
                    try:
                        track.net = net
                    except Exception:
                        pass
                tracks.append(track)
                segments_added += 1
            last_xy = (x, y)
            results.append({"ref": ref, "pin": pin, "x_mm": x, "y_mm": y})
        try:
            if tracks:
                board.create_items(tracks)
            board.push_commit(commit, "kicad-mcp ipc_route_power_ring")
        except Exception as exc:
            try:
                board.drop_commit(commit)
            except Exception:
                pass
            return {"success": False, "error": f"commit failed: {exc}"}
        return _attach_auto_open(
            {
                "success": True,
                "net": net_name,
                "layer": layer,
                "width_mm": width_mm,
                "nodes_routed": len(nodes),
                "segments_added": segments_added,
                "node_positions": results,
            }
        )

    @mcp.tool()
    def ipc_open_kicad(
        project_path: str,
        doc_type: str = "pcb",
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        """Launch KiCad (pcbnew or eeschema) with an explicit project file.

        Use this to start from a **cold** KiCad state — no editor open yet —
        when you have the project path. It deliberately refuses to spawn a
        standalone editor when a KiCad project manager is already running
        (that would create a second IPC server on the same socket and break
        GetOpenDocuments for every ipc_* tool); in that case open the editor
        from the running manager instead, or close KiCad first. The companion
        :func:`ipc_close_kicad` sends a clean shutdown.

        Args:
            project_path: Path to ``.kicad_pro``, ``.kicad_pcb``, or
                ``.kicad_sch``. The matching file for ``doc_type`` is
                derived (``.kicad_pcb`` for ``"pcb"``,
                ``.kicad_sch`` for ``"schematic"``).
            doc_type: ``"pcb"`` (default) or ``"schematic"``.
            timeout_s: How long to wait for the editor to register on the
                IPC bus before giving up. Default 15 s.

        Returns:
            On success ``{success: True, doc_type, binary, project_file,
            already_running}``. On failure a ``{success: False, error, …}``
            that may carry ``manager_running: True`` (a manager was already
            up — open the doc there, don't double-launch) or
            ``api_handler_missing: True`` (KiCad reached but GetOpenDocuments
            unhandled — dual-instance state or kipy↔KiCad version skew).
        """
        from kicad_mcp.utils.path_env import to_local_path

        if doc_type not in ("pcb", "schematic"):
            return {"success": False, "error": f"unknown doc_type {doc_type!r}"}
        if not _kipy_available():
            return {"success": False, "error": "kipy not installed."}

        # Normalize path; derive .kicad_pcb / .kicad_sch from input if given
        # a .kicad_pro
        local_path = to_local_path(project_path)
        if not os.path.exists(local_path):
            return {
                "success": False,
                "error": f"project_path does not exist: {project_path}",
            }
        base, ext = os.path.splitext(local_path)
        suffix = ".kicad_pcb" if doc_type == "pcb" else ".kicad_sch"
        if ext == ".kicad_pro":
            target_file = base + suffix
        elif ext == suffix:
            target_file = local_path
        elif ext in (".kicad_pcb", ".kicad_sch"):
            # User passed the *other* file — derive sibling
            target_file = os.path.splitext(local_path)[0] + suffix
        else:
            return {
                "success": False,
                "error": (
                    f"project_path must be .kicad_pro / .kicad_pcb / .kicad_sch, "
                    f"got {ext!r}"
                ),
            }
        if not os.path.isfile(target_file):
            return {
                "success": False,
                "error": f"derived {doc_type} file not found: {target_file}",
            }

        binary = _editor_binary_path(doc_type)
        if not binary:
            return {
                "success": False,
                "error": (
                    f"Could not locate {doc_type} binary "
                    f"({'eeschema' if doc_type == 'schematic' else 'pcbnew'}). "
                    "Set KICAD_BIN to your KiCad bin/ directory."
                ),
            }

        already_running = _editor_process_running(doc_type)
        editor_name = "eeschema" if doc_type == "schematic" else "pcbnew"
        # Guard against the dual-instance conflict: if a KiCad project manager
        # is already running but its editor frame is not, launching a
        # *standalone* editor here spins up a SECOND IPC API server on the same
        # socket. The two then fight and GetOpenDocuments stops resolving
        # ("no handler") — exactly the failure that blocks every other ipc_*
        # tool. Don't double-launch; tell the caller to open the editor from
        # the running manager (which shares the manager's API server) or to
        # close KiCad fully so we can cold-start a single clean instance.
        if not already_running and _kicad_manager_running():
            return {
                "success": False,
                "error": (
                    f"A KiCad project manager is already running. Launching a "
                    f"standalone {editor_name} now would open a second IPC API "
                    f"server on the same socket and break GetOpenDocuments "
                    f"(every ipc_* tool then fails with 'no handler'). Open the "
                    f"{doc_type} from the running KiCad project manager instead, "
                    f"or close KiCad entirely and call this again for a clean "
                    f"cold start."
                ),
                "doc_type": doc_type,
                "binary": binary,
                "project_file": target_file,
                "manager_running": True,
                "already_running": False,
            }
        if not already_running:
            try:
                if os.name == "nt" or binary.lower().endswith(".exe"):
                    DETACHED_PROCESS = 0x00000008  # noqa: N806
                    proc = subprocess.Popen(
                        [binary, target_file],
                        creationflags=DETACHED_PROCESS,
                        close_fds=True,
                    )
                else:
                    proc = subprocess.Popen(
                        [binary, target_file],
                        start_new_session=True,
                        close_fds=True,
                    )
                # Record the PID: a DETACHED editor sits outside the plugin's
                # kill-tree, so without this it orphans into a board-less ghost
                # that squats the IPC socket and breaks every link. Reaped by
                # ipc_close_kicad and the plugin's shutdown handler.
                from kicad_mcp.utils import spawned_registry
                spawned_registry.record(proc.pid)
            except Exception as exc:
                return {
                    "success": False,
                    "error": f"Failed to launch {os.path.basename(binary)}: {exc}",
                }

        # Poll IPC bus for the editor to register
        try:
            from kipy import KiCad  # type: ignore
            from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
                DocumentType,
            )
        except Exception as exc:
            return {"success": False, "error": f"kipy import failed: {exc}"}
        doc_const = (
            DocumentType.DOCTYPE_SCHEMATIC
            if doc_type == "schematic"
            else DocumentType.DOCTYPE_PCB
        )
        api_handler_missing = False
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                client = KiCad(timeout_ms=_ipc_timeout_ms())
                if client.get_open_documents(doc_const):
                    return {
                        "success": True,
                        "doc_type": doc_type,
                        "binary": os.path.basename(binary),
                        "project_file": target_file,
                        "already_running": already_running,
                    }
            except Exception as exc:
                # "no handler for GetOpenDocuments" never resolves by waiting —
                # it's a dual-instance API state or a kipy<->KiCad version skew,
                # not a slow editor launch. Break out and report it accurately
                # instead of burning the whole timeout on a misleading message.
                if "no handler" in str(exc).lower():
                    api_handler_missing = True
                    break
            time.sleep(0.3)
        if api_handler_missing:
            return {
                "success": False,
                "error": (
                    "KiCad's API is reachable but did not handle "
                    "GetOpenDocuments (needed to confirm the open board). This "
                    "is almost always a dual-instance API state (a second, "
                    "standalone editor running alongside the project manager) "
                    "or a kipy<->KiCad version skew. Ensure exactly ONE KiCad "
                    "instance is running (project manager + its editor), then "
                    "retry."
                ),
                "api_handler_missing": True,
                "binary": binary,
                "project_file": target_file,
                "already_running": already_running,
            }
        return {
            "success": False,
            "error": (
                f"Launched {os.path.basename(binary)} but it did not register on "
                f"the IPC bus within {timeout_s:.0f}s. Verify Preferences → "
                "Plugins → Enable KiCad API is on."
            ),
            "binary": binary,
            "project_file": target_file,
            "already_running": already_running,
        }

    @mcp.tool()
    def ipc_close_kicad(
        doc_type: str = "pcb",
        save: bool = True,
        force: bool = True,
    ) -> dict[str, Any]:
        """Close KiCad reliably and VERIFY the editor process exited.

        Why this needs more than one IPC call: ``CloseDocument`` closes a
        document tab but leaves the ``pcbnew``/``eeschema`` PROCESS
        running, and ``send()`` is fire-and-forget — a ``closed_count``
        derived from "command sent without exception" proves nothing. A
        scripted file-patch workflow needs the editor genuinely gone, or
        the next disk write races KiCad's in-memory copy and the GUI
        save clobbers the patch.

        Sequence:
          1. save the open document(s) (``save=True``, default) via the
             SaveDocument IPC so a force-terminate loses nothing;
          2. send CloseDocument (graceful);
          3. poll the OS process list for a clean exit;
          4. if the process is still alive and ``force`` (default True),
             terminate it (``taskkill /F`` / ``pkill``);
          5. delete the stale ``.lck`` lock file the kill leaves behind;
          6. return a VERIFIED ``running`` flag — ``success`` is True
             only when the process is confirmed gone.

        Args:
            doc_type: ``"pcb"`` (default) or ``"schematic"``.
            save: save the document before closing (default True).
            force: OS-terminate the process if the graceful close left
                it running (default True).

        Returns:
            ``{success, running, was_running, graceful, saved,
            force_terminated, locks_removed, ...}``.
        """
        if doc_type not in ("pcb", "schematic"):
            return {"success": False, "error": f"unknown doc_type {doc_type!r}"}
        result: dict[str, Any] = {"doc_type": doc_type}
        proj_files: list[str] = []

        if _kipy_available():
            try:
                from kipy import KiCad  # type: ignore
                from kipy.proto.common.types.base_types_pb2 import (  # type: ignore
                    DocumentType,
                )

                client = KiCad(timeout_ms=_ipc_timeout_ms())
                doc_const = (
                    DocumentType.DOCTYPE_SCHEMATIC
                    if doc_type == "schematic"
                    else DocumentType.DOCTYPE_PCB
                )
                try:
                    docs = client.get_open_documents(doc_const)
                except Exception:
                    docs = []
                for d in docs:
                    try:
                        if (
                            d.HasField("project")
                            and d.project.path
                            and d.project.name
                        ):
                            proj_files.append(
                                os.path.join(d.project.path, d.project.name)
                            )
                    except Exception:
                        pass
                if save and docs:
                    try:
                        from google.protobuf.empty_pb2 import Empty  # type: ignore
                        from kipy.proto.common.commands.editor_commands_pb2 import (  # type: ignore  # noqa: E501
                            SaveDocument,
                        )

                        for d in docs:
                            cmd = SaveDocument()
                            if hasattr(cmd, "document"):
                                cmd.document.CopyFrom(d)
                            # send() needs a response type; without it the save
                            # raised TypeError and was caught below → the
                            # graceful save before the force-kill never ran.
                            client._client.send(cmd, Empty)  # noqa: SLF001
                        result["saved"] = True
                        time.sleep(0.8)
                    except Exception as exc:
                        result["save_error"] = str(exc)
                if docs:
                    try:
                        from google.protobuf.empty_pb2 import Empty  # type: ignore
                        from kipy.proto.common.commands.editor_commands_pb2 import (  # type: ignore  # noqa: E501
                            CloseDocument,
                        )

                        for d in docs:
                            cmd = CloseDocument()
                            if hasattr(cmd, "document"):
                                cmd.document.CopyFrom(d)
                            client._client.send(cmd, Empty)  # noqa: SLF001
                    except Exception:
                        pass
            except Exception as exc:
                result["ipc_note"] = f"IPC unreachable: {exc}"

        # Poll for a clean graceful exit.
        deadline = time.time() + 4
        while time.time() < deadline and _editor_process_running(doc_type):
            time.sleep(0.5)
        was_running = _editor_process_running(doc_type)
        result["was_running"] = was_running
        result["graceful"] = not was_running

        # Force-terminate if the process survived the graceful close.
        if was_running and force:
            result["force_terminated"] = _kill_editor_process(doc_type)

        still = _editor_process_running(doc_type)
        result["running"] = still
        result["success"] = not still

        # Clear the spawned-editor registry: this close reaps whatever we
        # launched, so no recorded PID should outlive it as a ghost.
        try:
            from kicad_mcp.utils import spawned_registry
            result["reaped_spawned"] = spawned_registry.reap()
        except Exception:
            pass

        # Remove the stale lock file a force-terminate leaves behind.
        removed: list[str] = []
        for pf in proj_files:
            folder = os.path.dirname(pf)
            base = os.path.splitext(os.path.basename(pf))[0]
            for ext in (".kicad_pcb", ".kicad_sch"):
                lck = os.path.join(folder, f"~{base}{ext}.lck")
                if os.path.exists(lck):
                    try:
                        os.remove(lck)
                        removed.append(lck)
                    except Exception:
                        pass
        if removed:
            result["locks_removed"] = removed
        return result

    @mcp.tool()
    def ipc_reload_and_fill_zones(
        project_path: str = "",
        close_after: bool = False,
    ) -> dict[str, Any]:
        """Reload the active PCB from disk then fill all zones in one call.

        Equivalent to KiCad-GUI ``File → Revert`` + ``Edit → Fill All Zones``
        (``B`` hotkey). Use this after an external file-patch (``add_track_*``,
        ``patch_pcb_nets_from_netlist`` etc.) so the GUI picks up the disk
        state and the GND/Power pours are re-computed.

        Session lifecycle:
            * If ``project_path`` is given AND no PCB editor is running,
              KiCad is started against that project first (cold-open).
            * If ``close_after=True``, the PCB editor is closed once the
              fill completes (use for one-shot scripts; default False
              keeps the user's session intact).

        Args:
            project_path: Optional ``.kicad_pro`` / ``.kicad_pcb`` /
                ``.kicad_sch`` path. Used only when no editor is open.
            close_after: If True, close PCB editor after fill. Default
                False.

        Returns:
            ``{success, reloaded, zones_filled_action, action_status,
            auto_opened?, auto_closed?}``.
        """
        from kicad_mcp.utils.path_env import to_local_path
        if project_path:
            project_path = to_local_path(project_path)
            if not os.path.isfile(project_path):
                return {
                    "success": False,
                    "error": f"Project file not found: {project_path}",
                }
        if err := _require_editor("pcb", project_path=project_path):
            return err

        # Step 1: revert (reload from disk, silent)
        revert_result = _save_or_revert_via_proper_command("revert", "DOCTYPE_PCB")
        if not revert_result.get("success"):
            return {
                "success": False,
                "stage": "reload",
                "error": revert_result.get("error", "revert failed"),
                "detail": revert_result,
            }

        # Step 2: fill all zones via run_action
        # KiCad-10 canonical action: pcbnew.ZoneFiller.zoneFillAll
        action_variants = [
            "pcbnew.ZoneFiller.zoneFillAll",
            "pcbnew.ZoneFiller.fillAll",
            "pcbnew.EditorControl.zoneFillAll",
        ]
        last_err = None
        for action in action_variants:
            fill_result = _run_kicad_action(action)
            if fill_result.get("success"):
                resp = {
                    "success": True,
                    "reloaded": True,
                    "zones_filled_action": action,
                    "action_status": fill_result.get("action_status"),
                }
                if close_after:
                    cl = _close_editor_silent("pcb")
                    resp["auto_closed"] = {
                        "closed_count": cl.get("closed_count", 0),
                        "errors": cl.get("errors", []),
                    }
                return _attach_auto_open(resp)
            last_err = fill_result
            if fill_result.get("ipc_timed_out"):
                resp = {
                    "success": True,
                    "reloaded": True,
                    "zones_filled_action": action,
                    "ipc_timed_out": True,
                    "note": fill_result.get("note"),
                }
                if close_after:
                    cl = _close_editor_silent("pcb")
                    resp["auto_closed"] = {
                        "closed_count": cl.get("closed_count", 0),
                    }
                return _attach_auto_open(resp)
        return {
            "success": False,
            "stage": "fill_zones",
            "error": "All fill-zone action variants rejected by KiCad.",
            "known_action_variants": action_variants,
            "last_error": last_err,
        }

    @mcp.tool()
    def with_kicad_session(
        project_path: str,
        actions: list[dict[str, Any]],
        doc_type: str = "pcb",
        close_after: bool = True,
        stop_on_error: bool = True,
    ) -> dict[str, Any]:
        """Open KiCad, run a sequence of IPC actions, then optionally
        close. One cold-boot for many operations.

        Each action is a dict ``{"name": "<kicad-action-name>"}`` or
        ``{"reload": True}`` for a Revert + reload-from-disk step. The
        ``name`` form is dispatched via ``client.run_action(name)``
        (e.g. ``"pcbnew.ZoneFiller.zoneFillAll"``,
        ``"pcbnew.DRCTool.runDRC"``, ``"common.Control.save"``).

        Use this when scripting a series of live-KiCad operations that
        would otherwise require a manual GUI session — e.g. after a
        batch of file-edits run "reload + fill_zones + save_all" in
        one cold-start cycle.

        Args:
            project_path: ``.kicad_pro`` / ``.kicad_pcb`` / ``.kicad_sch``
                — the project to open if no editor is running.
            actions: List of action specs. Each is either
                ``{"name": "<kicad.action.name>"}`` or
                ``{"reload": True}`` (for SaveDocument-style revert).
            doc_type: Which editor to require — ``"pcb"`` (default)
                or ``"schematic"``.
            close_after: If True (default), close the editor after the
                last action. False = leave the session open for the
                user.
            stop_on_error: If True (default), stop at the first failing
                action. False = continue and report all results.

        Returns:
            ``{success, opened, results: [...], closed}`` where each
            entry in ``results`` is the dispatched action's reply dict.
        """
        from kicad_mcp.utils.path_env import to_local_path
        if project_path:
            project_path = to_local_path(project_path)
        if not actions:
            return {"success": False, "error": "actions list is empty"}
        if doc_type not in ("pcb", "schematic"):
            return {
                "success": False,
                "error": f"doc_type must be pcb or schematic, got {doc_type!r}",
            }
        if err := _require_editor(doc_type, project_path=project_path):
            return err
        opened_info = _consume_auto_open()

        results: list[dict[str, Any]] = []
        any_failed = False
        type_name = "DOCTYPE_PCB" if doc_type == "pcb" else "DOCTYPE_SCHEMATIC"
        for i, act in enumerate(actions):
            try:
                if act.get("reload"):
                    res = _save_or_revert_via_proper_command(
                        "revert", type_name,
                    )
                elif act.get("save"):
                    res = _save_or_revert_via_proper_command(
                        "save", type_name,
                    )
                elif "name" in act:
                    res = _run_kicad_action(act["name"])
                else:
                    res = {
                        "success": False,
                        "error": (
                            f"action[{i}] missing 'name'/'reload'/'save' "
                            f"key: {act!r}"
                        ),
                    }
            except Exception as exc:
                res = {
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append({"index": i, "action": act, **res})
            if not res.get("success"):
                any_failed = True
                if stop_on_error:
                    break

        closed_info: dict[str, Any] | None = None
        if close_after:
            cl = _close_editor_silent(doc_type)
            closed_info = {
                "closed_count": cl.get("closed_count", 0),
                "errors": cl.get("errors", []),
            }

        return {
            "success": not any_failed,
            "opened": opened_info,
            "actions_executed": len(results),
            "results": results,
            "closed": closed_info,
        }
