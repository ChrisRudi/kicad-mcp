# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin-Stubs & rotations-bewusste Hindernisse (route.py).

Zwei Nutzer-Regeln in einem: „Stubs an den ICs" (jeder verdrahtete Pin bekommt
eine kurze axiale Leitung nach außen) und „keine lokalen Busse über die
Bauteile" (der Router modelliert ein gedrehtes Bauteil mit der RICHTIGEN
Breite/Höhe, sonst zieht er einen waagrechten Bus quer durch einen waagrechten
Widerstand)."""

from __future__ import annotations

import math

from kicad_mcp.generators.schematic import route
from kicad_mcp.generators.common.constants import PIN_STUB_LEN


def test_pin_stub_points_outward_from_body():
    # Pin rechts vom Zentrum → Stub-Spitze noch weiter rechts (nach außen).
    tip = route._pin_stub_point(110.0, 100.0, 100.0, 100.0)
    assert tip == (110.0 + PIN_STUB_LEN, 100.0)
    # Pin über dem Zentrum → Stub nach oben (kleineres y).
    tip = route._pin_stub_point(100.0, 90.0, 100.0, 100.0)
    assert tip == (100.0, 90.0 - PIN_STUB_LEN)


def test_obstacle_set_is_rotation_aware():
    # Ein um 90° gedrehter Widerstand ist BREIT (x) und niedrig (y). Ohne den
    # Rotations-Swap modelliert der Router ihn schmal-hoch → ein waagrechter Bus
    # quer durch die Körpermitte bliebe frei. Prüfen: das Hindernis ist in x
    # deutlich breiter als in y (die tatsächliche Ausrichtung).
    part = {"lib_id": "Device:R", "ref": "R1", "pins": [{"num": "1"}, {"num": "2"}],
            "_place_x": 100.0, "_place_y": 100.0, "_rotation": 90}
    cells = route._build_obstacle_set([part], clearance=0.0)
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    assert span_x > span_y, f"gedrehter R nicht breiter als hoch: {(span_x, span_y)}"


def test_wired_ic_pins_get_a_stub():
    # Ein 2-Pin-Netz zwischen zwei nahen Bauteilen wird verdrahtet; JEDER
    # verdrahtete Pin muss eine kurze axiale Stub-Leitung (Länge PIN_STUB_LEN)
    # aus dem Körper heraus bekommen (die „Stubs an den ICs").
    from kicad_mcp.generators.schematic.builder import build_schematic
    from kicad_mcp.generators.schematic import layout_measure as lm

    parts = [
        {"ref": "R1", "name": "R", "lib_id": "Device:R", "value": "10k",
         "pins": [{"num": "1", "name": "1"}, {"num": "2", "name": "2"}]},
        {"ref": "R2", "name": "R", "lib_id": "Device:R", "value": "10k",
         "pins": [{"num": "1", "name": "1"}, {"num": "2", "name": "2"}]},
    ]
    nets = [{"name": "MID", "type": "signal",
             "connections": ["R1:2", "R2:1"]}]
    text = build_schematic(parts, nets, project_name="stubtest")
    _syms, _labels, wires = lm._parse(text)
    # mindestens eine sehr kurze axiale Leitung ~ PIN_STUB_LEN (der Stub)
    stubs = [w for w in wires
             if (abs(w.x1 - w.x2) < 0.01 or abs(w.y1 - w.y2) < 0.01)
             and abs(math.hypot(w.x2 - w.x1, w.y2 - w.y1) - PIN_STUB_LEN) < 0.05]
    assert stubs, "kein Pin-Stub der Länge PIN_STUB_LEN gefunden"
