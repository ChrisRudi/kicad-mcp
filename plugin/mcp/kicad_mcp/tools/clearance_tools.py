# SPDX-License-Identifier: GPL-3.0-or-later
"""Clearance engine — the shared copper-short check for board mutations.

This is the tool layer over ``clearance_worker`` (the warm pcbnew daemon).
Two surfaces:

* ``check_clearance`` — the standalone MCP tool. Board-wide by default
  ("does any net short another?"), or targeted at specific items.
* ``attach_clearance`` / ``check_clearance_impl`` — the *wiring* every
  board-mutating tool uses to fold a clearance effect-echo into its own
  result, so the agent never has to make a separate verify call after an
  edit (matching the project's anti-toolcall-explosion rule: check once per
  tranche, return the effect, don't re-read).

``attach_clearance`` is deliberately total: it never raises and never flips
the mutation's ``success``. When ``pcbnew`` is absent (CI / non-KiCad
Python) it records ``{checked: False, reason: ...}`` and the mutation
result is otherwise untouched — the engine is advisory, not a gate.

The spec builders (``via_spec`` / ``seg_spec`` / ``arc_specs``) are pure and
import-cheap so callers can construct the "what I just added" item list from
data already in scope and unit-test it without pcbnew.
"""
import importlib.util
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools._warm_daemon import WarmDaemon
from kicad_mcp.tools.clearance_worker import (
    MARK as _MARK,
    run as _run_in_process,  # cold in-process path, re-exported for unit tests
)
from kicad_mcp.utils.path_env import to_local_path

_HAS_PCBNEW = importlib.util.find_spec("pcbnew") is not None
_WORKER = os.path.join(os.path.dirname(__file__), "clearance_worker.py")
# First cold load + fill on a dense, fully-poured board can be slow; warm
# cache hits return fast. Keep a generous ceiling for the cold case.
_WORKER_TIMEOUT_S = 240
_DAEMON = WarmDaemon(_WORKER, _MARK, max_loads=25)

DEFAULT_CLEARANCE_MM = 0.2

__all__ = [
    "check_clearance_impl", "attach_clearance", "register_clearance_tools",
    "via_spec", "seg_spec", "arc_specs", "_run_in_process",
]


# ---------------------------------------------------------------------------
# Pure item-spec builders (no pcbnew) — turn "what I just added" into the
# worker's item-spec dicts. World coordinates in mm.
# ---------------------------------------------------------------------------


def via_spec(x_mm: float, y_mm: float, net_name: str,
             layer_pair, diameter_mm: float) -> dict[str, Any]:
    """Spec for a via subject: a copper circle of ``diameter_mm`` at
    (x_mm, y_mm) spanning ``layer_pair`` on net ``net_name``."""
    pair = list(layer_pair) if layer_pair else ["F.Cu", "B.Cu"]
    return {"kind": "via", "x_mm": float(x_mm), "y_mm": float(y_mm),
            "net": net_name or "", "layers": pair,
            "diameter_mm": float(diameter_mm)}


def seg_spec(x1_mm: float, y1_mm: float, x2_mm: float, y2_mm: float,
             net_name: str, layer: str, width_mm: float) -> dict[str, Any]:
    """Spec for a straight track subject on a single ``layer``."""
    return {"kind": "seg", "x1_mm": float(x1_mm), "y1_mm": float(y1_mm),
            "x2_mm": float(x2_mm), "y2_mm": float(y2_mm),
            "net": net_name or "", "layer": layer, "width_mm": float(width_mm)}


def arc_specs(start, mid, end, net_name: str, layer: str,
              width_mm: float) -> list[dict[str, Any]]:
    """Specs for an arc subject, approximated by its two chords
    (start→mid, mid→end). A coarse but cheap clearance approximation — good
    enough to catch an obvious short; the filler + full DRC remain the
    authority on curved geometry."""
    return [
        seg_spec(start[0], start[1], mid[0], mid[1], net_name, layer, width_mm),
        seg_spec(mid[0], mid[1], end[0], end[1], net_name, layer, width_mm),
    ]


# ---------------------------------------------------------------------------
# Engine calls
# ---------------------------------------------------------------------------


def check_clearance_impl(pcb_path: str, items=None,
                         clearance_mm: float = DEFAULT_CLEARANCE_MM) -> dict[str, Any]:
    """Core behind the ``check_clearance`` tool and the mutation wiring.

    Validates cheaply, then forwards to the warm clearance daemon. With
    ``items`` it runs the targeted check (only those subjects vs other-net
    copper); without, the board-wide different-net hard-copper scan.
    """
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if not _HAS_PCBNEW:
        return {"success": False, "checked": False,
                "reason": "pcbnew not importable — run the MCP server under "
                          "KiCad's bundled Python.",
                "error": "pcbnew not importable"}
    resp = _DAEMON.request(
        {"op": "check", "pcb_path": pcb_path, "items": items,
         "clearance_mm": clearance_mm},
        timeout=_WORKER_TIMEOUT_S,
    )
    if not resp.get("ok"):
        out = {"success": False,
               "error": resp.get("error", "clearance worker failed")}
        if "traceback" in resp:
            out["traceback"] = resp["traceback"]
        return out
    resp.pop("ok", None)
    resp.pop("id", None)
    return {"success": True, **resp}


def attach_clearance(result: dict[str, Any], pcb_path: str, items=None,
                     clearance_mm: float = DEFAULT_CLEARANCE_MM,
                     enabled: bool = True) -> dict[str, Any]:
    """Fold a post-mutation clearance check into ``result`` under the
    ``clearance`` key and return ``result``.

    Total by contract: never raises, never changes ``result["success"]``.
    ``enabled=False`` (or ``pcbnew`` absent) records why the check was
    skipped instead of running it. ``items`` empty → board-wide scan;
    ``items`` given → targeted check of just those subjects.
    """
    if not enabled:
        result["clearance"] = {"checked": False, "reason": "disabled"}
        return result
    try:
        chk = check_clearance_impl(pcb_path, items, clearance_mm)
    except Exception as exc:  # pragma: no cover - defensive; must never gate
        result["clearance"] = {"checked": False,
                               "reason": f"check error: {exc}"}
        return result
    if not chk.get("success"):
        result["clearance"] = {
            "checked": False,
            "reason": chk.get("reason") or chk.get("error") or "unavailable",
        }
    else:
        result["clearance"] = {
            "checked": True,
            "ok": chk.get("ok", True),
            "violation_count": chk.get("violation_count", 0),
            "violations": chk.get("violations", []),
            "mode": chk.get("mode"),
            "clearance_mm": chk.get("clearance_mm"),
        }
    return result


def register_clearance_tools(mcp: FastMCP) -> None:
    """Register the clearance-engine tool with the MCP server."""

    @mcp.tool()
    def check_clearance(
        pcb_path: str,
        items: str = "",
        clearance_mm: float = DEFAULT_CLEARANCE_MM,
    ) -> dict[str, Any]:
        """Copper-short / clearance check via KiCad's own geometry (headless).

        The shared clearance engine. It fills zones first (so a track over a
        pour is not falsely flagged) and then collides copper against
        other-net copper using ``SHAPE.Collide`` — KiCad's real geometry, not
        a text/regex guess. Runs against a **warm in-memory board** (cached by
        path+mtime): the first query on a big poured board pays the load+fill
        once, every later query on the unchanged file is fast.

        Use this when you want to confirm an edit did not create a short, or
        to audit a whole board for net-to-net clearance violations. The
        board-mutating tools (``add_via_to_pcb``, ``add_track_to_pcb``,
        ``add_vias_to_pcb``, ``add_arc_to_pcb``, ``add_zone_pour_to_pcb``,
        ``via_retype``, ``via_resize``, ``pcb_batch``) already fold this check
        into their own result under a ``clearance`` key — call this directly
        for an after-the-fact audit, a different clearance value, or tools
        that don't wire it in yet. This is *clearance*, not connectivity: for
        "is anything unconnected / load-bearing" use ``check_connectivity``;
        for the full rule set use ``run_drc_check``.

        Args:
            pcb_path: Path to the ``.kicad_pcb`` file (WSL or Windows form).
            items: Optional JSON array of item specs to target the check at
                just those subjects (the form the mutation tools build
                internally) — e.g.
                ``[{"kind":"via","x_mm":10,"y_mm":5,"net":"GND",
                "layers":["F.Cu","B.Cu"],"diameter_mm":0.6}]``. Leave empty
                (default) for a board-wide different-net scan of all hard
                copper (tracks / vias / pads).
            clearance_mm: Minimum copper-to-copper clearance in mm to enforce
                (default 0.2). Two different-net items closer than this are
                reported as a violation.

        Returns:
            ``{success, ok, violation_count, violations: [...], mode
            ("board"|"targeted"), zones_filled, cache_hit, clearance_mm}``.
            ``ok=True`` means no violation found. On failure / no pcbnew:
            ``{success: False, error}``. Read-only — never writes the board.
        """
        pcb_path = to_local_path(pcb_path)
        parsed = None
        if items:
            try:
                parsed = json.loads(items)
            except Exception as exc:
                return {"success": False,
                        "error": f"items is not valid JSON: {exc}"}
        return check_clearance_impl(pcb_path, parsed, clearance_mm)
