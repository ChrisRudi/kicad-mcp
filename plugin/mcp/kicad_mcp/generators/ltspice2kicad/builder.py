# SPDX-License-Identifier: GPL-3.0-or-later
# builder.py
"""KiCad .kicad_sch builder using MCP symbol_cache for real lib_symbols."""
from __future__ import annotations

import json
import os

from kicad_mcp.generators.sexpr import SExpr, uid, KICAD_SCH_VERSION, FONT_SIZE
from kicad_mcp.generators.symbol_cache import get_real_symbol
from kicad_mcp.generators.ltspice2kicad.models import (
    TransformedComponent,
    TransformedJunction,
    TransformedLabel,
    TransformedWire,
)


def _fmt(v: float) -> str:
    """Format mm value: max 4 decimal places, strip trailing zeros."""
    s = f"{v:.4f}"
    s = s.rstrip("0").rstrip(".")
    return s


def _indent_block(text: str, indent_level: int) -> str:
    """Indent a multi-line S-expression block."""
    prefix = "  " * indent_level
    lines = text.split("\n")
    return "\n".join(prefix + line if line.strip() else "" for line in lines)


def build_kicad_sch(
    components: list[TransformedComponent],
    wires: list[TransformedWire],
    junctions: list[TransformedJunction],
    labels: list[TransformedLabel],
    title: str = "LTspice Import",
    paper: str = "A3",
) -> str:
    """Build complete .kicad_sch using real KiCad symbols from libraries.

    All coordinates must already be in mm on KiCad grid.
    """
    s = SExpr()

    # Header
    s.open("kicad_sch")
    s.prop("version", KICAD_SCH_VERSION)
    s.prop_quoted("generator", "ltspice2kicad-rebuilder")
    s.prop_quoted("generator_version", "1.0")
    s.emit(f'(uuid "{uid(title + "_sch")}")')
    s.prop_quoted("paper", paper)
    s.blank()

    # Title block
    s.emit(f'(title_block (title "{_escape(title)}"))')
    s.blank()

    # Lib symbols — embed REAL symbols from KiCad libraries
    _emit_lib_symbols(s, components)
    s.blank()

    # Wires
    for wire in wires:
        s.emit(
            f"(wire (pts"
            f" (xy {_fmt(wire.x1_mm)} {_fmt(wire.y1_mm)})"
            f" (xy {_fmt(wire.x2_mm)} {_fmt(wire.y2_mm)}))"
            f' (stroke (width 0) (type default))'
            f' (uuid "{uid(f"w_{wire.x1_mm}_{wire.y1_mm}_{wire.x2_mm}_{wire.y2_mm}")}"))'
        )
    s.blank()

    # Junctions (only topology-confirmed)
    for junc in junctions:
        s.emit(
            f"(junction (at {_fmt(junc.x_mm)} {_fmt(junc.y_mm)})"
            f' (diameter 0) (color 0 0 0 0)'
            f' (uuid "{uid(f"j_{junc.x_mm}_{junc.y_mm}")}"))'
        )
    s.blank()

    # Net labels (non-power)
    for label in labels:
        if label.name == "0" or label.name.upper() in ("GND", "VCC", "VDD"):
            continue  # handled as power symbols
        s.emit(
            f'(label "{_escape(label.name)}"'
            f" (at {_fmt(label.x_mm)} {_fmt(label.y_mm)} {label.orientation})"
            f" (effects (font (size {FONT_SIZE} {FONT_SIZE})))"
            f' (uuid "{uid(f"l_{label.name}_{label.x_mm}_{label.y_mm}")}"))'
        )
    s.blank()

    # Component instances
    for comp in components:
        _emit_component(s, comp, title)
    s.blank()

    # No-connect flags
    for comp in components:
        for nc_pin in comp.nc_pins:
            for px, py, pnum in comp.pins_abs:
                if pnum == nc_pin:
                    s.emit(
                        f"(no_connect (at {_fmt(px)} {_fmt(py)})"
                        f' (uuid "{uid(f"nc_{comp.id}_{nc_pin}")}"))'
                    )
                    break

    # Sheet instances
    s.open("sheet_instances")
    s.emit('(path "/" (page "1"))')
    s.close()

    # Symbol instances
    s.open("symbol_instances")
    for comp in components:
        ref = comp.reference
        if comp.is_power and not ref.startswith("#"):
            ref = f"#{ref}"
        comp_uid = uid(f"sym_{comp.id}_{title}")
        s.emit(f'(path "/{comp_uid}" (reference "{_escape(ref)}") (unit 1))')
    s.close()

    s.close()  # kicad_sch
    return s.render()


def _emit_lib_symbols(
    s: SExpr,
    components: list[TransformedComponent],
) -> None:
    """Emit lib_symbols with real KiCad symbol definitions."""
    s.open("lib_symbols")
    seen: set[str] = set()

    for comp in components:
        lib_id = comp.kicad_symbol
        if lib_id in seen or not lib_id:
            continue
        seen.add(lib_id)

        real_sym = get_real_symbol(lib_id)
        if real_sym:
            indented = _indent_block(real_sym, s._indent)
            s._lines.append(indented)
        else:
            # Fallback: minimal placeholder
            _emit_placeholder(s, comp)

    s.close()


def _emit_placeholder(s: SExpr, comp: TransformedComponent) -> None:
    """Emit a minimal placeholder symbol when real symbol not found."""
    lib_id = comp.kicad_symbol
    base_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id

    s.open("symbol", f'"{lib_id}"')
    if comp.is_power:
        s.emit('(in_bom no) (on_board no)')
        s.emit(f'(property "Reference" "#PWR" (at 0 0 0) (hide yes)'
               f' (effects (font (size {FONT_SIZE} {FONT_SIZE}))))')
        s.emit(f'(property "Value" "{_escape(comp.value)}" (at 0 -1.5 0)'
               f' (effects (font (size {FONT_SIZE} {FONT_SIZE}))))')
    else:
        s.emit('(in_bom yes) (on_board yes)')
        s.emit(f'(property "Reference" "{comp.reference[0] if comp.reference else "U"}"'
               f' (at 0 0 0) (effects (font (size {FONT_SIZE} {FONT_SIZE}))))')
        s.emit(f'(property "Value" "{_escape(base_name)}" (at 0 -2 0)'
               f' (effects (font (size {FONT_SIZE} {FONT_SIZE}))))')

    s.emit(f'(property "Footprint" "{_escape(comp.footprint)}"'
           f' (at 0 0 0) (hide yes) (effects (font (size {FONT_SIZE} {FONT_SIZE}))))')
    s.emit(f'(property "Datasheet" "" (at 0 0 0) (hide yes)'
           f' (effects (font (size {FONT_SIZE} {FONT_SIZE}))))')

    # Unit with pins
    s.open("symbol", f'"{base_name}_0_1"')
    for px, py, pnum in comp.pins_abs:
        rpx = px - comp.x_mm
        rpy = py - comp.y_mm
        s.emit(f'(pin passive line (at {_fmt(rpx)} {_fmt(rpy)} 0) (length 0)'
               f' (name "{pnum}" (effects (font (size {FONT_SIZE} {FONT_SIZE}))))'
               f' (number "{pnum}" (effects (font (size {FONT_SIZE} {FONT_SIZE})))))')
    s.close()  # sub-symbol
    s.close()  # symbol


def _emit_component(
    s: SExpr,
    comp: TransformedComponent,
    title: str,
) -> None:
    """Emit a single component (symbol instance) as S-expression."""
    cx = _fmt(comp.x_mm)
    cy = _fmt(comp.y_mm)
    rot = comp.rotation
    comp_uid = uid(f"sym_{comp.id}_{title}")

    s.open("symbol")
    s.emit(f'(lib_id "{comp.kicad_symbol}")')
    s.emit(f"(at {cx} {cy} {rot})")
    s.emit("(unit 1)")

    if comp.mirror:
        # LTspice `mirror=true` is a horizontal flip = X-negation in the
        # symbol's local frame. KiCad calls that mirror-about-the-Y-axis,
        # i.e. ``(mirror y)`` per ``sch_geometry.py`` and the KiCad source
        # (`(mirror x)` would be a vertical flip = Y-negation, which is
        # **not** what LTspice's mirror means). Emitting ``(mirror x)``
        # here used to leave the rendered symbol mirrored about the
        # wrong axis, so wires/labels routed to the pre-flip pin set
        # ended up on the wrong side of the symbol.
        s.emit("(mirror y)")

    bom = "no" if comp.is_power else "yes"
    s.emit(f"(in_bom {bom}) (on_board {bom})")
    s.emit(f'(uuid "{comp_uid}")')

    # Properties
    ref = comp.reference
    if comp.is_power and not ref.startswith("#"):
        ref = f"#{ref}"

    ref_x = comp.x_mm + 3
    ref_y = comp.y_mm - 4
    val_y = comp.y_mm + 4

    hide_ref = " (hide yes)" if comp.is_power else ""
    s.emit(f'(property "Reference" "{_escape(ref)}"'
           f" (at {_fmt(ref_x)} {_fmt(ref_y)} 0){hide_ref}"
           f" (effects (font (size {FONT_SIZE} {FONT_SIZE}))))")
    s.emit(f'(property "Value" "{_escape(comp.value)}"'
           f" (at {_fmt(ref_x)} {_fmt(val_y)} 0)"
           f" (effects (font (size {FONT_SIZE} {FONT_SIZE}))))")
    s.emit(f'(property "Footprint" "{_escape(comp.footprint)}"'
           f" (at {cx} {cy} 0) (hide yes)"
           f" (effects (font (size {FONT_SIZE} {FONT_SIZE}))))")
    s.emit(f'(property "Datasheet" "" (at {cx} {cy} 0) (hide yes)'
           f" (effects (font (size {FONT_SIZE} {FONT_SIZE}))))")

    s.close()  # symbol


def _escape(s: str) -> str:
    """Escape a string for KiCad S-expression."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def write_kicad_sch(
    content: str,
    output_path: str,
) -> str:
    """Write .kicad_sch file (UTF-8) and companion .kicad_pro.

    Returns the path to the written .kicad_sch file.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Write schematic (UTF-8)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Write minimal project file
    name = os.path.splitext(os.path.basename(output_path))[0]
    pro_path = os.path.join(os.path.dirname(output_path), f"{name}.kicad_pro")
    with open(pro_path, "w", encoding="utf-8") as f:
        json.dump({
            "board": {"active_layer": 0},
            "meta": {"filename": f"{name}.kicad_pro", "version": 1},
        }, f, indent=2)

    return output_path
