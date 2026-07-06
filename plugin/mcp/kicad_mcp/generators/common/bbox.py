# SPDX-License-Identifier: GPL-3.0-or-later
"""
Bounding-box helpers for symbols and footprints.

Extracted from auto_place.py and pcb_builder.py.

Callers:
  - auto_place.py          (_bbox_cache, _get_symbol_bbox, _get_symbol_width, _get_symbol_height)
  - schematic_builder.py   (via auto_place re-export: _get_symbol_bbox)
  - test_generators.py     (via auto_place re-export: _get_symbol_bbox)
  - pcb_builder.py         (_fp_size, _read_courtyard_size, _courtyard_cache)
  - common/geometry.py     (_get_symbol_width, _get_symbol_height)
  - common/fd_refine.py    (_fp_size — fuer PCB)
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
import logging

from .constants import GRID, HALF_GRID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Symbol BBox (from auto_place.py)
# ---------------------------------------------------------------------------

_bbox_cache: dict[str, tuple[float, float]] = {}


def _iter_xy(node: list):
    """Alle ``(xy x y)``-Punkte unterhalb eines ``pts``-Knotens (polyline/arc)."""
    from ...utils.sexpr_parser import find_node
    pts = find_node(node, "pts")
    for pt in (pts or []):
        if isinstance(pt, list) and pt and pt[0] == "xy" and len(pt) >= 3:
            try:
                yield (float(pt[1]), float(pt[2]))
            except (TypeError, ValueError):
                continue


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
                elif node[0] == "circle":
                    c0 = find_node(node, "center")
                    r = find_node(node, "radius")
                    if c0 and r and len(c0) >= 3 and len(r) >= 2:
                        cx, cy, rad = float(c0[1]), float(c0[2]), float(r[1])
                        xs.extend([cx - rad, cx + rad])
                        ys.extend([cy - rad, cy + rad])
                elif node[0] in ("polyline", "arc"):
                    # Kondensatoren/Spulen/Dioden zeichnen ihren Körper als
                    # polyline/arc, NICHT als rectangle — ohne diese Punkte
                    # kollabiert die Bbox auf die Pin-Achse (Breite 0 für C/L)
                    # und die Überlappungsprüfung greift nicht.
                    for pt in _iter_xy(node):
                        xs.append(pt[0])
                        ys.append(pt[1])
                for c in node:
                    if isinstance(c, list):
                        _walk(c)

            _walk(tree)
            if xs and ys:
                # Untergrenze je Achse: ein Symbol ist nie 0 breit/hoch (sonst
                # „unsichtbare" Bauteile, die andere durchdringen dürfen).
                w = max(max(xs) - min(xs), GRID)
                h = max(max(ys) - min(ys), GRID)
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

    Elementgenau über den S-Expression-Parser: NUR Grafik-Elemente, deren
    eigener ``(layer …)`` auf ``*.CrtYd`` liegt, zählen zur Bbox. Die alte
    Regex-Fassung spannte lazy ÜBER Element-Grenzen („irgendein fp_poly,
    hinter dem irgendwo F.CrtYd steht") und las so beim SOIC-8 das
    Pin-1-Dreieck der Silkscreen als Courtyard — 0.48×0.33 mm statt ~6×5:
    das IC war für jede Abstandsrechnung ein Staubkorn, Bauteile wurden
    „kollisionsfrei" mitten auf den Chip gesetzt (Demo-Board-Messlatte).
    Returns (width, height) or None if not found.  Cached.
    """
    if fp_id in _courtyard_cache:
        return _courtyard_cache[fp_id]

    size: tuple[float, float] | None = None
    try:
        from ...utils.sexpr_parser import find_node, parse_sexpr
        from ..footprint_lib import read_kicad_mod
        raw = read_kicad_mod(fp_id)
        if raw:
            xs: list[float] = []
            ys: list[float] = []

            def _walk(node):
                if not isinstance(node, list) or not node:
                    return
                if node[0] in ("fp_line", "fp_rect", "fp_poly",
                               "fp_circle", "fp_arc"):
                    layer = find_node(node, "layer")
                    lname = ""
                    if layer and len(layer) > 1:
                        lname = str(layer[1]).strip('"')
                    if lname.endswith("CrtYd"):
                        for tag in ("start", "mid", "end", "center"):
                            sub = find_node(node, tag)
                            if sub and len(sub) >= 3:
                                xs.append(float(sub[1]))
                                ys.append(float(sub[2]))
                        for x, y in _iter_xy(node):
                            xs.append(x)
                            ys.append(y)
                for child in node:
                    if isinstance(child, list):
                        _walk(child)

            _walk(parse_sexpr(raw))
            if xs and ys:
                size = (max(xs) - min(xs), max(ys) - min(ys))
    except Exception:
        logger.debug("Failed to read courtyard for %s", fp_id)

    _courtyard_cache[fp_id] = size
    return size


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


_pad_pos_cache: dict[str, dict[str, tuple[float, float]]] = {}


def read_footprint_pad_positions(fp_id: str) -> dict[str, tuple[float, float]]:
    """Pad-Offsets (footprint-lokal, mm) aus dem echten ``.kicad_mod``.

    Geteilte Quelle für Builder (Routing-Padliste), Platzierung
    (Entwirren-Luftlinien) und alles Künftige — vorher lebte der Leser im
    Builder und war für die Platzierung unerreichbar (Zirkularimport).
    Returns {pad_num: (rel_x, rel_y)} oder leer; gecacht.
    """
    if fp_id in _pad_pos_cache:
        return _pad_pos_cache[fp_id]
    import re as _re
    result: dict[str, tuple[float, float]] = {}
    try:
        from ..footprint_lib import read_kicad_mod
        raw = read_kicad_mod(fp_id)
        if raw:
            pads = _re.findall(
                r'\(pad "([^"]+)"[^)]*\(at ([\d.-]+) ([\d.-]+)', raw)
            result = {num: (float(x), float(y)) for num, x, y in pads}
    except Exception as exc:
        logger.debug("Pad-Positionen für %s nicht lesbar: %s", fp_id, exc)
    _pad_pos_cache[fp_id] = result
    return result


_pads_full_cache: dict[str, list[dict]] = {}


def read_footprint_pads(fp_id: str) -> list[dict]:
    """ALLE Pads eines Footprints mit Geometrie — das Hindernis-Modell des
    Routers braucht Maße und Loch-Info, nicht nur Positionen.

    Returns Liste von ``{num, x, y, w, h, rot, through}`` (footprint-lokal,
    mm; ``through`` = thru_hole/np_thru_hole → blockiert beide Lagen).
    Leer, wenn der Footprint nicht lesbar ist; gecacht."""
    if fp_id in _pads_full_cache:
        return _pads_full_cache[fp_id]
    import re as _re
    pads: list[dict] = []
    try:
        from ..footprint_lib import read_kicad_mod
        raw = read_kicad_mod(fp_id)
        if raw:
            for m in _re.finditer(
                    r'\(pad\s+"([^"]*)"\s+(smd|thru_hole|np_thru_hole)\s+\S+'
                    r'\s*\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)'
                    r'\s*\(size\s+([-\d.]+)\s+([-\d.]+)\)', raw):
                pads.append({
                    "num": m.group(1),
                    "x": float(m.group(3)),
                    "y": float(m.group(4)),
                    "rot": float(m.group(5) or 0.0),
                    "w": float(m.group(6)),
                    "h": float(m.group(7)),
                    "through": m.group(2) in ("thru_hole", "np_thru_hole"),
                })
    except Exception as exc:
        logger.debug("Pads für %s nicht lesbar: %s", fp_id, exc)
    _pads_full_cache[fp_id] = pads
    return pads
