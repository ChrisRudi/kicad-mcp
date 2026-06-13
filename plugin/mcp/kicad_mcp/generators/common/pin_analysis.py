# SPDX-License-Identifier: GPL-3.0-or-later
"""
Pin position analysis for KiCad symbols.

Determines which side of a component (left/right/top/bottom) a pin sits on.
Used by schematic placement AND PCB placement for connectivity-aware positioning.

Callers:
  - schematic/defrag_place.py  (pin-side-aware child placement)
  - pcb/place.py               (footprint pin-side placement)
"""

import re


def get_pin_sides(part: dict, resolve_lib_id_fn, get_real_symbol_fn) -> dict[str, tuple[float, float]]:
    """Get relative pin positions for a component.

    Returns {pin_number: (rel_x, rel_y)} from real KiCad symbol.
    Falls back to simple left/right split.
    """
    lib_id = resolve_lib_id_fn(part)
    raw = get_real_symbol_fn(lib_id)
    if raw:
        pins = {}
        for m in re.finditer(
            r'\(pin\s+\w+\s+\w+\s+\(at\s+([-\d.]+)\s+([-\d.]+)', raw,
        ):
            x, y = float(m.group(1)), float(m.group(2))
            rest = raw[m.end():]
            nm = re.search(r'\(number\s+"([^"]+)"', rest[:200])
            if nm:
                pins[nm.group(1)] = (x, -y)  # negate Y for screen coords
        if pins:
            return pins

    # Fallback: even pins left, odd pins right
    result = {}
    for i, pin in enumerate(part.get("pins", [])):
        name = pin.get("name", str(pin.get("num", i)))
        result[name] = (-10.0 if i % 2 == 0 else 10.0, 0.0)
    return result


def find_pin_side(
    parent_ref: str, child_ref: str, parent: dict,
    connections: dict, nets: list[dict],
    pin_pos: dict[str, tuple[float, float]],
) -> str:
    """Determine which side of parent (left/right/top/bottom) a child belongs to.

    Looks up which parent pin connects to the child via the net graph,
    then checks the pin's relative position in the symbol.
    """
    for net_name, other_ref, _ in connections.get(parent_ref, []):
        if other_ref != child_ref:
            continue
        for net in nets:
            if net["name"] != net_name:
                continue
            for conn in net.get("connections", []):
                if not conn.startswith(parent_ref + ":"):
                    continue
                pin_name = conn.split(":", 1)[1]
                pos = pin_pos.get(pin_name)
                if pos is None:
                    # Try matching pin name to pin number
                    for pp in parent.get("pins", []):
                        if pp.get("name") == pin_name:
                            pos = pin_pos.get(str(pp.get("num", "")))
                            break
                if pos:
                    rx, ry = pos
                    if abs(rx) > abs(ry):
                        return "right" if rx > 0 else "left"
                    return "bottom" if ry > 0 else "top"
        break
    return "right"
