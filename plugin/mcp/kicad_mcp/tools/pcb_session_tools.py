# SPDX-License-Identifier: GPL-3.0-or-later
"""
Warm pcbnew session — the ``pcb_eval`` tool + session management.

Keeps a long-lived ``pcb_session_worker`` daemon that holds loaded +
zone-filled boards in memory, so analysis code runs in ms (the board is
loaded once, ~1 s, then reused). This is the fast scripting substrate:
instead of spawning a cold pcbnew process per ad-hoc script, the agent
runs its code against the warm board.

The server process stays pcbnew-free; it only manages the daemon over a
stdin/stdout pipe and forwards JSON requests.
"""
import importlib.util
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools._warm_daemon import WarmDaemon
from kicad_mcp.utils.path_env import to_local_path

_HAS_PCBNEW = importlib.util.find_spec("pcbnew") is not None
_WORKER = os.path.join(os.path.dirname(__file__), "pcb_session_worker.py")
_MARK = "@@PCBEVAL@@"
_DEFAULT_TIMEOUT = 30.0
_MAX_LOADS = 25  # recycle the daemon after this many fresh board loads (SWIG safety)


# The warm-board worker client. Recycle policy (mutated / SwigPyObject /
# load cap) and broken-pipe retry live in the shared WarmDaemon, kept in
# lock-step with the connectivity daemon.
_DAEMON = WarmDaemon(_WORKER, _MARK, max_loads=_MAX_LOADS)


def _eval_impl(pcb_path: str, code: str, timeout_s: float = _DEFAULT_TIMEOUT,
               max_chars: int = 8000) -> dict[str, Any]:
    if not _HAS_PCBNEW:
        return {"success": False, "error": "pcbnew not importable — run the MCP server under KiCad's bundled Python."}
    if not code or not code.strip():
        return {"success": False, "error": "code is empty"}
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    resp = _DAEMON.request(
        {"op": "eval", "pcb_path": pcb_path, "code": code, "max_chars": max_chars},
        timeout=timeout_s,
    )
    resp["success"] = bool(resp.get("ok"))
    return resp


def register_pcb_session_tools(mcp: FastMCP) -> None:
    """Register the warm-board session tools."""

    @mcp.tool()
    def pcb_eval(pcb_path: str, code: str, timeout_s: float = 30.0,
                 max_chars: int = 8000) -> dict[str, Any]:
        """Run analysis code against a WARM, in-memory pcbnew board (fast).

        The board is loaded + zone-filled once (~1 s) and cached by
        (path, mtime); every later call on the unchanged file reuses it and
        runs in milliseconds. Use this instead of spawning your own cold
        pcbnew script for ad-hoc geometry/connectivity analysis — same
        capability, ~100× faster on repeated queries.

        Your ``code`` runs with these pre-bound names:
          - ``board`` (loaded + filled BOARD), ``pcbnew``, ``math``
          - ``ctx`` — a dict that persists across calls on the same board
          - ``CX, CY`` (148.5, 105 reference centre; reassign if needed), ``MM``
          - Helpers (all flip/arc-accurate):
            ``world_pos(ref,num)``, ``find_pad(ref,num)``, ``fp_pads(ref)``,
            ``pads_on_net(net)``, ``cluster_of(ref,num)`` (connectivity),
            ``what_touches(x,y,r=0.4,layers=None,exclude_net=None)``,
            ``nearest_copper(x,y,...)``, ``rt(x,y)``/``xy(r,deg)``,
            ``ring_radius(n)``, ``fill()``, ``unconnected()``, ``nets()``.
          - ``helpers()`` — returns the full, always-current reference
            (every name → signature → return shape). Unsure what a helper
            returns? Run ``result = helpers()`` instead of guessing or
            reaching for raw pcbnew.

        Set a variable named ``result`` to whatever JSON-able value you
        want back; ``print(...)`` output is captured separately. The board
        may be mutated in memory for what-if analysis, but is NEVER written
        to disk — real edits stay with the text-patch tools. If the file
        changes on disk, the next call reloads automatically.

        Args:
            pcb_path: Path to the ``.kicad_pcb`` (WSL or Windows form).
            code: Python to execute against the warm board.
            timeout_s: Per-eval wall-clock limit; on timeout the daemon is
                recycled. Default 30.
            max_chars: Truncation budget for ``result`` and stdout. Default 8000.

        Returns:
            ``{success, result, result_truncated, stdout, cache_hit,
            zones_filled, elapsed_ms, loads}`` — or on failure
            ``{success: False, error, traceback?}``.

        Example:
            >>> pcb_eval(pcb, '''
            ... v = what_touches(160.45, 98.38, r=0.4)
            ... result = v
            ... ''')
        """
        pcb_path = to_local_path(pcb_path)
        return _eval_impl(pcb_path, code, timeout_s=timeout_s, max_chars=max_chars)

    @mcp.tool()
    def pcb_session_status() -> dict[str, Any]:
        """Report the warm-board ``pcb_eval`` session: which boards are
        cached, how many fresh loads have happened, and whether the worker
        daemon is alive.

        Use this before or between ``pcb_eval`` calls to understand cache
        state — e.g. to confirm a board is warm (so the next query is ms,
        not ~1 s), to see how close the daemon is to its load-recycle cap,
        or to debug why a query was unexpectedly slow (cold cache vs warm).
        Read-only: it never loads, mutates, or evicts a board.

        Args:
            (none) — reports on the single shared session daemon.

        Returns:
            ``{success: True, daemon, loads, cached:[{path, mtime_ns,
            filled, dirty}]}`` when the daemon is running;
            ``{success: True, daemon: "not running", cached: []}`` when it
            has not been started yet; or ``{success: False, error}`` if
            pcbnew is not importable in this interpreter.
        """
        if not _HAS_PCBNEW:
            return {"success": False, "error": "pcbnew not importable"}
        if not _DAEMON._alive():
            return {"success": True, "daemon": "not running", "cached": []}
        resp = _DAEMON.request({"op": "status"}, timeout=10.0)
        resp["success"] = bool(resp.get("ok"))
        return resp

    @mcp.tool()
    def pcb_session_reset(pcb_path: str = "") -> dict[str, Any]:
        """Drop boards from the warm ``pcb_eval`` cache so the next query
        reloads them fresh from disk.

        Use this after an out-of-band change the warm session can't see on
        its own — e.g. you edited the ``.kicad_pcb`` through a different
        process, a what-if mutation left the in-memory board dirty, or you
        simply want to free memory. The cache normally self-invalidates on
        mtime change, so you rarely need this; reach for it when you want to
        force a clean reload deterministically. Cheap and safe: it only
        evicts cache entries — the board reloads (~1 s) on the next
        ``pcb_eval`` call.

        The ``pcb_path`` is intentionally NOT existence-checked: resetting a
        board whose file was moved or deleted is a valid way to release its
        cached copy, so a missing path is a no-op success, not an error.

        Args:
            pcb_path: ``.kicad_pcb`` whose cached board to drop (WSL or
                Windows form). Empty (default) clears the entire cache.

        Returns:
            ``{success: True, cleared}`` naming what was dropped (the path
            or ``"all"``); ``{success: True, note}`` if the daemon was not
            running (nothing to reset); or ``{success: False, error}`` if
            pcbnew is not importable in this interpreter.
        """
        if not _HAS_PCBNEW:
            return {"success": False, "error": "pcbnew not importable"}
        if not _DAEMON._alive():
            return {"success": True, "note": "daemon not running; nothing to reset"}
        req = {"op": "reset"}
        if pcb_path:
            req["pcb_path"] = to_local_path(pcb_path)
        resp = _DAEMON.request(req, timeout=10.0)
        resp["success"] = bool(resp.get("ok"))
        return resp
