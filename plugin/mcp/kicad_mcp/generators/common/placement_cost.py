# SPDX-License-Identifier: GPL-3.0-or-later
"""
Shared placement cost function for schematic and PCB placement.

Computes the total Manhattan distance from a component's pins to all
connected already-placed pins.  Used by both schematic/defrag_place.py
and pcb/place.py to score candidate positions.

Callers:
  - schematic/defrag_place.py  (incremental_place_and_score)
  - pcb/place.py               (incremental PCB placement)
"""

import logging

logger = logging.getLogger(__name__)


def build_ref_to_nets(nets: list[dict]) -> dict[str, list[tuple[str, list[tuple[str, str]]]]]:
    """Pre-compute ref -> [(my_pin, [(other_ref, other_pin), ...])] index.

    For each ref, collects the nets it participates in and the other
    endpoints on those nets.  Avoids scanning all nets per cost call.
    """
    idx: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {}
    for net in nets:
        conns = net.get("connections", [])
        parsed = []
        for conn in conns:
            if ":" in conn:
                parsed.append(conn.split(":", 1))
        for cref, cpname in parsed:
            others = [(r, p) for r, p in parsed if r != cref]
            idx.setdefault(cref, []).append((cpname, others))
    return idx


def placement_cost(
    ref: str,
    x: float, y: float,
    placed_positions: dict[str, tuple[float, float]],
    ref_net_index: dict[str, list[tuple[str, list[tuple[str, str]]]]],
    power_weight: float = 1.0,
    nets: list[dict] | None = None,
) -> float:
    """Cost = total Manhattan distance to connected placed endpoints.

    This is a simplified version that works with pre-computed positions
    (no pin-level resolution).  Suitable for both schematic and PCB.

    Args:
        ref: component reference to evaluate
        x, y: candidate position
        placed_positions: {ref: (x, y)} of already-placed components
        ref_net_index: from build_ref_to_nets()
        power_weight: weight for power-net connections (default 1.0,
                      set to 0.3 for PCB to reduce power-net pull)
        nets: net list (needed to check power type if power_weight != 1.0)

    Returns: total weighted Manhattan distance (lower = better)
    """
    # Build power-net lookup: pin pair -> is_power
    power_pairs: set[tuple[str, str]] = set()
    if nets and power_weight != 1.0:
        for net in nets:
            if net.get("type") == "power":
                for conn in net.get("connections", []):
                    if ":" in conn:
                        power_pairs.add(tuple(conn.split(":", 1)))

    cost = 0.0
    for _my_pname, others in ref_net_index.get(ref, []):
        for other_ref, other_pname in others:
            if other_ref not in placed_positions:
                continue
            ox, oy = placed_positions[other_ref]
            dist = abs(x - ox) + abs(y - oy)
            # F10: reduce pull from power nets so components don't
            # cluster around GND/VCC but stay near signal neighbors
            weight = 1.0
            if power_pairs and (other_ref, other_pname) in power_pairs:
                weight = power_weight
            cost += dist * weight

    return cost
