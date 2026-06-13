# SPDX-License-Identifier: GPL-3.0-or-later
"""
KiCad Schematic (.kicad_sch) builder — symbol emission, lib symbols, instances.

Strangler-fig extraction from schematic_builder.py — all functions
copied verbatim (no behaviour changes).

Callers:
  - schematic_builder.py   (re-exports build_schematic for backward compat)
  - generation_tools.py    (build_schematic via schematic_builder)
  - esphome_tools.py       (build_schematic via schematic_builder)
  - tests                  (build_schematic via schematic_builder)

Routing rules (IEC 61082 / EDA best practices):
- Wires never pass through component bounding boxes
- No 4-way wire junctions (T-junctions only)
- Labels replace wires longer than WIRE_MAX_LENGTH
- Feedback paths use labels instead of wires across the sheet
- Text is always horizontal
- Functional block frames around component groups
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
import logging

from ..sexpr import (
    SExpr, uid, KICAD_SCH_VERSION, FONT_SIZE, PIN_SPACING,
    SYM_HALF_WIDTH, PIN_LENGTH,
)
from ..spice_models import get_spice_properties
from .place import place_schematic
from ..common.bbox import _get_symbol_bbox
from ..symbol_lib import resolve_lib_id
from ..symbol_cache import get_real_symbol
from ...utils.sexpr_parser import parse_sexpr, find_node

logger = logging.getLogger(__name__)

# Routing functions needed by builder functions
from .route import (  # noqa: E402
    _emit_wires_and_labels,
    _extract_pin_positions,
    _pins_from_real_symbol, _map_user_to_real_pins,
)


def build_schematic(
    parts: list[dict],
    nets: list[dict],
    project_name: str = "project",
    simulation: bool = False,
    intersheet_nets: list[dict] | None = None,
) -> str:
    """Build a .kicad_sch file from parts and nets.

    ``intersheet_nets`` (optional) — list of net dicts that cross sheet
    boundaries on the parent root-sheet (as produced by
    :func:`kicad_mcp.generators.schematic.multisheet.find_intersheet_nets`).
    Signal nets with a name in this set get a hierarchical_label on the
    sub-sheet so the matching pin on the root's sheet-symbol resolves.
    Pass ``None`` (default) on single-sheet projects.
    """
    # Auto-place components
    place_schematic(parts, nets)

    s = SExpr()

    # Header
    s.open("kicad_sch")
    s.prop("version", KICAD_SCH_VERSION)
    s.prop_quoted("generator", "kicad-mcp")
    s.prop_quoted("generator_version", "1.0")
    s.emit(f'(uuid "{uid(f"{project_name}_sch")}")')
    s.prop_quoted("paper", "A4")
    s.blank()

    # Lib symbols section
    _emit_lib_symbols(s, parts, nets, project_name)
    s.blank()

    # Symbol instances (placed by auto_place)
    _emit_symbol_instances(s, parts, project_name, simulation)
    s.blank()

    # PWR_FLAG symbols on power nets
    _emit_pwr_flags(s, parts, nets, project_name)
    s.blank()

    # Functional block frames (dashed rectangles around groups)
    _emit_block_frames(s, parts, project_name)
    s.blank()

    # Wires + labels — respecting routing rules. Sub-sheet calls pass
    # `intersheet_nets` so cross-sheet signals get hierarchical labels
    # that match the pins on the root sheet symbol.
    _hier_names: set[str] | None = (
        {n["name"] for n in intersheet_nets} if intersheet_nets else None
    )
    labeled_positions = _emit_wires_and_labels(
        s, parts, nets, project_name, intersheet_nets=_hier_names,
    )
    s.blank()

    # No-connect flags on unused pins
    _emit_no_connects(s, parts, nets, project_name, labeled_positions)
    s.blank()

    # Sheet + Symbol instances
    _emit_instances(s, parts, project_name)

    s.close()  # kicad_sch

    # Clean up placement metadata
    for part in parts:
        part.pop("_place_x", None)
        part.pop("_place_y", None)
        part.pop("_group", None)
        part.pop("_rotation", None)
        part.pop("_extra_units", None)

    return s.render()


# ── Lib symbols ──────────────────────────────────────────────────────────────

def _symbol_name(lib_id: str) -> str:
    if ":" in lib_id:
        return lib_id.split(":", 1)[1]
    return lib_id


def _fmt(v: float) -> str:
    return f"{round(v, 4):g}"


def _emit_lib_symbols(s: SExpr, parts: list[dict], nets: list[dict], project_name: str) -> None:
    s.open("lib_symbols")
    seen = set()
    for part in parts:
        lib_id = resolve_lib_id(part)
        if lib_id in seen:
            continue
        seen.add(lib_id)
        real_sym = get_real_symbol(lib_id)
        if real_sym:
            indented = _indent_block(real_sym, s._indent)
            s._lines.append(indented)
            logger.info(f"Embedded real KiCad symbol: {lib_id}")
        else:
            logger.warning(f"Symbol '{lib_id}' not found in KiCad libraries, using placeholder")
            _emit_placeholder_symbol(s, part, lib_id)

    pwr_flag = get_real_symbol("power:PWR_FLAG")
    if pwr_flag:
        indented = _indent_block(pwr_flag, s._indent)
        s._lines.append(indented)

    # Embed lib_symbols for real KiCad power symbols used by power nets
    from .route import get_power_symbol_info
    power_lib_ids_seen: set[str] = set()
    for net in nets:
        if net.get("type") != "power":
            continue
        pwr_info = get_power_symbol_info(net["name"])
        if pwr_info:
            lib_id = pwr_info[0]
            if lib_id not in power_lib_ids_seen:
                power_lib_ids_seen.add(lib_id)
                pwr_sym = get_real_symbol(lib_id)
                if pwr_sym:
                    indented = _indent_block(pwr_sym, s._indent)
                    s._lines.append(indented)
                    logger.info(f"Embedded power symbol: {lib_id}")

    s.close()


def _indent_block(text: str, indent_level: int) -> str:
    prefix = "  " * indent_level
    lines = text.split("\n")
    return "\n".join(prefix + line if line.strip() else "" for line in lines)


def _emit_placeholder_symbol(s: SExpr, part: dict, lib_id: str) -> None:
    pins = part.get("pins", [])
    n_pins = len(pins)
    half_h = max(n_pins * FONT_SIZE, FONT_SIZE * 2)
    ref_prefix = part["ref"][0] if part["ref"] else "U"
    sym_name = _symbol_name(lib_id)

    s.open("symbol", f'"{lib_id}"')
    s.kicad_property("Reference", ref_prefix, 0, round(half_h + PIN_SPACING, 4))
    s.kicad_property("Value", part.get("value", part["name"]), 0, round(-(half_h + PIN_SPACING), 4))
    s.kicad_property("Footprint", part.get("footprint", ""), 0, round(-(half_h + PIN_SPACING * 2), 4), hide=True)

    s.open("symbol", f'"{sym_name}_0_1"')
    s.emit(
        f"(rectangle (start -{_fmt(SYM_HALF_WIDTH)} {_fmt(half_h)}) (end {_fmt(SYM_HALF_WIDTH)} {_fmt(-half_h)})"
        f" (stroke (width 0.254) (type default)) (fill (type background)))"
    )
    for i, pin in enumerate(pins):
        y = round((n_pins - 1) * FONT_SIZE - i * PIN_SPACING, 4)
        pin_type = pin.get("type", "passive")
        s.pin(pin_type, pin["name"], pin["num"],
              round(-(SYM_HALF_WIDTH + PIN_LENGTH), 4), y, angle=0)
    s.close()
    s.close()


# ── Multi-unit detection ─────────────────────────────────────────────────────

def _detect_units(lib_id: str) -> dict[int, list[dict]]:
    """Detect units in a multi-unit KiCad symbol.

    Returns: {unit_number: [{'num': pin_num, 'x': x, 'y': y, 'type': type}, ...]}
    Empty dict if single-unit or not found.
    """
    raw = get_real_symbol(lib_id)
    if not raw:
        return {}

    tree = parse_sexpr(raw)
    units: dict[int, list[dict]] = {}

    def _walk(node: list) -> None:
        if not isinstance(node, list) or not node:
            return
        if node[0] == "symbol" and len(node) > 1 and isinstance(node[1], str):
            import re as _re
            m = _re.match(r'.*_(\d+)_(\d+)$', node[1])
            if m:
                unit_num = int(m.group(1))
                pins = []
                def _find_pins(n):
                    if not isinstance(n, list) or not n:
                        return
                    if n[0] == "pin":
                        at = find_node(n, "at")
                        num_node = find_node(n, "number")
                        if at and num_node and len(at) >= 3 and len(num_node) >= 2:
                            pin_type = n[1] if len(n) > 1 and isinstance(n[1], str) and n[1] != "(" else "passive"
                            pins.append({
                                "num": str(num_node[1]),
                                "x": float(at[1]),
                                "y": -float(at[2]),  # Negate Y: lib uses math Y, schematic uses screen Y
                                "type": pin_type,
                            })
                    for c in n:
                        if isinstance(c, list):
                            _find_pins(c)
                _find_pins(node)
                if pins:
                    units[unit_num] = pins
        for child in node:
            if isinstance(child, list):
                _walk(child)

    _walk(tree)
    return units


def _find_user_unit(part: dict, units: dict[int, list[dict]]) -> int:
    """Determine which unit the user's pins belong to."""
    user_pin_nums = {str(p["num"]) for p in part.get("pins", [])}
    user_pin_names = {p.get("name", "") for p in part.get("pins", [])}

    best_unit = 1
    best_overlap = 0
    for unit_num, unit_pins in units.items():
        unit_pin_nums = {p["num"] for p in unit_pins}
        overlap = len(user_pin_nums & unit_pin_nums) + len(user_pin_names & unit_pin_nums)
        if overlap > best_overlap:
            best_overlap = overlap
            best_unit = unit_num
    return best_unit


# ── Symbol instances ─────────────────────────────────────────────────────────

def _emit_symbol_instances(s: SExpr, parts: list[dict], project_name: str, simulation: bool = False) -> None:
    for part in parts:
        ref = part["ref"]
        lib_id = resolve_lib_id(part)
        x = round(part.get("_place_x", 50.8), 2)
        y = round(part.get("_place_y", 38.1), 2)
        sym_uid = uid(f"{project_name}_sym_{ref}")

        # Detect multi-unit symbols
        units = _detect_units(lib_id)
        user_unit = _find_user_unit(part, units) if units else 1

        rot = int(part.get("_rotation", 0))
        s.open("symbol", f'(lib_id "{lib_id}") (at {_fmt(x)} {_fmt(y)} {rot}) (unit {user_unit})')
        s.emit('(in_bom yes) (on_board yes)')
        s.emit(f'(uuid "{sym_uid}")')

        # Rule R12: Reference and Value always to the RIGHT of the component
        # Adjust for rotation: vertical parts need label offset in different direction
        sym_w, sym_h = _get_symbol_bbox(part)
        if rot == 90 or rot == 270:
            label_x = round(x + sym_h / 2 + 2.0, 2)
        else:
            label_x = round(x + sym_w / 2 + 2.0, 2)
        s.kicad_property("Reference", ref, label_x, round(y - FONT_SIZE, 2), angle=0)
        s.kicad_property("Value", part.get("value", part["name"]), label_x, round(y + FONT_SIZE, 2), angle=0)
        s.kicad_property("Footprint", part.get("footprint", ""), label_x, round(y + FONT_SIZE * 3, 2), hide=True)

        if simulation:
            sim_props = part.get("sim_properties") or get_spice_properties(part)
            for key, val in sim_props.items():
                s.kicad_property(key, val, label_x, round(y + FONT_SIZE * 5, 2), hide=True)

        real_sym = get_real_symbol(lib_id)
        if real_sym:
            real_pins = _pins_from_real_symbol(real_sym)
            emitted_pins = set()
            user_to_real = _map_user_to_real_pins(part, real_pins)
            for pin in part.get("pins", []):
                real_num = user_to_real.get(str(pin["num"]), str(pin["num"]))
                pin_uid = uid(f"{project_name}_{ref}_pin{real_num}")
                s.pin_instance(real_num, pin_uid)
                emitted_pins.add(real_num)
            # Emit remaining pins from the same unit AND unit 0 (common/shared pins)
            unit_pins = set()
            if units:
                unit_pins = {p["num"] for p in units.get(user_unit, [])}
                # Unit 0 is the "common" unit — its pins appear on every unit
                unit_pins |= {p["num"] for p in units.get(0, [])}
            else:
                unit_pins = set(real_pins.keys())
            for pnum in sorted(unit_pins, key=lambda x: int(x) if x.isdigit() else 0):
                if pnum not in emitted_pins and pnum in real_pins:
                    pin_uid = uid(f"{project_name}_{ref}_pin{pnum}")
                    s.pin_instance(pnum, pin_uid)
        else:
            for pin in part.get("pins", []):
                pin_uid = uid(f"{project_name}_{ref}_pin{pin['num']}")
                s.pin_instance(pin["num"], pin_uid)

        s.close()

        # Multi-unit: place additional units (e.g., power unit for dual op-amps)
        if len(units) > 1:
            _emit_additional_units(s, part, lib_id, units, user_unit,
                                   x, y, project_name, parts)


def _emit_additional_units(
    s: SExpr, part: dict, lib_id: str,
    units: dict[int, list[dict]], main_unit: int,
    main_x: float, main_y: float,
    project_name: str, all_parts: list[dict],
) -> None:
    """Place additional units of multi-unit symbols (e.g., power unit of dual op-amp).

    General rule: Every unit with pins that are connected to nets gets placed.
    Power units are placed near the main unit. Unused signal units are skipped.
    """
    ref = part["ref"]

    # Build set of connected pin numbers (from user spec)
    user_pin_names = {p.get("name", "") for p in part.get("pins", [])}
    user_pin_nums = {str(p["num"]) for p in part.get("pins", [])}

    for unit_num, unit_pins in sorted(units.items()):
        if unit_num == main_unit:
            continue

        # Unit 0 is the "common" unit in KiCad — its pins appear on ALL unit
        # placements automatically. Do NOT create a separate placement for it.
        if unit_num == 0:
            continue

        # Check if this unit has power pins → always place power units
        is_power_unit = all(p.get("type") == "power_in" for p in unit_pins)

        # Check if any pin in this unit is connected via user spec
        unit_pin_nums = {p["num"] for p in unit_pins}
        has_connected_pins = bool(unit_pin_nums & user_pin_nums) or bool(unit_pin_nums & user_pin_names)

        if not is_power_unit and not has_connected_pins:
            continue

        # Place this unit near the main unit
        # Power units: offset below; signal units: offset to the right
        # Offsets MUST be multiples of 1.27mm (KiCad grid) to keep pins on-grid
        if is_power_unit:
            ux = round(main_x, 2)
            uy = round(main_y + 15.24, 2)  # 12 * 1.27 = 15.24mm below main
        else:
            ux = round(main_x + 20.32, 2)  # 16 * 1.27 = 20.32mm to the right
            uy = round(main_y, 2)

        unit_uid = uid(f"{project_name}_sym_{ref}_unit{unit_num}")

        s.open("symbol", f'(lib_id "{lib_id}") (at {_fmt(ux)} {_fmt(uy)} 0) (unit {unit_num})')
        s.emit('(in_bom no) (on_board no)')  # Only main unit in BOM
        s.emit(f'(uuid "{unit_uid}")')

        eu_label_x = round(ux + 5.0, 2)
        s.kicad_property("Reference", ref, eu_label_x, round(uy - FONT_SIZE, 2), angle=0)
        s.kicad_property("Value", part.get("value", part["name"]), eu_label_x, round(uy + FONT_SIZE, 2), angle=0, hide=True)
        s.kicad_property("Footprint", part.get("footprint", ""), round(ux, 2), round(uy - PIN_SPACING * 3, 2), hide=True)

        # Pin instances for this unit
        for pin_info in unit_pins:
            pnum = pin_info["num"]
            pin_uid = uid(f"{project_name}_{ref}_unit{unit_num}_pin{pnum}")
            s.pin_instance(pnum, pin_uid)

        s.close()

        # Store extra unit position for wire routing
        part.setdefault("_extra_units", []).append({
            "unit": unit_num,
            "x": ux,
            "y": uy,
            "pins": unit_pins,
        })

        logger.info(f"Placed {ref} unit {unit_num} ({'power' if is_power_unit else 'signal'}) at ({ux}, {uy})")


# ── PWR_FLAG ─────────────────────────────────────────────────────────────────

def _emit_pwr_flags(s: SExpr, parts: list[dict], nets: list[dict], project_name: str) -> None:
    _pin_pos_cache: dict[str, dict[str, tuple[float, float]]] = {}
    pin_to_net: dict[str, str] = {}
    for net in nets:
        for conn in net.get("connections", []):
            pin_to_net[conn] = net["name"]

    flag_count = 0
    power_net_positions: dict[str, tuple[float, float]] = {}

    for part in parts:
        ref = part["ref"]
        lib_id = resolve_lib_id(part)
        sx = round(part.get("_place_x", 50.8), 2)
        sy = round(part.get("_place_y", 38.1), 2)

        if lib_id not in _pin_pos_cache:
            _pin_pos_cache[lib_id] = _extract_pin_positions(lib_id, part)
        pin_pos = _pin_pos_cache[lib_id]

        # Build extra-unit pin lookup
        extra_units = part.get("_extra_units", [])
        extra_pin_lookup: dict[str, tuple[float, float, float, float]] = {}
        for eu in extra_units:
            for ep in eu.get("pins", []):
                extra_pin_lookup[ep["num"]] = (eu["x"], eu["y"], ep["x"], ep["y"])

        real_sym = get_real_symbol(lib_id)
        real_pins_map = _pins_from_real_symbol(real_sym) if real_sym else {}
        u2r = _map_user_to_real_pins(part, real_pins_map) if real_pins_map else {}

        for pin in part.get("pins", []):
            conn_key = f"{ref}:{pin['name']}"
            net_name = pin_to_net.get(conn_key)
            if not net_name:
                continue
            net_obj = next((n for n in nets if n["name"] == net_name), None)
            if not net_obj or net_obj.get("type") != "power":
                continue
            if net_name in power_net_positions:
                continue
            pnum = str(pin["num"])
            real_num = u2r.get(pnum, pnum)

            # Check extra unit first
            if real_num in extra_pin_lookup:
                ux, uy, lx, ly = extra_pin_lookup[real_num]
                power_net_positions[net_name] = (round(ux + lx, 2), round(uy + ly, 2))
            else:
                lp = pin_pos.get(pnum)
                if lp:
                    power_net_positions[net_name] = (round(sx + lp[0], 2), round(sy + lp[1], 2))

    for net_name, (px, py) in power_net_positions.items():
        flag_uid = uid(f"{project_name}_pwrflag_{net_name}_{flag_count}")
        flag_count += 1
        fx = round(px - 5.08, 2)
        fy = py
        s.open("symbol", f'(lib_id "power:PWR_FLAG") (at {_fmt(fx)} {_fmt(fy)} 0) (unit 1)')
        s.emit("(in_bom no) (on_board no)")
        s.emit(f'(uuid "{flag_uid}")')
        s.kicad_property("Reference", f"#FLG0{flag_count}", fx, round(fy - 2.54, 2), hide=True)
        s.kicad_property("Value", "PWR_FLAG", fx, round(fy + 2.54, 2), hide=True)
        pin_uid = uid(f"{project_name}_pwrflag_pin_{net_name}_{flag_count}")
        s.pin_instance("1", pin_uid)
        s.close()
        wire_uid = uid(f"{project_name}_pwrflag_wire_{net_name}_{flag_count}")
        s.wire(fx, fy, px, py, wire_uid)

    if flag_count:
        logger.info("Placed %d PWR_FLAG symbols on power nets", flag_count)


# ── Functional block frames ──────────────────────────────────────────────────

def _emit_block_frames(s: SExpr, parts: list[dict], project_name: str) -> None:
    """Draw dashed rectangles around functional groups.

    Rule: Each group of components gets a labeled frame to improve
    visual separation and readability.
    """
    GROUP_LABELS = {
        "connector_in": "Input",
        "connector_out": "Output",
        "connector_pwr": "Power Supply",
        "power_reg": "Voltage Regulator",
        "main_ic": "Main IC",
        "transistor": "Active Stage",
        "passive": "Passives",
        "power_passive": "Power Network",
        "bypass_cap": None,  # skip — visually part of their IC
        "diode": None,       # skip — usually part of another group
        "pullup": None,
        "indicator": None,
        "other": None,
    }
    FRAME_PAD = 8.0  # mm padding around group

    groups: dict[str, list[dict]] = {}
    for part in parts:
        g = part.get("_group", "other")
        if g not in groups:
            groups[g] = []
        groups[g].append(part)

    frame_count = 0
    for group_name, group_parts in groups.items():
        label = GROUP_LABELS.get(group_name)
        if not label or len(group_parts) < 3:
            continue

        placed = [p for p in group_parts if "_place_x" in p]
        if len(placed) < 3:
            continue

        # Compute bounding box of group
        min_x = min(p["_place_x"] - _get_symbol_bbox(p)[0] / 2 for p in placed) - FRAME_PAD
        max_x = max(p["_place_x"] + _get_symbol_bbox(p)[0] / 2 for p in placed) + FRAME_PAD
        min_y = min(p["_place_y"] - _get_symbol_bbox(p)[1] / 2 for p in placed) - FRAME_PAD
        max_y = max(p["_place_y"] + _get_symbol_bbox(p)[1] / 2 for p in placed) + FRAME_PAD

        # Draw dashed rectangle
        corners = [
            (round(min_x, 2), round(min_y, 2)),
            (round(max_x, 2), round(min_y, 2)),
            (round(max_x, 2), round(max_y, 2)),
            (round(min_x, 2), round(max_y, 2)),
            (round(min_x, 2), round(min_y, 2)),  # close rectangle
        ]
        frame_uid = uid(f"{project_name}_frame_{group_name}_{frame_count}")
        s.polyline(corners, frame_uid)

        # Label at top-left
        text_uid = uid(f"{project_name}_frametext_{group_name}_{frame_count}")
        s.text_note(label, round(min_x + 2, 2), round(min_y + 2, 2), text_uid)

        frame_count += 1

    if frame_count:
        logger.info("Drew %d functional block frames", frame_count)


# ── No-connect flags ─────────────────────────────────────────────────────────

def _emit_no_connects(
    s: SExpr, parts: list[dict], nets: list[dict], project_name: str,
    labeled_positions: set[tuple[float, float]] | None = None,
) -> None:
    """Place no-connect flags on ALL unused pins of ALL components.

    General rule: Every pin that has no net must have a no-connect flag.
    This applies to all components (not just MCUs), preventing ERC errors.
    Only pins belonging to the placed unit are considered.
    """
    # Build set of connected pin names per ref: "REF:pin_name"
    connected_pin_names: set[str] = set()
    for net in nets:
        for conn in net.get("connections", []):
            connected_pin_names.add(conn)

    # Build set of user-defined pin names per ref
    ref_pin_names: dict[str, set[str]] = {}
    for part in parts:
        ref = part["ref"]
        ref_pin_names[ref] = {p.get("name", "") for p in part.get("pins", [])}

    _labeled = labeled_positions or set()
    nc_count = 0

    for part in parts:
        ref = part["ref"]
        lib_id = resolve_lib_id(part)
        sym_x = round(part.get("_place_x", 50.8), 2)
        sym_y = round(part.get("_place_y", 38.1), 2)

        # Get pin positions for the placed unit
        pin_positions = _extract_pin_positions(lib_id, part)

        # Determine which pins are connected
        connected_user_nums = set()
        for pin in part.get("pins", []):
            conn_key = f"{ref}:{pin['name']}"
            if conn_key in connected_pin_names:
                connected_user_nums.add(str(pin["num"]))

        # Map user pin nums to real pin nums
        real_sym = get_real_symbol(lib_id)
        real_pins = _pins_from_real_symbol(real_sym) if real_sym else {}
        user_to_real = _map_user_to_real_pins(part, real_pins)

        # Get set of real pin nums that are connected
        connected_real_nums = set()
        for u_num in connected_user_nums:
            connected_real_nums.add(user_to_real.get(u_num, u_num))

        # Detect multi-unit — only mark pins from the placed unit
        units = _detect_units(lib_id) if real_sym else {}
        if units:
            main_unit = _find_user_unit(part, units)
            placed_unit_pins = {p["num"] for p in units.get(main_unit, [])}
        else:
            placed_unit_pins = set(pin_positions.keys())

        nc_positions: set[tuple[float, float]] = set()

        for pin_num_key, (local_x, local_y) in pin_positions.items():
            # Only consider pins from the placed unit
            real_num = user_to_real.get(pin_num_key, pin_num_key)
            if placed_unit_pins and real_num not in placed_unit_pins:
                continue

            if real_num in connected_real_nums:
                continue

            abs_x = round(sym_x + local_x, 2)
            abs_y = round(sym_y + local_y, 2)
            pos = (abs_x, abs_y)

            if pos in _labeled:
                continue
            if pos in nc_positions:
                continue

            nc_positions.add(pos)
            nc_uid = uid(f"{project_name}_nc_{ref}_{real_num}_{nc_count}")
            nc_count += 1
            s.no_connect(abs_x, abs_y, nc_uid)

        # Also place no-connects on extra unit pins (e.g. LM358 Unit B)
        for eu in part.get("_extra_units", []):
            eu_x = eu["x"]
            eu_y = eu["y"]
            for ep in eu.get("pins", []):
                real_num = str(ep["num"])
                if real_num in connected_real_nums:
                    continue
                abs_x = round(eu_x + ep["x"], 2)
                abs_y = round(eu_y + ep["y"], 2)
                pos = (abs_x, abs_y)
                if pos in _labeled or pos in nc_positions:
                    continue
                nc_positions.add(pos)
                nc_uid = uid(f"{project_name}_nc_{ref}_eu_{real_num}_{nc_count}")
                nc_count += 1
                s.no_connect(abs_x, abs_y, nc_uid)

    if nc_count > 0:
        logger.info("Placed %d no-connect flags on unused pins", nc_count)


# ── Sheet/symbol instances ───────────────────────────────────────────────────

def _emit_instances(s: SExpr, parts: list[dict], project_name: str) -> None:
    uid(f"{project_name}_sch")
    s.open("sheet_instances")
    s.emit('(path "/" (page "1"))')
    s.close()
    s.blank()
    s.open("symbol_instances")
    for part in parts:
        ref = part["ref"]
        lib_id = resolve_lib_id(part)
        value = part.get("value", part["name"])
        footprint = part.get("footprint", "")

        # Detect multi-unit to emit correct unit number
        units = _detect_units(lib_id)
        main_unit = _find_user_unit(part, units) if units else 1

        sym_uuid = uid(f"{project_name}_sym_{ref}")
        s.emit(
            f'(path "/{sym_uuid}" (reference "{ref}") (unit {main_unit})'
            f' (value "{value}") (footprint "{footprint}"))'
        )

        # Emit additional unit instances
        for extra in part.get("_extra_units", []):
            unit_num = extra["unit"]
            unit_uid = uid(f"{project_name}_sym_{ref}_unit{unit_num}")
            s.emit(
                f'(path "/{unit_uid}" (reference "{ref}") (unit {unit_num})'
                f' (value "{value}") (footprint "{footprint}"))'
            )
    s.close()
