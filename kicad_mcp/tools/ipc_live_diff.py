# SPDX-License-Identifier: GPL-3.0-or-later
# ipc_live_diff.py
# Pure diff + attribution + summary engine for the IPC live layer.
# Purpose: compare two KIID->record snapshots of the live PCB (footprints,
#          tracks, vias) and classify added/removed/changed, split into
#          agent-self-writes vs real user edits, and render a natural-language
#          hand-off summary.
# Inputs:  snapshot dicts {kiid: record}; record = make_record(...).
# Outputs: diff dict, attribution dict, summary string. ALL pure functions.
# Deps:    stdlib only -> unit-testable with dict fixtures, no kipy/KiCad.

from __future__ import annotations

import logging
from typing import Any, Iterable

log = logging.getLogger("kicad_mcp.ipc_live_diff")

_NM_PER_MM = 1_000_000


def make_record(item_type: str, sig: Iterable, x=None, y=None,
                layer: Any = None, net: str = "") -> dict:
    """Build one snapshot record.

    item_type: "footprint" | "track" | "via".
    sig:       tuple of the diff-relevant fields (equality => unchanged).
    x, y:      position in integer nanometres (for region summary); for a
               track use its start point.
    layer:     layer id/name (for grouping in the summary).
    net:       net name (for the summary).
    """
    return {"t": item_type, "sig": tuple(sig), "x": x, "y": y,
            "layer": layer, "net": net}


# --- signature builders (primitives in -> tuple out; testable) --------------


def fp_signature(x: int, y: int, orientation_deg: float, layer: Any) -> tuple:
    return (int(x), int(y), round(float(orientation_deg), 3), layer)


def track_signature(sx: int, sy: int, ex: int, ey: int, width: int,
                    layer: Any, net: str) -> tuple:
    return (int(sx), int(sy), int(ex), int(ey), int(width), layer, net or "")


def via_signature(x: int, y: int, diameter: int, drill: int, net: str,
                  via_type: Any = None) -> tuple:
    return (int(x), int(y), int(diameter), int(drill), net or "",
            str(via_type) if via_type is not None else "")


# --- diff -------------------------------------------------------------------


def diff_snapshots(old: dict, new: dict) -> dict:
    """Pure O(n) dict diff. old/new: {kiid: record}.

    Returns {added:[kiid], removed:[kiid], changed:[kiid]} (sorted lists).
    A kiid present in both but with a different ``sig`` counts as changed.
    """
    old_keys = set(old)
    new_keys = set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(
        k for k in (old_keys & new_keys) if old[k]["sig"] != new[k]["sig"])
    return {"added": added, "removed": removed, "changed": changed}


def attribute(diff_result: dict, new: dict, expected: dict | None) -> dict:
    """Split each diff bucket into agent (expected self-write) vs user.

    expected: {kiid: record} the agent just wrote (its intended new state);
    for an agent-deleted item store a record whose ``sig`` is ``None``.
    A change/add whose NEW record sig equals expected[kiid].sig is an agent
    self-write; everything else is a real user edit. ``expected`` empty/None
    -> every change attributed to the user (the always-available fallback).
    """
    expected = expected or {}
    out = {"agent": {"added": [], "removed": [], "changed": []},
           "user": {"added": [], "removed": [], "changed": []}}
    for bucket in ("added", "changed"):
        for k in diff_result[bucket]:
            exp = expected.get(k)
            if exp is not None and k in new and new[k]["sig"] == exp["sig"]:
                out["agent"][bucket].append(k)
            else:
                out["user"][bucket].append(k)
    for k in diff_result["removed"]:
        exp = expected.get(k)
        if exp is not None and exp.get("sig") is None:
            out["agent"]["removed"].append(k)
        else:
            out["user"]["removed"].append(k)
    return out


# --- natural-language summary ----------------------------------------------


def _quadrant(x, y, cx, cy) -> str:
    if x is None or y is None:
        return "unknown region"
    horiz = "right" if x >= cx else "left"
    vert = "lower" if y >= cy else "upper"  # KiCad Y grows downward
    return f"{vert}-{horiz} quadrant"


def _layer_label(layer) -> str:
    return str(layer) if layer not in (None, "") else "unknown layer"


def _verb(item_type: str, kind: str) -> str:
    if item_type == "footprint":
        return {"added": "placed", "removed": "deleted", "changed": "moved"}[kind]
    if item_type == "track":
        return {"added": "routed", "removed": "deleted", "changed": "re-routed"}[kind]
    if item_type == "via":
        return {"added": "added", "removed": "removed", "changed": "moved"}[kind]
    return kind


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def summarize_user(user_diff: dict, snap_old: dict, snap_new: dict,
                   center_x: int, center_y: int) -> str:
    """Render the user-attributed diff as a plain-language hand-off.

    Groups by (change kind, item type, layer, board quadrant) and counts.
    snap_old supplies records for removed items, snap_new for added/changed.
    Returns a single sentence; "No user changes ..." when empty.
    """
    groups: dict[tuple, int] = {}
    for kind in ("added", "removed", "changed"):
        for k in user_diff.get(kind, []):
            rec = snap_old.get(k) if kind == "removed" else snap_new.get(k)
            if rec is None:
                rec = snap_old.get(k) or snap_new.get(k) or {}
            key = (rec.get("t", "item"), kind, _layer_label(rec.get("layer")),
                   _quadrant(rec.get("x"), rec.get("y"), center_x, center_y))
            groups[key] = groups.get(key, 0) + 1

    if not groups:
        return "No user changes since the last agent step."

    phrases = []
    # stable, readable order: footprints, tracks, vias; changed, added, removed
    type_order = {"footprint": 0, "track": 1, "via": 2}
    kind_order = {"changed": 0, "added": 1, "removed": 2}
    for (itype, kind, layer, region), n in sorted(
            groups.items(),
            key=lambda kv: (type_order.get(kv[0][0], 9),
                            kind_order.get(kv[0][1], 9), kv[0][2], kv[0][3])):
        noun = itype if itype != "item" else "item"
        phrases.append(
            f"{_verb(itype, kind)} {_plural(n, noun)} on {layer} "
            f"in the {region}")
    return "User " + "; ".join(phrases) + "."
