# SPDX-License-Identifier: GPL-3.0-or-later
"""Objektive Qualitäts-Messung eines FERTIGEN Schaltplans (``.kicad_sch``).

DER eine Qualitäts-Richter des Projekts (der Vorgänger ``schematic_scorer``,
der die ``parts/nets``-Platzierung VOR der Emission bewertete, ist ersetzt —
ein Urteil statt zwei): dieses Modul parst das erzeugte ``.kicad_sch`` und
misst, was am Ende wirklich auf dem Blatt steht — inklusive **Labels,
Power-Symbolen und Drähten** und damit auch den größten Lesbarkeits-Killer:
Labels/Bauteile, die ÜBEREINANDER liegen.

Kernnutzen: dieselbe Messung läuft auf UNSEREM Output UND auf echten
Profi-Referenz-Schaltbildern (deren ``.kicad_sch`` wir haben) → direkte,
objektive Distanz „wie weit sind wir vom Profi". Reines Parsen + Geometrie,
kein KiCad nötig (Symbol-Bboxes über die Symbol-Lib, mit Fallback).

Metriken (alles „weniger = besser", 0 = ideal):
    comp_overlaps      Paare überlappender Bauteil-Rahmen
    label_overlaps     Labels, die auf einem Bauteilkörper liegen
    label_wrong_dir    Netz-Labels, die NICHT vom Bauteil weg zeigen
    annot_overlaps     Paare, deren Referenz/Wert-Text (R1/1k) übereinanderliegt
    wire_through_body  Draht-Segmente, die quer durch ein fremdes Bauteil laufen
    wire_crossings     sich kreuzende Draht-Segmente (ohne Junction)
    diag_wires         nicht-orthogonale (diagonale) Draht-Segmente
    offgrid            Elemente nicht auf dem 1.27-mm-Raster
    wirelength_mm      Gesamt-Drahtlänge (Kontext, nicht per se schlecht)
"""

from __future__ import annotations

# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
import math
import re
from dataclasses import dataclass, field

GRID = 1.27

# ── Symbol-Bounding-Box je lib_id (aus der KiCad-Symbol-Lib, gecacht) ────────
_BBOX: dict[tuple[str, int], tuple[float, float]] = {}


def _bbox_for_lib(lib_id: str, n_pins: int = 2) -> tuple[float, float]:
    """(Breite, Höhe) des Symbol-Rahmens in mm — aus der echten Symbol-Lib,
    sonst grober Fallback aus der Pin-Zahl."""
    if (lib_id, n_pins) in _BBOX:
        return _BBOX[(lib_id, n_pins)]
    w = h = 0.0
    _PIN_LEN = 2.54  # Pins ragen ~2.54 mm aus dem Körper
    try:
        from ..symbol_cache import get_real_symbol
        from ...utils.sexpr_parser import find_node, parse_sexpr
        raw = get_real_symbol(lib_id)
        if raw:
            pxs, pys = [], []      # Pin-Enden
            rxs, rys = [], []      # Körper-Graphik (rectangle/polyline)

            def _walk(node):
                if not isinstance(node, list) or not node:
                    return
                if node[0] == "pin":
                    at = find_node(node, "at")
                    if at and len(at) >= 3:
                        pxs.append(float(at[1])); pys.append(float(at[2]))
                elif node[0] == "rectangle":
                    s, e = find_node(node, "start"), find_node(node, "end")
                    if s and e:
                        # .extend (nicht +=): in dieser verschachtelten Closure
                        # rebindet ``rxs += [...]`` die freie Variable → local →
                        # UnboundLocalError → alle Symbole fielen auf die
                        # 2.54×2.54-Fallback-Bbox zurück (Metrik war blind für
                        # Bauteil-Überlappungen außer exakten Stapeln).
                        rxs.extend([float(s[1]), float(e[1])])
                        rys.extend([float(s[2]), float(e[2])])
                elif node[0] == "polyline":
                    for pt in (find_node(node, "pts") or []):
                        if isinstance(pt, list) and pt and pt[0] == "xy" \
                                and len(pt) >= 3:
                            rxs.append(float(pt[1])); rys.append(float(pt[2]))
                for c in node:
                    if isinstance(c, list):
                        _walk(c)
            _walk(parse_sexpr(raw))
            # Körper-Bbox bevorzugen (Rectangle/Polyline = sichtbarer Körper);
            # sonst Pin-Extent, aber um die Pin-Länge geschrumpft (sonst zählen
            # aufeinander zeigende Pins verbundener Teile als Überlappung).
            if rxs and rys:
                w, h = max(rxs) - min(rxs), max(rys) - min(rys)
            elif pxs and pys:
                w = max(max(pxs) - min(pxs) - 2 * _PIN_LEN, GRID)
                h = max(max(pys) - min(pys) - 2 * _PIN_LEN, GRID)
    except Exception:
        # Symbol-Geometrie nicht parsebar → Pin-Zahl-Fallback-Bbox unten
        pass
    if w <= 0 or h <= 0:
        if ":" not in lib_id:
            # lib_id OHNE Doppelpunkt = UNSER Platzhalter, dessen Geometrie
            # wir EXAKT kennen (builder._emit_placeholder_symbol): Breite
            # 2×SYM_HALF_WIDTH, Höhe 2×max(n·FONT, 2·FONT). Der alte Mini-
            # Fallback (2.54 breit) machte die Metrik blind — der Optimierer
            # parkte R2 mitten IM MP1584-Platzhalter, weil badness nichts sah.
            from ..sexpr import FONT_SIZE as _F, SYM_HALF_WIDTH as _HW
            h = 2 * max(n_pins * _F, 2 * _F)
            w = 2 * _HW
        else:
            # Echtes ``Lib:Name``, hier nur nicht ladbar (Profi-Referenzen!)
            # → konservativ klein schätzen, sonst reißt die Eichung.
            h = max(n_pins * GRID, GRID * 2)
            w = GRID * 2
    _BBOX[(lib_id, n_pins)] = (w, h)
    return (w, h)


@dataclass
class _Sym:
    lib_id: str
    x: float
    y: float
    rot: int
    is_power: bool
    n_pins: int = 2

    def half(self) -> tuple[float, float]:
        w, h = _bbox_for_lib(self.lib_id, self.n_pins)
        if self.rot in (90, 270):
            w, h = h, w
        return w / 2.0, h / 2.0


@dataclass
class _Label:
    text: str
    x: float
    y: float
    angle: int


@dataclass
class _Wire:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class Metrics:
    comp_overlaps: int = 0
    crowding: int = 0
    label_overlaps: int = 0
    label_wrong_dir: int = 0
    label_label_overlaps: int = 0
    label_wire_overlaps: int = 0
    annot_overlaps: int = 0
    annot_body_overlaps: int = 0
    wire_through_body: int = 0
    wire_overlaps: int = 0
    wire_crossings: int = 0
    diag_wires: int = 0
    offgrid: int = 0
    wirelength_mm: float = 0.0
    n_symbols: int = 0
    n_labels: int = 0
    n_wires: int = 0
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "comp_overlaps", "crowding", "label_overlaps", "label_wrong_dir",
            "label_label_overlaps", "label_wire_overlaps",
            "annot_overlaps", "annot_body_overlaps",
            "wire_through_body", "wire_overlaps",
            "wire_crossings", "diag_wires", "offgrid",
            "wirelength_mm", "n_symbols", "n_labels", "n_wires")}

    def breakdown(self) -> dict[str, int]:
        """Nur die Verstoß-Zähler ≠ 0 (die badness-Dimensionen, ohne
        Kontextfelder wie Drahtlänge) — für Tool-Results und Reports."""
        return {k: getattr(self, k) for k in _DEFAULT_WEIGHTS
                if getattr(self, k)}

    def badness(self, weights: dict | None = None) -> float:
        """Gewichtete Gesamt-Schlechtigkeit (0 = ideal). Die harten
        Lesbarkeits-Killer (Überlappungen) wiegen am schwersten."""
        w = weights or _DEFAULT_WEIGHTS
        return (w["comp_overlaps"] * self.comp_overlaps
                + w["crowding"] * self.crowding
                + w["label_overlaps"] * self.label_overlaps
                + w["label_wrong_dir"] * self.label_wrong_dir
                + w["label_label_overlaps"] * self.label_label_overlaps
                + w["label_wire_overlaps"] * self.label_wire_overlaps
                + w["annot_overlaps"] * self.annot_overlaps
                + w["annot_body_overlaps"] * self.annot_body_overlaps
                + w["wire_through_body"] * self.wire_through_body
                + w["wire_overlaps"] * self.wire_overlaps
                + w["wire_crossings"] * self.wire_crossings
                + w["diag_wires"] * self.diag_wires
                + w["offgrid"] * self.offgrid)


_DEFAULT_WEIGHTS = {
    "comp_overlaps": 100.0,   # größter Hebel: nichts übereinander
    "crowding": 10.0,         # „mehr Luft lassen": Körper-Spalt < 2.54 mm ist
    #                           Gedränge (Profi-Referenzen: überall ≥ 3 mm)
    "label_overlaps": 100.0,  # dito für Labels
    "label_wrong_dir": 20.0,
    "label_label_overlaps": 25.0,  # zwei Netz-Labels überdecken sich
    "label_wire_overlaps": 22.0,   # Netz-Label liegt über einem (fremden) Draht
    "annot_overlaps": 25.0,   # Referenz/Wert-Text zweier Bauteile übereinander
    "annot_body_overlaps": 25.0,  # Referenz/Wert-Text liegt auf einem FREMDEN
    #                               Bauteilkörper (der „19k/U1/MP1584"-Salat)
    "wire_through_body": 30.0,  # Leitung quer durch ein fremdes Bauteil
    "wire_overlaps": 18.0,    # zwei Leitungen liegen ÜBEREINANDER (kollinear)
    "wire_crossings": 0.0,    # Kreuzungen (X) sind OK (Nutzer-Vorgabe) — weiter
    #                           gemessen/berichtet, aber NICHT bestraft.
    "diag_wires": 15.0,
    "offgrid": 5.0,
}

# ── Parsen ───────────────────────────────────────────────────────────────────
_SYM_RE = re.compile(
    r'\(symbol\s+\(lib_id\s+"([^"]+)"\)\s+\(at\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)\)')
_LABEL_RE = re.compile(
    r'\((?:global_label|label|hierarchical_label)\s+"([^"]+)"\s+'
    r'\(at\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)\)')
# tolerant gegen ein- UND mehrzeiliges (wire (pts (xy ..)(xy ..))) — DOTALL/lazy
_WIRE_RE = re.compile(
    r'\(wire\b.*?\(xy\s+([-\d.]+)\s+([-\d.]+)\).*?\(xy\s+([-\d.]+)\s+([-\d.]+)\)',
    re.DOTALL)

# Kopf einer Referenz/Wert-Property; Position & hide werden aus dem BALANCIERTEN
# Property-Block geholt (das reale Format ist mehrzeilig, mit ``(hide yes)`` NACH
# verschachtelten Klammern — ein simpler Tail-Regex verpasst es).
_PROP_HEAD_RE = re.compile(r'\(property\s+"(Reference|Value)"\s+"([^"]*)"')
_PROP_AT_RE = re.compile(r'\(at\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)\)')

#: geschätzte KiCad-Zeichenbreite (mm) bei Standard-Textgröße 1.27 — konservativ
#: gewählt, sodass die Profi-Referenz-Schaltbilder 0 Annotations-Überlappungen
#: haben (am Goldstandard geeicht), wir aber die echten Kollisionen sehen.
_ANNOT_CHAR_W = 0.6
_ANNOT_LINE_H = 1.27
_ANNOT_MARGIN = 0.3


def _balanced_end(text: str, start: int) -> int:
    """Index knapp hinter der zu ``text[start]=='('`` passenden ``)`` (naiv,
    ohne String-Literale mit Klammern — für Property-Blöcke ausreichend)."""
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _embedded_pin_counts(text: str) -> dict[str, int]:
    """Pin-Zahl je Symbol-DEFINITION aus dem eingebetteten ``lib_symbols``-Block
    des Dokuments. Für unsere Doppelpunkt-losen Platzhalter ist das die einzige
    Quelle der wahren Geometrie (die KiCad-Lib kennt sie nicht) — ohne sie maß
    die Metrik jeden Platzhalter mit der 2-Pin-Fallback-Höhe (5.08 statt z. B.
    20.32 mm beim 8-Pin-MP1584) und war blind für Gedränge/Überdeckung an ihm."""
    counts: dict[str, int] = {}
    start = text.find("(lib_symbols")
    if start < 0:
        return counts
    block = text[start:_balanced_end(text, start)]
    i = block.find('(symbol "', 1)
    while i > 0:
        end = _balanced_end(block, i)
        name_m = re.match(r'\(symbol\s+"([^"]+)"', block[i:end])
        if name_m:
            counts[name_m.group(1)] = block.count("(pin ", i, end)
        i = block.find('(symbol "', end)
    return counts


def _annot_boxes(text: str) -> list[list[tuple[float, float, float, float]]]:
    """Je Symbol-INSTANZ die Boxen der SICHTBAREN Referenz/Wert-Texte
    (cx, cy, w, h) — block-genau über die Symbol-Grenzen geparst; verborgene
    Felder (``(hide yes)`` — Footprint, Power-Ref) zählen nicht.

    Rotations-bewusst: KiCad rendert Property-Text relativ zur Symbol-Rotation
    — der EFFEKTIVE Winkel ist (Symbol-Rot + Property-Winkel). Bei 90/270 steht
    der Text senkrecht (Box hochkant), sonst waagrecht. Anker: ohne
    ``(justify …)``-Token ist KiCad-Text ZENTRIERT (die frühere Annahme „Text
    läuft vom Anker nach rechts" verfehlte den fremden Körper unter dem Text um
    genau die halbe Breite — der „19k/U1/MP1584"-Salat blieb unsichtbar);
    ``left``/``right`` verschieben entsprechend. Die Liste ist index-gleich zu
    den Symbolen aus ``_parse`` (gleiche Regex, gleiche Reihenfolge)."""
    matches = list(_SYM_RE.finditer(text))
    starts = [m.start() for m in matches] + [len(text)]
    syms: list[list[tuple[float, float, float, float]]] = []
    for k, sm in enumerate(matches):
        block = text[starts[k]:starts[k + 1]]
        sym_rot = int(sm.group(4))
        boxes = []
        for hm in _PROP_HEAD_RE.finditer(block):
            val = hm.group(2)
            if not val:
                continue
            pend = _balanced_end(block, hm.start())
            prop = block[hm.start():pend]
            if "(hide yes)" in prop:
                continue
            at = _PROP_AT_RE.search(prop)
            if not at:
                continue
            x, y = float(at.group(1)), float(at.group(2))
            w = max(len(val), 1) * _ANNOT_CHAR_W
            shift = 0.0                       # zentriert (KiCad-Default)
            if "(justify left" in prop:
                shift = w / 2.0               # Text läuft vom Anker weg
            elif "(justify right" in prop:
                shift = -w / 2.0
            eff = (sym_rot + int(at.group(3))) % 360
            if eff in (90, 270):
                # senkrecht: 90 läuft vom Anker nach oben, 270 nach unten
                sgn = -1.0 if eff == 90 else 1.0
                boxes.append((x, y + sgn * shift, _ANNOT_LINE_H, w))
            else:
                # waagrecht (0 wie 180 — KiCad normalisiert auf lesbar)
                boxes.append((x + shift, y, w, _ANNOT_LINE_H))
        syms.append(boxes)
    return syms


def _annot_box_overlap(a, b) -> bool:
    return (abs(a[0] - b[0]) < (a[2] + b[2]) / 2 - _ANNOT_MARGIN
            and abs(a[1] - b[1]) < (a[3] + b[3]) / 2 - _ANNOT_MARGIN)


def _parse(text: str) -> tuple[list[_Sym], list[_Label], list[_Wire]]:
    syms = []
    pin_counts = _embedded_pin_counts(text)
    for m in _SYM_RE.finditer(text):
        lib = m.group(1)
        # „echtes Bauteil?" — semantisch über in_bom/on_board (die kurz nach dem
        # Symbol-Kopf stehen) und über die Referenz. Power-/Flag-Symbole tragen
        # in KiCad IMMER eine Referenz mit führendem ``#`` (``#PWR``/``#FLG``) —
        # der universelle Marker, unabhängig vom lib_id. Profi-Referenzen nutzen
        # eine EIGENE Symbol-Lib (``sallen_key_schlib:GND``) mit ``in_bom yes``;
        # ohne die ``#``-Erkennung zählten deren GND/VDD/VSS als „Bauteil" und
        # ein Draht in ihren Stub als „Draht quer durchs Bauteil" (falsch-positiv,
        # das die Metrik-Eichung auf 0 sprengte).
        tail = text[m.end():m.end() + 500]
        ref_m = re.search(r'\(property\s+"Reference"\s+"([^"]*)"', tail)
        ref_val = ref_m.group(1) if ref_m else ""
        is_power = (lib.startswith("power:") or "PWR_FLAG" in lib
                    or ref_val.startswith("#")
                    or "(in_bom no)" in tail or "(on_board no)" in tail)
        syms.append(_Sym(lib, float(m.group(2)), float(m.group(3)),
                         int(m.group(4)), is_power,
                         n_pins=max(pin_counts.get(lib, 0), 2)))
    labels = [_Label(m.group(1), float(m.group(2)), float(m.group(3)),
                     int(m.group(4))) for m in _LABEL_RE.finditer(text)]
    wires = [_Wire(float(m.group(1)), float(m.group(2)),
                   float(m.group(3)), float(m.group(4)))
             for m in _WIRE_RE.finditer(text)]
    return syms, labels, wires


def _on_grid(v: float) -> bool:
    return abs(v / GRID - round(v / GRID)) < 0.02


def _seg_cross(a: _Wire, b: _Wire) -> bool:
    """ECHTE Kreuzung: Schnittpunkt strikt im Inneren BEIDER Segmente.

    Kalibriert am Goldstandard (Profi-Schaltbilder): ein geteilter Knoten
    (Endpunkt = Endpunkt) ist eine Verbindung, keine Kreuzung; ein Endpunkt,
    der auf dem anderen Segment liegt (T-Junction), ebenso. Nur wo zwei Netze
    sich OHNE Knoten überkreuzen, ist es ein echter Lesbarkeits-Fehler."""
    pa = {(round(a.x1, 2), round(a.y1, 2)), (round(a.x2, 2), round(a.y2, 2))}
    pb = {(round(b.x1, 2), round(b.y1, 2)), (round(b.x2, 2), round(b.y2, 2))}
    if pa & pb:
        return False  # geteilter Endpunkt → Verbindung, keine Kreuzung

    def ccw(ax, ay, bx, by, cx, cy):
        return (cy - ay) * (bx - ax) - (by - ay) * (cx - ax)
    d1 = ccw(b.x1, b.y1, b.x2, b.y2, a.x1, a.y1)
    d2 = ccw(b.x1, b.y1, b.x2, b.y2, a.x2, a.y2)
    d3 = ccw(a.x1, a.y1, a.x2, a.y2, b.x1, b.y1)
    d4 = ccw(a.x1, a.y1, a.x2, a.y2, b.x2, b.y2)
    # ein d==0 = Endpunkt liegt auf dem anderen Segment (T-Junction) → kein Kreuz
    if 0 in (round(d1, 3), round(d2, 3), round(d3, 3), round(d4, 3)):
        return False
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


_PIN_REACH = 2.54  # Pins ragen ~2.54 mm aus dem Körper — ein Draht ans eigene
#                    Bauteil endet in diesem Ring, quert es aber nicht.


def _dist_point_rect(px, py, cx, cy, hw, hh) -> float:
    """Kürzester Abstand von (px,py) zum Rechteck-Rand (0 wenn innen)."""
    return math.hypot(max(0.0, abs(px - cx) - hw), max(0.0, abs(py - cy) - hh))


def _seg_through_rect(x1, y1, x2, y2, cx, cy, hw, hh) -> bool:
    """Quert das Segment (x1,y1)-(x2,y2) das INNERE des Rechtecks (Mitte cx,cy,
    Halb-Maße hw,hh)? Liang-Barsky; ein bloßes Entlangstreifen an der Kante
    (t0>=t1) zählt nicht."""
    xmin, xmax, ymin, ymax = cx - hw, cx + hw, cy - hh, cy + hh
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - xmin), (dx, xmax - x1),
                 (-dy, y1 - ymin), (dy, ymax - y1)):
        if abs(p) < 1e-9:
            if q < 0:
                return False           # parallel und außerhalb
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
    return t0 < t1 - 1e-6


def _seg_overlap(a: _Wire, b: _Wire) -> bool:
    """Liegen zwei Segmente ÜBEREINANDER — kollinear (beide waagrecht auf gleichem
    y bzw. beide senkrecht auf gleichem x) und mit gemeinsamer STRECKE (nicht nur
    einem Punkt)? Das ist „zwei Leitungen übereinander", anders als eine Kreuzung
    (X) oder ein geteilter Endpunkt (fortlaufende gerade Leitung)."""
    a_h = abs(a.y1 - a.y2) < 0.01
    b_h = abs(b.y1 - b.y2) < 0.01
    a_v = abs(a.x1 - a.x2) < 0.01
    b_v = abs(b.x1 - b.x2) < 0.01
    if a_h and b_h and abs(a.y1 - b.y1) < 0.01:
        lo1, hi1 = sorted((a.x1, a.x2))
        lo2, hi2 = sorted((b.x1, b.x2))
        return min(hi1, hi2) - max(lo1, lo2) > 0.05
    if a_v and b_v and abs(a.x1 - b.x1) < 0.01:
        lo1, hi1 = sorted((a.y1, a.y2))
        lo2, hi2 = sorted((b.y1, b.y2))
        return min(hi1, hi2) - max(lo1, lo2) > 0.05
    return False


_LABEL_CHAR_W = 0.6   # wie bei den Annotations-Boxen — Referenzen bleiben 0
_LABEL_LINE_H = 1.4


def _label_box(lb: _Label) -> tuple[float, float, float, float]:
    """Achsen-parallele Text-Box eines Netz-Labels — der Text ragt vom Anker in
    Winkel-Richtung (0=rechts, 90=oben, 180=links, 270=unten)."""
    w = max(len(lb.text), 1) * _LABEL_CHAR_W
    h = _LABEL_LINE_H
    x, y, a = lb.x, lb.y, lb.angle
    if a == 180:
        return (x - w, y - h / 2, x, y + h / 2)
    if a == 90:
        return (x - h / 2, y - w, x + h / 2, y)
    if a == 270:
        return (x - h / 2, y, x + h / 2, y + w)
    return (x, y - h / 2, x + w, y + h / 2)   # 0 = rechts (Default)


_PINS_LOCAL: dict[str, list[tuple[float, float]]] = {}


def _pins_for_lib(lib_id: str) -> list[tuple[float, float]]:
    """Lokale Pin-ANSCHLUSSPUNKTE eines Lib-Symbols (Blatt-Rahmen, Y-down)."""
    if lib_id in _PINS_LOCAL:
        return _PINS_LOCAL[lib_id]
    pts: list[tuple[float, float]] = []
    try:
        from ..symbol_cache import get_real_symbol
        from ...utils.sexpr_parser import find_node, parse_sexpr

        raw = get_real_symbol(lib_id)
        if raw:
            def _walk(node):
                if not isinstance(node, list) or not node:
                    return
                if node[0] == "pin":
                    at = find_node(node, "at")
                    if at and len(at) >= 3:
                        pts.append((float(at[1]), -float(at[2])))
                for c in node:
                    if isinstance(c, list):
                        _walk(c)
            _walk(parse_sexpr(raw))
    except Exception:
        # Symbol nicht lesbar → leere Pin-Liste (Aufrufer behandelt das)
        pass
    _PINS_LOCAL[lib_id] = pts
    return pts


def _sym_pin_world(s: "_Sym") -> list[tuple[float, float]]:
    """Pin-Anschlusspunkte einer Symbol-INSTANZ in Welt-Koordinaten."""
    out = []
    rad = math.radians(-s.rot)
    ca, sa = math.cos(rad), math.sin(rad)
    for lx, ly in _pins_for_lib(s.lib_id):
        rx = lx * ca - ly * sa
        ry = lx * sa + ly * ca
        out.append((s.x + rx, s.y + ry))
    return out


def _eff_half(s: "_Sym") -> tuple[float, float]:
    """Effektive Halb-Maße für Zonen-Prüfungen: min(Grafik-Bbox, Pin-Käfig).
    Symbole wie WS2812B/MB6S zeichnen Deko-Polylines ÜBER den Pin-Käfig hinaus
    — Labels/Drähte an ihren Pins sind dann keine Überdeckung des „Körpers"."""
    hw, hh = s.half()
    pw = _sym_pin_world(s)
    if pw:
        phw = max(abs(px - s.x) for px, py in pw)
        phh = max(abs(py - s.y) for px, py in pw)
        if phw > 0.1:
            hw = min(hw, phw)
        if phh > 0.1:
            hh = min(hh, phh)
    return hw, hh


def measure_text(text: str) -> Metrics:
    """Ein ``.kicad_sch`` (als String) parsen und objektiv vermessen."""
    syms, labels, wires = _parse(text)
    m = Metrics(n_symbols=len(syms), n_labels=len(labels), n_wires=len(wires))
    bodies = [s for s in syms if not s.is_power]

    # Referenz/Wert-Texte: (a) übereinander (verschiedener Bauteile), (b) auf
    # einem FREMDEN Bauteilkörper. Das eigene Paar bzw. der bewusst IN den
    # eigenen Körper gelegte Wert (LAN8720 mit Unterkanten-Pins) sind erlaubt —
    # nur fremde Körper zählen. Power-Symbol-Texte (GND/VCC) sind KiCad-Standard
    # dicht am Symbol und bleiben beim Körper-Check außen vor.
    aboxes = _annot_boxes(text)
    for i in range(len(aboxes)):
        for j in range(i + 1, len(aboxes)):
            if any(_annot_box_overlap(a, b)
                   for a in aboxes[i] for b in aboxes[j]):
                m.annot_overlaps += 1
    for i, s in enumerate(syms):
        if s.is_power or not aboxes[i]:
            continue
        for t in bodies:
            if t is s:
                continue
            hw, hh = _eff_half(t)
            if any(abs(b[0] - t.x) < hw + b[2] / 2 - _ANNOT_MARGIN
                   and abs(b[1] - t.y) < hh + b[3] / 2 - _ANNOT_MARGIN
                   for b in aboxes[i]):
                m.annot_body_overlaps += 1
                m.details.append(
                    f"Referenz/Wert von {s.lib_id} liegt auf {t.lib_id}")

    # Bauteil-Überlappungen (Rahmen, mit kleinem Spalt)
    for i in range(len(bodies)):
        ahw, ahh = bodies[i].half()
        for j in range(i + 1, len(bodies)):
            bhw, bhh = bodies[j].half()
            if (abs(bodies[i].x - bodies[j].x) < ahw + bhw - 0.1
                    and abs(bodies[i].y - bodies[j].y) < ahh + bhh - 0.1):
                m.comp_overlaps += 1
            else:
                # Enge („mehr Luft lassen"): beide Achsen-Spalte < 2.54 mm —
                # das Bauteil klebt ohne Not am Nachbarn (an den Profi-
                # Referenzen geeicht: dort ist ÜBERALL mehr Platz).
                gx = abs(bodies[i].x - bodies[j].x) - (ahw + bhw)
                gy = abs(bodies[i].y - bodies[j].y) - (ahh + bhh)
                if gx < 2.54 and gy < 2.54:
                    m.crowding += 1

    # Draht-Endpunkte → für die Label-Richtung (welcher Draht kommt am Label an)
    ends: dict[tuple[float, float], list[_Wire]] = {}
    for w in wires:
        ends.setdefault((round(w.x1, 2), round(w.y1, 2)), []).append(w)
        ends.setdefault((round(w.x2, 2), round(w.y2, 2)), []).append(w)

    # Label auf Bauteilkörper?  +  Label zeigt vom Draht weg (nach außen)?
    # Geprüft wird die TEXT-BOX des Labels gegen den Bauteil-Rahmen (nicht nur der
    # Anker) — sonst ragt der Text über einen Nachbarn (z. B. ein C), obwohl der
    # Ankerpunkt daneben liegt (der motor_driver-Fall).
    _DIR = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}
    for lb in labels:
        lx0, ly0, lx1, ly1 = _label_box(lb)
        lcx, lcy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
        lhw, lhh = (lx1 - lx0) / 2, (ly1 - ly0) / 2
        for s in bodies:
            hw, hh = _eff_half(s)
            # Pin-Zone zählt zum Körper (+2.84 = Pin-Länge + Rand): ein Label,
            # das längs durch die Pin-Nummern-Spalte eines ICs schreibt oder in
            # den Körper ragt, ist Überdeckung — die Profi-Referenzen bleiben
            # auch mit dieser Zone bei 0 (geeicht).
            zx, zy = hw + _PIN_REACH + 0.3, hh + _PIN_REACH + 0.3
            if (abs(lcx - s.x) < zx + lhw - 0.2
                    and abs(lcy - s.y) < zy + lhh - 0.2):
                m.label_overlaps += 1
                m.details.append(f"Label '{lb.text}' auf/an {s.lib_id}")
                break
        # „Weg zeigen": das Label sitzt am Draht-Ende und soll in FREIEN Raum
        # ragen. Auswärts-Richtung = weg vom Draht (der zur Schaltung führt) =
        # die Richtung, in der das ferne Draht-Ende NICHT liegt. Liegt dort ein
        # Bauteilkörper, zeigt das Label in die Schaltung statt raus → Fehler.
        key = (round(lb.x, 2), round(lb.y, 2))
        wl = ends.get(key)
        if wl:
            w = wl[0]
            far = (w.x2, w.y2) if (round(w.x1, 2), round(w.y1, 2)) == key \
                else (w.x1, w.y1)
            out = (lb.x - far[0], lb.y - far[1])   # vom Draht weg
            n = math.hypot(*out) or 1.0
            out = (out[0] / n, out[1] / n)
            probe = (lb.x + out[0] * 3.81, lb.y + out[1] * 3.81)
            for s in bodies:
                hw, hh = s.half()
                if abs(probe[0] - s.x) < hw and abs(probe[1] - s.y) < hh:
                    m.label_wrong_dir += 1
                    m.details.append(f"Label '{lb.text}' ragt in {s.lib_id}")
                    break

    # Drähte: Länge, Diagonalen, Kreuzungen
    for w in wires:
        m.wirelength_mm += math.hypot(w.x2 - w.x1, w.y2 - w.y1)
        if abs(w.x1 - w.x2) > 0.01 and abs(w.y1 - w.y2) > 0.01:
            m.diag_wires += 1
    for i in range(len(wires)):
        for j in range(i + 1, len(wires)):
            if _seg_cross(wires[i], wires[j]):
                m.wire_crossings += 1
            elif _seg_overlap(wires[i], wires[j]):
                m.wire_overlaps += 1

    # Leitung quer durch einen Bauteil-Körper (Regel: Drähte gehen nie durch
    # Bauteile — auch nicht durchs EIGENE). Kalibriert am Goldstandard: ein
    # KORREKT angeschlossener Draht endet am Pin-Tip, der ~2.54 mm AUSSERHALB der
    # Körper-Kante sitzt — sein Segment betritt das Körper-Innere also gar nicht.
    # Nur ein Segment, das das Innere WIRKLICH durchquert (Bus über den Chip,
    # Stub in die falsche Richtung), wird gezählt. Die früher nötige Pin-Ring-
    # Ausnahme („Endpunkt < 2.84 mm vom Rand → ganzes Segment ignorieren") ist
    # RAUS: sie versteckte reale Busse quer über große ICs (STM32) und
    # Widerstands-Körper. Kleiner Shrink 0.4, damit ein Draht, der exakt an der
    # Kante entlangläuft, nicht schon zählt.
    for w in wires:
        for s in bodies:
            hw, hh = _eff_half(s)
            # Ein-Pin-Bauteile (TestPoint, Mount, Flag) haben ihren Anschluss GENAU
            # im Symbol-Ursprung = Körper-Mitte; ein Anschluss-Stub startet dann
            # zwangsläufig „im" (winzigen) Körper. Endet ein Segment an der Mitte
            # (≤0.6 mm), ist das eine legitime Ein-Pin-Verbindung, kein Bus quer
            # durchs Bauteil — echte Busse haben NIE einen Endpunkt im Zentrum.
            if (math.hypot(w.x1 - s.x, w.y1 - s.y) <= 0.6
                    or math.hypot(w.x2 - s.x, w.y2 - s.y) <= 0.6):
                continue
            # Endet das Segment AN EINEM PIN dieses Symbols, ist es dessen
            # Anschluss — auch wenn der Pin INNERHALB der Grafik-Bbox sitzt
            # (WS2812B/MB6S: Deko-Polylines größer als der Pin-Käfig; der
            # Power-Stub zum VDD-Pin ist keine Querung).
            _pw = _sym_pin_world(s)
            if any(math.hypot(w.x1 - px, w.y1 - py) <= 0.4
                   or math.hypot(w.x2 - px, w.y2 - py) <= 0.4
                   for px, py in _pw):
                continue
            if _seg_through_rect(w.x1, w.y1, w.x2, w.y2,
                                 s.x, s.y, hw - 0.4, hh - 0.4):
                m.wire_through_body += 1
                m.details.append(f"Draht quert {s.lib_id}")

    # Label ↔ Label und Label ↔ Draht — „Label, Draht und Bauteile dürfen sich
    # gegenseitig nicht überdecken". (Label↔Bauteil steckt in label_overlaps,
    # Draht↔Bauteil in wire_through_body, Draht↔Draht in wire_overlaps.)
    lboxes = [_label_box(lb) for lb in labels]
    for i in range(len(lboxes)):
        ax0, ay0, ax1, ay1 = lboxes[i]
        for j in range(i + 1, len(lboxes)):
            bx0, by0, bx1, by1 = lboxes[j]
            if (ax0 < bx1 - 0.2 and bx0 < ax1 - 0.2
                    and ay0 < by1 - 0.2 and by0 < ay1 - 0.2):
                m.label_label_overlaps += 1
                m.details.append(
                    f"Labels '{labels[i].text}'/'{labels[j].text}' überdecken sich")
    for lb, box in zip(labels, lboxes):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        hw, hh = (box[2] - box[0]) / 2 - 0.1, (box[3] - box[1]) / 2 - 0.1
        akey = (round(lb.x, 2), round(lb.y, 2))
        for w in wires:
            # den EIGENEN Stub ausschließen (Draht endet am Label-Anker)
            if akey in ((round(w.x1, 2), round(w.y1, 2)),
                        (round(w.x2, 2), round(w.y2, 2))):
                continue
            if _seg_through_rect(w.x1, w.y1, w.x2, w.y2, cx, cy, hw, hh):
                m.label_wire_overlaps += 1
                m.details.append(f"Label '{lb.text}' liegt über einem Draht")
                break

    # Off-grid (Bauteile + Labels)
    for s in syms:
        if not (_on_grid(s.x) and _on_grid(s.y)):
            m.offgrid += 1
    for lb in labels:
        if not (_on_grid(lb.x) and _on_grid(lb.y)):
            m.offgrid += 1

    m.wirelength_mm = round(m.wirelength_mm, 1)
    return m


def measure_file(path: str) -> Metrics:
    """Ein ``.kicad_sch`` von der Platte vermessen."""
    with open(path, encoding="utf-8") as fh:
        return measure_text(fh.read())
