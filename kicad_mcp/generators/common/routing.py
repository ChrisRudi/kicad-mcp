# SPDX-License-Identifier: GPL-3.0-or-later
"""
Shared routing helpers: L-Bend, rasterization, obstacle checks.

Extracted from schematic_builder.py and pcb_router.py.

Callers:
  - schematic/route.py     (ROUTE_GRID, _l_path_clear, _try_lbend — A*-Router + L-Bend Fallback)
  - auto_place.py          (via schematic_builder lazy import: _build_mst_edges nutzt
                             ROUTE_GRID indirekt)
  - pcb_router.py          (_segment_hits_obstacle, _segments_cross — L-Shape Routing)
"""

from .constants import HALF_GRID

# Default routing grid (same as HALF_GRID / 50mil KiCad grid)
ROUTE_GRID = HALF_GRID


def _l_path_cells(
    a: tuple[int, int], corner: tuple[int, int], b: tuple[int, int],
) -> set[tuple[int, int]]:
    """Return all grid cells along an L-shaped path (a->corner->b)."""
    cells: set[tuple[int, int]] = set()
    ax, ay = a
    cx, cy = corner
    bx, by = b
    for x in range(min(ax, cx), max(ax, cx) + 1):
        for y in range(min(ay, cy), max(ay, cy) + 1):
            cells.add((x, y))
    for x in range(min(cx, bx), max(cx, bx) + 1):
        for y in range(min(cy, by), max(cy, by) + 1):
            cells.add((x, y))
    return cells


def _l_path_clear(
    a: tuple[int, int], corner: tuple[int, int], b: tuple[int, int],
    obstacles: set[tuple[int, int]],
) -> bool:
    """Check if an L-shaped path (a->corner->b) is obstacle-free."""
    return not _l_path_cells(a, corner, b) & obstacles


def _try_lbend(
    start: tuple[float, float], end: tuple[float, float],
    check_obstacles: set[tuple[int, int]],
    mark_obstacles: list[set[tuple[int, int]]],
    grid: float = ROUTE_GRID,
) -> list[tuple[float, float]] | None:
    """Try an L-bend route between start and end.

    Uses check_obstacles for clearance check (can be minimal/zero-clearance).
    On success, marks all path cells in each set in mark_obstacles.
    Returns list of waypoints or None.
    """
    sg = (round(start[0] / grid), round(start[1] / grid))
    eg = (round(end[0] / grid), round(end[1] / grid))
    corner1 = (sg[0], eg[1])
    corner2 = (eg[0], sg[1])

    chosen_corner = None
    if _l_path_clear(sg, corner1, eg, check_obstacles):
        chosen_corner = corner1
    elif _l_path_clear(sg, corner2, eg, check_obstacles):
        chosen_corner = corner2

    if chosen_corner is None:
        return None

    cx, cy = chosen_corner[0] * grid, chosen_corner[1] * grid
    l_path = [start, (round(cx, 2), round(cy, 2)), end]

    # Mark all cells along the L-path as obstacles
    cells = _l_path_cells(sg, chosen_corner, eg)
    for obs_set in mark_obstacles:
        obs_set |= cells

    return l_path


def _segment_hits_obstacle(
    x1: float, y1: float, x2: float, y2: float,
    obstacles: list[tuple[float, float, float, float]],
    margin: float = 0.2,
) -> int:
    """Count how many footprint obstacle boxes a segment passes through."""
    hits = 0
    for ox1, oy1, ox2, oy2 in obstacles:
        seg_x1, seg_x2 = min(x1, x2), max(x1, x2)
        seg_y1, seg_y2 = min(y1, y2), max(y1, y2)
        if (seg_x2 > ox1 + margin and seg_x1 < ox2 - margin and
                seg_y2 > oy1 + margin and seg_y1 < oy2 - margin):
            hits += 1
    return hits


def _segments_cross(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2) -> bool:
    """Check if two line segments intersect (simplified axis-aligned check)."""
    a_horiz = abs(ay1 - ay2) < 0.1
    b_horiz = abs(by1 - by2) < 0.1

    if a_horiz == b_horiz:
        return False  # parallel segments don't cross

    if a_horiz:
        # A is horizontal, B is vertical
        return (min(ax1, ax2) < min(bx1, bx2) < max(ax1, ax2) and
                min(by1, by2) < min(ay1, ay2) < max(by1, by2))
    else:
        # A is vertical, B is horizontal
        return (min(bx1, bx2) < min(ax1, ax2) < max(bx1, bx2) and
                min(ay1, ay2) < min(by1, by2) < max(ay1, ay2))
