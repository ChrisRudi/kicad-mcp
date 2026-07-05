# SPDX-License-Identifier: GPL-3.0-or-later
"""Objektive Qualitäts-Messung eines FERTIGEN Schaltplans (``.kicad_sch``).

Anders als ``schematic_scorer`` (bewertet die ``parts/nets``-Platzierung VOR der
Emission) parst dieses Modul das erzeugte ``.kicad_sch`` und misst, was am Ende
wirklich auf dem Blatt steht — inklusive **Labels, Power-Symbolen und Drähten**.
Damit sieht es den größten Lesbarkeits-Killer, den der parts/nets-Scorer NICHT
sehen kann: Labels/Bauteile, die ÜBEREINANDER liegen.

Kernnutzen: dieselbe Messung läuft auf UNSEREM Output UND auf echten
Profi-Referenz-Schaltbildern (deren ``.kicad_sch`` wir haben) → direkte,
objektive Distanz „wie weit sind wir vom Profi". Reines Parsen + Geometrie,
kein KiCad nötig (Symbol-Bboxes über die Symbol-Lib, mit Fallback).

Metriken (alles „weniger = besser", 0 = ideal):
    comp_overlaps      Paare überlappender Bauteil-Rahmen
    label_overlaps     Labels, die auf einem Bauteilkörper liegen
    label_wrong_dir    Netz-Labels, die NICHT vom Bauteil weg zeigen
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
_BBOX: dict[str, tuple[float, float]] = {}


def _bbox_for_lib(lib_id: str, n_pins: int = 2) -> tuple[float, float]:
    """(Breite, Höhe) des Symbol-Rahmens in mm — aus der echten Symbol-Lib,
    sonst grober Fallback aus der Pin-Zahl."""
    if lib_id in _BBOX:
        return _BBOX[lib_id]
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
                        rxs += [float(s[1]), float(e[1])]
                        rys += [float(s[2]), float(e[2])]
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
        pass
    if w <= 0 or h <= 0:
        h = max(n_pins * GRID, GRID * 2)
        w = GRID * 2
    _BBOX[lib_id] = (w, h)
    return (w, h)


@dataclass
class _Sym:
    lib_id: str
    x: float
    y: float
    rot: int
    is_power: bool

    def half(self) -> tuple[float, float]:
        w, h = _bbox_for_lib(self.lib_id)
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
    label_overlaps: int = 0
    label_wrong_dir: int = 0
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
            "comp_overlaps", "label_overlaps", "label_wrong_dir",
            "wire_crossings", "diag_wires", "offgrid",
            "wirelength_mm", "n_symbols", "n_labels", "n_wires")}

    def badness(self, weights: dict | None = None) -> float:
        """Gewichtete Gesamt-Schlechtigkeit (0 = ideal). Die harten
        Lesbarkeits-Killer (Überlappungen) wiegen am schwersten."""
        w = weights or _DEFAULT_WEIGHTS
        return (w["comp_overlaps"] * self.comp_overlaps
                + w["label_overlaps"] * self.label_overlaps
                + w["label_wrong_dir"] * self.label_wrong_dir
                + w["wire_crossings"] * self.wire_crossings
                + w["diag_wires"] * self.diag_wires
                + w["offgrid"] * self.offgrid)


_DEFAULT_WEIGHTS = {
    "comp_overlaps": 100.0,   # größter Hebel: nichts übereinander
    "label_overlaps": 100.0,  # dito für Labels
    "label_wrong_dir": 20.0,
    "wire_crossings": 8.0,
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


def _parse(text: str) -> tuple[list[_Sym], list[_Label], list[_Wire]]:
    syms = []
    for m in _SYM_RE.finditer(text):
        lib = m.group(1)
        # „echtes Bauteil?" — semantisch über in_bom/on_board (die kurz nach dem
        # Symbol-Kopf stehen). Power-Symbole & PWR_FLAG sind no/no → keine
        # Bauteile → aus der Überlappungs-Wertung raus (kalibriert am Profi).
        tail = text[m.end():m.end() + 140]
        is_power = (lib.startswith("power:") or "PWR_FLAG" in lib
                    or "(in_bom no)" in tail or "(on_board no)" in tail)
        syms.append(_Sym(lib, float(m.group(2)), float(m.group(3)),
                         int(m.group(4)), is_power))
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


def measure_text(text: str) -> Metrics:
    """Ein ``.kicad_sch`` (als String) parsen und objektiv vermessen."""
    syms, labels, wires = _parse(text)
    m = Metrics(n_symbols=len(syms), n_labels=len(labels), n_wires=len(wires))
    bodies = [s for s in syms if not s.is_power]

    # Bauteil-Überlappungen (Rahmen, mit kleinem Spalt)
    for i in range(len(bodies)):
        ahw, ahh = bodies[i].half()
        for j in range(i + 1, len(bodies)):
            bhw, bhh = bodies[j].half()
            if (abs(bodies[i].x - bodies[j].x) < ahw + bhw - 0.1
                    and abs(bodies[i].y - bodies[j].y) < ahh + bhh - 0.1):
                m.comp_overlaps += 1

    # Draht-Endpunkte → für die Label-Richtung (welcher Draht kommt am Label an)
    ends: dict[tuple[float, float], list[_Wire]] = {}
    for w in wires:
        ends.setdefault((round(w.x1, 2), round(w.y1, 2)), []).append(w)
        ends.setdefault((round(w.x2, 2), round(w.y2, 2)), []).append(w)

    # Label auf Bauteilkörper?  +  Label zeigt vom Draht weg (nach außen)?
    _DIR = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}
    for lb in labels:
        for s in bodies:
            hw, hh = s.half()
            if abs(lb.x - s.x) < hw and abs(lb.y - s.y) < hh:
                m.label_overlaps += 1
                m.details.append(f"Label '{lb.text}' auf {s.lib_id}")
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
