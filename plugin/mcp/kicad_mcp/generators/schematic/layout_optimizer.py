# SPDX-License-Identifier: GPL-3.0-or-later
"""Layout-Optimierer — echte Hill-Climb-Schleife gegen die objektive Metrik.

Der Platzierungs-Pipeline (``place.py``) folgt eine **Such-Schleife**: sie
verschiebt/dreht Bauteile in kleinen Schritten, emittiert nach jedem Schritt den
FERTIGEN Schaltplan neu und misst ihn mit :mod:`layout_measure`. Ein Schritt
wird nur behalten, wenn die ``badness`` (gewichtete Summe aus Überlappungen,
Label-Richtung, Draht-Kreuzungen, Diagonalen, Off-Grid) SINKT — sonst
zurückgerollt. Zielwert ist die 0 der Profi-Referenz-Schaltbilder, an der die
Metrik geeicht ist.

Warum echt und nicht heuristisch: Einzelregeln (mehr Abstand, Label weg vom
Bauteil, …) wirken gegeneinander — mehr Abstand erzeugt längere Drähte und damit
Kreuzungen. Eine Regel für sich lokal zu erfüllen verschlechtert oft das Ganze.
Die Suche optimiert stattdessen die EINE Zahl, die alle Lesbarkeits-Killer
zusammenfasst, und findet so den Kompromiss, den keine Einzelregel trifft.

Die Nachbarschaft (welche Verschiebungen/Drehungen probiert werden) steht als
**wartbare Liste** benannter Operatoren in :data:`OPERATORS` — neue Idee =
neuer Listen-Eintrag, kein Eingriff in den Motor. Jeder Operator SCHLÄGT nur
Kandidaten VOR; Anwenden/Messen/Zurückrollen macht der Treiber zentral.

Alle Verschiebungen sind Vielfache des Rasters (``GRID``) — die Suche hält das
Layout raster- und (über die 100er-Gewichtung von Überlappung in der Metrik)
überlappungsfrei, ohne dass ein separater Aufräum-Schritt die Gewinne wieder
zerstört.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Iterator

from . import layout_measure as lm
from ..common.constants import GRID

# Ein Kandidat ist eine Liste von (part, feld, neuer_wert) — der Treiber wendet
# sie an und misst; besser → behalten, sonst → zurückrollen.
Candidate = list[tuple[dict, str, float]]

#: Felder, die die Platzierung ausmachen (Snapshot/Restore-Einheit).
_STATE_FIELDS = ("_place_x", "_place_y", "_rotation")

_PASSIVE_PREFIXES = ("C", "L", "R")


def _is_flippable_passive(part: dict) -> bool:
    """C/L/R mit genau 2 Pins: Pin 1↔2 tauschbar (180°-Dreh), ohne Bedeutung
    zu ändern — die Nutzer-Regel „bei Kondensatoren/Spulen/Widerständen kann
    Pin 1 und 2 vertauscht werden"."""
    return (part.get("ref", "")[:1] in _PASSIVE_PREFIXES
            and len(part.get("pins", [])) == 2)


# ── Operatoren: schlagen Kandidaten vor (die „~20 Regeln" der Suche) ─────────
# Jeder Operator ist reine Vorschlags-Logik über die aktuell platzierten Teile.
# Konvention: ein Kandidat bewegt/dreht GENAU EIN Bauteil (fein-granular, damit
# der Treiber jeden Effekt einzeln bewerten kann) — Ausnahme ``swap_partners``.

def _translators(dxdy: tuple[int, int]):
    def make(placed, nets, rng) -> Iterator[Candidate]:
        gx, gy = dxdy[0] * GRID, dxdy[1] * GRID
        for p in placed:
            yield [(p, "_place_x", round(p["_place_x"] + gx, 2)),
                   (p, "_place_y", round(p["_place_y"] + gy, 2))]
    return make


def _rotate(delta: int):
    def make(placed, nets, rng) -> Iterator[Candidate]:
        for p in placed:
            if _is_flippable_passive(p) and delta in (90, 270):
                continue  # Passives kippen 180°, nicht 90° (sonst quer)
            yield [(p, "_rotation", (int(p.get("_rotation", 0)) + delta) % 360)]
    return make


def _flip_passives(placed, nets, rng) -> Iterator[Candidate]:
    for p in placed:
        if _is_flippable_passive(p):
            yield [(p, "_rotation", (int(p.get("_rotation", 0)) + 180) % 360)]


def _partner_centroid(part: dict, placed_by_ref: dict, nets: list) -> tuple | None:
    """Schwerpunkt der über Netze verbundenen Nachbarn eines Bauteils."""
    ref = part.get("ref")
    xs, ys = [], []
    for net in nets:
        conns = [c.split(":", 1)[0] for c in net.get("connections", [])]
        if ref not in conns:
            continue
        for other in conns:
            if other != ref and other in placed_by_ref:
                op = placed_by_ref[other]
                xs.append(op["_place_x"]); ys.append(op["_place_y"])
    if not xs:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _pull_to_partners(placed, nets, rng) -> Iterator[Candidate]:
    """Ein Bauteil einen Rasterschritt zum Schwerpunkt seiner Netz-Nachbarn
    ziehen — kürzere Drähte, weniger Kreuzungen (kompaktiert wie ein Mensch)."""
    by_ref = {p["ref"]: p for p in placed}
    for p in placed:
        c = _partner_centroid(p, by_ref, nets)
        if not c:
            continue
        dx = GRID if c[0] > p["_place_x"] + 0.01 else -GRID if c[0] < p["_place_x"] - 0.01 else 0
        dy = GRID if c[1] > p["_place_y"] + 0.01 else -GRID if c[1] < p["_place_y"] - 0.01 else 0
        if dx or dy:
            yield [(p, "_place_x", round(p["_place_x"] + dx, 2)),
                   (p, "_place_y", round(p["_place_y"] + dy, 2))]


def _align_axis(axis: str):
    """Ein Bauteil auf die X- bzw. Y-Koordinate eines seiner Netz-Nachbarn
    setzen — bündige Reihen/Spalten, gerade Drähte (weniger Diagonalen/Jogs)."""
    fld = "_place_x" if axis == "x" else "_place_y"

    def make(placed, nets, rng) -> Iterator[Candidate]:
        by_ref = {p["ref"]: p for p in placed}
        for p in placed:
            ref = p["ref"]
            seen = set()
            for net in nets:
                conns = [c.split(":", 1)[0] for c in net.get("connections", [])]
                if ref not in conns:
                    continue
                for other in conns:
                    if other == ref or other in seen or other not in by_ref:
                        continue
                    seen.add(other)
                    tgt = round(by_ref[other][fld], 2)
                    if abs(tgt - p[fld]) > 0.01:
                        yield [(p, fld, tgt)]
    return make


def _swap_partners(placed, nets, rng) -> Iterator[Candidate]:
    """Zwei über ein Netz verbundene Bauteile ihre Plätze tauschen lassen —
    kann verhedderte Reihenfolgen (Kreuzungen) auf einen Schlag entwirren."""
    by_ref = {p["ref"]: p for p in placed}
    emitted = set()
    for net in nets:
        conns = [c.split(":", 1)[0] for c in net.get("connections", [])]
        uniq = [r for r in dict.fromkeys(conns) if r in by_ref]
        for i, ra in enumerate(uniq):
            for rb in uniq[i + 1:]:
                key = (ra, rb) if ra < rb else (rb, ra)
                if key in emitted:
                    continue
                emitted.add(key)
                a, b = by_ref[ra], by_ref[rb]
                yield [(a, "_place_x", b["_place_x"]), (a, "_place_y", b["_place_y"]),
                       (b, "_place_x", a["_place_x"]), (b, "_place_y", a["_place_y"])]


@dataclass(frozen=True)
class MoveOp:
    key: str
    title: str
    make: Callable[[list, list, random.Random], Iterator[Candidate]]


#: Die wartbare Nachbarschaft der Suche — jeder Eintrag eine benannte
#: Verschiebungs-/Dreh-Idee. Reihenfolge = Probier-Reihenfolge (billige
#: Ein-Schritt-Nudges zuerst, teure Struktur-Operatoren zuletzt).
OPERATORS: list[MoveOp] = [
    MoveOp("nudge_e", "1 Raster nach rechts", _translators((1, 0))),
    MoveOp("nudge_w", "1 Raster nach links", _translators((-1, 0))),
    MoveOp("nudge_n", "1 Raster nach oben", _translators((0, -1))),
    MoveOp("nudge_s", "1 Raster nach unten", _translators((0, 1))),
    MoveOp("shift_e2", "2 Raster nach rechts", _translators((2, 0))),
    MoveOp("shift_w2", "2 Raster nach links", _translators((-2, 0))),
    MoveOp("shift_n2", "2 Raster nach oben", _translators((0, -2))),
    MoveOp("shift_s2", "2 Raster nach unten", _translators((0, 2))),
    MoveOp("hop_ne", "diagonal rechts-oben", _translators((1, -1))),
    MoveOp("hop_nw", "diagonal links-oben", _translators((-1, -1))),
    MoveOp("hop_se", "diagonal rechts-unten", _translators((1, 1))),
    MoveOp("hop_sw", "diagonal links-unten", _translators((-1, 1))),
    MoveOp("rotate_cw", "90° im Uhrzeigersinn drehen", _rotate(90)),
    MoveOp("rotate_ccw", "90° gegen den Uhrzeigersinn", _rotate(270)),
    MoveOp("flip_passive", "Passiv Pin 1↔2 (180°)", _flip_passives),
    MoveOp("align_x", "X auf Netz-Nachbarn ausrichten", _align_axis("x")),
    MoveOp("align_y", "Y auf Netz-Nachbarn ausrichten", _align_axis("y")),
    MoveOp("pull_partners", "zum Nachbar-Schwerpunkt kompaktieren", _pull_to_partners),
    MoveOp("swap_partners", "verbundene Bauteile tauschen", _swap_partners),
    MoveOp("settle", "1 Raster nach oben (2. Runde)", _translators((0, -1))),
]


# ── Snapshot / Restore / Apply ───────────────────────────────────────────────

def _snapshot(parts: list[dict]) -> list[dict]:
    return [{f: p.get(f) for f in _STATE_FIELDS} for p in parts]


def _restore(parts: list[dict], snap: list[dict]) -> None:
    for p, s in zip(parts, snap):
        for f in _STATE_FIELDS:
            v = s[f]
            if v is None:
                p.pop(f, None)
            else:
                p[f] = v


def _apply(cand: Candidate) -> None:
    for part, field, value in cand:
        part[field] = value


def _kick(placed: list[dict], rng: random.Random) -> None:
    """Zufalls-Störung für den Neustart: ein paar Teile um 1–2 Raster
    versetzen, um aus einem lokalen Minimum zu springen."""
    k = max(1, len(placed) // 3)
    for p in rng.sample(placed, min(k, len(placed))):
        p["_place_x"] = round(p["_place_x"] + rng.choice((-2, -1, 1, 2)) * GRID, 2)
        p["_place_y"] = round(p["_place_y"] + rng.choice((-2, -1, 1, 2)) * GRID, 2)


# ── Treiber ──────────────────────────────────────────────────────────────────

def optimize(
    parts: list[dict],
    nets: list[dict],
    emit_fn: Callable[[], str],
    *,
    weights: dict | None = None,
    max_evals: int = 1500,
    restarts: int = 1,
    seed: int = 0,
) -> dict:
    """Verbessere die Platzierung per Hill-Climb gegen ``layout_measure.badness``.

    ``emit_fn`` muss den FERTIGEN Schaltplan aus der AKTUELLEN Platzierung der
    ``parts`` neu emittieren, OHNE neu zu platzieren (sonst überschreibt die
    Pipeline die Such-Schritte) — typisch
    ``lambda: build_schematic(parts, nets, ..., place=False, keep_placement=True)``.

    Mutiert die ``_place_*``-Felder der ``parts`` in-place auf das beste
    gefundene Layout. Gibt ``{start, badness, evals, improved}`` zurück.

    Use this when a generated schematic still shows crossings, labels pointing
    into neighbours, or a residual overlap: it searches real placements and
    keeps only measured improvements. It never makes the metric worse than the
    input (das Eingangs-Layout ist die Untergrenze).
    """
    placed = [p for p in parts if "_place_x" in p]
    if len(placed) < 2:
        return {"start": 0.0, "badness": 0.0, "evals": 0, "improved": False}

    rng = random.Random(seed)

    def fit() -> float:
        return lm.measure_text(emit_fn()).badness(weights)

    start_bad = fit()
    evals = 1
    best_snap = _snapshot(parts)
    best_bad = start_bad
    gbest_snap, gbest_bad = best_snap, best_bad

    for r in range(restarts + 1):
        if r > 0:
            _restore(parts, gbest_snap)
            _kick(placed, rng)
            best_snap = _snapshot(parts)
            best_bad = fit()
            evals += 1

        improving = True
        while improving and evals < max_evals and best_bad > 0:
            improving = False
            for op in OPERATORS:
                for cand in op.make(placed, nets, rng):
                    if evals >= max_evals:
                        break
                    _apply(cand)
                    b = fit()
                    evals += 1
                    if b < best_bad - 1e-6:
                        best_bad = b
                        best_snap = _snapshot(parts)
                        improving = True
                    else:
                        _restore(parts, best_snap)
                if evals >= max_evals or best_bad <= 0:
                    break

        if best_bad < gbest_bad:
            gbest_bad, gbest_snap = best_bad, best_snap
        if gbest_bad <= 0:
            break

    _restore(parts, gbest_snap)
    return {
        "start": round(start_bad, 2),
        "badness": round(gbest_bad, 2),
        "evals": evals,
        "improved": gbest_bad < start_bad - 1e-6,
    }
