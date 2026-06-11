# SPDX-License-Identifier: GPL-3.0-or-later
"""Author a complete KiCad library symbol (.kicad_sym entry) from a pin spec.

Generates a standard rectangular-IC symbol: a body rectangle plus pins laid
out on the requested sides (left / right / top / bottom), evenly pitched and
centred. The output is a depth-balanced ``(symbol "Name" …)`` block ready to
embed in a ``kicad_symbol_lib`` file or in a schematic's ``lib_symbols``.

This is the authoring counterpart to ``symbol_cache`` (which *reads* existing
symbols). It exists so an agent can create a custom part via an MCP tool
instead of hand-editing a ``.kicad_sym`` file — hand edits are exactly what
corrupted the iFloat 74HC589 symbol.
"""

from .schematic_patcher import _fmt

# KiCad electrical pin types (the set the file format accepts).
VALID_PIN_TYPES = frozenset({
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector",
    "open_emitter", "no_connect",
})

VALID_SIDES = frozenset({"left", "right", "top", "bottom"})

_PITCH = 2.54        # standard pin pitch (mm)
_PIN_LEN = 2.54      # standard pin length (mm)
_FONT = "(effects (font (size 1.27 1.27)))"


class SymbolSpecError(ValueError):
    """Raised when a pin spec is malformed (bad type, side, missing field)."""


def _norm_pins(pins: list[dict]) -> list[dict]:
    """Validate + normalise the pin spec, auto-assigning missing sides.

    Pins without an explicit ``side`` are split deterministically: the first
    half go down the left edge (top → bottom), the rest up the right edge.
    """
    if not pins:
        raise SymbolSpecError("pins must be a non-empty list")
    out: list[dict] = []
    seen_numbers: set[str] = set()
    for i, p in enumerate(pins):
        if not isinstance(p, dict):
            raise SymbolSpecError(f"pin #{i} is not an object: {p!r}")
        number = str(p.get("number", "")).strip()
        if not number:
            raise SymbolSpecError(f"pin #{i} is missing 'number'")
        if number in seen_numbers:
            raise SymbolSpecError(f"duplicate pin number {number!r}")
        seen_numbers.add(number)
        ptype = str(p.get("type", "passive")).strip() or "passive"
        if ptype not in VALID_PIN_TYPES:
            raise SymbolSpecError(
                f"pin {number!r}: invalid type {ptype!r}; "
                f"valid: {', '.join(sorted(VALID_PIN_TYPES))}"
            )
        side = str(p.get("side", "")).strip().lower()
        if side and side not in VALID_SIDES:
            raise SymbolSpecError(
                f"pin {number!r}: invalid side {side!r}; "
                f"valid: {', '.join(sorted(VALID_SIDES))}"
            )
        out.append({
            "number": number,
            "name": str(p.get("name", "~")).strip() or "~",
            "type": ptype,
            "side": side,
        })

    no_side = [p for p in out if not p["side"]]
    if no_side:
        half = (len(no_side) + 1) // 2
        for j, p in enumerate(no_side):
            p["side"] = "left" if j < half else "right"
    return out


def _layout(pins: list[dict], width_mm: float) -> tuple[float, float, list[dict]]:
    """Compute body half-width/half-height and per-pin (x, y, angle).

    Coordinates are KiCad library coordinates (Y-up). Pin ``(at)`` is the
    connection endpoint; the pin body runs ``_PIN_LEN`` toward the rectangle.
    """
    sides = {s: [p for p in pins if p["side"] == s] for s in VALID_SIDES}

    n_vert = max(len(sides["left"]), len(sides["right"]), 1)
    n_horiz = max(len(sides["top"]), len(sides["bottom"]), 0)

    half_h = max(((n_vert - 1) * _PITCH) / 2 + _PITCH, _PITCH)

    if width_mm and width_mm > 0:
        half_w = width_mm / 2
    else:
        longest = 0
        for p in sides["left"] + sides["right"]:
            longest = max(longest, len(p["name"]))
        # ~1.0 mm per glyph + margin, with sane floor.
        half_w = max(_PITCH, (longest * 1.0) / 2 + _PITCH)
    # Make sure top/bottom pin rows fit within the body width.
    if n_horiz:
        half_w = max(half_w, ((n_horiz - 1) * _PITCH) / 2 + _PITCH)

    placed: list[dict] = []

    def _stack_vertical(side_pins, x, angle):
        n = len(side_pins)
        top = ((n - 1) * _PITCH) / 2
        for k, p in enumerate(side_pins):
            placed.append({**p, "x": x, "y": top - k * _PITCH, "angle": angle})

    def _stack_horizontal(side_pins, y, angle):
        n = len(side_pins)
        left = -((n - 1) * _PITCH) / 2
        for k, p in enumerate(side_pins):
            placed.append({**p, "x": left + k * _PITCH, "y": y, "angle": angle})

    _stack_vertical(sides["left"], -(half_w + _PIN_LEN), 0)
    _stack_vertical(sides["right"], half_w + _PIN_LEN, 180)
    _stack_horizontal(sides["top"], half_h + _PIN_LEN, 270)
    _stack_horizontal(sides["bottom"], -(half_h + _PIN_LEN), 90)
    return half_w, half_h, placed


def render_library_symbol(
    name: str,
    pins: list[dict],
    *,
    reference: str = "U",
    value: str = "",
    footprint: str = "",
    datasheet: str = "",
    description: str = "",
    width_mm: float = 0.0,
    indent: int = 1,
) -> str:
    """Render a rectangular-IC ``(symbol "name" …)`` block as text.

    Args:
        name: The symbol name (bare, no ``Lib:`` prefix).
        pins: List of ``{number, name?, type?, side?}``. ``type`` is a KiCad
            pin type (default ``passive``); ``side`` is left/right/top/bottom
            (auto-split left/right when omitted).
        reference: Reference prefix (``"U"``, ``"RN"``, …).
        value: Value field (defaults to ``name``).
        footprint / datasheet / description: optional metadata properties.
        width_mm: Body width override; 0 = auto from pin-name lengths.
        indent: Indent levels (2 spaces each) for the top-level ``(symbol``.

    Returns:
        A newline-terminated, depth-balanced symbol block.
    """
    if not name or not str(name).strip():
        raise SymbolSpecError("symbol name must be non-empty")
    name = str(name).strip()
    value = value or name

    norm = _norm_pins(pins)
    half_w, half_h, placed = _layout(norm, width_mm)

    pad = "  " * indent
    p2 = pad + "  "
    p3 = p2 + "  "

    def prop(key: str, val: str, x: float, y: float, hide: bool) -> list[str]:
        # effects sub-block, then one extra ')' to close the (property.
        if hide:
            eff = '(effects (font (size 1.27 1.27)) (hide yes)))'
        else:
            eff = '(effects (font (size 1.27 1.27))))'
        return [
            f'{p2}(property "{key}" "{val}" (at {_fmt(x)} {_fmt(y)} 0)',
            f'{p3}{eff}',
        ]

    lines: list[str] = [f'{pad}(symbol "{name}"']
    lines.append(f'{p2}(pin_names (offset 1.016))')
    lines.append(f'{p2}(exclude_from_sim no)')
    lines.append(f'{p2}(in_bom yes)')
    lines.append(f'{p2}(on_board yes)')
    lines += prop("Reference", reference, 0, half_h + _PITCH, False)
    lines += prop("Value", value, 0, -(half_h + _PITCH), False)
    lines += prop("Footprint", footprint, 0, 0, True)
    lines += prop("Datasheet", datasheet, 0, 0, True)
    if description:
        lines += prop("Description", description, 0, 0, True)

    # Body graphic unit ("name_0_1").
    lines.append(f'{p2}(symbol "{name}_0_1"')
    lines.append(
        f'{p3}(rectangle (start {_fmt(-half_w)} {_fmt(half_h)}) '
        f'(end {_fmt(half_w)} {_fmt(-half_h)})'
    )
    lines.append(f'{p3}  (stroke (width 0.254) (type default)) '
                 f'(fill (type background)))')
    lines.append(f'{p2})')

    # Pin unit ("name_1_1").
    lines.append(f'{p2}(symbol "{name}_1_1"')
    for p in placed:
        lines.append(
            f'{p3}(pin {p["type"]} line '
            f'(at {_fmt(p["x"])} {_fmt(p["y"])} {p["angle"]}) '
            f'(length {_fmt(_PIN_LEN)})'
        )
        lines.append(f'{p3}  (name "{p["name"]}" {_FONT})')
        lines.append(f'{p3}  (number "{p["number"]}" {_FONT}))')
    lines.append(f'{p2})')

    lines.append(f'{pad})')
    return "\n".join(lines) + "\n"
