# SPDX-License-Identifier: GPL-3.0-or-later
"""
Force-Directed Refinement for schematic and PCB placement.

Extracted from auto_place.py and pcb_builder.py.

Callers:
  - pcb_builder.py         (_fd_pcb_refine — PCB Placement nach _compute_pcb_placement)
"""

import math

from .bbox import _fp_size


def _fd_pcb_refine(
    result: dict[str, tuple[float, float, int]],
    connectivity: dict,
    ref_to_part: dict,
    parts: list[dict],
    x_min: float, y_min: float, x_max: float, y_max: float,
    occupied: list,
) -> None:
    """Force-directed refinement to minimize total wire length (PCB).

    Gently moves components toward their connected neighbors while
    respecting board boundaries and avoiding overlaps.
    Fixed components: connectors (at board edges).
    """
    from .classify import _classify_component

    fixed = set()
    for p in parts:
        g = p.get("_pcb_group", _classify_component(p))
        if g.startswith("connector"):
            fixed.add(p["ref"])

    movable = [ref for ref in result if ref not in fixed]

    MIN_GAP = 2.0

    for iteration in range(60):
        temp = max(0.2, 3.0 * (1.0 - iteration / 60))

        for ref in movable:
            if ref not in result:
                continue
            x, y, rot = result[ref]
            fx, fy = 0.0, 0.0
            w1, h1 = _fp_size(ref_to_part.get(ref, {}))

            # Hard repulsion — MUST NOT overlap (physics!)
            for other_ref in result:
                if other_ref == ref:
                    continue
                ox, oy, orot = result[other_ref]
                dx, dy = x - ox, y - oy
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 0.5:
                    dist = 0.5
                    dx, dy = 1.0, 0.5

                w2, h2 = _fp_size(ref_to_part.get(other_ref, {}))
                if orot in (90, 270):
                    w2, h2 = h2, w2
                # Axis-aligned overlap check (not circular)
                min_dx = (w1 + w2) / 2 + MIN_GAP
                min_dy = (h1 + h2) / 2 + MIN_GAP

                overlap_x = min_dx - abs(dx) if abs(dx) < min_dx else 0
                overlap_y = min_dy - abs(dy) if abs(dy) < min_dy else 0

                if overlap_x > 0 and overlap_y > 0:
                    # Actual overlap! Strong push
                    push = max(overlap_x, overlap_y) + 2.0
                    fx += push * (1 if dx >= 0 else -1) * 15.0
                    fy += push * (1 if dy >= 0 else -1) * 15.0
                elif dist < (w1 + w2 + h1 + h2) / 2:
                    # Close but not overlapping — gentle spread
                    force = 10.0 / max(dist, 1)
                    fx += force * dx / dist
                    fy += force * dy / dist

            # Gentle attraction to connected components (weaker than repulsion)
            for nb, _ in connectivity.get(ref, []):
                if nb not in result:
                    continue
                nx, ny, _ = result[nb]
                dx, dy = nx - x, ny - y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 2:
                    continue
                # Only attract if farther than needed — don't pull into overlap
                w2, h2 = _fp_size(ref_to_part.get(nb, {}))
                min_dist = (max(w1, h1) + max(w2, h2)) / 2 + MIN_GAP
                if dist > min_dist * 1.5:
                    force = 0.05 * (dist - min_dist)
                    fx += force * dx / dist
                    fy += force * dy / dist

            disp = math.sqrt(fx * fx + fy * fy)
            if disp > 0.1:
                scale = min(disp, temp) / disp
                new_x = x + fx * scale
                new_y = y + fy * scale
                new_x = max(x_min + 5, min(x_max - 5, new_x))
                new_y = max(y_min + 5, min(y_max - 5, new_y))
                result[ref] = (round(new_x, 2), round(new_y, 2), rot)
