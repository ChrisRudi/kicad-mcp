# SPDX-License-Identifier: GPL-3.0-or-later
# symbols.py
"""Symbol inventory extraction from .asy (LTspice) and .kicad_sym (KiCad) files."""
from __future__ import annotations

import os
import re

from kicad_mcp.generators.ltspice2kicad.models import Pin, PinVector, SymbolMeta


def _compute_pin_vectors(pins: list[Pin]) -> list[PinVector]:
    """Compute distance vectors between all pin pairs."""
    vectors: list[PinVector] = []
    for i, a in enumerate(pins):
        for b in pins[i + 1:]:
            vectors.append(PinVector(
                pin_a=a.name, pin_b=b.name,
                dx=b.x - a.x, dy=b.y - a.y,
            ))
    return vectors


def _compute_offsets(
    pins: list[Pin], origin_x: int, origin_y: int, width: int, height: int,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Compute pin_1_offset and centroid_offset (diagnostic only)."""
    pin_1_offset = (0, 0)
    if pins:
        pin_1_offset = (pins[0].x - origin_x, pins[0].y - origin_y)

    centroid_x = origin_x + width // 2
    centroid_y = origin_y + height // 2
    centroid_offset = (centroid_x - origin_x, centroid_y - origin_y)

    return pin_1_offset, centroid_offset


def parse_asy(filepath: str) -> SymbolMeta | None:
    """Parse an LTspice .asy symbol file and extract metadata.

    Reads with Windows-1252 encoding.
    """
    pins: list[Pin] = []
    lines_geom: list[tuple[int, int, int, int]] = []
    rects: list[tuple[int, int, int, int]] = []
    origin_x, origin_y = 0, 0
    name = os.path.splitext(os.path.basename(filepath))[0]

    try:
        with open(filepath, encoding="cp1252", errors="replace") as f:
            for raw_line in f:
                line = raw_line.strip()
                parts = line.split()
                if not parts:
                    continue

                # PIN / PINATTR are handled in the second pass below; first
                # pass only collects geometry (LINE / RECTANGLE).
                if parts[0] == "LINE" and len(parts) >= 5:
                    try:
                        lines_geom.append((
                            int(parts[1]), int(parts[2]),
                            int(parts[3]), int(parts[4]),
                        ))
                    except (ValueError, IndexError):
                        # fehlerhafte LINE-Koordinaten — Geometrie-Zeile überspringen
                        pass

                elif parts[0] == "RECTANGLE" and len(parts) >= 5:
                    try:
                        rects.append((
                            int(parts[1]), int(parts[2]),
                            int(parts[3]), int(parts[4]),
                        ))
                    except (ValueError, IndexError):
                        # fehlerhafte RECTANGLE-Koordinaten — Geometrie-Zeile überspringen
                        pass

    except (OSError, IOError):
        return None

    # Second pass: properly extract pins
    pins = []
    try:
        with open(filepath, encoding="cp1252", errors="replace") as f:
            pending_pin: dict | None = None
            for raw_line in f:
                line = raw_line.strip()
                parts = line.split()
                if not parts:
                    continue

                if parts[0] == "PIN" and len(parts) >= 4:
                    # Save previous pending pin
                    if pending_pin:
                        pins.append(Pin(**pending_pin))

                    orient_map = {"TOP": 270, "BOTTOM": 90,
                                  "LEFT": 180, "RIGHT": 0,
                                  "VLEFT": 270, "VRIGHT": 90}
                    orient_str = parts[3] if len(parts) >= 4 else "RIGHT"
                    pending_pin = {
                        "name": str(len(pins) + 1),
                        "number": str(len(pins) + 1),
                        "x": int(parts[1]),
                        "y": int(parts[2]),
                        "orientation": orient_map.get(orient_str, 0),
                    }

                elif parts[0] == "PINATTR" and pending_pin is not None and len(parts) >= 3:
                    # pylint: disable=unsupported-assignment-operation  # narrowed above
                    if parts[1] == "PinName":
                        pending_pin["name"] = parts[2]
                    elif parts[1] == "SpiceOrder":
                        pending_pin["number"] = parts[2]

            # Don't forget the last pin
            if pending_pin:
                pins.append(Pin(**pending_pin))

    except (OSError, IOError):
        return None

    # Compute bounding box
    all_x = [p.x for p in pins]
    all_y = [p.y for p in pins]
    for x1, y1, x2, y2 in lines_geom:
        all_x.extend([x1, x2])
        all_y.extend([y1, y2])
    for x1, y1, x2, y2 in rects:
        all_x.extend([x1, x2])
        all_y.extend([y1, y2])

    if not all_x:
        return None

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    width = max_x - min_x
    height = max_y - min_y

    pin_1_offset, centroid_offset = _compute_offsets(
        pins, origin_x, origin_y, width, height,
    )

    return SymbolMeta(
        name=name,
        width=width,
        height=height,
        origin_x=origin_x,
        origin_y=origin_y,
        pin_1_offset=pin_1_offset,
        centroid_offset=centroid_offset,
        pins=pins,
        pin_vectors=_compute_pin_vectors(pins),
    )


def parse_kicad_sym_entry(name: str, sexpr_text: str) -> SymbolMeta | None:
    """Parse a single KiCad symbol from .kicad_sym S-expression text.

    Extracts pin positions, bounding box, and origin.
    KiCad coordinates are in mm; we convert to integer mils (x1000)
    for consistent internal representation, then back to mm in builder.
    """
    pins: list[Pin] = []

    # KiCad 10 uses multi-line pin format with nested S-expressions:
    #   (pin passive line
    #       (at 0 3.81 270)
    #       (length 1.27)
    #       (name "..." (effects ...))
    #       (number "D" (effects ...))
    #   )
    # Use balanced-parenthesis extraction to get the full pin block.
    at_pattern = re.compile(r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)')
    name_pattern = re.compile(r'\(name\s+"([^"]*)"')
    number_pattern = re.compile(r'\(number\s+"([^"]*)"')

    def _extract_pin_blocks(text: str) -> list[str]:
        """Extract top-level (pin ...) blocks using balanced parentheses."""
        blocks = []
        i = 0
        while i < len(text):
            idx = text.find('(pin ', i)
            if idx < 0:
                break
            # Check it's a real pin definition (not pin_numbers etc.)
            rest = text[idx + 5:]
            if not rest or not rest.lstrip()[:1].isalpha():
                i = idx + 5
                continue
            # Check for pin type + style pattern
            m = re.match(r'\w+\s+\w+', rest.lstrip())
            if not m:
                i = idx + 5
                continue
            # Extract balanced block
            depth = 0
            end = idx
            for j in range(idx, len(text)):
                if text[j] == '(':
                    depth += 1
                elif text[j] == ')':
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
            blocks.append(text[idx:end])
            i = end
        return blocks

    for block in _extract_pin_blocks(sexpr_text):

        at_m = at_pattern.search(block)
        if not at_m:
            continue

        px = float(at_m.group(1))
        py = float(at_m.group(2))
        angle = float(at_m.group(3)) if at_m.group(3) else 0.0

        name_m = name_pattern.search(block)
        number_m = number_pattern.search(block)

        pin_name = name_m.group(1) if name_m else ""
        pin_number = number_m.group(1) if number_m else str(len(pins) + 1)

        pins.append(Pin(
            name=pin_name,
            number=pin_number,
            x=round(px * 1000),  # mm -> mils*1000 for integer math
            y=round(py * 1000),
            orientation=round(angle) % 360,
        ))

    if not pins:
        return None

    all_x = [p.x for p in pins]
    all_y = [p.y for p in pins]
    width = max(all_x) - min(all_x) if all_x else 0
    height = max(all_y) - min(all_y) if all_y else 0

    pin_1_offset, centroid_offset = _compute_offsets(
        pins, 0, 0, width, height,
    )

    return SymbolMeta(
        name=name,
        width=width,
        height=height,
        origin_x=0,
        origin_y=0,
        pin_1_offset=pin_1_offset,
        centroid_offset=centroid_offset,
        pins=pins,
        pin_vectors=_compute_pin_vectors(pins),
    )
