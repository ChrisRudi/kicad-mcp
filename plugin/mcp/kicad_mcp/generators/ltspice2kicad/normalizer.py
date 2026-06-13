# SPDX-License-Identifier: GPL-3.0-or-later
# normalizer.py
"""Coordinate normalization: LTspice units -> KiCad mm."""
from __future__ import annotations

from kicad_mcp.generators.ltspice2kicad.scaler import BASE_MM

KICAD_GRID_FINE = 0.635  # mm
PAGE_OFFSET = 25.4        # mm


def snap_to_grid(value: float, grid: float = KICAD_GRID_FINE) -> float:
    """Snap to KiCad grid. This is the ONLY grid-snap definition."""
    return round(round(value / grid) * grid, 4)


def transform(
    coord_lt: int,
    coord_min: int,
    lgu: int,
    s: int,
    page_offset: float = PAGE_OFFSET,
) -> float:
    """Transform a single LTspice coordinate to KiCad mm.

    Formula (Section 2.6):
        x_mm = (((x_lt - x_min) / LGU) * BASE_MM * s) + PAGE_OFFSET

    This is the ONLY coordinate transformation in the system.
    """
    if lgu < 1:
        lgu = 1
    raw = (((coord_lt - coord_min) / lgu) * BASE_MM * s) + page_offset
    return snap_to_grid(raw)


def compute_bounds(
    coords_x: list[int], coords_y: list[int],
) -> tuple[int, int]:
    """Compute bounding box minimum (x_min, y_min) from all coordinates."""
    if not coords_x or not coords_y:
        return 0, 0
    return min(coords_x), min(coords_y)


def rotate_pin(
    dx: int, dy: int, angle: int, mirror: bool = False,
) -> tuple[int, int]:
    """Rotate a pin offset (dx, dy) relative to symbol origin.

    Applies rotation first, then mirror (Y-axis).
    Angles: 0, 90, 180, 270 degrees.
    """
    if angle == 90:
        dx, dy = -dy, dx
    elif angle == 180:
        dx, dy = -dx, -dy
    elif angle == 270:
        dx, dy = dy, -dx

    if mirror:
        dx = -dx

    return dx, dy
