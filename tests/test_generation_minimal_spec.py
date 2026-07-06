# SPDX-License-Identifier: GPL-3.0-or-later
"""Feld-Regression (0.24.1): Ein LLM liefert Specs MINIMAL — ref + value +
Pins mit Nummern. Der Validator hatte name/footprint/Pin-name/Pin-type als
Pflicht erzwungen und den Feld-Agenten in eine Fehlerwand laufen lassen
(„zeichne einen astabilen Multivibrator" → 5 Retries → PowerShell-Flucht).
Seitdem: ``normalize_parts`` ergänzt Ableitbares, abgelehnt wird nur echt
Unbrauchbares, und die Fehler-Results tragen einen Minimal-Spec-Hint."""

from __future__ import annotations

import asyncio
import json

from kicad_mcp.generators.validator import normalize_parts, validate_all


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


_MINIMAL_PARTS = [
    {"ref": "Q1", "value": "BC547",
     "pins": [{"num": "1", "name": "C"}, {"num": "2", "name": "B"},
              {"num": "3", "name": "E"}]},
    {"ref": "R1", "value": "47k", "pins": [{"num": "1"}, {"num": "2"}]},
    {"ref": "C1", "value": "2.2u", "pins": [{"num": "1"}, {"num": "2"}]},
    {"ref": "D1", "value": "LED",
     "pins": [{"num": "1", "name": "K"}, {"num": "2", "name": "A"}]},
    {"ref": "J1", "value": "PWR", "pins": [{"num": "1"}, {"num": "2"}]},
]
_NETS = [
    {"name": "VCC", "type": "power", "connections": ["J1:1", "R1:1"]},
    {"name": "GND", "type": "power", "connections": ["J1:2", "Q1:3"]},
    {"name": "SIG", "connections": ["R1:2", "Q1:2", "C1:1"]},
    {"name": "OUT", "connections": ["Q1:1", "D1:1"]},
    {"name": "LED_A", "connections": ["D1:2", "C1:2"]},
]


def test_minimal_spec_validates_clean():
    parts = json.loads(json.dumps(_MINIMAL_PARTS))
    assert validate_all(parts, json.loads(json.dumps(_NETS))) == []
    # Normalisierung hat die abgeleiteten Felder gesetzt
    assert parts[1]["name"] == "47k"
    assert parts[1]["footprint"] == ""
    assert parts[1]["pins"][0]["name"] == "1"
    assert parts[1]["pins"][0]["type"] == "passive"


def test_normalize_keeps_explicit_fields():
    parts = [{"ref": "U1", "name": "NE555", "value": "NE555",
              "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
              "pins": [{"num": "1", "name": "GND", "type": "power_in"}]}]
    normalize_parts(parts)
    assert parts[0]["name"] == "NE555"
    assert parts[0]["footprint"].startswith("Package_SO")
    assert parts[0]["pins"][0] == {"num": "1", "name": "GND",
                                   "type": "power_in"}


def test_truly_broken_specs_still_fail_with_hint():
    # ref fehlt / Pins fehlen / doppelte ref → weiterhin Fehler
    errs = validate_all([{"value": "47k"}], _NETS)
    assert any("ref" in e for e in errs)
    errs = validate_all([{"ref": "R1", "value": "a",
                          "pins": [{"num": "1"}]},
                         {"ref": "R1", "value": "b",
                          "pins": [{"num": "1"}]}], _NETS)
    assert any("duplicate ref" in e for e in errs)

    # Tool-Result trägt den Minimal-Spec-Hint
    from kicad_mcp.tools.generation_tools import register_generation_tools
    mcp = _FakeMCP()
    register_generation_tools(mcp)
    r = asyncio.run(mcp.tools["generate_schematic"](
        output_path="/tmp/nie-geschrieben.kicad_sch",
        parts=json.dumps([{"value": "kaputt"}]), nets=json.dumps(_NETS)))
    assert r["success"] is False
    assert "Minimal-Spec" in r.get("hint", "")


def test_minimal_spec_generates_schematic(tmp_path):
    from kicad_mcp.tools.generation_tools import register_generation_tools
    mcp = _FakeMCP()
    register_generation_tools(mcp)
    out = tmp_path / "mini.kicad_sch"
    r = asyncio.run(mcp.tools["generate_schematic"](
        output_path=str(out),
        parts=json.dumps(_MINIMAL_PARTS), nets=json.dumps(_NETS)))
    assert r["success"] is True, r
    text = out.read_text(encoding="utf-8")
    for ref in ("Q1", "R1", "C1", "D1", "J1"):
        assert f'"{ref}"' in text


def test_fuzzy_symbol_never_changes_passive_class():
    """Universaltest-Fund (0.25.1): value "100n" als Suchname matchte den FET
    "BSC100N10NSFG" — ein Kondensator mit MOSFET-Symbol, dessen fremde
    Pin-Geometrie einen echten Kurzschluss erzeugte. 2-Pin-R/C/L/D dürfen
    per Fuzzy-Suche nie die Bauteilklasse wechseln."""
    from kicad_mcp.generators.symbol_lib import resolve_lib_id
    for ref, value, family in (
            ("C4", "100n", "Device:C"),
            ("R9", "330R", "Device:R"),
            ("L2", "10uH", "Device:L"),
    ):
        part = {"ref": ref, "name": value, "value": value,
                "pins": [{"num": "1"}, {"num": "2"}]}
        lib_id = resolve_lib_id(part)
        assert lib_id.startswith(family), f"{ref} ({value}) → {lib_id}"
