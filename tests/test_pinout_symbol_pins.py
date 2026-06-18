# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for extracting a symbol's pin list from a .kicad_sym file."""
from __future__ import annotations

import pytest

from kicad_mcp.generators.pinout.symbol_pins import extract_symbol_pins


_SYM_LIB = '''\
(kicad_symbol_lib
  (version 20231120)
  (generator "test")
  (symbol "DRV8313"
    (pin_names (offset 1.016))
    (in_bom yes)
    (on_board yes)
    (property "Reference" "U" (at 0 10 0))
    (property "Value" "DRV8313" (at 0 -10 0))
    (symbol "DRV8313_0_1"
      (rectangle (start -5 5) (end 5 -5))
    )
    (symbol "DRV8313_1_1"
      (pin power_in line (at -7.62 2.54 0) (length 2.54)
        (name "VM" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27)))))
      (pin output line (at 7.62 2.54 180) (length 2.54)
        (name "OUT1" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27)))))
      (pin input line (at -7.62 0 0) (length 2.54)
        (name "~{RESET}" (effects (font (size 1.27 1.27))))
        (number "3" (effects (font (size 1.27 1.27)))))
    )
  )
  (symbol "BASE_OPAMP"
    (symbol "BASE_OPAMP_0_1"
      (rectangle (start -5 5) (end 5 -5))
    )
    (symbol "BASE_OPAMP_1_1"
      (pin input line (at -7.62 2.54 0) (length 2.54)
        (name "IN+" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27)))))
      (pin output line (at 7.62 0 180) (length 2.54)
        (name "OUT" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27)))))
    )
  )
  (symbol "DERIVED_OPAMP"
    (extends "BASE_OPAMP")
    (property "Value" "DERIVED_OPAMP" (at 0 -10 0))
  )
  (symbol "DUAL_GATE"
    (symbol "DUAL_GATE_1_1"
      (pin input line (at -7.62 2.54 0) (length 2.54)
        (name "A1" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27)))))
    )
    (symbol "DUAL_GATE_2_1"
      (pin input line (at -7.62 2.54 0) (length 2.54)
        (name "A2" (effects (font (size 1.27 1.27))))
        (number "4" (effects (font (size 1.27 1.27)))))
    )
  )
)
'''


@pytest.fixture()
def sym_file(tmp_path):
    p = tmp_path / "TestLib.kicad_sym"
    p.write_text(_SYM_LIB, encoding="utf-8")
    return str(p)


def test_happy_number_name_type(sym_file):
    res = extract_symbol_pins(sym_file, "DRV8313")
    assert res["success"] is True
    assert res["pin_count"] == 3
    pins = {p["num"]: p for p in res["pins"]}
    assert pins["1"] == {"num": "1", "name": "VM", "type": "power_in"}
    assert pins["2"]["type"] == "output"
    assert pins["3"]["name"] == "~{RESET}"  # raw, normalisation happens in diff
    assert "extends" not in res


def test_extends_symbol_inlines_base_pins(sym_file):
    res = extract_symbol_pins(sym_file, "DERIVED_OPAMP")
    assert res["success"] is True
    assert res["extends"] == "BASE_OPAMP"
    assert res["pin_count"] == 2
    names = {p["name"] for p in res["pins"]}
    assert names == {"IN+", "OUT"}


def test_multi_unit_collects_all_units(sym_file):
    res = extract_symbol_pins(sym_file, "DUAL_GATE")
    assert res["success"] is True
    nums = {p["num"] for p in res["pins"]}
    assert nums == {"1", "4"}


def test_missing_file_error_path(tmp_path):
    res = extract_symbol_pins(str(tmp_path / "nope.kicad_sym"), "X")
    assert res["success"] is False
    assert "not found" in res["error"].lower()


def test_missing_symbol_error_path(sym_file):
    res = extract_symbol_pins(sym_file, "DOES_NOT_EXIST")
    assert res["success"] is False
    assert "not found" in res["error"].lower()
