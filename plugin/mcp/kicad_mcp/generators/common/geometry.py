# SPDX-License-Identifier: GPL-3.0-or-later
"""
Geometry helpers: snap-to-grid, overlap resolution, boundary clamping.

Extracted from auto_place.py.

Callers:
  - auto_place.py          (_snap, _resolve_overlaps — Placement-Pipeline Phase 9)
"""

from .bbox import _get_symbol_height, _get_symbol_width
from .constants import GRID, HALF_GRID


def _snap(val: float, grid: float = HALF_GRID) -> float:
    return round(val / grid) * grid


#: Minimaler Luftspalt zwischen zwei Bauteil-Rahmen (mm). > HALF_GRID, damit
#: das abschließende Grid-Snap den Spalt nie auf Null drückt.
_OVERLAP_MARGIN = 2.0


def _half_extents(part: dict) -> tuple[float, float]:
    """Halbe Breite/Höhe des Symbol-Rahmens, ROTATIONS-BEWUSST.

    Bei 90/270° tauschen Breite und Höhe — sonst prüft die Überlappung die
    falsche Achse und lässt gedrehte Bauteile ineinander stehen (der Bug, der
    kleine Rs im großen IC-Rahmen ließ)."""
    w = _get_symbol_width(part)
    h = _get_symbol_height(part)
    if int(part.get("_rotation", 0)) in (90, 270):
        w, h = h, w
    return w / 2.0, h / 2.0


def _resolve_overlaps(parts: list[dict]) -> bool:
    """Schiebe überlappende Bauteile auseinander (ein Durchgang).

    Trennt entlang der Achse der GERINGSTEN Durchdringung um genau so viel, dass
    ein ``_OVERLAP_MARGIN``-Spalt bleibt — minimale Störung. Rotations-bewusst.
    Gibt True zurück, solange noch etwas geschoben wurde; der Aufrufer wiederholt
    bis nichts mehr bewegt wird → garantiert überlappungsfrei. Wird KEIN Bauteil
    mehr bewegt, überlappt auch keines mehr (dieselbe Rahmen-Definition)."""
    placed = [p for p in parts if "_place_x" in p]
    moved = False
    for i, a in enumerate(placed):
        ahw, ahh = _half_extents(a)
        for b in placed[i + 1:]:
            bhw, bhh = _half_extents(b)
            ax, ay = a["_place_x"], a["_place_y"]
            bx, by = b["_place_x"], b["_place_y"]
            need_x = ahw + bhw + _OVERLAP_MARGIN
            need_y = ahh + bhh + _OVERLAP_MARGIN
            dx, dy = abs(ax - bx), abs(ay - by)
            if dx >= need_x or dy >= need_y:
                continue  # schon getrennt (mind. eine Achse frei)
            pen_x, pen_y = need_x - dx, need_y - dy
            if pen_y <= pen_x:
                b["_place_y"] += pen_y if by >= ay else -pen_y
            else:
                b["_place_x"] += pen_x if bx >= ax else -pen_x
            moved = True
    return moved


def _boxes_overlap(a: dict, b: dict, margin: float = _OVERLAP_MARGIN) -> bool:
    """Überlappen die Rahmen von ``a`` und ``b`` (rotations-bewusst, mit Spalt)?"""
    ahw, ahh = _half_extents(a)
    bhw, bhh = _half_extents(b)
    return (abs(a["_place_x"] - b["_place_x"]) < ahw + bhw + margin
            and abs(a["_place_y"] - b["_place_y"]) < ahh + bhh + margin)


def force_no_overlap(parts: list[dict], max_ring: int = 60) -> None:
    """GARANTIE: nach diesem Aufruf überlappt kein Bauteil mehr.

    Das sanfte ``_resolve_overlaps`` kann bei sehr großen Symbolen (z. B. ein
    volles LQFP-48 mit 81 mm Höhe) oszillieren — ein kleines Bauteil wird aus
    dem IC geschoben, landet im Nachbarn, wird zurückgedrückt. Dieser Finisher
    platziert deterministisch: große Symbole zuerst als Anker (fix), dann jedes
    weitere Bauteil; überlappt es einen bereits fixierten Anker, sucht es
    RINGWEISE nach außen (Grid-Schritte) die nächste freie Zelle. Endet immer
    (endlich viele Teile, unbegrenztes Blatt) und stört die schon guten Layouts
    nicht (überlappt nichts → bleibt, wo es ist). Grid-Schritte → snap-stabil.
    """
    placed = [p for p in parts if "_place_x" in p]
    # Anker-Reihenfolge: größte Fläche zuerst (kleine Teile weichen, nicht ICs).
    placed.sort(key=lambda p: -(_get_symbol_width(p) * _get_symbol_height(p)))
    fixed: list[dict] = []
    for p in placed:
        if not any(_boxes_overlap(p, f) for f in fixed):
            fixed.append(p)
            continue
        ox, oy = p["_place_x"], p["_place_y"]
        done = False
        for ring in range(1, max_ring):
            for dx in range(-ring, ring + 1):
                # nur der Ring-Rand (nicht das Innere doppelt prüfen)
                ys = (-ring, ring) if abs(dx) < ring else range(-ring, ring + 1)
                for dy in ys:
                    p["_place_x"] = ox + dx * GRID
                    p["_place_y"] = oy + dy * GRID
                    if not any(_boxes_overlap(p, f) for f in fixed):
                        done = True
                        break
                if done:
                    break
            if done:
                break
        if not done:  # extrem unwahrscheinlich — zur Sicherheit weit weg
            p["_place_x"] = ox + max_ring * GRID
            p["_place_y"] = oy
        fixed.append(p)


def clamp_to_bounds(
    x: float, y: float, w: float, h: float,
    bounds: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Clamp position inside bounds (x_min, y_min, x_max, y_max)."""
    x_min, y_min, x_max, y_max = bounds
    x = max(x_min + w / 2, min(x, x_max - w / 2))
    y = max(y_min + h / 2, min(y, y_max - h / 2))
    return x, y
