# SPDX-License-Identifier: GPL-3.0-or-later
"""
Simple PCB autorouter — generates traces directly as S-expression segments.

Strategy (Manhattan routing):
1. Route power nets first (wider traces)
2. For each net, connect pads using L-shaped or Z-shaped routes
3. Use F.Cu for horizontal segments, B.Cu for vertical
4. Place vias at layer transitions
5. Avoid crossing existing traces

This is a simple router — not comparable to Freerouting — but produces
functional 2-layer boards for simple circuits.

Extracted from pcb_router.py (Strangler Fig refactoring).

Callers:
    - kicad_mcp.generators.pcb_router (legacy thin wrapper)
    - kicad_mcp.generators.pcb_generator (route_pcb)
"""

import logging
import re

from ..common.constants import (
    LAYER_B,
    LAYER_F,
    POWER_TRACE_W,
    SIGNAL_TRACE_W,
    VIA_DRILL,
    VIA_SIZE,
)
from ..common.routing import _segment_hits_obstacle, _segments_cross

logger = logging.getLogger(__name__)


def route_pcb(
    pad_positions: dict[str, list[tuple[float, float, str]]],
    net_info: dict[str, tuple[int, str]],
    nets: list[dict],
    fp_obstacles: list[tuple[float, float, float, float]] | None = None,
    board_rect: tuple[float, float, float, float] | None = None,
) -> str:
    """Route all nets and return S-expression segments + vias.

    Args:
        pad_positions: {net_name: [(x, y, pad_ref), ...]} — pad locations per net
        net_info: {net_name: (net_number, net_name)} — net numbering
        nets: original net list (for type info)
        fp_obstacles: list of (x1, y1, x2, y2) footprint bounding boxes to avoid
        board_rect: (x1, y1, x2, y2) board outline — traces must stay inside

    Returns:
        String of S-expression (segment ...) and (via ...) lines.
    """
    net_types = {n["name"]: n.get("type", "signal") for n in nets}
    lines = []
    used_segments: list[tuple[float, float, float, float, str]] = []  # track existing traces
    obstacles = fp_obstacles or []
    board = board_rect

    # Sort nets: power first, then by pad count (fewer pads = easier)
    net_names = sorted(
        pad_positions.keys(),
        key=lambda n: (0 if net_types.get(n) == "power" else 1, len(pad_positions[n]))
    )

    for net_name in net_names:
        pads = pad_positions[net_name]
        if len(pads) < 2:
            continue

        ni = net_info.get(net_name)
        if not ni:
            continue
        net_num, _ = ni

        is_power = net_types.get(net_name) == "power"
        trace_w = POWER_TRACE_W if is_power else SIGNAL_TRACE_W

        # Connect pads using minimum spanning tree (greedy nearest-neighbor)
        segments = _route_net_mst(pads, net_num, trace_w, used_segments, obstacles, board)
        lines.extend(segments)

        # Track used segments for collision avoidance
        for seg in segments:
            coords = _extract_segment_coords(seg)
            if coords:
                used_segments.append(coords)

    routed_count = len([n for n in net_names if len(pad_positions.get(n, [])) >= 2])
    logger.info("Routed %d nets, %d trace segments", routed_count, len(lines))
    return "\n".join(lines)


def _route_net_mst(
    pads: list[tuple[float, float, str]],
    net_num: int,
    trace_w: float,
    existing: list[tuple[float, float, float, float, str]],
    fp_obstacles: list[tuple[float, float, float, float]] | None = None,
    board_rect: tuple[float, float, float, float] | None = None,
) -> list[str]:
    """Route a single net using greedy nearest-neighbor MST.

    For each unconnected pad, find the nearest connected pad and
    route an L-shaped trace between them.
    """
    if len(pads) < 2:
        return []

    lines = []
    connected = [pads[0]]
    remaining = list(pads[1:])

    while remaining:
        # Find closest pair (connected, remaining)
        best_dist = float("inf")
        best_conn = None
        best_rem_idx = 0

        for _ci, (cx, cy, _) in enumerate(connected):
            for ri, (rx, ry, _) in enumerate(remaining):
                dist = abs(cx - rx) + abs(cy - ry)  # Manhattan distance
                if dist < best_dist:
                    best_dist = dist
                    best_conn = (cx, cy)
                    best_rem_idx = ri

        if best_conn is None:
            break

        target = remaining.pop(best_rem_idx)
        connected.append(target)

        # Route L-shaped trace from best_conn to target
        x1, y1 = best_conn
        x2, y2 = target[0], target[1]

        segs = _route_l_shape(x1, y1, x2, y2, net_num, trace_w, existing,
                              fp_obstacles, board_rect)
        lines.extend(segs)

    return lines


def _route_l_shape(
    x1: float, y1: float, x2: float, y2: float,
    net_num: int, trace_w: float,
    existing: list[tuple[float, float, float, float, str]],
    fp_obstacles: list[tuple[float, float, float, float]] | None = None,
    board_rect: tuple[float, float, float, float] | None = None,
) -> list[str]:
    """Route an L-shaped trace between two points.

    Tries two L-shapes (horizontal-first vs vertical-first) and picks
    the one with fewer crossings and obstacle hits.
    Uses F.Cu for horizontal, B.Cu for vertical.
    If same layer works (pure horizontal or vertical), no via needed.
    """
    lines = []
    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    obs = fp_obstacles or []

    if dx < 0.1 and dy < 0.1:
        return []  # same point

    # Pure horizontal
    if dy < 0.5:
        lines.append(_segment(x1, y1, x2, y2, LAYER_F, trace_w, net_num))
        return lines

    # Pure vertical
    if dx < 0.5:
        lines.append(_segment(x1, y1, x2, y2, LAYER_B, trace_w, net_num))
        return lines

    # L-shape option A: horizontal first (F.Cu), then vertical (B.Cu)
    mid_x, mid_y = x2, y1  # corner point
    segs_a = [
        _segment(x1, y1, mid_x, mid_y, LAYER_F, trace_w, net_num),
        _via(mid_x, mid_y, net_num),
        _segment(mid_x, mid_y, x2, y2, LAYER_B, trace_w, net_num),
    ]

    # L-shape option B: vertical first (B.Cu), then horizontal (F.Cu)
    mid_x2, mid_y2 = x1, y2
    segs_b = [
        _segment(x1, y1, mid_x2, mid_y2, LAYER_B, trace_w, net_num),
        _via(mid_x2, mid_y2, net_num),
        _segment(mid_x2, mid_y2, x2, y2, LAYER_F, trace_w, net_num),
    ]

    # Score: trace crossings + footprint obstacle hits (weighted heavier)
    cross_a = sum(1 for seg in existing if _segments_cross(x1, y1, mid_x, mid_y, *seg[:4]))
    cross_b = sum(1 for seg in existing if _segments_cross(x1, y1, mid_x2, mid_y2, *seg[:4]))

    obs_a = (_segment_hits_obstacle(x1, y1, mid_x, mid_y, obs) +
             _segment_hits_obstacle(mid_x, mid_y, x2, y2, obs))
    obs_b = (_segment_hits_obstacle(x1, y1, mid_x2, mid_y2, obs) +
             _segment_hits_obstacle(mid_x2, mid_y2, x2, y2, obs))

    score_a = cross_a + obs_a * 5  # obstacle hits penalized heavily
    score_b = cross_b + obs_b * 5

    return segs_a if score_a <= score_b else segs_b


def _segment(x1: float, y1: float, x2: float, y2: float,
             layer: str, width: float, net_num: int) -> str:
    return (f'  (segment (start {x1:.3f} {y1:.3f}) (end {x2:.3f} {y2:.3f}) '
            f'(width {width}) (layer "{layer}") (net {net_num}))')


def _via(x: float, y: float, net_num: int) -> str:
    return (f'  (via (at {x:.3f} {y:.3f}) (size {VIA_SIZE}) (drill {VIA_DRILL}) '
            f'(layers "{LAYER_F}" "{LAYER_B}") (net {net_num}))')


def _extract_segment_coords(seg_str: str) -> tuple[float, float, float, float, str] | None:
    """Extract (x1,y1,x2,y2,layer) from a segment S-expression string."""
    start = re.search(r'\(start ([\d.]+) ([\d.]+)\)', seg_str)
    end = re.search(r'\(end ([\d.]+) ([\d.]+)\)', seg_str)
    layer = re.search(r'\(layer "([^"]+)"\)', seg_str)
    if start and end and layer:
        return (float(start.group(1)), float(start.group(2)),
                float(end.group(1)), float(end.group(2)),
                layer.group(1))
    return None


