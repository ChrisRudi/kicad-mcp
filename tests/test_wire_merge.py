# SPDX-License-Identifier: GPL-3.0-or-later
"""Der Draht-Merge (`builder._merge_overlapping_wires`) führt kollinear
ÜBEREINANDER liegende Segmente zu ihrer Vereinigung zusammen („keine Leitungen
übereinander"), ohne fortlaufende Leitungen (nur geteilter Endpunkt) oder
diagonale/getrennte Segmente anzutasten."""

from __future__ import annotations

import json
import os

from kicad_mcp.generators.schematic.builder import (
    _merge_overlapping_wires, build_schematic,
)
from kicad_mcp.generators.schematic import layout_measure as lm

_KIT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "kicad_mcp", "resources", "data", "demo_kits")


def _wire(x1, y1, x2, y2, u):
    return f'  (wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (uuid "{u}"))'


def test_overlapping_horizontal_wires_merge():
    lines = [_wire(10, 50, 30, 50, "a"), _wire(20, 50, 40, 50, "b")]
    out = [ln for ln in _merge_overlapping_wires(lines) if "(wire" in ln]
    assert len(out) == 1                       # zwei → eine Vereinigung
    assert "(xy 10 50)" in out[0] and "(xy 40 50)" in out[0]


def test_contiguous_wires_are_not_merged():
    # nur geteilter Endpunkt (x=20) → fortlaufende Leitung, bleibt zwei Segmente
    lines = [_wire(10, 50, 20, 50, "a"), _wire(20, 50, 30, 50, "b")]
    out = [ln for ln in _merge_overlapping_wires(lines) if "(wire" in ln]
    assert len(out) == 2


def test_separate_collinear_wires_are_not_merged():
    # gleiche Linie, aber LÜCKE dazwischen (verschiedene Netze) → nicht mergen
    lines = [_wire(10, 50, 20, 50, "a"), _wire(30, 50, 40, 50, "b")]
    out = [ln for ln in _merge_overlapping_wires(lines) if "(wire" in ln]
    assert len(out) == 2


def test_diagonal_wires_untouched():
    lines = [_wire(10, 10, 20, 25, "a")]
    out = [ln for ln in _merge_overlapping_wires(lines) if "(wire" in ln]
    assert len(out) == 1 and "(xy 10 10)" in out[0] and "(xy 20 25)" in out[0]


def test_all_kits_have_zero_wire_overlaps_after_build():
    # End-to-End: nach dem Merge misst KEIN Kit mehr Leitungen übereinander.
    for kit in ("buck_converter", "production_ready", "usb_sensor_hub",
                "ac_dc_supply"):
        with open(os.path.join(_KIT_DIR, f"{kit}.json"), encoding="utf-8") as fh:
            spec = json.load(fh)
        parts = json.loads(json.dumps(spec["parts"]))
        nets = json.loads(json.dumps(spec["nets"]))
        m = lm.measure_text(build_schematic(parts, nets, project_name=kit))
        assert m.wire_overlaps == 0, f"{kit}: {m.wire_overlaps} Leitungen übereinander"
