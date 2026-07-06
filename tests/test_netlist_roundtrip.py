# SPDX-License-Identifier: GPL-3.0-or-later
"""Netzlisten-Roundtrip (Nutzer-Vorschlag): „Nimm die Original-Schaltung,
mach eine Netzliste. Nimm deine GEZEICHNETE Schaltung, erstelle daraus eine
Netzliste. Wenn beide matchen, melde Erfolg."

Der harte elektrische Gate: die Ist-Netzliste kommt über
``kicad-cli sch export netlist`` aus dem fertigen ``.kicad_sch`` (KiCads
eigene Konnektivitäts-Engine — Drähte, Stubs, Labels, Power-Symbole,
Junctions), pin-genau verglichen mit der Soll-Netzliste des Kits. Vor diesem
Gate waren ALLE 10 Kits elektrisch falsch (Total-Kurzschlüsse, zerfallene
Netze, offene Pins) — bei badness 0 und grünem ERC."""

from __future__ import annotations

import glob
import json
import os
import shutil

import pytest

from kicad_mcp.generators.schematic.builder import build_schematic
from kicad_mcp.generators.schematic import netlist_check as nc

_KITS = sorted(glob.glob(os.path.join(
    os.path.dirname(__file__), "..", "kicad_mcp", "resources", "data",
    "demo_kits", "*.json")))

pytestmark = pytest.mark.skipif(
    shutil.which("kicad-cli") is None,
    reason="kicad-cli nicht installiert (Netzlisten-Export braucht KiCad)")


@pytest.mark.parametrize(
    "kit_path", _KITS, ids=[os.path.splitext(os.path.basename(p))[0] for p in _KITS])
def test_drawn_schematic_matches_spec_netlist(kit_path, tmp_path):
    spec = json.load(open(kit_path, encoding="utf-8"))
    parts = json.loads(json.dumps(spec["parts"]))
    nets = json.loads(json.dumps(spec["nets"]))
    key = os.path.splitext(os.path.basename(kit_path))[0]

    text = build_schematic(parts, nets, project_name=key)
    sch = tmp_path / f"{key}.kicad_sch"
    sch.write_text(text, encoding="utf-8")

    actual = nc.extract_netlist(str(sch))
    assert actual is not None, "kicad-cli Netzlisten-Export fehlgeschlagen"

    res = nc.compare_netlists(nets, actual,
                              pin_aliases=nc.build_pin_aliases(parts))
    assert res["match"], (
        "Gezeichnete Schaltung ≠ Soll-Netzliste:\n"
        + "\n".join(res["merged"] + res["split"] + res["missing"]))


def test_roundtrip_survives_layout_optimizer(tmp_path):
    # Der Optimizer verschiebt/dreht Bauteile und re-emittiert — die
    # elektrische Identität muss das überleben (Smoke an einem kleinen Kit;
    # der volle 10-Kit-Optimizer-Lauf ist zu langsam für jede Suite).
    kit = os.path.join(os.path.dirname(__file__), "..", "kicad_mcp",
                       "resources", "data", "demo_kits", "kit_seeding.json")
    spec = json.load(open(kit, encoding="utf-8"))
    parts = json.loads(json.dumps(spec["parts"]))
    nets = json.loads(json.dumps(spec["nets"]))
    text = build_schematic(parts, nets, project_name="seedopt",
                           optimize=True, optimize_evals=300)
    sch = tmp_path / "seedopt.kicad_sch"
    sch.write_text(text, encoding="utf-8")
    actual = nc.extract_netlist(str(sch))
    assert actual is not None
    res = nc.compare_netlists(nets, actual,
                              pin_aliases=nc.build_pin_aliases(parts))
    assert res["match"], (
        "Optimizer hat die elektrische Identität zerstört:\n"
        + "\n".join(res["merged"] + res["split"] + res["missing"]))


def test_junctions_present_at_t_joins():
    # Nutzer-Regel: „wenn aus einer geraden Leitung eine Leitung abzweigt,
    # muss ein Punkt das kennzeichnen" — T-Abzweige tragen Junction-Punkte.
    # WELCHES Kit T-Abzweige routet, hängt vom Layout ab (Abstands-Regeln
    # verschieben das) — der Wächter prüft deshalb über ALLE Kits: mindestens
    # eines muss Junctions emittieren, sonst ist der Junction-Pass tot.
    kit_dir = os.path.join(os.path.dirname(__file__), "..", "kicad_mcp",
                           "resources", "data", "demo_kits")
    found = []
    for name in sorted(os.listdir(kit_dir)):
        if not name.endswith(".json"):
            continue
        spec = json.load(open(os.path.join(kit_dir, name), encoding="utf-8"))
        parts = json.loads(json.dumps(spec["parts"]))
        nets = json.loads(json.dumps(spec["nets"]))
        text = build_schematic(parts, nets, project_name=f"j_{name[:-5]}")
        if "(junction (at " in text:
            found.append(name[:-5])
    assert found, "kein einziges Kit emittiert Junctions — Junction-Pass tot?"
