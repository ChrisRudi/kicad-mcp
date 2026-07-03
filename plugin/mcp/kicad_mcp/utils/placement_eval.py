# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure, headless placement evaluation — the scratchpad behind "Entwirren".

Given hypothetical footprint poses plus the net→pad topology, *score* a candidate
layout WITHOUT touching KiCad: signal-net ratsnest crossings, footprint overlaps
and wirelength. This is the non-mutating notepad the agent reasons against
(propose → score → refine → … → apply once), so the board is touched only when a
final layout is chosen.

Design notes
------------
* **Signal nets only.** GND/VCC connect to almost everything and become copper
  pours, not routed airwires — counting them would dominate the metric and
  collapse every layout onto one point. High-fanout / power-named nets are
  excluded (see :func:`is_power_net`).
* **Ratsnest = per-net MST.** KiCad draws the airwires as a minimum spanning tree
  per net; we do the same (Prim's) so "crossings" matches what the user sees.
* **Crossings = proper segment intersections** between airwires of *different*
  pads. Airwires meeting at a shared pad (adjacent MST edges) do not count.
* **Rotation/flip** go through :func:`pcb_local_to_world` — never hand-rolled
  (the KiCad-CW / B.Cu-mirror footgun lives there).

Pure math + stdlib; imports only the (pure) geometry helper. Fully unit-tested
without KiCad.
"""

from __future__ import annotations

import math
import re

from kicad_mcp.utils.pcb_geometry import pcb_local_to_world

# Net-name patterns that mark a power/ground net (case-insensitive, whole-ish).
_POWER_RE = re.compile(
    r"^/?(gnd\w*|agnd|dgnd|pgnd|vss|vee|vcc\w*|vdd\w*|vbus|vin|vout|vbat|"
    r"vsys|\+?\d+v\d*|\+?\d+vd?|3v3|5v|1v8|1v2|12v|pwr|power|earth)$",
    re.IGNORECASE,
)


def is_power_net(name: str, pad_count: int = 0, footprint_count: int = 0) -> bool:
    """True if ``name`` looks like a power/ground net (excluded from scoring).

    Two signals: a name match (``GND``, ``+3V3``, ``VCC_IO`` …), or very high
    fan-out — a net touching more than half of all footprints is a rail whatever
    it is called. ``footprint_count`` enables the fan-out test (skipped if 0)."""
    if _POWER_RE.match((name or "").strip()):
        return True
    if footprint_count and pad_count > max(4, footprint_count // 2):
        return True
    return False


# --------------------------------------------------------------------------- #
# Geometry primitives
# --------------------------------------------------------------------------- #

def _orient(ax, ay, bx, by, cx, cy) -> float:
    """Signed area sign of triangle (a, b, c): >0 ccw, <0 cw, 0 collinear."""
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def segments_cross(p1, p2, p3, p4) -> bool:
    """True if segment ``p1p2`` properly crosses ``p3p4`` (they intersect at a
    point interior to both). Touching only at an endpoint does **not** count —
    airwires that share a pad are not a crossing. Collinear overlap is ignored
    (airwires effectively never lie exactly collinear)."""
    d1 = _orient(p3[0], p3[1], p4[0], p4[1], p1[0], p1[1])
    d2 = _orient(p3[0], p3[1], p4[0], p4[1], p2[0], p2[1])
    d3 = _orient(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1])
    d4 = _orient(p1[0], p1[1], p2[0], p2[1], p4[0], p4[1])
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def mst_edges(points: list) -> list:
    """Prim's minimum spanning tree over ``points`` ([(x, y), …]); returns the
    edges as index pairs ``[(i, j), …]``. Empty for < 2 points. This is one
    net's ratsnest topology (shortest set of airwires connecting all its pads)."""
    n = len(points)
    if n < 2:
        return []
    in_tree = [False] * n
    best = [math.inf] * n     # best edge cost to reach node
    parent = [-1] * n
    best[0] = 0.0
    edges = []
    for _ in range(n):
        u = -1
        for v in range(n):
            if not in_tree[v] and (u == -1 or best[v] < best[u]):
                u = v
        in_tree[u] = True
        if parent[u] != -1:
            edges.append((parent[u], u))
        ux, uy = points[u]
        for v in range(n):
            if not in_tree[v]:
                dx, dy = points[v][0] - ux, points[v][1] - uy
                d = dx * dx + dy * dy
                if d < best[v]:
                    best[v] = d
                    parent[v] = u
    return edges


def _seg_len(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def count_crossings(airwires: list) -> int:
    """Number of proper crossings among ``airwires`` (each ``(pid_a, pid_b, pa,
    pb)`` where ``pid_*`` is a hashable pad id and ``pa/pb`` are ``(x, y)``).
    Pairs sharing a pad id are skipped — meeting at a pad is not a crossing.
    O(n²): fine for the airwire counts a scorer sees; note it if it ever isn't."""
    n = len(airwires)
    total = 0
    for i in range(n):
        ai0, ai1, pa1, pa2 = airwires[i]
        for j in range(i + 1, n):
            bj0, bj1, pb1, pb2 = airwires[j]
            if ai0 in (bj0, bj1) or ai1 in (bj0, bj1):
                continue  # share a pad → not a crossing
            if segments_cross(pa1, pa2, pb1, pb2):
                total += 1
    return total


def _fp_world_aabb(fp: dict) -> tuple:
    """World-space axis-aligned bounding box of a footprint from its pose + size
    ``bbox=[w, h]`` (courtyard). Rotation near 90/270° swaps w/h."""
    x, y = float(fp["x"]), float(fp["y"])
    w, h = float(fp["bbox"][0]), float(fp["bbox"][1])
    rot = float(fp.get("rot", 0.0)) % 180.0
    if 45.0 < rot < 135.0:
        w, h = h, w
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def _boxes_overlap(a: tuple, b: tuple) -> bool:
    """True if two AABBs overlap with positive area (touching edges is fine)."""
    return (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


# --------------------------------------------------------------------------- #
# Scorer
# --------------------------------------------------------------------------- #

def pad_world_positions(footprints: list) -> dict:
    """Map ``(ref, pad_name) -> (x, y)`` in world mm for every pad, applying each
    footprint's pose (rotation is KiCad-CW / flip via ``pcb_local_to_world``)."""
    out = {}
    for fp in footprints:
        anchor = (float(fp["x"]), float(fp["y"]))
        rot = float(fp.get("rot", 0.0))
        flipped = bool(fp.get("flipped", False))
        for pad in fp.get("pads", []):
            wx, wy = pcb_local_to_world(
                anchor, rot, float(pad["lx"]), float(pad["ly"]), flipped)
            out[(fp["ref"], pad["name"])] = (wx, wy)
    return out


def evaluate_layout(footprints: list, nets: dict,
                    power_nets=None) -> dict:
    """Score a *hypothetical* layout without touching KiCad.

    Args:
        footprints: list of ``{ref, x, y, rot?, flipped?, bbox:[w,h],
            pads:[{name, lx, ly}]}`` (positions in mm, pad offsets footprint-local).
        nets: ``{net_name: [[ref, pad], …]}`` — pad membership per net.
        power_nets: net names to exclude; ``None`` → auto-detect
            (:func:`is_power_net`, name + high fan-out).

    Returns:
        ``{signal_crossings, overlaps, wirelength_mm, airwires, signal_nets,
        excluded_power_nets}`` — the numbers the agent reasons on.
    """
    pads = pad_world_positions(footprints)
    fp_count = len(footprints)

    excluded = set(power_nets) if power_nets is not None else set()
    airwires = []
    signal_nets = 0
    for name, members in nets.items():
        pad_ids = [tuple(m) for m in members]
        if power_nets is None and is_power_net(name, len(pad_ids), fp_count):
            excluded.add(name)
        if name in excluded:
            continue
        pts = [(pads[pid], pid) for pid in pad_ids if pid in pads]
        if len(pts) < 2:
            continue
        signal_nets += 1
        coords = [p[0] for p in pts]
        for i, j in mst_edges(coords):
            airwires.append((pts[i][1], pts[j][1], coords[i], coords[j]))

    crossings = count_crossings(airwires)
    wirelength = sum(_seg_len(a[2], a[3]) for a in airwires)

    boxes = [(_fp_world_aabb(fp), fp["ref"]) for fp in footprints if "bbox" in fp]
    overlaps = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _boxes_overlap(boxes[i][0], boxes[j][0]):
                overlaps += 1

    return {
        "signal_crossings": crossings,
        "overlaps": overlaps,
        "wirelength_mm": round(wirelength, 3),
        "airwires": len(airwires),
        "signal_nets": signal_nets,
        "excluded_power_nets": sorted(excluded),
    }
