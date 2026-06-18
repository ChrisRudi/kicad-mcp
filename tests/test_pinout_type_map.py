# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for pinout type-mapping + pin-name normalisation."""
from __future__ import annotations

import pytest

from kicad_mcp.generators.symbol_author import VALID_PIN_TYPES
from kicad_mcp.generators.pinout.type_map import (
    map_datasheet_type,
    normalize_pin_name,
)


# Every (raw token, expected KiCad type) the spec table prescribes.
_MAPPING_CASES = [
    ("I", "input"), ("IN", "input"), ("INPUT", "input"), ("DI", "input"),
    ("O", "output"), ("OUT", "output"), ("OUTPUT", "output"), ("DO", "output"),
    ("I/O", "bidirectional"), ("IO", "bidirectional"), ("B", "bidirectional"),
    ("BIDIR", "bidirectional"), ("DIO", "bidirectional"),
    ("P", "power_in"), ("PWR", "power_in"), ("POWER", "power_in"),
    ("SUPPLY", "power_in"), ("VCC", "power_in"), ("VDD", "power_in"),
    ("VS", "power_in"), ("VM", "power_in"), ("VIN", "power_in"),
    ("G", "power_in"), ("GND", "power_in"), ("GROUND", "power_in"),
    ("VSS", "power_in"), ("RTN", "power_in"),
    ("EP", "power_in"), ("PAD", "power_in"), ("POWERPAD", "power_in"),
    ("PO", "power_out"), ("VREF_OUT", "power_out"), ("LDO_OUT", "power_out"),
    ("OC", "open_collector"), ("OD", "open_collector"),
    ("OPEN-DRAIN", "open_collector"), ("OPEN-COLLECTOR", "open_collector"),
    ("PAS", "passive"), ("PASSIVE", "passive"),
    ("NC", "no_connect"), ("N/C", "no_connect"), ("DNC", "no_connect"),
]


@pytest.mark.parametrize("raw,expected", _MAPPING_CASES)
def test_map_each_table_row(raw, expected):
    assert map_datasheet_type(raw) == expected
    assert expected in VALID_PIN_TYPES


def test_map_is_case_and_punctuation_insensitive():
    assert map_datasheet_type("  i/o ") == "bidirectional"
    assert map_datasheet_type("(GND)") == "power_in"


def test_unknown_token_returns_none():
    assert map_datasheet_type("ZZZ") is None
    assert map_datasheet_type("") is None
    assert map_datasheet_type("   ") is None


def test_map_accepts_exact_kicad_type_name():
    assert map_datasheet_type("tri_state") == "tri_state"
    assert map_datasheet_type("open_emitter") == "open_emitter"


@pytest.mark.parametrize("raw", [
    "~{RESET}", "~RESET", "nRESET", "/RESET", "RESET#", "RESET_N",
])
def test_active_low_forms_collapse_to_single_token(raw):
    assert normalize_pin_name(raw) == "~RESET"


def test_unicode_overline_active_low():
    assert normalize_pin_name("RESET̅") == "~RESET"


def test_non_active_low_names_preserved():
    assert normalize_pin_name("VCC") == "VCC"
    assert normalize_pin_name("NC") == "NC"  # leading N must NOT be eaten
    assert normalize_pin_name(" gpio42 ") == "GPIO42"


def test_separators_unified_and_suffix_kept():
    # functional suffixes are NOT stripped (name fidelity)
    assert normalize_pin_name("OUT-1") == "OUT_1"
    assert normalize_pin_name("OUT.1") == "OUT_1"
    assert normalize_pin_name("SPEED_PWM") == "SPEED_PWM"


def test_empty_name():
    assert normalize_pin_name("") == ""
    assert normalize_pin_name(None) == ""
