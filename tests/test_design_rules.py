# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Design-Wächter rule registry (utils/design_rules). Headless —
board text → context → rules, no KiCad."""

from __future__ import annotations

from kicad_mcp.utils import design_rules as dr


def _fp(ref, value, pads):
    """pads: list of (padname, netname)."""
    pad_lines = "\n".join(
        f'\t\t(pad "{pn}" smd rect (at {i} 0) (layers "F.Cu") (net {i+1} "{net}"))'
        for i, (pn, net) in enumerate(pads))
    return (f'\t(footprint "X" (layer "F.Cu")\n\t\t(uuid "{ref}")\n'
            f'\t\t(at 10 10 0)\n'
            f'\t\t(property "Reference" "{ref}" (at 0 0 0))\n'
            f'\t\t(property "Value" "{value}" (at 0 0 0))\n{pad_lines}\n\t)')


def _board(*fps):
    return "(kicad_pcb (version 20240108)\n" + "\n".join(fps) + "\n)\n"


def test_registry_has_expected_rules():
    keys = {r.key for r in dr.RULES}
    assert {"i2c_pullups", "crystal_load_caps"} <= keys
    cat = dr.rule_catalog()
    assert all({"key", "title", "severity"} <= set(c) for c in cat)


def test_context_built_once_has_power_and_buses():
    board = _board(
        _fp("U1", "MCU", [("1", "SDA"), ("2", "SCL"), ("3", "VCC")]),
        _fp("U2", "SNS", [("1", "SDA"), ("2", "SCL")]))
    ctx = dr.build_context(board)
    assert "VCC" in ctx.power
    assert any(b["kind"] == "I2C" for b in ctx.buses)


def test_i2c_pullups_rule():
    ok = _board(
        _fp("U1", "MCU", [("1", "SDA"), ("2", "SCL")]),
        _fp("U2", "SNS", [("1", "SDA"), ("2", "SCL")]),
        _fp("R1", "4k7", [("1", "SDA"), ("2", "VCC")]),
        _fp("R2", "4k7", [("1", "SCL"), ("2", "VCC")]))
    assert dr.run_rules(dr.build_context(ok), only={"i2c_pullups"}) == []

    bad = _board(
        _fp("U1", "MCU", [("1", "SDA"), ("2", "SCL")]),
        _fp("U2", "SNS", [("1", "SDA"), ("2", "SCL")]))
    issues = dr.run_rules(dr.build_context(bad), only={"i2c_pullups"})
    assert {i["net"] for i in issues} == {"SDA", "SCL"}


def test_crystal_load_caps_rule():
    # Y1 with load caps C1 (XIN↔GND), C2 (XOUT↔GND) → clean
    ok = _board(
        _fp("Y1", "16MHz", [("1", "XIN"), ("2", "XOUT")]),
        _fp("C1", "18pF", [("1", "XIN"), ("2", "GND")]),
        _fp("C2", "18pF", [("1", "XOUT"), ("2", "GND")]))
    assert dr.run_rules(dr.build_context(ok), only={"crystal_load_caps"}) == []

    # missing the XOUT cap → one issue on XOUT
    bad = _board(
        _fp("Y1", "16MHz", [("1", "XIN"), ("2", "XOUT")]),
        _fp("C1", "18pF", [("1", "XIN"), ("2", "GND")]))
    issues = dr.run_rules(dr.build_context(bad), only={"crystal_load_caps"})
    assert [i["net"] for i in issues] == ["XOUT"]


def test_crystal_detected_by_value_when_ref_not_Y():
    bad = _board(_fp("U9", "8MHz XTAL", [("1", "OSC_IN"), ("2", "OSC_OUT")]))
    issues = dr.run_rules(dr.build_context(bad), only={"crystal_load_caps"})
    assert {i["net"] for i in issues} == {"OSC_IN", "OSC_OUT"}


def test_run_all_rules_combines():
    board = _board(
        _fp("U1", "MCU", [("1", "SDA"), ("2", "SCL")]),
        _fp("U2", "SNS", [("1", "SDA"), ("2", "SCL")]),
        _fp("Y1", "16MHz", [("1", "XIN"), ("2", "XOUT")]))
    issues = dr.run_rules(dr.build_context(board))
    rules_hit = {i["rule"] for i in issues}
    assert "i2c_missing_pullup" in rules_hit
    assert "crystal_missing_load_cap" in rules_hit
