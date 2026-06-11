# SPDX-License-Identifier: GPL-3.0-or-later
"""
Bounding-box helpers for symbols and footprints.

Extracted from auto_place.py and pcb_builder.py.

Callers:
  - auto_place.py          (_bbox_cache, _get_symbol_bbox, _get_symbol_width, _get_symbol_height)
  - schematic_builder.py   (via auto_place re-export: _get_symbol_bbox)
  - schematic_scorer.py    (via auto_place re-export: _get_symbol_bbox)
  - test_generators.py     (via auto_place re-export: _get_symbol_bbox)
  - pcb_builder.py         (_fp_size, _read_courtyard_size, _courtyard_cache)
  - common/geometry.py     (_get_symbol_width, _get_symbol_height)
  - common/fd_refine.py    (_fp_size — fuer PCB)
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
import logging
import re

from .constants import GRID, HALF_GRID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Symbol BBox (from auto_place.py)
# ---------------------------------------------------------------------------

_bbox_cache: dict[str, tuple[float, float]] = {}


def _get_symbol_bbox(comp: dict) -> tuple[float, float]:
    from ..symbol_lib import resolve_lib_id
    lib_id = resolve_lib_id(comp)
    if lib_id in _bbox_cache:
        return _bbox_cache[lib_id]
    try:
        from ...utils.sexpr_parser import find_node, parse_sexpr
        from ..symbol_cache import get_real_symbol
        raw = get_real_symbol(lib_id)
        if raw:
            tree = parse_sexpr(raw)
            xs, ys = [], []

            def _walk(node):
                if not isinstance(node, list) or not node:
                    return
                if node[0] == "pin":
                    at = find_node(node, "at")
                    if at and len(at) >= 3:
                        xs.append(float(at[1]))
                        ys.append(float(at[2]))
                elif node[0] in ("rectangle", "rect"):
                    s = find_node(node, "start")
                    e = find_node(node, "end")
                    if s and e:
                        xs.extend([float(s[1]), float(e[1])])
                        ys.extend([float(s[2]), float(e[2])])
                for c in node:
                    if isinstance(c, list):
                        _walk(c)

            _walk(tree)
            if xs and ys:
                w, h = max(xs) - min(xs), max(ys) - min(ys)
                _bbox_cache[lib_id] = (w, h)
                return (w, h)
    except Exception:
        logger.debug("Failed to parse symbol bbox for %s, using fallback", lib_id)
    n = len(comp.get("pins", []))
    h = max(n * HALF_GRID * 2, GRID * 3)
    w = GRID * 4
    _bbox_cache[lib_id] = (w, h)
    return (w, h)


def _get_symbol_width(comp: dict) -> float:
    return _get_symbol_bbox(comp)[0]


def _get_symbol_height(comp: dict) -> float:
    return _get_symbol_bbox(comp)[1]


# ---------------------------------------------------------------------------
# Footprint BBox (from pcb_builder.py)
# ---------------------------------------------------------------------------

_courtyard_cache: dict[str, tuple[float, float] | None] = {}


def _read_courtyard_size(fp_id: str) -> tuple[float, float] | None:
    """Read courtyard bounding box (width, height) from a .kicad_mod file.

    Checks fp_rect and fp_line on F.CrtYd layer.
    Returns (width, height) or None if not found.  Cached.
    """
    if fp_id in _courtyard_cache:
        return _courtyard_cache[fp_id]

    try:
        from ..footprint_lib import read_kicad_mod
        raw = read_kicad_mod(fp_id)
        if raw:
            # Try fp_rect first (KiCad 8+ style)
            rect_m = re.search(
                r'\(fp_rect\s[^)]*\(start ([\d.-]+) ([\d.-]+)\)\s*'
                r'\(end ([\d.-]+) ([\d.-]+)\).*?F\.CrtYd', raw, re.DOTALL)
            if rect_m:
                x1, y1 = float(rect_m.group(1)), float(rect_m.group(2))
                x2, y2 = float(rect_m.group(3)), float(rect_m.group(4))
                size = (abs(x2 - x1), abs(y2 - y1))
                _courtyard_cache[fp_id] = size
                return size

            # Try fp_poly on F.CrtYd (ESP32-WROOM etc. use polygons)
            poly_m = re.search(
                r'\(fp_poly\s(.*?)F\.CrtYd', raw, re.DOTALL)
            if poly_m:
                coords = re.findall(r'\(xy ([\d.-]+) ([\d.-]+)\)', poly_m.group(1))
                if coords:
                    xs = [float(c[0]) for c in coords]
                    ys = [float(c[1]) for c in coords]
                    size = (max(xs) - min(xs), max(ys) - min(ys))
                    _courtyard_cache[fp_id] = size
                    return size

            # Fallback: fp_line on F.CrtYd — collect all start/end coords
            crtyd_lines = re.findall(
                r'\(fp_line\s.*?\"F\.CrtYd\".*?\)', raw, re.DOTALL)
            if crtyd_lines:
                coords = re.findall(
                    r'\((?:start|end) ([\d.-]+) ([\d.-]+)\)',
                    ' '.join(crtyd_lines))
                if coords:
                    xs = [float(c[0]) for c in coords]
                    ys = [float(c[1]) for c in coords]
                    size = (max(xs) - min(xs), max(ys) - min(ys))
                    _courtyard_cache[fp_id] = size
                    return size
    except Exception:
        logger.debug("Failed to read courtyard for %s", fp_id)

    _courtyard_cache[fp_id] = None
    return None


def _fp_size(part: dict) -> tuple[float, float]:
    """Get footprint bounding box — real courtyard if available, else estimate."""
    fp = part.get("footprint", "")
    if fp:
        courtyard = _read_courtyard_size(fp)
        if courtyard:
            return courtyard
    n = len(part.get("pins", []))
    if "DIP-8" in fp:
        return (10.0, 8.0)
    if "DIP" in fp:
        return (10.0, max(8.0, n * 1.27))
    if "Axial" in fp or "R_Axial" in fp:
        return (12.0, 3.0)
    if "Disc" in fp or "Radial" in fp or "CP_Radial" in fp:
        return (6.0, 6.0)
    if "ESP32" in fp or "WROOM" in fp:
        return (20.0, 26.0)
    if "LGA" in fp or "QFN" in fp:
        return (5.0, 5.0)
    if "TO-92" in fp:
        return (5.0, 5.0)
    if "SOT" in fp or "TSOP" in fp:
        return (3.0, 3.0)
    if "Aosong" in fp or "DHT" in fp:
        return (8.0, 14.0)
    if "PinHeader" in fp:
        return (3.0, max(3.0, n * 2.54))
    if "Potentiometer" in fp or "Bourns" in fp:
        return (10.0, 10.0)
    return (max(6.0, n * 1.5), max(6.0, n * 1.5))
