# SPDX-License-Identifier: GPL-3.0-or-later
"""
Force-Directed Refinement for schematic and PCB placement.

Extracted from auto_place.py and pcb_builder.py.

Callers:
  - pcb_builder.py         (_fd_pcb_refine — PCB Placement nach _compute_pcb_placement)
"""

import math

from .bbox import _fp_size


def _fd_pcb_refine(
    result: dict[str, tuple[float, float, int]],
    connectivity: dict,
    ref_to_part: dict,
    parts: list[dict],
    x_min: float, y_min: float, x_max: float, y_max: float,
    occupied: list,
    extra_fixed: set | None = None,
) -> None:
    """Force-directed refinement to minimize total wire length (PCB).

    Gently moves components toward their connected neighbors while
    respecting board boundaries and avoiding overlaps.
    Fixed components: connectors (at board edges).
    """
    from .classify import _classify_component

    fixed = set(extra_fixed or ())   # z. B. Montageloch-Keepouts
    for p in parts:
        g = p.get("_pcb_group", _classify_component(p))
        if g.startswith("connector"):
            fixed.add(p["ref"])

    movable = [ref for ref in result if ref not in fixed]

    MIN_GAP = 2.0

    for iteration in range(60):
        temp = max(0.2, 3.0 * (1.0 - iteration / 60))

        for ref in movable:
            if ref not in result:
                continue
            x, y, rot = result[ref]
            fx, fy = 0.0, 0.0
            w1, h1 = _fp_size(ref_to_part.get(ref, {}))
            if rot in (90, 270):
                # Eigene Rotation tauscht die Ausdehnung — ohne den Swap prüfte
                # die Abstoßung ein liegendes Bauteil mit der stehenden Bbox
                # (Kollisionen an gedrehten Teilen blieben unsichtbar).
                w1, h1 = h1, w1

            # Hard repulsion — MUST NOT overlap (physics!)
            for other_ref in result:
                if other_ref == ref:
                    continue
                ox, oy, orot = result[other_ref]
                dx, dy = x - ox, y - oy
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 0.5:
                    dist = 0.5
                    dx, dy = 1.0, 0.5

                w2, h2 = _fp_size(ref_to_part.get(other_ref, {}))
                if orot in (90, 270):
                    w2, h2 = h2, w2
                # Axis-aligned overlap check (not circular)
                min_dx = (w1 + w2) / 2 + MIN_GAP
                min_dy = (h1 + h2) / 2 + MIN_GAP

                overlap_x = min_dx - abs(dx) if abs(dx) < min_dx else 0
                overlap_y = min_dy - abs(dy) if abs(dy) < min_dy else 0

                if overlap_x > 0 and overlap_y > 0:
                    # Actual overlap! Strong push
                    push = max(overlap_x, overlap_y) + 2.0
                    fx += push * (1 if dx >= 0 else -1) * 15.0
                    fy += push * (1 if dy >= 0 else -1) * 15.0
                elif dist < (w1 + w2 + h1 + h2) / 2:
                    # Close but not overlapping — gentle spread
                    force = 10.0 / max(dist, 1)
                    fx += force * dx / dist
                    fy += force * dy / dist

            # Gentle attraction to connected components (weaker than repulsion)
            for nb, _ in connectivity.get(ref, []):
                if nb not in result:
                    continue
                nx, ny, _ = result[nb]
                dx, dy = nx - x, ny - y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 2:
                    continue
                # Only attract if farther than needed — don't pull into overlap
                w2, h2 = _fp_size(ref_to_part.get(nb, {}))
                min_dist = (max(w1, h1) + max(w2, h2)) / 2 + MIN_GAP
                if dist > min_dist * 1.5:
                    force = 0.05 * (dist - min_dist)
                    fx += force * dx / dist
                    fy += force * dy / dist

            disp = math.sqrt(fx * fx + fy * fy)
            if disp > 0.1:
                scale = min(disp, temp) / disp
                new_x = x + fx * scale
                new_y = y + fy * scale
                new_x = max(x_min + 5, min(x_max - 5, new_x))
                new_y = max(y_min + 5, min(y_max - 5, new_y))
                result[ref] = (round(new_x, 2), round(new_y, 2), rot)


def _resolve_pcb_overlaps(
    result: dict[str, tuple[float, float, int]],
    ref_to_part: dict,
    x_min: float, y_min: float, x_max: float, y_max: float,
    fixed: set | None = None,
    min_gap: float = 2.0,
    passes: int = 25,
) -> int:
    """Deterministischer Hart-Entzerrer NACH der Kräfte-Physik.

    Die Force-Directed-Verfeinerung kann mit Rest-Überlappungen auslaufen:
    ihre Schrittweite ist auf ``temp`` (→ 0.2 mm) gedeckelt, eine späte
    Kollision über mehrere mm ist damit unentrinnbar — genau die C3-auf-U1-
    Bilder der Demo-Boards (Messlatte: 105× hole_clearance, Bauteil-Shorts).
    Dieser Pass schiebt jedes kollidierende Paar entlang der Achse der
    geringsten Durchdringung symmetrisch auseinander (Fixe — Stecker an der
    Kante — bleiben stehen), bis nichts mehr überlappt oder ``passes``
    erschöpft ist. Deterministisch (sortierte Ref-Reihenfolge, keine
    Zufälle). Gibt die Zahl der VERBLEIBENDEN Überlappungen zurück (0 = gut).
    """
    fixed = fixed or set()
    refs = sorted(result)

    def _size(ref: str) -> tuple[float, float]:
        w, h = _fp_size(ref_to_part.get(ref, {}))
        if result[ref][2] in (90, 270):
            w, h = h, w
        return w, h

    def _clamp(ref: str, x: float, y: float) -> tuple[float, float]:
        w, h = _size(ref)
        return (max(x_min + w / 2, min(x, x_max - w / 2)),
                max(y_min + h / 2, min(y, y_max - h / 2)))

    def _overlaps(a: str, b: str) -> tuple[float, float]:
        """(Durchdringung x, y) — beide > 0 heißt Kollision."""
        ax, ay, _ = result[a]
        bx, by, _ = result[b]
        aw, ah = _size(a)
        bw, bh = _size(b)
        return ((aw + bw) / 2 + min_gap - abs(ax - bx),
                (ah + bh) / 2 + min_gap - abs(ay - by))

    def _separate_single(mov: str, anchor: str) -> None:
        """``mov`` auf die erste Kandidaten-Position schieben, die nach der
        Board-Klemmung WIRKLICH frei von ``anchor`` ist — mit Richtungs- und
        Achswechsel: eine an die Board-Kante geklemmte Bewegung löst nichts
        (der naive Schub drückte dann jeden Pass erneut gegen die Wand)."""
        mx, my, mrot = result[mov]
        ancx, ancy, _ = result[anchor]
        mw, mh = _size(mov)
        aw2, ah2 = _size(anchor)
        need_x = (mw + aw2) / 2 + min_gap + 0.1
        need_y = (mh + ah2) / 2 + min_gap + 0.1
        sx = 1.0 if mx >= ancx else -1.0
        sy = 1.0 if my >= ancy else -1.0
        ox, oy = _overlaps(mov, anchor)
        cands = [(ancx + need_x * sx, my), (ancx - need_x * sx, my),
                 (mx, ancy + need_y * sy), (mx, ancy - need_y * sy)]
        if oy < ox:  # y-Achse ist der kürzere Weg → zuerst probieren
            cands = cands[2:] + cands[:2]
        for cand_x, cand_y in cands:
            new_x, new_y = _clamp(mov, cand_x, cand_y)
            result[mov] = (new_x, new_y, mrot)
            nox, noy = _overlaps(mov, anchor)
            if nox <= 0 or noy <= 0:
                return
        result[mov] = (mx, my, mrot)  # nichts frei → zurück (nächster Pass)

    remaining = 0
    for _ in range(passes):
        remaining = 0
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                if a in fixed and b in fixed:
                    continue
                ox, oy = _overlaps(a, b)
                if ox <= 0 or oy <= 0:
                    continue
                remaining += 1
                if a in fixed:
                    _separate_single(b, a)
                    continue
                if b in fixed:
                    _separate_single(a, b)
                    continue
                # beide beweglich: symmetrisch entlang der Achse der
                # geringsten Durchdringung; deckungsgleiche Zentren
                # deterministisch nach Ref-Ordnung trennen
                ax, ay, arot = result[a]
                bx, by, brot = result[b]
                sx = 1.0 if ax >= bx else -1.0
                sy = 1.0 if ay >= by else -1.0
                if abs(ax - bx) < 1e-9 and abs(ay - by) < 1e-9:
                    sx, sy = 1.0, 1.0
                if ox <= oy:
                    d = (ox / 2 + 0.1) * sx
                    result[a] = (*_clamp(a, ax + d, ay), arot)
                    result[b] = (*_clamp(b, bx - d, by), brot)
                else:
                    d = (oy / 2 + 0.1) * sy
                    result[a] = (*_clamp(a, ax, ay + d), arot)
                    result[b] = (*_clamp(b, bx, by - d), brot)
                nox, noy = _overlaps(a, b)
                if nox > 0 and noy > 0:
                    # Board-Kante hat eine Hälfte geklemmt → einseitig lösen
                    _separate_single(a, b)
        if remaining == 0:
            break
    # Runden wie _place (2 Nachkommastellen) — Determinismus über Plattformen
    for ref in refs:
        x, y, rot = result[ref]
        result[ref] = (round(x, 2), round(y, 2), rot)
    return remaining
