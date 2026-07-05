# SPDX-License-Identifier: GPL-3.0-or-later
"""Nutzer-Regel „billig, aber Profi-Standard": Geht ein R, C oder L an ein
Power-Netz (GND/VCC/3V3 …), steht er SENKRECHT — Rotation 0/180, Pins
oben/unten, das Power-Symbol direkt darüber/darunter. So zeichnet es jedes
Profi-Schaltbild (Pull-up, Pull-down, Abblock-C). Verankert in
``place._orient_power_passives``; ``_rot_locked`` schützt die Konvention
davor, dass der Layout-Optimierer sie wieder wegdreht."""

from __future__ import annotations

import json
import os

from kicad_mcp.generators.schematic import layout_optimizer as opt
from kicad_mcp.generators.schematic import place as pl
from kicad_mcp.generators.schematic.builder import build_schematic

_KIT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "kicad_mcp", "resources", "data", "demo_kits")


def _load(kit: str):
    with open(os.path.join(_KIT_DIR, f"{kit}.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    return (json.loads(json.dumps(spec["parts"])),
            json.loads(json.dumps(spec["nets"])))


def _power_rcl(parts: list[dict], nets: list[dict]) -> list[dict]:
    """Alle 2-Pin-R/C/L, die an einem Power-Netz hängen."""
    power_refs: set[str] = set()
    for net in nets:
        if net.get("type") == "power":
            for conn in net.get("connections", []):
                power_refs.add(conn.split(":")[0])
    out = []
    for p in parts:
        ref = p.get("ref", "")
        prefix = "".join(c for c in ref if c.isalpha())
        if prefix in ("R", "C", "L") and ref in power_refs \
                and len(p.get("pins", [])) == 2:
            out.append(p)
    return out


# ── Einheit: die Regel selbst ───────────────────────────────────────────────

def test_orient_rotates_horizontal_passive_and_locks():
    parts = [{"ref": "R1", "value": "10k", "_rotation": 90,
              "pins": [{"num": "1"}, {"num": "2"}]},
             {"ref": "R2", "value": "10k", "_rotation": 90,
              "pins": [{"num": "1"}, {"num": "2"}]}]
    nets = [{"name": "GND", "type": "power", "connections": ["R1:2"]},
            {"name": "SIG", "connections": ["R2:1"]}]
    pl._orient_power_passives(parts, nets)
    # R1 hängt an GND → senkrecht + gesperrt
    assert parts[0]["_rotation"] == 0 and parts[0]["_rot_locked"]
    # R2 ist reines Signal-Teil → unangetastet
    assert parts[1]["_rotation"] == 90
    assert "_rot_locked" not in parts[1]


def test_orient_keeps_existing_vertical_and_ignores_ics():
    parts = [{"ref": "C1", "value": "100n", "_rotation": 180,
              "pins": [{"num": "1"}, {"num": "2"}]},
             {"ref": "U1", "value": "NE555", "_rotation": 90,
              "pins": [{"num": str(i)} for i in range(1, 9)]}]
    nets = [{"name": "VCC", "type": "power",
             "connections": ["C1:1", "U1:8"]}]
    pl._orient_power_passives(parts, nets)
    assert parts[0]["_rotation"] == 180  # 180 ist auch senkrecht — bleibt
    assert parts[1]["_rotation"] == 90   # ICs sind ausgenommen
    assert "_rot_locked" not in parts[1]


# ── Optimierer respektiert die Sperre ───────────────────────────────────────

def test_optimizer_apply_skips_locked_rotation():
    part = {"ref": "R1", "_rotation": 0, "_rot_locked": True}
    opt._apply([(part, "_rotation", 90)])
    assert part["_rotation"] == 0, "gesperrte Drehung muss ignoriert werden"
    opt._apply([(part, "_rotation", 180)])
    assert part["_rotation"] == 180, "0↔180 bleibt erlaubt (beides senkrecht)"
    opt._apply([(part, "_place_x", 12.7)])
    assert part["_place_x"] == 12.7, "Verschieben bleibt immer erlaubt"


# ── Ende-zu-Ende: nach dem vollen Build (inkl. Optimierer) hält die Regel ───

def test_power_passives_vertical_after_full_build():
    parts, nets = _load("production_ready")
    build_schematic(parts, nets, project_name="t",
                    optimize=True, optimize_evals=200, optimize_seconds=5.0)
    hits = _power_rcl(parts, nets)
    assert hits, "Kit muss Power-R/C/L enthalten, sonst prüft der Test nichts"
    for p in hits:
        assert int(p.get("_rotation", 0)) % 180 == 0, \
            f"{p['ref']} an Power-Netz muss senkrecht stehen"
