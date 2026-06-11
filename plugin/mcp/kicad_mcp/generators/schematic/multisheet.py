# SPDX-License-Identifier: GPL-3.0-or-later
"""
6.1: Multi-sheet support for large schematics.

When the part count exceeds MULTISHEET_THRESHOLD, the circuit is
partitioned by functional group. Each group becomes a sub-sheet with
hierarchical pins for inter-sheet connections.

Caller: builder.py (wraps build_schematic for large circuits)
"""

from collections import defaultdict
import logging
import os

from ..common.constants import MARGIN
from ..sexpr import KICAD_SCH_VERSION, SExpr, uid

logger = logging.getLogger(__name__)

MULTISHEET_THRESHOLD = int(os.getenv("KICAD_MULTISHEET_THRESHOLD", "40"))

# Group priority for sheet ordering
_GROUP_ORDER = {
    "power_reg": 0,
    "connector_pwr": 0,
    "main_ic": 1,
    "connector_in": 2,
    "connector_out": 3,
    "passive": 4,
    "power_passive": 4,
    "indicator": 5,
    "transistor": 5,
}

_GROUP_DISPLAY_NAME = {
    "power_reg": "Power Supply",
    "connector_pwr": "Power Supply",
    "main_ic": "Main Circuit",
    "connector_in": "Input",
    "connector_out": "Output",
    "passive": "Passives",
    "power_passive": "Power Network",
    "indicator": "Indicators",
    "transistor": "Active Stage",
}


def should_use_multisheet(parts: list[dict]) -> bool:
    """Check if the circuit is large enough to warrant multi-sheet."""
    return len(parts) >= MULTISHEET_THRESHOLD


def partition_by_group(
    parts: list[dict], nets: list[dict],
) -> dict[str, tuple[list[dict], list[dict]]]:
    """Partition parts and nets into functional groups.

    Returns: {group_name: (group_parts, group_nets)}
    Each net that spans multiple groups gets hierarchical pins.
    """
    # Classify parts into groups
    groups: dict[str, list[dict]] = defaultdict(list)
    ref_to_group: dict[str, str] = {}

    for part in parts:
        group = part.get("_group", "passive")
        # Merge small groups
        if group in ("connector_pwr", "power_passive"):
            group = "power_reg"
        elif group in ("indicator",):
            group = "passive"
        groups[group].append(part)
        ref_to_group[part["ref"]] = group

    # Assign nets to groups (a net belongs to the group of its first connection)
    group_nets: dict[str, list[dict]] = defaultdict(list)
    for net in nets:
        conns = net.get("connections", [])
        conn_groups = set()
        for conn in conns:
            ref = conn.split(":")[0] if ":" in conn else ""
            if ref in ref_to_group:
                conn_groups.add(ref_to_group[ref])

        if len(conn_groups) == 1:
            group_nets[conn_groups.pop()].append(net)
        elif conn_groups:
            # Net spans multiple groups — assign to the "most important" group
            primary = min(conn_groups, key=lambda g: _GROUP_ORDER.get(g, 99))
            group_nets[primary].append(net)

    return {g: (groups[g], group_nets.get(g, []))
            for g in groups}


def find_intersheet_nets(
    parts: list[dict], nets: list[dict],
) -> list[dict]:
    """Find nets that connect parts across different functional groups.

    These nets need hierarchical labels on each sub-sheet.
    """
    ref_to_group: dict[str, str] = {}
    for part in parts:
        group = part.get("_group", "passive")
        if group in ("connector_pwr", "power_passive"):
            group = "power_reg"
        elif group in ("indicator",):
            group = "passive"
        ref_to_group[part["ref"]] = group

    intersheet = []
    for net in nets:
        if net.get("type") == "power":
            continue  # Power nets use global labels, not hierarchical
        conns = net.get("connections", [])
        groups_in_net = set()
        for conn in conns:
            ref = conn.split(":")[0] if ":" in conn else ""
            if ref in ref_to_group:
                groups_in_net.add(ref_to_group[ref])
        if len(groups_in_net) > 1:
            intersheet.append(net)

    return intersheet


def build_root_sheet(
    group_names: list[str],
    intersheet_nets: list[dict],
    project_name: str,
    parts: list[dict] | None = None,
) -> str:
    """Build the root sheet containing sub-sheet references.

    Each functional group becomes a sheet symbol on the root page.
    Global power nets are connected via global labels.
    Inter-sheet signals use hierarchical labels.
    """
    # Build ref→group mapping for filtering pins per sheet
    ref_to_group: dict[str, str] = {}
    if parts:
        for p in parts:
            group = p.get("_group", "passive")
            if group in ("connector_pwr", "power_passive"):
                group = "power_reg"
            elif group in ("indicator",):
                group = "passive"
            ref_to_group[p["ref"]] = group

    s = SExpr()
    s.open("kicad_sch")
    s.prop("version", KICAD_SCH_VERSION)
    s.prop_quoted("generator", "kicad-mcp")
    s.prop_quoted("generator_version", "1.0")
    s.emit(f'(uuid "{uid(f"{project_name}_root")}")')
    s.prop_quoted("paper", "A4")
    s.blank()

    # Lib symbols section (empty for root — sub-sheets have the real symbols)
    s.open("lib_symbols")
    s.close()
    s.blank()

    # Place sheet symbols in a grid
    sheet_w = 40.0
    sheet_h = 25.0
    cols = 3
    start_x = MARGIN + 10
    start_y = MARGIN + 10

    sorted_groups = sorted(group_names,
                           key=lambda g: _GROUP_ORDER.get(g, 99))

    for i, group in enumerate(sorted_groups):
        col = i % cols
        row = i // cols
        sx = start_x + col * (sheet_w + 20)
        sy = start_y + row * (sheet_h + 20)

        display_name = _GROUP_DISPLAY_NAME.get(group, group.replace("_", " ").title())
        filename = f"{project_name}_{group}.kicad_sch"

        # Find hierarchical pins for this sheet
        # Collect inter-sheet pins relevant to this group
        pins = []
        pin_y_offset = 3.0
        for net in intersheet_nets:
            conns = net.get("connections", [])
            # Check if any connection in this net belongs to this group
            net_touches_group = False
            for conn in conns:
                ref = conn.split(":")[0] if ":" in conn else ""
                if ref in ref_to_group and ref_to_group[ref] == group:
                    net_touches_group = True
                    break
            if not net_touches_group:
                continue
            pin_uid = uid(f"{project_name}_shpin_{group}_{net['name']}")
            pins.append((net["name"], "bidirectional", 0, pin_y_offset, pin_uid))
            pin_y_offset += 2.54
            if pin_y_offset > sheet_h - 2:
                break

        # Adjust sheet height for pins
        actual_h = max(sheet_h, pin_y_offset + 3.0)

        sheet_uid = uid(f"{project_name}_sheet_{group}")
        s.sheet(display_name, filename, sx, sy, sheet_w, actual_h, sheet_uid, pins)
        s.blank()

    # Sheet instances
    s.open("sheet_instances")
    s.emit('(path "/" (page "1"))')
    for i, group in enumerate(sorted_groups):
        sheet_uid = uid(f"{project_name}_sheet_{group}")
        s.emit(f'(path "/{sheet_uid}" (page "{i + 2}"))')
    s.close()

    s.close()  # kicad_sch
    return s.render()
