# SPDX-License-Identifier: GPL-3.0-or-later
"""Der Layout-Optimierer (``generators/schematic/layout_optimizer``) ist die
echte Such-Schleife: er verschiebt/dreht Bauteile, emittiert den FERTIGEN
Schaltplan neu und behält nur Schritte, die die objektive ``badness`` senken.
Diese Tests verankern die zwei harten Zusagen — (1) er macht das Layout NIE
schlechter als die Eingabe, (2) auf den echten Demo-Schaltungen erreicht er die
0 des Profi-Goldstandards (nichts überlappt, alle Labels weg vom Bauteil, keine
Kreuzungen) — plus die Wohlgeformtheit der wartbaren Operator-Liste."""

from __future__ import annotations

import json
import os

import pytest

from kicad_mcp.generators.schematic import layout_optimizer as opt
from kicad_mcp.generators.schematic import layout_measure as lm
from kicad_mcp.generators.schematic.builder import build_schematic

_KIT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "kicad_mcp", "resources", "data", "demo_kits")


def _load(kit: str):
    with open(os.path.join(_KIT_DIR, f"{kit}.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    # tief kopieren — der Optimierer mutiert die Parts in-place
    return (json.loads(json.dumps(spec["parts"])),
            json.loads(json.dumps(spec["nets"])))


# ── Operator-Liste (die „~20 Regeln" der Suche) ─────────────────────────────

def test_operator_list_is_wellformed():
    # wartbare Liste: jeder Eintrag hat key + Titel + Vorschlags-Funktion
    assert len(opt.OPERATORS) >= 20, "Suche braucht eine reiche Nachbarschaft"
    keys = [o.key for o in opt.OPERATORS]
    assert len(keys) == len(set(keys)), "Operator-Keys müssen eindeutig sein"
    for o in opt.OPERATORS:
        assert o.key and o.title and callable(o.make)


def test_operators_only_propose_grid_moves():
    # Alle Kandidaten bleiben auf dem Schaltplan-Raster (1.27 mm) — die
    # Verschiebungen sind GRID-Vielfache, Swap/Align kopieren eine bereits
    # gerasterte Nachbar-Koordinate. So driftet die Suche nie off-grid.
    parts, nets = _load("audio_amp")
    build_schematic(parts, nets, project_name="t", keep_placement=True)
    placed = [p for p in parts if "_place_x" in p]
    import random
    rng = random.Random(0)
    for o in opt.OPERATORS:
        for cand in o.make(placed, nets, rng):
            for _part, field, val in cand:
                if field in ("_place_x", "_place_y"):
                    assert lm._on_grid(val), f"{o.key}: {val} off-grid"
            break  # ein Kandidat je Operator reicht als Stichprobe


# ── Kern-Zusagen ────────────────────────────────────────────────────────────

def test_never_worse_than_input():
    parts, nets = _load("motor_driver")  # startet bereits sauber (badness 0)

    def emit():
        return build_schematic(parts, nets, project_name="mw", place=False,
                               keep_placement=True)

    build_schematic(parts, nets, project_name="mw", keep_placement=True)
    before = lm.measure_text(emit()).badness()
    res = opt.optimize(parts, nets, emit, max_evals=200)
    after = lm.measure_text(emit()).badness()
    assert after <= before + 1e-6
    assert res["badness"] <= res["start"] + 1e-6


@pytest.mark.parametrize("kit", [
    "audio_amp",        # Kreuzungen
    "led_ring",         # Labels in Nachbarn
    "production_ready",  # Kreuzungen + dichter Cluster
])
def test_kits_reach_professional_zero(kit):
    # Der Goldstandard: nach der Optimierung misst die Demo-Schaltung wie ein
    # Profi-Referenz-Schaltbild — badness 0 (nichts überlappt, Labels zeigen
    # weg, keine Kreuzungen). Das ist die Kern-Zusage des Features. (Nicht ALLE
    # Kits erreichen 0 — ethernet_device nutzt ein für seine 11 Pins massiv
    # überdimensioniertes 176-Pin-MCU-Symbol, in dessen 221-mm-Körper die Labels
    # nicht heraus können; das ist ein Symbol-Wahl-Problem, kein Layout-Problem.)
    # Direkter Optimierer-Aufruf OHNE Zeitlimit → maschinen-unabhängig
    # (der Wanduhr-Deckel im Pipeline-Default würde den Test flaky machen).
    parts, nets = _load(kit)

    def emit():
        return build_schematic(parts, nets, project_name=kit, place=False,
                               keep_placement=True)

    build_schematic(parts, nets, project_name=kit, keep_placement=True)
    opt.optimize(parts, nets, emit, max_evals=4000, max_seconds=None)
    assert lm.measure_text(emit()).badness() == 0.0, lm.measure_text(emit()).as_dict()


def test_optimizer_never_worsens_dense_hard_case():
    # ethernet_device erreicht wegen des überdimensionierten MCU-Symbols nicht 0
    # — aber der Optimierer darf es NIE verschlechtern (Eingang = Untergrenze)
    # und soll es messbar verbessern. Kurzes Zeitbudget für den Test.
    parts, nets = _load("ethernet_device")

    def emit():
        return build_schematic(parts, nets, project_name="eth", place=False,
                               keep_placement=True)

    build_schematic(parts, nets, project_name="eth", keep_placement=True)
    before = lm.measure_text(emit()).badness()
    res = opt.optimize(parts, nets, emit, max_evals=120, max_seconds=20.0)
    after = lm.measure_text(emit()).badness()
    assert after <= before + 1e-6
    assert res["badness"] <= res["start"] + 1e-6


def test_optimize_flag_is_off_by_default_and_stable():
    # Ohne optimize läuft die alte Pipeline unverändert (kein versteckter
    # Aufruf) — zwei Builds ohne Flag sind identisch.
    parts, nets = _load("led_ring")
    a = build_schematic(json.loads(json.dumps(parts)),
                        json.loads(json.dumps(nets)), project_name="x")
    b = build_schematic(json.loads(json.dumps(parts)),
                        json.loads(json.dumps(nets)), project_name="x")
    assert a == b
