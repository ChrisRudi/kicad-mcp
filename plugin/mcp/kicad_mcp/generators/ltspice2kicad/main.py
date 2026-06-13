# SPDX-License-Identifier: GPL-3.0-or-later
# main.py
"""Pipeline orchestration and CLI entry point for LTspice-to-KiCad rebuilder."""
from __future__ import annotations

import os
import sys

from kicad_mcp.generators.ltspice2kicad.aligner import align_wires_to_pins
from kicad_mcp.generators.ltspice2kicad.builder import build_kicad_sch, write_kicad_sch
from kicad_mcp.generators.ltspice2kicad.mapping import (
    find_mapping,
    get_explicit_nc_pins,
    get_kicad_footprint,
    get_kicad_symbol,
    get_mirror_semantic,
    get_pin_map,
    get_power_symbol,
    transcode_value,
)
from kicad_mcp.generators.ltspice2kicad.models import (
    Component,
    RebuildResult,
    SymbolMeta,
    TransformedComponent,
    TransformedJunction,
    TransformedLabel,
    TransformedWire,
)
from kicad_mcp.generators.ltspice2kicad.normalizer import (
    compute_bounds,
    rotate_pin,
    snap_to_grid,
    transform,
)
from kicad_mcp.generators.ltspice2kicad.parser import parse_asc
from kicad_mcp.generators.ltspice2kicad.scaler import (
    collect_all_coordinates,
    compute_scale_factor,
)
from kicad_mcp.generators.ltspice2kicad.symbols import parse_asy
from kicad_mcp.generators.symbol_cache import get_real_symbol as _get_real_kicad_symbol
from kicad_mcp.generators.ltspice2kicad.symbols import parse_kicad_sym_entry
from kicad_mcp.generators.ltspice2kicad.topology import (
    extract_nets,
    find_confirmed_junctions,
)
from kicad_mcp.generators.ltspice2kicad.validator import format_report, validate

# Default LTspice symbol library paths (searched in order)
_LTSPICE_LIB_PATHS = [
    os.path.expanduser("~/AppData/Local/LTspice/lib/sym"),
    os.path.expanduser("~/.wine/drive_c/Program Files/LTC/LTspiceXVII/lib/sym"),
    os.path.expanduser("~/LTspiceXVII/lib/sym"),
]

# Cache for parsed .asy symbol metadata
_asy_cache: dict[str, SymbolMeta | None] = {}
# Cache for parsed KiCad symbol metadata
_kicad_sym_cache: dict[str, SymbolMeta | None] = {}


def _find_asy_file(symbol_name: str) -> str | None:
    """Locate the .asy file for a given LTspice symbol name."""
    base = symbol_name.lower().replace("\\", "/").split("/")[-1]
    for lib_dir in _LTSPICE_LIB_PATHS:
        if not os.path.isdir(lib_dir):
            continue
        # Direct match (case-insensitive search)
        for entry in os.listdir(lib_dir):
            if entry.lower() == base + ".asy":
                return os.path.join(lib_dir, entry)
        # Check subdirectories one level deep
        for sub in os.listdir(lib_dir):
            sub_path = os.path.join(lib_dir, sub)
            if os.path.isdir(sub_path):
                for entry in os.listdir(sub_path):
                    if entry.lower() == base + ".asy":
                        return os.path.join(sub_path, entry)
    return None


def _get_symbol_meta(symbol_name: str) -> SymbolMeta | None:
    """Get cached SymbolMeta for an LTspice symbol (from .asy file)."""
    if symbol_name not in _asy_cache:
        asy_path = _find_asy_file(symbol_name)
        if asy_path:
            _asy_cache[symbol_name] = parse_asy(asy_path)
        else:
            _asy_cache[symbol_name] = None
    return _asy_cache[symbol_name]


def _get_kicad_symbol_meta(lib_id: str) -> SymbolMeta | None:
    """Get cached SymbolMeta for a KiCad symbol (from .kicad_sym library)."""
    if lib_id not in _kicad_sym_cache:
        real_sym = _get_real_kicad_symbol(lib_id)
        if real_sym:
            name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
            _kicad_sym_cache[lib_id] = parse_kicad_sym_entry(name, real_sym)
        else:
            _kicad_sym_cache[lib_id] = None
    return _kicad_sym_cache[lib_id]


def _compute_orientation_correction(
    comp: Component,
    pin_map: dict[str, str],
    can_mirror: bool,
    kicad_lib_id: str,
) -> int:
    """Compute extra rotation needed to match LTspice pin orientation.

    Compares pin-pair vectors between LTspice (after rotation) and KiCad
    to determine if KiCad symbol needs additional rotation.

    Returns extra rotation in degrees (0, 90, 180, 270).
    """
    sym_meta = _get_symbol_meta(comp.type_ltspice)
    ki_meta = _get_kicad_symbol_meta(kicad_lib_id)
    if not sym_meta or not ki_meta or len(pin_map) < 2:
        return 0

    # Get first two mapped pins
    asy_pins = {p.name: p for p in sym_meta.pins}
    ki_pins = {p.number: p for p in ki_meta.pins}
    pairs = []
    for lt_name, ki_num in pin_map.items():
        lt_pin = asy_pins.get(lt_name)
        if lt_pin is None:
            for p in sym_meta.pins:
                if p.number == lt_name:
                    lt_pin = p
                    break
        ki_pin = ki_pins.get(ki_num)
        if lt_pin and ki_pin:
            pairs.append((lt_pin, ki_pin))
        if len(pairs) >= 2:
            break

    if len(pairs) < 2:
        return 0

    # Compare base orientation (R0, no mirror) between LTspice and KiCad.
    # Only correct if they have different base axes (e.g. LT vertical, KiCad horizontal).
    lt_a, lt_b = pairs[0][0], pairs[1][0]
    lt_dx = lt_b.x - lt_a.x
    lt_dy = lt_b.y - lt_a.y
    # Do NOT apply component rotation — compare base orientations only

    ki_a, ki_b = pairs[0][1], pairs[1][1]
    ki_dx = (ki_b.x - ki_a.x) / 1000.0
    ki_dy = -(ki_b.y - ki_a.y) / 1000.0  # negate y

    import math
    lt_angle = math.atan2(lt_dy, lt_dx)
    ki_angle = math.atan2(ki_dy, ki_dx)
    # Compare axes only (mod 180)
    lt_axis = round(math.degrees(lt_angle)) % 180
    ki_axis = round(math.degrees(ki_angle)) % 180
    diff = lt_axis - ki_axis
    extra_rot = round(diff / 90) * 90 % 360
    return extra_rot


def _compute_kicad_origin(
    comp: Component,
    pin_map: dict[str, str],
    can_mirror: bool,
    kicad_lib_id: str,
    pins_abs: list[tuple[float, float, str]],
) -> tuple[float, float]:
    """Compute KiCad symbol origin so that KiCad pins land on transformed LTspice pins.

    The KiCad symbol has its own pin offsets relative to its origin.
    When placed at (ox, oy) with rotation, pin positions become:
        pin_abs = (ox + rotated_kicad_pin_offset)
    We need: pin_abs == transformed_ltspice_pin
    So: ox = transformed_ltspice_pin - rotated_kicad_pin_offset

    Uses first mapped pin as anchor.
    """
    ki_meta = _get_kicad_symbol_meta(kicad_lib_id)
    if not ki_meta or not ki_meta.pins or not pins_abs:
        # No KiCad pin data — use LTspice origin directly
        return pins_abs[0][0], pins_abs[0][1] if pins_abs else (0.0, 0.0)

    # Build KiCad pin lookup by number
    ki_pins = {p.number: p for p in ki_meta.pins}

    import math
    angle_rad = math.radians(comp.rotation)
    cos_a = round(math.cos(angle_rad))
    sin_a = round(math.sin(angle_rad))

    # Compute origin from ALL matching pins (least-squares centroid)
    # This minimizes drift across all pins when scaling isn't exact
    ox_candidates = []
    oy_candidates = []

    for target_x, target_y, ki_num in pins_abs:
        ki_pin = ki_pins.get(ki_num)
        if ki_pin is None:
            continue

        # KiCad pin offset in mm (stored as mm*1000 in SymbolMeta)
        # IMPORTANT: KiCad symbol y-axis is inverted vs schematic y-axis
        kpx_mm = ki_pin.x / 1000.0
        kpy_mm = -ki_pin.y / 1000.0  # negate for schematic coords

        # Mirror BEFORE rotation. KiCad's ``(mirror y)`` (mirror about
        # the Y axis = horizontal flip = X-negation) matches LTspice's
        # `mirror=true`; the builder now emits the right token so this
        # local X-negation lines up with the rendered file.
        if can_mirror:
            kpx_mm = -kpx_mm

        # Rotate KiCad pin offset by component rotation
        # KiCad schematic rotation is CW in screen coords (y-down),
        # which is the inverse/transpose of the standard CCW matrix.
        rpx = kpx_mm * cos_a + kpy_mm * sin_a
        rpy = -kpx_mm * sin_a + kpy_mm * cos_a

        # Origin = target_pin_position - rotated_kicad_pin_offset
        ox_candidates.append(target_x - rpx)
        oy_candidates.append(target_y - rpy)

    if ox_candidates:
        ox = snap_to_grid(sum(ox_candidates) / len(ox_candidates))
        oy = snap_to_grid(sum(oy_candidates) / len(oy_candidates))
        return ox, oy

    # No match found — fallback
    return pins_abs[0][0], pins_abs[0][1]


def _compute_kicad_pin_abs(
    origin_x: float,
    origin_y: float,
    rotation: int,
    mirror: bool,
    kicad_lib_id: str,
    pin_map: dict[str, str],
) -> list[tuple[float, float, str]]:
    """Compute absolute pin positions from KiCad symbol placed at origin.

    Returns list of (x_mm, y_mm, kicad_pin_number) based on real
    KiCad symbol pin offsets, ensuring wires connect to actual pins.
    """
    ki_meta = _get_kicad_symbol_meta(kicad_lib_id)
    if not ki_meta or not ki_meta.pins:
        return []

    import math
    angle_rad = math.radians(rotation)
    cos_a = round(math.cos(angle_rad))
    sin_a = round(math.sin(angle_rad))

    ki_pins_by_num = {p.number: p for p in ki_meta.pins}
    result: list[tuple[float, float, str]] = []

    for _lt_name, ki_num in pin_map.items():
        ki_pin = ki_pins_by_num.get(ki_num)
        if not ki_pin:
            result.append((origin_x, origin_y, ki_num))
            continue

        # KiCad pin offset in mm, y negated for schematic coords
        kpx = ki_pin.x / 1000.0
        kpy = -ki_pin.y / 1000.0

        # Mirror BEFORE rotation: (mirror x) negates X in symbol space
        if mirror:
            kpx = -kpx

        # Rotate (CW in screen coords = inverse of standard CCW)
        rpx = kpx * cos_a + kpy * sin_a
        rpy = -kpx * sin_a + kpy * cos_a

        px = snap_to_grid(origin_x + rpx)
        py = snap_to_grid(origin_y + rpy)
        result.append((px, py, ki_num))

    return result


def _compute_abs_pin_positions(
    comp: Component,
    pin_map: dict[str, str],
    can_mirror: bool,
    x_min: int,
    y_min: int,
    lgu: int,
    s: int,
) -> list[tuple[float, float, str]]:
    """Compute absolute KiCad pin positions for a component.

    Uses real .asy pin offsets with global scaling. This gives the
    LTspice-based wire endpoints (before KiCad pin correction).

    Returns list of (x_mm, y_mm, kicad_pin_number).
    """
    pins_abs: list[tuple[float, float, str]] = []
    sym_meta = _get_symbol_meta(comp.type_ltspice)

    if sym_meta and sym_meta.pins:
        asy_pins = {p.name: p for p in sym_meta.pins}

        for lt_name, ki_num in pin_map.items():
            asy_pin = asy_pins.get(lt_name)
            if asy_pin is None:
                for p in sym_meta.pins:
                    if p.number == lt_name:
                        asy_pin = p
                        break

            if asy_pin:
                dx, dy = asy_pin.x, asy_pin.y
                dx, dy = rotate_pin(dx, dy, comp.rotation, can_mirror)
                px_mm = transform(comp.x + dx, x_min, lgu, s)
                py_mm = transform(comp.y + dy, y_min, lgu, s)
                pins_abs.append((px_mm, py_mm, ki_num))
            else:
                cx_mm = transform(comp.x, x_min, lgu, s)
                cy_mm = transform(comp.y, y_min, lgu, s)
                pins_abs.append((cx_mm, cy_mm, ki_num))
    else:
        cx_mm = transform(comp.x, x_min, lgu, s)
        cy_mm = transform(comp.y, y_min, lgu, s)
        for lt_name, ki_num in pin_map.items():
            pins_abs.append((cx_mm, cy_mm, ki_num))

    return pins_abs


def _compute_lt_pin_positions(
    comp: Component,
    pin_map: dict[str, str],
    can_mirror: bool,
) -> list[tuple[int, int, str, str]]:
    """Compute absolute LTspice pin positions for topology.

    Returns list of (abs_x, abs_y, comp_id, lt_pin_name).
    """
    positions: list[tuple[int, int, str, str]] = []
    sym_meta = _get_symbol_meta(comp.type_ltspice)

    if sym_meta and sym_meta.pins:
        asy_pins = {p.name: p for p in sym_meta.pins}
        for lt_name in pin_map:
            asy_pin = asy_pins.get(lt_name)
            if asy_pin is None:
                for p in sym_meta.pins:
                    if p.number == lt_name:
                        asy_pin = p
                        break
            if asy_pin:
                dx, dy = asy_pin.x, asy_pin.y
                dx, dy = rotate_pin(dx, dy, comp.rotation, can_mirror)
                positions.append((comp.x + dx, comp.y + dy, comp.id, lt_name))
            else:
                positions.append((comp.x, comp.y, comp.id, lt_name))
    else:
        # Fallback: all pins at origin
        for lt_name in pin_map:
            positions.append((comp.x, comp.y, comp.id, lt_name))

    return positions


def convert_asc_to_kicad(
    input_path: str,
    output_path: str,
    title: str = "",
    ltspice_lib: str = "",
) -> RebuildResult:
    """Run the full 10-stage pipeline: ASC -> KiCad schematic.

    Args:
        input_path: Path to .asc file.
        output_path: Path for output .kicad_sch file.
        title: Optional title for the schematic.

    Returns:
        RebuildResult with success status, metrics, and validation.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if ltspice_lib and os.path.isdir(ltspice_lib):
        _LTSPICE_LIB_PATHS.insert(0, ltspice_lib)

    if not title:
        title = os.path.splitext(os.path.basename(input_path))[0]

    # --- Stage 1: Parse ---
    try:
        schematic = parse_asc(input_path)
    except (OSError, IOError) as e:
        return RebuildResult(success=False, errors=[f"Cannot read {input_path}: {e}"])

    if not schematic.components and not schematic.wires:
        return RebuildResult(success=False, errors=["Empty schematic: no components or wires found"])

    # --- Stage 2: Topology ---
    # Build pin positions map using REAL pin offsets from .asy files
    lt_pin_positions: dict[tuple[int, int], tuple[str, str]] = {}
    for comp in schematic.components:
        mapping = find_mapping(comp.type_ltspice)
        if mapping is None:
            warnings.append(f"WARNING: No mapping for {comp.type_ltspice} ({comp.reference})")
            continue
        pin_map = get_pin_map(comp.type_ltspice, comp.mirror)
        positions = _compute_lt_pin_positions(comp, pin_map, comp.mirror)
        for abs_x, abs_y, comp_id, lt_pin_name in positions:
            lt_pin_positions[(abs_x, abs_y)] = (comp_id, lt_pin_name)

    confirmed_junctions = find_confirmed_junctions(schematic)

    # --- Stage 3-4: Mapping ---
    mapped_components: list[tuple] = []  # (comp, mapping_entry, pin_map)
    for comp in schematic.components:
        mapping = find_mapping(comp.type_ltspice)
        if mapping is None:
            continue

        # Check mirror semantic
        can_mirror = comp.mirror
        use_mirrored_pins = comp.mirror  # use mirrored pin map?
        mirror_extra_rot = 0  # extra rotation to emulate mirror
        mirror_sem = get_mirror_semantic(comp.type_ltspice)
        if comp.mirror:
            if mirror_sem == "forbidden":
                warnings.append(
                    f"WARNING: Mirror ignored for {comp.reference} "
                    f"({comp.type_ltspice}) — mirror_semantic=forbidden"
                )
                can_mirror = False
                use_mirrored_pins = False
            elif mirror_sem == "restricted":
                mirrored_map = mapping.get("pin_map_mirrored")
                if not mirrored_map:
                    warnings.append(
                        f"WARNING: Mirror ignored for {comp.reference} "
                        f"({comp.type_ltspice}) — no pin_map_mirrored defined"
                    )
                    can_mirror = False
                    use_mirrored_pins = False
                else:
                    # For restricted symbols, KiCad (mirror x) + rotation
                    # doesn't match LTspice's post-rotation mirror.
                    # Instead, emulate mirror via +180° rotation + swapped pins.
                    can_mirror = False
                    use_mirrored_pins = True
                    mirror_extra_rot = 180

        pin_map = get_pin_map(comp.type_ltspice, use_mirrored_pins)
        mapped_components.append((comp, mapping, pin_map, can_mirror))

    # --- Stage 5: Scaling ---
    s, lgu = compute_scale_factor(schematic)

    # --- Stage 6: Normalization ---
    all_coords = collect_all_coordinates(schematic)
    _all_x = [c for i, c in enumerate(all_coords) if i % 2 == 0]
    _all_y = [c for i, c in enumerate(all_coords) if i % 2 == 1]
    # More reliable: collect x and y separately
    xs: list[int] = []
    ys: list[int] = []
    for comp in schematic.components:
        xs.append(comp.x)
        ys.append(comp.y)
    for wire in schematic.wires:
        xs.extend([wire.x1, wire.x2])
        ys.extend([wire.y1, wire.y2])
    for label in schematic.labels:
        xs.append(label.x)
        ys.append(label.y)

    x_min, y_min = compute_bounds(xs, ys)

    # --- Stage 7: Reconstruction ---
    transformed_components: list[TransformedComponent] = []
    power_counter: dict[str, int] = {}

    for comp, mapping, pin_map, can_mirror in mapped_components:
        kicad_sym = get_kicad_symbol(comp.type_ltspice)
        footprint = get_kicad_footprint(comp.type_ltspice)

        # Value transcoding
        value = transcode_value(comp.value) if comp.value else ""

        # Build absolute pin positions from real .asy data (transformed LTspice pins)
        # Use comp.mirror (the actual LTspice mirror state), not can_mirror
        # (which may be False for restricted symbols that emulate mirror via rotation)
        lt_pins_abs = _compute_abs_pin_positions(
            comp, pin_map, comp.mirror, x_min, y_min, lgu, s,
        )

        # Compute extra rotation to match LTspice pin orientation
        extra_rot = _compute_orientation_correction(
            comp, pin_map, can_mirror, kicad_sym,
        )
        effective_rotation = (comp.rotation + extra_rot + mirror_extra_rot) % 360

        # Compute KiCad symbol origin so its pins land on the
        # transformed LTspice pin positions (uses first pin as anchor)
        # Use a temporary component with the effective rotation
        comp_adjusted = Component(
            id=comp.id, type_ltspice=comp.type_ltspice,
            x=comp.x, y=comp.y,
            rotation=effective_rotation, mirror=can_mirror,
            reference=comp.reference, value=comp.value,
        )
        cx_mm, cy_mm = _compute_kicad_origin(
            comp_adjusted, pin_map, can_mirror, kicad_sym, lt_pins_abs,
        )

        # Recompute pins_abs from the actual KiCad symbol at its placed origin
        # This ensures wires will snap to where KiCad actually puts the pins
        pins_abs = _compute_kicad_pin_abs(
            cx_mm, cy_mm, effective_rotation, can_mirror, kicad_sym, pin_map,
        )
        if not pins_abs:
            pins_abs = lt_pins_abs  # fallback if no KiCad meta

        nc_pins = get_explicit_nc_pins(comp.type_ltspice)

        transformed_components.append(TransformedComponent(
            id=comp.id,
            type_ltspice=comp.type_ltspice,
            kicad_symbol=kicad_sym,
            x_mm=cx_mm,
            y_mm=cy_mm,
            rotation=effective_rotation,
            mirror=can_mirror,
            reference=comp.reference,
            value=value,
            footprint=footprint,
            pins_abs=pins_abs,
            nc_pins=nc_pins,
        ))

    # Power symbols from labels
    for label in schematic.labels:
        power_lib = get_power_symbol(label.name)
        if power_lib:
            power_counter[label.name] = power_counter.get(label.name, 0) + 1
            px_mm = transform(label.x, x_min, lgu, s)
            py_mm = transform(label.y, y_min, lgu, s)

            ref_num = sum(power_counter.values())
            ref = f"#PWR0{200 + ref_num}"

            transformed_components.append(TransformedComponent(
                id=f"PWR_{ref_num}",
                type_ltspice=label.name,
                kicad_symbol=power_lib,
                x_mm=px_mm,
                y_mm=py_mm,
                rotation=0,
                mirror=False,
                reference=ref,
                value=label.name if label.name != "0" else "GND",
                is_power=True,
                pins_abs=[(px_mm, py_mm, "1")],
            ))

    # Build LTspice pin -> KiCad pin position mapping for wire correction
    # Maps (lt_abs_x, lt_abs_y) -> (kicad_pin_x_mm, kicad_pin_y_mm)
    lt_to_kicad_pin: dict[tuple[int, int], tuple[float, float]] = {}
    for comp, mapping, pin_map, can_mirror in mapped_components:
        sym_meta = _get_symbol_meta(comp.type_ltspice)
        if not sym_meta:
            continue
        asy_pins = {p.name: p for p in sym_meta.pins}
        # Find the TransformedComponent for this comp
        tc = None
        for tc_candidate in transformed_components:
            if tc_candidate.id == comp.id and not tc_candidate.is_power:
                tc = tc_candidate
                break
        if not tc:
            continue

        for lt_name, ki_num in pin_map.items():
            asy_pin = asy_pins.get(lt_name)
            if asy_pin is None:
                for p in sym_meta.pins:
                    if p.number == lt_name:
                        asy_pin = p
                        break
            if not asy_pin:
                continue
            # LTspice absolute pin position (use actual LTspice mirror, not KiCad can_mirror)
            dx, dy = rotate_pin(asy_pin.x, asy_pin.y, comp.rotation, comp.mirror)
            lt_abs = (comp.x + dx, comp.y + dy)
            # Find corresponding KiCad pin position
            for px, py, pn in tc.pins_abs:
                if pn == ki_num:
                    lt_to_kicad_pin[lt_abs] = (px, py)
                    break

    # Transform wires — correct endpoints that land on LTspice pins
    # to hit the actual KiCad pin positions instead.
    # When a pin redirect changes direction, insert a jog segment
    # to maintain Manhattan geometry.
    transformed_wires: list[TransformedWire] = []
    for wire in schematic.wires:
        # Default transform
        tx1 = transform(wire.x1, x_min, lgu, s)
        ty1 = transform(wire.y1, y_min, lgu, s)
        tx2 = transform(wire.x2, x_min, lgu, s)
        ty2 = transform(wire.y2, y_min, lgu, s)

        # Override with KiCad pin positions if endpoint is on/near a pin
        def _find_pin(wx, wy):
            exact = lt_to_kicad_pin.get((wx, wy))
            if exact:
                return exact
            # Tolerance search — LTspice grid is typically 16 units,
            # allow up to 2 units tolerance for rounding
            best = None
            best_dist = 3  # max tolerance in LTspice units
            for (px, py), kicad_pos in lt_to_kicad_pin.items():
                d = abs(px - wx) + abs(py - wy)
                if d < best_dist:
                    best_dist = d
                    best = kicad_pos
            return best

        pin1 = _find_pin(wire.x1, wire.y1)
        pin2 = _find_pin(wire.x2, wire.y2)

        x1 = pin1[0] if pin1 else tx1
        y1 = pin1[1] if pin1 else ty1
        x2 = pin2[0] if pin2 else tx2
        y2 = pin2[1] if pin2 else ty2

        # Check if we need a jog to maintain Manhattan geometry
        is_original_h = (wire.y1 == wire.y2)  # horizontal
        is_original_v = (wire.x1 == wire.x2)  # vertical

        if is_original_h and abs(y1 - y2) > 0.01:
            # Was horizontal, now endpoints at different y — insert jog via midpoint
            mid_x = (x1 + x2) / 2
            mid_x = snap_to_grid(mid_x)
            transformed_wires.append(TransformedWire(x1, y1, mid_x, y1))
            transformed_wires.append(TransformedWire(mid_x, y1, mid_x, y2))
            transformed_wires.append(TransformedWire(mid_x, y2, x2, y2))
            continue
        elif is_original_v and abs(x1 - x2) > 0.01:
            # Was vertical, now endpoints at different x — insert jog
            mid_y = (y1 + y2) / 2
            mid_y = snap_to_grid(mid_y)
            transformed_wires.append(TransformedWire(x1, y1, x1, mid_y))
            transformed_wires.append(TransformedWire(x1, mid_y, x2, mid_y))
            transformed_wires.append(TransformedWire(x2, mid_y, x2, y2))
            continue

        tw = TransformedWire(x1_mm=x1, y1_mm=y1, x2_mm=x2, y2_mm=y2)
        if abs(tw.x1_mm - tw.x2_mm) > 0.001 or abs(tw.y1_mm - tw.y2_mm) > 0.001:
            transformed_wires.append(tw)

    # Transform junctions (only confirmed ones)
    transformed_junctions: list[TransformedJunction] = []
    for jx, jy in confirmed_junctions:
        transformed_junctions.append(TransformedJunction(
            x_mm=transform(jx, x_min, lgu, s),
            y_mm=transform(jy, y_min, lgu, s),
        ))

    # Transform labels (non-power only)
    transformed_labels: list[TransformedLabel] = []
    for label in schematic.labels:
        if get_power_symbol(label.name):
            continue  # handled as power symbol
        transformed_labels.append(TransformedLabel(
            name=label.name,
            x_mm=transform(label.x, x_min, lgu, s),
            y_mm=transform(label.y, y_min, lgu, s),
            orientation=label.orientation,
        ))

    # --- Stage 8: Pin-Wire Alignment ---
    # Wire endpoints are already corrected to KiCad pin positions
    # via lt_to_kicad_pin map in Stage 7. The aligner would mis-snap
    # free wire ends to wrong pins, so we only use it for diagnostics.
    aligned_wires = transformed_wires
    _, align_warnings = align_wires_to_pins(
        transformed_wires, transformed_components,
    )
    # Keep warnings for diagnostics but don't modify wires
    warnings.extend(align_warnings)

    # --- Stage 9: KiCad Export ---
    content = build_kicad_sch(
        components=transformed_components,
        wires=aligned_wires,
        junctions=transformed_junctions,
        labels=transformed_labels,
        title=title,
    )
    write_kicad_sch(content, output_path)

    # --- Stage 10: Validation ---
    validation = validate(
        components=transformed_components,
        wires=aligned_wires,
        junctions=transformed_junctions,
        labels=transformed_labels,
        confirmed_junction_count=len(confirmed_junctions),
        output_path=output_path,
    )

    val_errors = [v for v in validation if v.level == "ERROR"]
    val_warnings = [v for v in validation if v.level == "WARNING"]
    errors.extend(v.message for v in val_errors)
    warnings.extend(v.message for v in val_warnings)

    # Extract nets for reporting
    nets = extract_nets(schematic, lt_pin_positions)

    return RebuildResult(
        success=len(val_errors) == 0,
        output_path=output_path,
        scale_factor=s,
        lgu=lgu,
        component_count=len(transformed_components),
        wire_count=len(aligned_wires),
        net_count=len(nets),
        validation=validation,
        errors=errors,
        warnings=warnings,
    )


def main() -> None:
    """CLI entry point: python main.py input.asc output.kicad_sch [--lib PATH] [--title TEXT]"""
    if len(sys.argv) < 3:
        print("Usage: python main.py <input.asc> <output.kicad_sch> [--lib PATH] [--title TEXT]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    title = ""
    ltspice_lib = ""

    # Simple arg parsing (no external deps)
    args = sys.argv[3:]
    i = 0
    while i < len(args):
        if args[i] == "--lib" and i + 1 < len(args):
            ltspice_lib = args[i + 1]
            i += 2
        elif args[i] == "--title" and i + 1 < len(args):
            title = args[i + 1]
            i += 2
        else:
            # Legacy positional: third arg is title
            if not title:
                title = args[i]
            i += 1

    result = convert_asc_to_kicad(input_path, output_path, title, ltspice_lib)

    # Print report
    report = format_report(result.validation)
    print(report)
    print(f"\nScale factor: {result.scale_factor}, LGU: {result.lgu}")
    print(f"Components: {result.component_count}, Wires: {result.wire_count}, Nets: {result.net_count}")

    if result.warnings:
        print(f"\nWarnings ({len(result.warnings)}):")
        for w in result.warnings:
            print(f"  {w}")

    if result.success:
        print(f"\nOutput: {result.output_path}")
    else:
        print(f"\nFAILED with {len(result.errors)} error(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
