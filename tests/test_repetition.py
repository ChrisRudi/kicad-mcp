# SPDX-License-Identifier: GPL-3.0-or-later
"""Nutzer-Regel: „Wiederholung im Schaltplan sollte zu gleichartigen
Schaltungsteilen führen — oder Symmetrie." Wiederholte Teilschaltungen
(Multivibrator-Hälften, LED-Ketten-Glieder) werden strukturell erkannt
(``common/repetition``), identisch gestempelt und in Leseordnung gestellt
(``place._uniform_repeated_units``); der Layout-Optimierer bewegt solche
Einheiten nur noch starr. Dazu: „auf dem Blatt zentrieren" —
``place._center_on_sheet`` schiebt das Gesamt-Layout in die Blattmitte."""

from __future__ import annotations

import json
import os

from kicad_mcp.generators.common.constants import SHEET_H, SHEET_W
from kicad_mcp.generators.common.repetition import find_repeated_units
from kicad_mcp.generators.schematic import layout_optimizer as opt
from kicad_mcp.generators.schematic import place as pl

_KIT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "kicad_mcp", "resources", "data", "demo_kits")


def _load_kit(kit: str):
    with open(os.path.join(_KIT_DIR, f"{kit}.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    return (json.loads(json.dumps(spec["parts"])),
            json.loads(json.dumps(spec["nets"])))


def _multivib():
    parts = [
        {"ref": "Q1", "value": "BC547",
         "pins": [{"num": "1"}, {"num": "2"}, {"num": "3"}]},
        {"ref": "Q2", "value": "BC547",
         "pins": [{"num": "1"}, {"num": "2"}, {"num": "3"}]},
        {"ref": "R1", "value": "470R", "pins": [{"num": "1"}, {"num": "2"}]},
        {"ref": "R2", "value": "47k", "pins": [{"num": "1"}, {"num": "2"}]},
        {"ref": "R3", "value": "47k", "pins": [{"num": "1"}, {"num": "2"}]},
        {"ref": "R4", "value": "470R", "pins": [{"num": "1"}, {"num": "2"}]},
        {"ref": "C1", "value": "2.2u", "pins": [{"num": "1"}, {"num": "2"}]},
        {"ref": "C2", "value": "2.2u", "pins": [{"num": "1"}, {"num": "2"}]},
        {"ref": "D1", "value": "LED", "pins": [{"num": "1"}, {"num": "2"}]},
        {"ref": "D2", "value": "LED", "pins": [{"num": "1"}, {"num": "2"}]},
        {"ref": "J1", "value": "PWR", "pins": [{"num": "1"}, {"num": "2"}]},
    ]
    nets = [
        {"name": "VCC", "type": "power",
         "connections": ["J1:1", "R1:1", "R2:1", "R3:1", "R4:1"]},
        {"name": "GND", "type": "power",
         "connections": ["J1:2", "Q1:3", "Q2:3"]},
        {"name": "LED1_A", "connections": ["R1:2", "D1:2"]},
        {"name": "Q1_C", "connections": ["D1:1", "Q1:1", "C1:1"]},
        {"name": "Q1_B", "connections": ["Q1:2", "R2:2", "C2:2"]},
        {"name": "LED2_A", "connections": ["R4:2", "D2:2"]},
        {"name": "Q2_C", "connections": ["D2:1", "Q2:1", "C2:1"]},
        {"name": "Q2_B", "connections": ["Q2:2", "R3:2", "C1:2"]},
    ]
    return parts, nets


# ── Erkennung ────────────────────────────────────────────────────────────────

def test_multivibrator_halves_are_detected():
    parts, nets = _multivib()
    units = find_repeated_units(parts, nets)
    assert len(units) == 2
    assert units[0][0] == "Q1" and units[1][0] == "Q2"  # Leseordnung
    assert len(units[0]) == len(units[1]) == 5
    # Kreuzkopplungs-C und Basis-R korrekt der näheren Hälfte zugeordnet
    assert "C1" in units[0] and "C2" in units[1]


def test_led_chain_members_in_reading_order():
    parts, nets = _load_kit("led_ring")
    units = find_repeated_units(parts, nets)
    assert [u[0] for u in units] == ["D1", "D2", "D3", "D4", "D5", "D6"]


def test_no_false_positives_on_non_repetitive_kits():
    for kit in ("usb_sensor_hub", "ethernet_device", "production_ready",
                "audio_amp", "buck_converter"):
        parts, nets = _load_kit(kit)
        assert find_repeated_units(parts, nets) == [], kit


# ── Uniforme Platzierung ─────────────────────────────────────────────────────

def test_units_get_identical_relative_layout():
    parts, nets = _multivib()
    pl.place_schematic(parts, nets)
    by_ref = {p["ref"]: p for p in parts}
    units = find_repeated_units(parts, nets)

    def rel(unit):
        ax, ay = by_ref[unit[0]]["_place_x"], by_ref[unit[0]]["_place_y"]
        return [(round(by_ref[r]["_place_x"] - ax, 2),
                 round(by_ref[r]["_place_y"] - ay, 2),
                 int(by_ref[r].get("_rotation", 0))) for r in unit]

    assert rel(units[0]) == rel(units[1]), \
        "beide Hälften müssen identisch aussehen (Symmetrie)"
    # Leseordnung: Einheit 2 rechts von Einheit 1
    assert by_ref["Q2"]["_place_x"] > by_ref["Q1"]["_place_x"]
    for unit in units:
        for r in unit:
            # EINE Formation: alle Instanzen teilen die Gruppen-Id, der
            # Optimierer bewegt die ganze Reihe starr (Leseordnung bleibt).
            assert by_ref[r].get("_rep_unit") == 0


# ── Optimierer: starr verschieben, nie einzeln drehen ───────────────────────

def test_optimizer_moves_units_rigidly():
    a = {"ref": "Q1", "_place_x": 10.0, "_place_y": 10.0, "_rep_unit": 0}
    b = {"ref": "R1", "_place_x": 15.0, "_place_y": 12.0, "_rep_unit": 0}
    opt._UNIT_GROUPS.clear()
    opt._UNIT_GROUPS[0] = [a, b]
    try:
        opt._apply([(a, "_place_x", 12.54)])
        assert a["_place_x"] == 12.54
        assert b["_place_x"] == 17.54, "Mitglied muss mitwandern (starr)"
        opt._apply([(a, "_rotation", 90)])
        assert "_rotation" not in a, "Einzeldrehung bräche die Gleichartigkeit"
    finally:
        opt._UNIT_GROUPS.clear()


# ── Blatt-Zentrierung ────────────────────────────────────────────────────────

def test_layout_is_centered_on_sheet():
    for kit in ("kit_seeding", "buck_converter"):
        parts, nets = _load_kit(kit)
        pl.place_schematic(parts, nets)
        xs = [p["_place_x"] for p in parts if "_place_x" in p]
        ys = [p["_place_y"] for p in parts if "_place_y" in p]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        assert abs(cx - SHEET_W / 2) <= 1.27, f"{kit}: X-Mitte {cx}"
        assert abs(cy - SHEET_H / 2) <= 1.27, f"{kit}: Y-Mitte {cy}"
