# SPDX-License-Identifier: GPL-3.0-or-later
"""
Connectivity / ratsnest tools for KiCad PCB files.

Closes the long-standing gap (see reference notes / TODO #1): the
headless ``kicad-cli pcb drc`` does NOT run the "unconnected items"
check, so net connectivity was invisible to MCP clients. The work uses
the ``pcbnew`` Python API directly (KiCad's own engine, no GUI) and —
crucially — fills zones first, otherwise every pad that connects only
through a copper pour (e.g. each GND pad on a GND plane) is falsely
reported as unconnected.

**Performance.** The pcbnew work runs in a long-lived *warm daemon*
(``connectivity_worker.py``, run by FILE PATH, not ``-m`` — importing the
package would cost ~3 s). The daemon keeps loaded boards in memory cached
by path+mtime, so:

  * **① Warm cache** — the first query on a dense, copper-poured board
    pays ``LoadBoard`` + fill once (~seconds–minutes on a big mainboard);
    every later query on the unchanged file is a cache hit and returns
    fast. (Previously each call spawned a cold process and re-filled — the
    240 s wall-clock on large boards.)
  * **② Scoped / optional fill** — ``overview`` accepts ``fill=False`` for
    a fast, pour-blind ratsnest pass; ``pad`` / ``whatif`` fill only the
    relevant net's zones, not the whole board.

The daemon recycles itself after a ``whatif`` (which mutates the in-memory
board) and after a load cap, sidestepping KiCad's SWIG degradation.

Tool: ``check_connectivity(pcb_path, mode=...)`` with three modes:
  - ``overview``        : global unconnected count + nets that split into
                          more than one cluster (i.e. not fully routed).
  - ``pad``             : the electrical cluster of one pad (REF.PAD).
  - ``whatif``          : remove the via/track nearest (x_mm, y_mm) in
                          memory, recompute, and report which pads lose
                          their connection — i.e. "is this element
                          load-bearing, or safe to delete?".
"""
import importlib.util
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools._warm_daemon import WarmDaemon
from kicad_mcp.tools.connectivity_worker import (
    MARK as _MARK,
    run as _run_in_process,  # cold in-process path, re-exported for unit tests
    validate as _validate,
)
from kicad_mcp.utils.path_env import to_local_path

_HAS_PCBNEW = importlib.util.find_spec("pcbnew") is not None
_WORKER = os.path.join(os.path.dirname(__file__), "connectivity_worker.py")
# First cold load + fill on a dense, fully-poured mainboard can be slow;
# warm cache hits return in ms. Keep a generous ceiling for the cold case.
_CONN_TIMEOUT_S = 240
_DAEMON = WarmDaemon(_WORKER, _MARK, max_loads=25)

__all__ = ["check_connectivity_impl", "register_connectivity_tools", "_run_in_process"]


def check_connectivity_impl(
    pcb_path: str,
    mode: str = "overview",
    ref_pad: str = "",
    x_mm: float | None = None,
    y_mm: float | None = None,
    fill: bool = True,
) -> dict[str, Any]:
    """Core implementation behind the ``check_connectivity`` MCP tool.

    Validates cheaply (no daemon round-trip on bad args / missing file),
    then forwards to the warm connectivity daemon.
    """
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if not _HAS_PCBNEW:
        return {"success": False, "error": "pcbnew not importable — run the MCP server under KiCad's bundled Python."}

    err = _validate(mode, ref_pad, x_mm, y_mm)
    if err:
        return err

    resp = _DAEMON.request(
        {"op": mode, "pcb_path": pcb_path, "ref_pad": ref_pad,
         "x_mm": x_mm, "y_mm": y_mm, "fill": bool(fill)},
        timeout=_CONN_TIMEOUT_S,
    )
    if not resp.get("ok"):
        out = {"success": False, "error": resp.get("error", "connectivity worker failed")}
        if "traceback" in resp:
            out["traceback"] = resp["traceback"]
        return out
    resp.pop("ok", None)
    resp.pop("id", None)
    return {"success": True, **resp}


def register_connectivity_tools(mcp: FastMCP) -> None:
    """Register connectivity / ratsnest tools with the MCP server."""

    @mcp.tool()
    def check_connectivity(
        pcb_path: str,
        mode: str = "overview",
        ref_pad: str = "",
        x_mm: float | None = None,
        y_mm: float | None = None,
        fill: bool = True,
    ) -> dict[str, Any]:
        """Net connectivity / ratsnest check via KiCad's own engine (headless).

        Fills the role ``kicad-cli pcb drc`` cannot: it reports
        **unconnected items** and lets you ask whether a specific via/track
        is *load-bearing*. Zones are filled before analysis, so pads that
        connect only through a copper pour are NOT falsely flagged. Runs
        against a **warm in-memory board** (cached by path+mtime): the first
        query on a big poured board pays the load+fill once, every later
        query on the unchanged file is fast.

        Use this when the user asks "is anything unconnected", "did my last
        edit orphan a net", or "is it safe to delete this via" — the third
        question is what plain DRC and text/regex scans cannot answer.

        Modes:
          - ``"overview"`` (default): global unconnected count plus every
            net that splits into more than one cluster (= not fully
            routed), with the pad-group sizes per net.
          - ``"pad"``: pass ``ref_pad`` like ``"D_TVS1.2"`` → the size of
            that pad's electrical cluster and the pads in it (fills only
            that net's zones).
          - ``"whatif"``: pass ``x_mm`` and ``y_mm`` → removes the nearest
            via/track in memory, recomputes, and lists pads that lose
            their connection. ``load_bearing=True`` means do NOT delete it
            (or route a replacement first). Optionally also pass
            ``ref_pad`` to report that pad's cluster size after removal.

        Args:
            pcb_path: Path to the ``.kicad_pcb`` file (WSL or Windows form).
            mode: ``"overview"`` | ``"pad"`` | ``"whatif"``. Default ``"overview"``.
            ref_pad: ``"REF.PAD"`` e.g. ``"D_TVS1.2"`` (required for ``pad``;
                optional extra report for ``whatif``).
            x_mm: X in mm (required for ``whatif``).
            y_mm: Y in mm (required for ``whatif``).
            fill: ``overview`` only — set ``False`` for a fast, pour-blind
                ratsnest pass on a huge board (pads connecting *only* through
                a copper pour will then show as unconnected). Default
                ``True`` (correct, fills all zones once and caches them).
                Ignored for ``pad`` / ``whatif`` (those always fill the
                relevant net).

        Returns:
            Mode-specific dict, always including ``success`` (plus
            ``cache_hit`` / ``zones_filled`` for triage). On failure:
            ``{"success": False, "error": "<text>"}``. This tool is
            read-only — ``whatif`` mutates only the in-memory board and
            never writes to disk.
        """
        pcb_path = to_local_path(pcb_path)
        return check_connectivity_impl(pcb_path, mode, ref_pad, x_mm, y_mm, fill)
