# SPDX-License-Identifier: GPL-3.0-or-later
"""
Geometry helpers: snap-to-grid, overlap resolution, boundary clamping.

Extracted from auto_place.py.

Callers:
  - auto_place.py          (_snap, _resolve_overlaps — Placement-Pipeline Phase 9)
"""

from .bbox import _get_symbol_height, _get_symbol_width
from .constants import HALF_GRID


def _snap(val: float, grid: float = HALF_GRID) -> float:
    return round(val / grid) * grid


def _resolve_overlaps(parts: list[dict]) -> bool:
    placed = [p for p in parts if "_place_x" in p]
    moved = False
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            ax, ay = a["_place_x"], a["_place_y"]
            bx, by = b["_place_x"], b["_place_y"]
            aw = _get_symbol_width(a) / 2 + 4
            ah = _get_symbol_height(a) / 2 + 4
            bw = _get_symbol_width(b) / 2 + 4
            bh = _get_symbol_height(b) / 2 + 4
            if abs(ax - bx) < aw + bw and abs(ay - by) < ah + bh:
                if abs(ax - bx) < abs(ay - by):
                    shift = (ah + bh) - abs(ay - by) + 5
                    b["_place_y"] += shift if by >= ay else -shift
                else:
                    shift = (aw + bw) - abs(ax - bx) + 5
                    b["_place_x"] += shift if bx >= ax else -shift
                moved = True
    return moved


def clamp_to_bounds(
    x: float, y: float, w: float, h: float,
    bounds: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Clamp position inside bounds (x_min, y_min, x_max, y_max)."""
    x_min, y_min, x_max, y_max = bounds
    x = max(x_min + w / 2, min(x, x_max - w / 2))
    y = max(y_min + h / 2, min(y, y_max - h / 2))
    return x, y
