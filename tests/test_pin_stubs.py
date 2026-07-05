# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin-Stubs & rotations-bewusste Hindernisse (route.py).

Zwei Nutzer-Regeln in einem: „Stubs an den ICs" (jeder verdrahtete Pin bekommt
eine kurze axiale Leitung nach außen) und „keine lokalen Busse über die
Bauteile" (der Router modelliert ein gedrehtes Bauteil mit der RICHTIGEN
Breite/Höhe, sonst zieht er einen waagrechten Bus quer durch einen waagrechten
Widerstand)."""

from __future__ import annotations

import math

import pytest

from kicad_mcp.generators.symbol_cache import get_real_symbol

from kicad_mcp.generators.schematic import route
from kicad_mcp.generators.common.constants import PIN_STUB_LEN

# Diese Tests prüfen gegen die ECHTE KiCad-Symbol-Bibliothek (reale Bbox,
# Pin-Zahlen, Gegenrotation) — ohne installiertes KiCad liefert die
# Symbol-Auflösung Fallback-Geometrie und die Aussagen stimmen nicht mehr
# (CI-Job "KiCad mocked"). Der Echt-KiCad-Job fährt sie weiterhin.
_needs_symbol_lib = pytest.mark.skipif(
    not get_real_symbol("74xx:74HC595"),
    reason="KiCad-Symbol-Bibliothek nicht installiert")


def test_pin_stub_points_outward_from_body():
    # Pin rechts vom Zentrum → Stub-Spitze noch weiter rechts (nach außen).
    tip = route._pin_stub_point(110.0, 100.0, 100.0, 100.0)
    assert tip == (110.0 + PIN_STUB_LEN, 100.0)
    # Pin über dem Zentrum → Stub nach oben (kleineres y).
    tip = route._pin_stub_point(100.0, 90.0, 100.0, 100.0)
    assert tip == (100.0, 90.0 - PIN_STUB_LEN)


@_needs_symbol_lib
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


@_needs_symbol_lib
def test_rotated_part_annotations_are_counter_rotated():
    # KiCad rendert Property-Text relativ zur Symbol-Rotation: bei rot=90/270
    # muss der Property-Winkel gegenrotieren (270/90), sonst stehen Referenz
    # und Wert als vertikaler Buchstabensalat übereinander („10uC1").
    from kicad_mcp.generators.schematic.builder import build_schematic
    import re

    parts = [
        {"ref": "C9", "name": "C", "lib_id": "Device:C", "value": "4u7",
         "pins": [{"num": "1", "name": "1"}, {"num": "2", "name": "2"}],
         "_rotation": 90},
    ]
    text = build_schematic(parts, [], project_name="rottest",
                           keep_placement=True, place=False)
    blk = text[text.find('"C9"') - 400: text.find('"C9"') + 600]
    m = re.search(r'\(property "Value" "4u7" \(at [-\d.]+ [-\d.]+ (\d+)\)', text)
    assert m, blk
    assert m.group(1) == "270", f"Value-Winkel {m.group(1)} statt 270 (rot=90)"


@_needs_symbol_lib
def test_fuzzy_symbol_match_rejects_oversized_symbol():
    # „STM32F407" mit 11 deklarierten Pins darf NICHT aufs 176-Pin-BGA-Symbol
    # gematcht werden (der ethernet_device-Fall) → Platzhalter-Box. Ein Teil
    # mit vielen deklarierten Pins darf das große Symbol weiterhin bekommen.
    from kicad_mcp.generators.symbol_lib import _pin_count_sane
    few = {"pins": [{"num": str(i)} for i in range(11)]}
    many = {"pins": [{"num": str(i)} for i in range(60)]}
    big = "MCU_ST_STM32F4:STM32F407IEHx"
    assert not _pin_count_sane(few, big)
    assert _pin_count_sane(many, big)
    # kleine Symbole immer ok
    assert _pin_count_sane(few, "Device:R")
