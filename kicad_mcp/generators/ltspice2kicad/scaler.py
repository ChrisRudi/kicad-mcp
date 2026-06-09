# SPDX-License-Identifier: GPL-3.0-or-later
# scaler.py
"""LGU detection and isotropic scaling factor computation."""
from __future__ import annotations

from functools import reduce
from math import gcd

from kicad_mcp.generators.ltspice2kicad.models import ParsedSchematic, SymbolMeta

KICAD_GRID_FINE = 0.635   # mm (finest KiCad grid)
KICAD_GRID_STD = 1.27     # mm (standard KiCad grid)
BASE_MM = 2.54 / 16       # 0.15875 mm per normalized LTspice step


def detect_lgu(coordinates: list[int]) -> int:
    """Detect least grid unit (LGU) from all LTspice coordinates.

    Computes GCD over all coordinate differences.
    """
    deltas: set[int] = set()
    sorted_coords = sorted(set(coordinates))
    for i in range(len(sorted_coords) - 1):
        d = sorted_coords[i + 1] - sorted_coords[i]
        if d > 0:
            deltas.add(d)
    if not deltas:
        return 1
    return reduce(gcd, deltas)


def collect_all_coordinates(schematic: ParsedSchematic) -> list[int]:
    """Collect all x and y coordinates from a parsed schematic."""
    coords: list[int] = []
    for comp in schematic.components:
        coords.extend([comp.x, comp.y])
    for wire in schematic.wires:
        coords.extend([wire.x1, wire.y1, wire.x2, wire.y2])
    for label in schematic.labels:
        coords.extend([label.x, label.y])
    for junc in schematic.junctions:
        coords.extend([junc.x, junc.y])
    return coords


def compute_scale_factor(
    schematic: ParsedSchematic,
    symbol_ratios: list[float] | None = None,
) -> tuple[int, int]:
    """Compute global isotropic integer scale factor and LGU.

    Args:
        schematic: Parsed LTspice schematic.
        symbol_ratios: Optional list of raw per-symbol scale ratios.

    Returns:
        (scale_factor, lgu) tuple.
    """
    all_coords = collect_all_coordinates(schematic)
    lgu = detect_lgu(all_coords)
    if lgu < 1:
        lgu = 1

    # Determine s from symbol ratios or default pin-matching heuristic.
    # The key constraint: transformed LTspice pin distances must match
    # KiCad symbol pin distances, so wires connect to symbol pins.
    if symbol_ratios:
        raw = max(symbol_ratios)
    else:
        # Default: match LTspice standard pin spacing to KiCad
        # LTspice res: 80 units between pins, KiCad Device:R: 7.62mm
        # (80/lgu) * BASE_MM * s = 7.62 => s = 7.62 * lgu / (80 * BASE_MM)
        raw = 7.62 * lgu / (80 * BASE_MM)

    s = max(1, round(raw))

    return s, lgu
