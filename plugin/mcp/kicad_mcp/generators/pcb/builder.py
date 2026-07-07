# SPDX-License-Identifier: GPL-3.0-or-later
"""
KiCad PCB (.kicad_pcb) generator — core builder logic.

Extracted from pcb_builder.py (Strangler Fig refactoring).

Generates a complete PCB from parts, nets, and board configuration.

Callers
-------
- kicad_mcp/generators/pcb_builder.py  (thin re-export wrapper)
- kicad_mcp/tools/generation_tools.py  (via pcb_builder.build_pcb)
- kicad_mcp/tools/esphome_tools.py     (via pcb_builder.build_pcb)
- tests/test_generators.py             (via pcb_builder.build_pcb)
"""

import logging

from kicad_mcp.utils.pcb_geometry import pcb_local_to_world

from ..common.bbox import read_footprint_pad_positions as \
    _read_footprint_pad_positions
from ..common.constants import EURO_DIVIDER_SIZES, JLCPCB_RULES
from ..footprint_lib import build_footprint_with_nets, resolve_footprint
from ..sexpr import KICAD_PCB_VERSION, SExpr, uid
from .place import _compute_pcb_placement

logger = logging.getLogger(__name__)

# Standard layer definitions
_LAYERS_2 = [
    (0, "F.Cu", "signal"),
    (31, "B.Cu", "signal"),
    (32, "B.Adhes", "user", "B.Adhesive"),
    (33, "F.Adhes", "user", "F.Adhesive"),
    (34, "B.Paste", "user"),
    (35, "F.Paste", "user"),
    (36, "B.SilkS", "user", "B.Silkscreen"),
    (37, "F.SilkS", "user", "F.Silkscreen"),
    (38, "B.Mask", "user"),
    (39, "F.Mask", "user"),
    (44, "Edge.Cuts", "user"),
    (45, "Margin", "user"),
    (46, "B.CrtYd", "user", "B.Courtyard"),
    (47, "F.CrtYd", "user", "F.Courtyard"),
    (48, "B.Fab", "user"),
    (49, "F.Fab", "user"),
]


def build_pcb(
    parts: list[dict],
    nets: list[dict],
    board: dict | None = None,
    project_name: str = "project",
) -> str:
    """Build a .kicad_pcb file from parts, nets, and board config.

    Args:
        parts: List of component dicts with ref, name, footprint, value, pins
        nets: List of net dicts with name, type, connections
        board: Board config dict with shape, width, depth, layers, thickness
        project_name: Project name for UUID generation

    Returns:
        Complete .kicad_pcb file content as string
    """
    board = dict(board or {})
    s = SExpr()

    # Board offset: center the PCB on the A4 page (297x210mm)
    bw, bh = _get_board_dims(board)
    board["_ox"] = round((297.0 - bw) / 2, 2)
    board["_oy"] = round((210.0 - bh) / 2, 2)

    # Header
    s.open("kicad_pcb")
    s.prop("version", KICAD_PCB_VERSION)
    s.prop_quoted("generator", "kicad-mcp")
    s.prop_quoted("generator_version", "1.0")
    s.emit(f'(general (thickness {board.get("thickness", 1.6)}))')
    s.prop_quoted("paper", "A4")
    s.blank()

    # Layers
    _emit_layers(s, board)
    s.blank()

    # Setup (design rules)
    _emit_setup(s, board)
    s.blank()

    # Nets
    _emit_nets(s, nets)
    s.blank()

    # Board outline
    _emit_board_outline(s, board, project_name)
    s.blank()

    # Footprints
    _emit_footprints(s, parts, nets, board, project_name)
    s.blank()

    # GND zone (if GND net exists)
    _emit_gnd_zone(s, nets, board, project_name)

    # Autoroute traces
    _emit_routed_traces_from_placements(s, parts, nets, board, project_name)

    s.close()  # kicad_pcb
    return s.render()


def _emit_layers(s: SExpr, board: dict) -> None:
    """Emit layer definitions."""
    s.open("layers")
    for layer_def in _LAYERS_2:
        num, name, ltype = layer_def[0], layer_def[1], layer_def[2]
        alias = layer_def[3] if len(layer_def) > 3 else None
        if alias:
            s.emit(f'({num} "{name}" {ltype} "{alias}")')
        else:
            s.emit(f'({num} "{name}" {ltype})')
    s.close()


def _emit_setup(s: SExpr, board: dict) -> None:
    """Emit design rules setup section — JLCPCB defaults like a real user."""
    rules = board.get("design_rules", JLCPCB_RULES)

    s.open("setup")
    s.emit('(pad_to_mask_clearance 0.05)')
    s.emit('(aux_axis_origin 0 0)')
    s.emit('(grid_origin 0 0)')
    s.open("pcbplotparams")
    s.close()
    s.close()

    # Net class with design rules (like KiCad's Board Setup → Design Rules)
    s.open("net_class", '"Default"', '"Default net class"')
    s.emit(f'(clearance {rules["min_clearance"]})')
    s.emit(f'(trace_width {rules["min_track_width"]})')
    s.emit(f'(via_dia {rules["min_via_diameter"]})')
    s.emit(f'(via_drill {rules["min_via_drill"]})')
    s.emit('(uvia_dia 0.3)')
    s.emit('(uvia_drill 0.1)')
    s.close()


def _emit_nets(s: SExpr, nets: list[dict]) -> None:
    """Emit net definitions."""
    s.emit('(net 0 "")')
    for i, net in enumerate(nets, 1):
        s.emit(f'(net {i} "{net["name"]}")')


def _emit_board_outline(s: SExpr, board: dict, project_name: str) -> None:
    """Emit board outline on Edge.Cuts layer."""
    shape = board.get("shape", "rectangle")

    if shape == "euro_divider":
        euro_type = board.get("euro_type", "half_euro")
        dims = EURO_DIVIDER_SIZES.get(euro_type, EURO_DIVIDER_SIZES["half_euro"])
        w, h = dims["width"], dims["height"]
    elif shape == "circle":
        diameter = board.get("diameter", 50)
        ox, oy = board.get("_ox", 0), board.get("_oy", 0)
        cx, cy = ox + diameter / 2, oy + diameter / 2
        r = diameter / 2
        s.emit(
            f'(gr_circle (center {cx} {cy}) (end {cx + r} {cy})'
            f' (layer "Edge.Cuts") (stroke (width 0.1) (type default))'
            f' (uuid "{uid(f"{project_name}_outline")}"))'
        )
        return
    else:  # rectangle (default)
        w = board.get("width", 100)
        h = board.get("depth", 100)

    ox, oy = board.get("_ox", 0), board.get("_oy", 0)
    s.gr_rect(ox, oy, ox + w, oy + h, "Edge.Cuts", uid(f"{project_name}_outline"))

    # Mounting holes — like a real user would add for any board ≥ 30x30mm
    # (Regel geteilt mit der Platzierung, die die Ecken freihält)
    from .board_geom import board_has_mounting_holes
    if board_has_mounting_holes(board, w, h):
        _emit_mounting_holes(s, w, h, project_name, ox, oy)


def _emit_mounting_holes(s: SExpr, w: float, h: float, project_name: str,
                         ox: float = 0, oy: float = 0) -> None:
    """Emit M3 mounting holes at board corners."""
    from .board_geom import MOUNTING_HOLE_RADIUS, mounting_hole_positions
    hole_r = MOUNTING_HOLE_RADIUS
    positions = [(ox + mx, oy + my)
                 for mx, my in mounting_hole_positions(w, h)]
    for i, (mx, my) in enumerate(positions):
        hole_uid = uid(f"{project_name}_mh_{i}")
        s.open("footprint", '"MountingHole:MountingHole_3.2mm_M3"',
               '(layer "F.Cu")', f'(at {mx} {my})')
        s.emit(f'(uuid "{hole_uid}")')
        s.kicad_property("Reference", f"MH{i+1}", 0, -3)
        s.kicad_property("Value", "MountingHole", 0, 3, hide=True)
        s.emit(
            f'(pad "" np_thru_hole circle (at 0 0) (size {hole_r*2} {hole_r*2})'
            f' (drill {hole_r*2}) (layers "*.Cu" "*.Mask"))'
        )
        s.close()


def _emit_footprints(
    s: SExpr, parts: list[dict], nets: list[dict], board: dict, project_name: str
) -> None:
    """Place and emit footprints like a real KiCad user would.

    - MCU/main IC centered on the board
    - Bypass caps close to their IC
    - Connectors at board edges
    - Passives (R, C) rotated 90° when vertical saves space
    - Avoids overlaps using footprint bounding boxes
    """
    # Build net lookups
    net_numbers = {"": 0}
    for i, net in enumerate(nets, 1):
        net_numbers[net["name"]] = i

    pin_to_net_name = {}
    for net in nets:
        for conn in net.get("connections", []):
            pin_to_net_name[conn] = net["name"]

    # Board dimensions
    board_w, board_h = _get_board_dims(board)

    # ── Intelligent placement ──────────────────────────────────────
    placements = _compute_pcb_placement(parts, nets, board_w, board_h, board=board)
    ox, oy = board.get("_ox", 0), board.get("_oy", 0)

    for part in parts:
        ref = part["ref"]
        fp_id = resolve_footprint(part)
        fp_uid = uid(f"{project_name}_fp_{ref}")
        ref_uid = uid(f"{project_name}_fp_{ref}_ref")
        val_uid = uid(f"{project_name}_fp_{ref}_val")

        bx, by, rotation = placements.get(ref, (board_w / 2, board_h / 2, 0))
        px, py = bx + ox, by + oy

        # Build pad net mapping — try both pin number and pin name as connection key
        pad_nets = {}
        for pin in part.get("pins", []):
            conn_by_num = f"{ref}:{pin['num']}"
            conn_by_name = f"{ref}:{pin['name']}"
            net_name = pin_to_net_name.get(conn_by_num, "")
            if not net_name:
                net_name = pin_to_net_name.get(conn_by_name, "")
            net_num = net_numbers.get(net_name, 0)
            if net_num > 0:
                pad_nets[str(pin["num"])] = (net_num, net_name)

        # Symbol UUID for schematic↔PCB link
        sym_uuid = uid(f"{project_name}_sym_{ref}")

        # Embed real footprint or stub
        real_fp = build_footprint_with_nets(
            fp_id, ref, part.get("value", part["name"]),
            px, py, pad_nets, fp_uid, ref_uid, val_uid, sym_uuid,
            rotation=rotation,
        )

        if real_fp:
            s.emit(real_fp)
        else:
            logger.warning(
                "Footprint '%s' not in KiCad library for %s — stub emitted.",
                fp_id, ref,
            )
            _emit_stub_footprint(
                s, part, fp_id, ref, px, py,
                pad_nets, fp_uid, ref_uid, val_uid,
            )


def _get_board_dims(board: dict) -> tuple[float, float]:
    """Return (width, height) of the board in mm."""
    shape = board.get("shape", "rectangle")
    if shape == "euro_divider":
        euro_type = board.get("euro_type", "half_euro")
        dims = EURO_DIVIDER_SIZES.get(euro_type, EURO_DIVIDER_SIZES["half_euro"])
        return dims["width"], dims["height"]
    if shape == "circle":
        d = board.get("diameter", 50)
        return d, d
    return board.get("width", 100), board.get("depth", 100)


def _emit_stub_footprint(
    s: SExpr, part: dict, fp_id: str, ref: str, x: float, y: float,
    pad_nets: dict, fp_uid: str, ref_uid: str, val_uid: str,
) -> None:
    """Emit a minimal stub footprint when the KiCad library is unavailable.

    Uses KiCad-standard pad sizes (2.54 mm pitch THT) and puts all
    graphics on F.Fab — never on copper layers.  The result is DRC-clean
    but ugly; the user should run "Update from Library" to replace it.
    """
    PITCH = 2.54   # KiCad standard THT pitch
    PAD_DIA = 1.6  # standard THT pad
    DRILL = 0.8    # standard THT drill

    sym_uuid = uid(f"stub_sym_{ref}")
    s.open("footprint", f'"{fp_id}"', '(layer "F.Cu")', f'(at {x} {y})')
    s.emit(f'(uuid "{fp_uid}")')
    s.emit(f'(path "/{sym_uuid}")')
    s.emit(
        f'(property "Reference" "{ref}" (at 0 -2 0) (layer "F.SilkS")'
        f' (uuid "{ref_uid}") (effects (font (size 1 1) (thickness 0.15))))'
    )
    val = part.get("value", part["name"])
    s.emit(
        f'(property "Value" "{val}" (at 0 2 0) (layer "F.Fab")'
        f' (uuid "{val_uid}") (effects (font (size 1 1) (thickness 0.15))))'
    )

    pins = part.get("pins", [])
    n = len(pins)

    # Compute positions: DIP-like dual-inline at 2.54mm pitch
    positions = _stub_pad_positions(n, PITCH)

    for i, pin in enumerate(pins):
        net_info = pad_nets.get(str(pin["num"]))
        net_str = f' (net {net_info[0]} "{net_info[1]}")' if net_info else ""
        px, py = positions[i]
        s.emit(
            f'(pad "{pin["num"]}" thru_hole circle (at {px} {py})'
            f' (size {PAD_DIA} {PAD_DIA}) (drill {DRILL})'
            f' (layers "*.Cu" "*.Mask"){net_str})'
        )

    # Body outline on F.Fab (NOT copper!) — so DRC stays clean
    if n >= 2:
        ext = PITCH  # extension beyond outermost pads
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        x0, x1 = min(xs) - ext, max(xs) + ext
        y0, y1 = min(ys) - ext, max(ys) + ext
        crtyd_uid = uid(f"stub_crtyd_{ref}")
        s.emit(
            f'(fp_rect (start {x0} {y0}) (end {x1} {y1})'
            f' (stroke (width 0.12) (type default)) (fill none) (layer "F.Fab")'
            f' (uuid "{crtyd_uid}"))'
        )

    s.close()


def _stub_pad_positions(n_pads: int, pitch: float = 2.54) -> list[tuple[float, float]]:
    """Compute DIP-like pad positions at standard 2.54 mm pitch."""
    if n_pads == 0:
        return []

    if n_pads <= 2:
        # 2-pin passive: horizontal, 5.08mm apart (like R_Axial_P5.08)
        return [(-pitch, 0.0), (pitch, 0.0)][:n_pads]

    # DIP dual-inline layout
    left_count = (n_pads + 1) // 2
    right_count = n_pads - left_count
    row_x = 3.81  # half body width (DIP standard 7.62mm / 2)

    positions = []
    half_h = (left_count - 1) * pitch / 2
    for i in range(left_count):
        positions.append((-row_x, round(-half_h + i * pitch, 2)))

    half_h_r = (right_count - 1) * pitch / 2
    for i in range(right_count):
        positions.append((row_x, round(half_h_r - i * pitch, 2)))

    return positions


def _emit_gnd_zone(s: SExpr, nets: list[dict], board: dict, project_name: str) -> None:
    """Emit a GND copper pour zone if GND net exists."""
    gnd_num = None
    for i, net in enumerate(nets, 1):
        if net["name"].upper() == "GND":
            gnd_num = i
            break

    if gnd_num is None:
        return

    shape = board.get("shape", "rectangle")
    if shape == "circle":
        return  # Skip zone for circular boards (complex polygon)

    if shape == "euro_divider":
        euro_type = board.get("euro_type", "half_euro")
        dims = EURO_DIVIDER_SIZES.get(euro_type, EURO_DIVIDER_SIZES["half_euro"])
        w, h = dims["width"], dims["height"]
    else:
        w = board.get("width", 100)
        h = board.get("depth", 100)

    zone_uid = uid(f"{project_name}_gnd_zone")
    s.open("zone", f'(net {gnd_num})', '(net_name "GND")', '(layer "F.Cu")',
           f'(uuid "{zone_uid}")')
    s.emit("(fill yes)")
    s.open("polygon")
    s.open("pts")
    ox, oy = board.get("_ox", 0), board.get("_oy", 0)
    s.emit(f"(xy {ox} {oy}) (xy {ox + w} {oy}) (xy {ox + w} {oy + h}) (xy {ox} {oy + h})")
    s.close()  # pts
    s.close()  # polygon
    s.close()  # zone


# _read_footprint_pad_positions ist nach common.bbox umgezogen (geteilt mit
# der Platzierung fürs Entwirren) — Import steht oben bei den anderen.


def _emit_routed_traces_from_placements(
    s: SExpr, parts: list[dict], nets: list[dict], board: dict, project_name: str,
) -> None:
    """Route traces using pad positions computed from placements.

    Instead of parsing the rendered PCB, computes pad absolute positions
    directly from footprint placements + pin offsets (THT pitch = 2.54mm).
    GND is skipped (handled by copper pour zone).
    """
    try:
        from collections import defaultdict

        from .route import route_pcb

        board_w, board_h = _get_board_dims(board)
        placements = _compute_pcb_placement(parts, nets, board_w, board_h, board=board)
        ox, oy = board.get("_ox", 0), board.get("_oy", 0)

        # Build net lookups
        net_numbers = {}
        for i, net in enumerate(nets, 1):
            net_numbers[net["name"]] = i

        pin_to_net = {}
        for net in nets:
            for conn in net.get("connections", []):
                pin_to_net[conn] = net["name"]

        # Compute absolute pad positions from placements + pin offsets
        pad_positions: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
        net_info: dict[str, tuple[int, str]] = {}

        for part in parts:
            ref = part["ref"]
            if ref not in placements:
                continue
            fp_x, fp_y, fp_rot = placements[ref]

            # Read real pad positions from footprint library
            fp_id = part.get("footprint", "")
            real_pad_pos = _read_footprint_pad_positions(fp_id)
            from ..common.bbox import read_footprint_pads as \
                _read_footprint_pads_full

            for pin in part.get("pins", []):
                conn_by_num = f"{ref}:{pin['num']}"
                conn_by_name = f"{ref}:{pin['name']}"
                net_name = pin_to_net.get(conn_by_num, pin_to_net.get(conn_by_name, ""))
                if not net_name:
                    continue

                net_num = net_numbers.get(net_name, 0)
                if net_num == 0:
                    continue

                # Use real pad position if available, else estimate
                pad_num = str(pin["num"])
                if pad_num in real_pad_pos:
                    rel_x, rel_y = real_pad_pos[pad_num]
                else:
                    # Fallback: estimate based on pin index
                    pin_idx = list(p["num"] for p in part["pins"]).index(pin["num"])
                    rel_x = pin_idx * 2.54
                    rel_y = 0

                # Pad world coordinate via the canonical KiCad-aware
                # transform (math-CW rotation matrix because KiCad's
                # screen-Y is down — see CLAUDE.md §Rotation). The
                # builder currently does not track per-part layer so
                # ``flipped=False`` is assumed; revisit if PCB-gen
                # gains B.Cu-side parts (CLAUDE.md §B.Cu-Flip).
                wx, wy = pcb_local_to_world(
                    (fp_x, fp_y), fp_rot, rel_x, rel_y, flipped=False,
                )
                abs_x = round(wx + ox, 3)
                abs_y = round(wy + oy, 3)

                pad_geo = next(
                    (pd for pd in _read_footprint_pads_full(fp_id)
                     if pd["num"] == pad_num), None)
                pad_through = bool(pad_geo and pad_geo["through"])
                pw, ph = (pad_geo["w"], pad_geo["h"]) if pad_geo else (1.0, 1.0)
                if pad_geo and (fp_rot + pad_geo["rot"]) % 180 == 90:
                    pw, ph = ph, pw
                pad_positions[net_name].append(
                    (abs_x, abs_y, f"{ref}:{pad_num}", pad_through, pw, ph))
                net_info[net_name] = (net_num, net_name)

        # GND wird MIT geroutet (Zone bleibt als Fläche obendrauf, aber die
        # 0-DRC-offen-Garantie kommt aus Kupferzügen, nicht aus einer
        # ungefüllten Zone — kicad-cli füllt beim DRC nicht).
        filtered = {
            name: pads for name, pads in pad_positions.items()
            if len(pads) >= 2
        }

        if not filtered:
            return

        # Hindernis-Modell des Routers: JEDES Pad mit echter Geometrie
        # (auch netzlose — Thermal-Pads, NPTH), plus die Montagelöcher.
        from ..common.bbox import read_footprint_pads
        from .board_geom import (MOUNTING_HOLE_RADIUS,
                                 board_has_mounting_holes,
                                 mounting_hole_positions)
        pad_net: dict[tuple[str, str], int] = {}
        for net in nets:
            for conn in net.get("connections", []):
                if ":" in conn:
                    r, p = conn.split(":", 1)
                    pad_net[(r, p)] = net_numbers.get(net["name"], 0)
        all_pads: list[tuple[float, float, float, float, int, bool]] = []
        for part in parts:
            ref = part["ref"]
            if ref not in placements:
                continue
            fp_x, fp_y, fp_rot = placements[ref]
            name_by_num = {str(p["num"]): str(p.get("name", ""))
                           for p in part.get("pins", [])}
            for pad in read_footprint_pads(part.get("footprint", "")):
                wx, wy = pcb_local_to_world(
                    (fp_x, fp_y), fp_rot, pad["x"], pad["y"], flipped=False)
                w, h = pad["w"], pad["h"]
                if (fp_rot + pad["rot"]) % 180 == 90:
                    w, h = h, w
                num = pad["num"]
                nn = pad_net.get((ref, num), 0)
                if nn == 0:  # Pin-NAME als Fallback (wie beim Routen)
                    nn = pad_net.get((ref, name_by_num.get(num, "")), 0)
                all_pads.append((round(wx + ox, 3), round(wy + oy, 3),
                                 w, h, nn, pad["through"]))
        if board_has_mounting_holes(board, board_w, board_h):
            hole_d = MOUNTING_HOLE_RADIUS * 2
            for mx, my in mounting_hole_positions(board_w, board_h):
                all_pads.append((round(mx + ox, 3), round(my + oy, 3),
                                 hole_d + 0.4, hole_d + 0.4, 0, True))

        # Board outline rectangle
        board_rect = (ox, oy, ox + board_w, oy + board_h)

        trace_text = route_pcb(filtered, net_info, nets, all_pads, board_rect)
        if trace_text.strip():
            s.blank()
            for line in trace_text.strip().split('\n'):
                s.emit(line.strip())

    except Exception as e:
        logger.warning("Autorouting failed: %s", e)


# ── DRC-based rip-up / reroute ───────────────────────────────────────────────


def _net_num_to_name(pcb_text: str, net_num: int) -> str:
    """Extract net name from (net N "name") declarations in PCB text."""
    import re
    match = re.search(rf'\(net {net_num} "([^"]+)"\)', pcb_text)
    return match.group(1) if match else ""


def _find_kicad_cli() -> str | None:
    """Find kicad-cli executable."""
    import os
    cli = os.environ.get("KICAD_CLI_PATH", "")
    if cli and os.path.exists(cli):
        return cli
    return None


def _to_win_path(path: str) -> str:
    """Convert WSL path to Windows path if needed."""
    if path.startswith("/mnt/"):
        import re
        m = re.match(r"/mnt/([a-z])/(.+)", path)
        if m:
            return f"{m.group(1).upper()}:/{m.group(2)}"
    return path
