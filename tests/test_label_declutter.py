# SPDX-License-Identifier: GPL-3.0-or-later
"""Der Label-Declutter (`builder._declutter_labels`) dreht/spiegelt ein Netz-
Label, dessen Text-Box einen fremden Draht (oder Körper/anderes Label) trifft,
auf eine freie Auswärts-Richtung — und legt seinen Stub entsprechend um, ohne
die Verbindung (Pin↔Label) zu verlieren."""

from __future__ import annotations

import re

from kicad_mcp.generators.schematic.builder import _declutter_labels


def _label_line(name, x, y, a):
    return (f'  (label "{name}" (at {x} {y} {a})'
            f' (effects (font (size 1.27 1.27))) (uuid "l"))')


def _wire_line(x1, y1, x2, y2, u):
    return f'  (wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (uuid "{u}"))'


def test_label_over_wire_is_turned_to_free_direction():
    # Label bei (100,100) Winkel 0 (Text nach rechts) → Box überdeckt den
    # senkrechten Draht bei x=101. Der eigene Stub kommt von links (Pin 95,100).
    lines = [
        _label_line("NET", 100, 100, 0),
        _wire_line(95, 100, 100, 100, "stub"),   # eigener Stub, Pin bei (95,100)
        _wire_line(101, 95, 101, 105, "foreign"),  # fremder Draht quert die Box
    ]
    out = _declutter_labels(lines, parts=[])
    lbl = next(ln for ln in out if "(label " in ln)
    m = re.search(r'\(at (-?[\d.]+) (-?[\d.]+) (\d+)\)', lbl)
    ang = int(m.group(3))
    assert ang != 0, "Label wurde nicht aus der Kollision gedreht"
    # der eigene Stub endet weiterhin am (neuen) Label-Anker → Verbindung intakt
    lx, ly = float(m.group(1)), float(m.group(2))
    stub = next(ln for ln in out if '"stub"' in ln)
    pts = re.findall(r'\(xy (-?[\d.]+) (-?[\d.]+)\)', stub)
    ends = {(round(float(a), 2), round(float(b), 2)) for a, b in pts}
    assert (round(lx, 2), round(ly, 2)) in ends
    assert (95.0, 100.0) in ends           # Pin bleibt fix


def test_free_label_is_left_untouched():
    lines = [_label_line("NET", 100, 100, 0),
             _wire_line(95, 100, 100, 100, "stub")]
    out = _declutter_labels(lines, parts=[])
    assert out == lines
