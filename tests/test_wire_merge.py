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


def test_overlapping_wires_with_shared_endpoint_merge():
    # Überlappung MIT geteiltem Endpunkt (x=10): gleicher Knoten = garantiert
    # gleiches Netz → Vereinigung ist sicher.
    lines = [_wire(10, 50, 30, 50, "a"), _wire(10, 50, 40, 50, "b")]
    out = [ln for ln in _merge_overlapping_wires(lines) if "(wire" in ln]
    assert len(out) == 1                       # zwei → eine Vereinigung
    assert "(xy 10 50)" in out[0] and "(xy 40 50)" in out[0]


def test_overlapping_wires_without_shared_endpoint_stay():
    # Überlappung OHNE geteilten Endpunkt kann zwei VERSCHIEDENE Netze
    # betreffen (zwei Nachbar-Stubs übereinander) — vereinigen wäre ein
    # Kurzschluss (Netzlisten-Roundtrip-Befund USB_DM/USB_DP) → bleibt zwei.
    lines = [_wire(10, 50, 30, 50, "a"), _wire(20, 50, 40, 50, "b")]
    out = [ln for ln in _merge_overlapping_wires(lines) if "(wire" in ln]
    assert len(out) == 2


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


def _run_union(seglist):
    # Baut ein SExpr mit je einem Draht pro Segment, ruft die Netz-bewusste
    # Vereinigung und gibt die verbleibenden Draht-Zeilen zurück.
    from kicad_mcp.generators.sexpr import SExpr
    from kicad_mcp.generators.schematic.route import _union_same_net_overlaps
    s = SExpr()
    for i, (x1, y1, x2, y2, _net) in enumerate(seglist):
        s.wire(x1, y1, x2, y2, f"w{i}")
    segs = list(seglist)
    _union_same_net_overlaps(s, segs, lambda v: round(v, 2),
                             lambda key: key, "t")
    return [ln for ln in s._lines if "(wire" in ln]


def test_union_merges_same_net_interior_overlap():
    # Zwei vertikale Segmente DESSELBEN Netzes, die sich innen überlappen
    # (nicht nur am Endpunkt) → eine Vereinigung über die volle Spanne.
    wires = _run_union([(77.47, 85.09, 77.47, 90.17, "GND"),
                        (77.47, 87.63, 77.47, 92.71, "GND")])
    assert len(wires) == 1
    assert "(xy 77.47 85.09)" in wires[0] and "(xy 77.47 92.71)" in wires[0]


def test_union_keeps_different_nets_apart():
    # Gleiche Geometrie, aber ZWEI Netze → NICHT vereinen (wäre Kurzschluss).
    wires = _run_union([(77.47, 85.09, 77.47, 90.17, "NET_A"),
                        (77.47, 87.63, 77.47, 92.71, "NET_B")])
    assert len(wires) == 2


def test_union_leaves_touching_segments():
    # Nur geteilter Endpunkt (kein Innen-Überlapp) → bleibt unangetastet.
    wires = _run_union([(50, 10, 50, 20, "GND"),
                        (50, 20, 50, 30, "GND")])
    assert len(wires) == 2


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
