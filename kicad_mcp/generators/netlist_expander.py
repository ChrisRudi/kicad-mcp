# SPDX-License-Identifier: GPL-3.0-or-later
"""
Netlist expander — converts compact netlists into full parts+nets for build_schematic.

The missing bridge between:
  - Compact netlist input (just refs + connections)
  - symbol_lib (resolves lib_id from name/prefix)
  - symbol_cache (extracts real pin data from KiCad libraries)
  - template_matcher (provides placement coordinates)
  - build_schematic (needs full parts+nets with pins)

Usage:
    parts, nets = expand_netlist(compact_parts, compact_nets)
    content = build_schematic(parts, nets, project_name)
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
import logging

from .symbol_lib import resolve_lib_id
from .symbol_cache import get_real_symbol
from .schematic.route import _pins_from_real_symbol
from .template_matcher import match_templates

logger = logging.getLogger(__name__)


def _extract_pins_for_lib(lib_id: str) -> list[dict]:
    """Extract pin definitions from a KiCad library symbol.

    Returns list of {"num": str, "name": str, "type": str, "x": float, "y": float}
    """
    raw = get_real_symbol(lib_id)
    if not raw:
        logger.warning(f"No real symbol found for {lib_id}")
        return []

    # Get pin positions
    pin_positions = _pins_from_real_symbol(raw)

    # Also extract pin names and types from the raw S-expression
    from ..utils.sexpr_parser import parse_sexpr, find_node

    tree = parse_sexpr(raw)
    pins = []

    def _walk(node: list) -> None:
        if not isinstance(node, list) or not node:
            return
        if node[0] == "pin" and len(node) > 1:
            # pin type is the second element (e.g., "passive", "input")
            pin_type = node[1] if isinstance(node[1], str) else "passive"
            find_node(node, "at")
            num_node = find_node(node, "number")
            name_node = find_node(node, "name")

            if num_node and len(num_node) >= 2:
                num = str(num_node[1])
                name = str(name_node[1]) if name_node and len(name_node) >= 2 else num
                pos = pin_positions.get(num, (0, 0))
                pins.append({
                    "num": num,
                    "name": name if name else num,
                    "type": pin_type,
                    "x": pos[0],
                    "y": pos[1],
                })
        for child in node:
            if isinstance(child, list):
                _walk(child)

    _walk(tree)
    return pins


def _ref_prefix(ref: str) -> str:
    """Extract letter prefix from reference designator."""
    return "".join(c for c in ref if c.isalpha())


# Default footprints by prefix
_DEFAULT_FOOTPRINTS = {
    "R": "Resistor_SMD:R_0603_1608Metric",
    "C": "Capacitor_SMD:C_0603_1608Metric",
    "L": "Inductor_SMD:L_0603_1608Metric",
    "D": "Diode_SMD:D_SOD-123",
    "Q": "Package_TO_SOT_SMD:SOT-23",
    "U": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
}


def expand_netlist(
    compact_parts: list[dict],
    compact_nets: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Expand compact netlist into full parts+nets for build_schematic.

    Compact part format (minimal):
        {"ref": "R1", "value": "1k"}
        {"ref": "Q1", "name": "NPN", "value": "BC547"}

    The expander:
    1. Resolves lib_id via symbol_lib
    2. Extracts real pins from KiCad library (symbol_cache)
    3. Adds default footprints
    4. Runs template matching for placement hints

    Compact net format:
        {"name": "VCC", "connections": ["R1:1", "R2:1"], "type": "power"}
        {"name": "N1", "connections": ["R1:2", "Q1:B"]}

    Connections use "REF:PIN_NAME" format (same as generate_schematic).

    Returns:
        (expanded_parts, expanded_nets) ready for build_schematic
    """
    expanded_parts = []
    lib_cache: dict[str, list[dict]] = {}  # lib_id -> pins

    for cp in compact_parts:
        ref = cp["ref"]
        prefix = _ref_prefix(ref)

        # Build a lookup-friendly part dict for resolve_lib_id
        lookup = {
            "ref": ref,
            "name": cp.get("name", prefix),
            "value": cp.get("value", ""),
            "lib_id": cp.get("lib", ""),
        }

        # Resolve the KiCad library symbol
        lib_id = cp.get("lib") or resolve_lib_id(lookup)

        # Get pins from the real KiCad symbol
        if lib_id not in lib_cache:
            lib_cache[lib_id] = _extract_pins_for_lib(lib_id)
        real_pins = lib_cache[lib_id]

        # Use user-provided pins if specified, otherwise use library pins
        if "pins" in cp and cp["pins"]:
            pins = cp["pins"]
        elif real_pins:
            pins = real_pins
            logger.info(f"{ref}: auto-resolved {len(pins)} pins from {lib_id}")
        else:
            logger.warning(f"{ref}: no pins found for {lib_id}, using empty")
            pins = []

        # Build expanded part
        part = {
            "ref": ref,
            "name": cp.get("name", lookup["name"]),
            "lib": lib_id,
            "lib_id": lib_id,
            "value": cp.get("value", cp.get("name", prefix)),
            "footprint": cp.get("footprint", _DEFAULT_FOOTPRINTS.get(prefix, "")),
            "pins": pins,
        }

        # Pass through placement hints if provided
        for key in ("x", "y", "rotation", "mirror"):
            if key in cp:
                part[key] = cp[key]

        expanded_parts.append(part)

    # Run template matching for placement
    matches = match_templates(expanded_parts, compact_nets)
    if matches:
        best = matches[0]
        logger.info(
            f"Template match: {best.template_id} "
            f"(confidence {best.confidence:.0%})"
        )
        # Apply template placement to parts that don't have explicit positions
        for ref, (rx, ry, rot) in best.placement.items():
            for part in expanded_parts:
                if part["ref"] == ref and "x" not in part:
                    part["x"] = rx
                    part["y"] = ry
                    part["rotation"] = rot
                    logger.info(f"  {ref} -> ({rx}, {ry}) rot={rot}")

    # Nets pass through mostly unchanged, just ensure format
    expanded_nets = []
    for cn in compact_nets:
        net = {
            "name": cn["name"],
            "connections": cn["connections"],
        }
        if "type" in cn:
            net["type"] = cn["type"]
        expanded_nets.append(net)

    return expanded_parts, expanded_nets
