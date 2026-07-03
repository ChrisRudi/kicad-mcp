# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure ``.kicad_sch`` S-expression parsing helpers.

Extracted from ``tools/schematic_tools.py`` so tool modules *and*
``generators/review`` can share them without importing a private name across
the tool layer. No MCP/FastMCP dependency; the only I/O is reading the file.
"""
# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
from typing import Any

from kicad_mcp.utils.sexpr_parser import find_node, find_nodes, parse_sexpr


def parse_schematic(sch_path: str) -> list:
    """Parse a .kicad_sch file into S-expression tree."""
    with open(sch_path, encoding="utf-8") as f:
        return parse_sexpr(f.read())


def extract_components(
    tree: list,
    include_pins: bool = False,
    include_properties: bool = False,
) -> list[dict[str, Any]]:
    """Extract all symbol instances (components) from a schematic tree.

    Lean by default (agent token budget): the duplicative ``properties``
    block and the per-pin list are omitted unless explicitly requested via
    ``include_properties`` / ``include_pins``.
    """
    components = []
    symbols = find_nodes(tree, "symbol")

    for sym in symbols:
        # Skip power symbols and lib_symbols section
        lib_id_node = find_node(sym, "lib_id")
        if not lib_id_node or len(lib_id_node) < 2:
            continue
        lib_id = str(lib_id_node[1])

        # Get properties
        props = {}
        for prop_node in find_nodes(sym, "property"):
            if len(prop_node) >= 3:
                props[str(prop_node[1])] = str(prop_node[2])

        reference = props.get("Reference", "?")
        value = props.get("Value", "")
        footprint = props.get("Footprint", "")

        # Get position
        at_node = find_node(sym, "at")
        x = float(at_node[1]) if at_node and len(at_node) > 2 else 0.0
        y = float(at_node[2]) if at_node and len(at_node) > 2 else 0.0

        # Check DNP and in_bom flags — KiCad-10 stores them as
        # ``(dnp yes|no)`` / ``(in_bom yes|no)`` and *always emits the
        # node*, so existence is not the same as the flag being set.
        dnp_node = find_node(sym, "dnp")
        dnp = (
            dnp_node is not None
            and len(dnp_node) > 1
            and str(dnp_node[1]).lower() == "yes"
        )
        in_bom_node = find_node(sym, "in_bom")
        in_bom = True
        if in_bom_node and len(in_bom_node) > 1 and str(in_bom_node[1]) == "no":
            in_bom = False

        # Get unit number
        unit_node = find_node(sym, "unit")
        unit = int(unit_node[1]) if unit_node and len(unit_node) > 1 else 1

        rec: dict[str, Any] = {
            "reference": reference,
            "value": value,
            "library_id": lib_id,
            "footprint": footprint,
            "position": [x, y],
            "unit": unit,
            "dnp": dnp,
            "in_bom": in_bom,
        }
        # Opt-in extras (board-independent token bloat otherwise):
        #  - properties duplicates Reference/Value/Footprint (already above)
        #    and carries empty Datasheet/Description noise → return only the
        #    non-empty EXTRA props.
        if include_properties:
            rec["properties"] = {
                k: v
                for k, v in props.items()
                if k not in ("Reference", "Value", "Footprint") and v
            }
        if include_pins:
            # Two pin spellings occur: an INSTANCE pin ``(pin "1" (uuid …))``
            # carries the number as ``pin[1]``, while a DEFINITION pin
            # ``(pin passive_line (name "VIN") (number "3"))`` carries the
            # type as ``pin[1]`` and the number in a ``(number …)`` child.
            # Prefer the child node, fall back to ``pin[1]``.
            pins = []
            for pin in find_nodes(sym, "pin"):
                if len(pin) < 2:
                    continue
                num_node = find_node(pin, "number")
                number = (
                    str(num_node[1])
                    if num_node and len(num_node) > 1
                    else str(pin[1])
                )
                entry = {"number": number}
                name_node = find_node(pin, "name")
                if name_node and len(name_node) > 1:
                    nm = str(name_node[1])
                    if nm and nm != "~":
                        entry["name"] = nm
                pins.append(entry)
            rec["pins"] = pins
        components.append(rec)

    return components
