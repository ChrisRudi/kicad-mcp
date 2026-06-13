# SPDX-License-Identifier: GPL-3.0-or-later
# aligner.py
"""Pin-Wire alignment: snap wire endpoints to actual KiCad pin coordinates."""
from __future__ import annotations

import math

from kicad_mcp.generators.ltspice2kicad.models import (
    TransformedComponent,
    TransformedWire,
)
from kicad_mcp.generators.ltspice2kicad.normalizer import snap_to_grid

KICAD_GRID_STD = 1.27  # mm


def calc_max_snap(components: list[TransformedComponent], grid: float = KICAD_GRID_STD) -> float:
    """Calculate maximum snap distance based on smallest pin spacing.

    Limited to half the smallest pin distance to prevent mis-snapping
    to the wrong pin.
    """
    all_pin_dists: list[float] = []
    for comp in components:
        pins = comp.pins_abs
        for i, (x1, y1, _n1) in enumerate(pins):
            for x2, y2, _n2 in pins[i + 1:]:
                d = abs(x1 - x2) + abs(y1 - y2)
                if d > 0.01:
                    all_pin_dists.append(d)
    if all_pin_dists:
        return max(grid, min(all_pin_dists) / 2)
    return grid


def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """Manhattan distance."""
    return abs(x1 - x2) + abs(y1 - y2)


def _find_nearest_pin(
    wx: float, wy: float,
    pin_positions: dict[tuple[float, float], tuple[str, str]],
) -> tuple[float, float] | None:
    """Find nearest pin position to a wire endpoint."""
    best: tuple[float, float] | None = None
    best_dist = math.inf
    for (px, py) in pin_positions:
        d = _distance(wx, wy, px, py)
        if d < best_dist:
            best_dist = d
            best = (px, py)
    return best


def align_wires_to_pins(
    wires: list[TransformedWire],
    components: list[TransformedComponent],
    max_snap: float | None = None,
) -> tuple[list[TransformedWire], list[str]]:
    """Align wire endpoints to nearest pin coordinates.

    After this step, every wire endpoint MUST either:
    - lie exactly on a pin coordinate, OR
    - lie exactly on another wire endpoint, OR
    - lie on grid

    Args:
        wires: Transformed wires (mm coordinates).
        components: Transformed components with absolute pin positions.
        max_snap: Maximum snap distance in mm. Auto-computed if None.

    Returns:
        (aligned_wires, warnings) tuple.
    """
    if max_snap is None:
        max_snap = calc_max_snap(components)

    # 1. Collect all pin positions
    pin_positions: dict[tuple[float, float], tuple[str, str]] = {}
    for comp in components:
        for px, py, pnum in comp.pins_abs:
            pin_positions[(px, py)] = (comp.id, pnum)

    warnings: list[str] = []
    aligned: list[TransformedWire] = []

    for wire in wires:
        x1, y1 = wire.x1_mm, wire.y1_mm
        x2, y2 = wire.x2_mm, wire.y2_mm

        # Snap endpoint 1
        nearest1 = _find_nearest_pin(x1, y1, pin_positions)
        if nearest1 and _distance(x1, y1, nearest1[0], nearest1[1]) <= max_snap:
            x1, y1 = nearest1
        else:
            x1 = snap_to_grid(x1)
            y1 = snap_to_grid(y1)

        # Snap endpoint 2
        nearest2 = _find_nearest_pin(x2, y2, pin_positions)
        if nearest2 and _distance(x2, y2, nearest2[0], nearest2[1]) <= max_snap:
            x2, y2 = nearest2
        else:
            x2 = snap_to_grid(x2)
            y2 = snap_to_grid(y2)

        # Skip zero-length wires
        if abs(x1 - x2) < 0.001 and abs(y1 - y2) < 0.001:
            continue

        aligned.append(TransformedWire(x1_mm=x1, y1_mm=y1, x2_mm=x2, y2_mm=y2))

    # 3. Check for unconnected wire endpoints
    all_pin_set = set(pin_positions.keys())
    all_wire_ends: set[tuple[float, float]] = set()
    for w in aligned:
        all_wire_ends.add((w.x1_mm, w.y1_mm))
        all_wire_ends.add((w.x2_mm, w.y2_mm))

    for w in aligned:
        for ep in ((w.x1_mm, w.y1_mm), (w.x2_mm, w.y2_mm)):
            if ep not in all_pin_set and ep not in all_wire_ends - {ep}:
                # This endpoint doesn't touch a pin or another wire
                # Check if any other wire shares this exact endpoint
                count = sum(
                    1 for w2 in aligned
                    if (w2.x1_mm, w2.y1_mm) == ep or (w2.x2_mm, w2.y2_mm) == ep
                )
                if count <= 1:
                    warnings.append(
                        f"WARNING: Wire endpoint ({ep[0]:.2f}, {ep[1]:.2f}) "
                        f"not connected to pin or other wire"
                    )

    return aligned, warnings
