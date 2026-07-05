# SPDX-License-Identifier: GPL-3.0-or-later
"""Power-Rail-Namen (``P5V`` / ``P3V3`` / ``5V`` / ``3V3`` …) müssen auf die
kompakten KiCad-Power-Symbole normalisieren — sonst landen sie als wiederholte
Text-Label (der Text-Stau, den wir gegen die Original-Schaltbilder abbauen)."""

from __future__ import annotations

from kicad_mcp.generators.schematic.route import (
    get_power_symbol_info, _normalize_power_name,
)


def test_common_rail_names_map_to_power_symbols():
    assert get_power_symbol_info("P5V") == ("power:+5V", "supply")
    assert get_power_symbol_info("P3V3") == ("power:+3V3", "supply")
    assert get_power_symbol_info("5V") == ("power:+5V", "supply")
    assert get_power_symbol_info("3V3") == ("power:+3V3", "supply")
    assert get_power_symbol_info("3.3V") == ("power:+3V3", "supply")
    # kanonische Namen bleiben unverändert erkannt
    assert get_power_symbol_info("GND") == ("power:GND", "ground")
    assert get_power_symbol_info("VCC") == ("power:VCC", "supply")


def test_signal_names_are_not_power():
    assert get_power_symbol_info("DATA0") is None
    assert get_power_symbol_info("REF_CLK") is None
    assert _normalize_power_name("MDIO") is None
