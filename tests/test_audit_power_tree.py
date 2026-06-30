# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for `_audit_power_tree` net detection.

Bug (2026-06-30): pad nets are written both as ``(net 5 "+5V")`` (number +
name) and ``(net "+5V")`` (name only). The pad-net regex required a number,
so name-only boards reported 0 power nets. Both spellings must now resolve.
"""
from kicad_mcp.tools.audit_tools import _audit_power_tree


def _board(net_line_a: str, net_line_b: str) -> str:
    # Tab-indented footprint with the `) (uuid …) (at …)` anchor the parser
    # keys on, a source-hinted ref (LDO) and two pads on +5V.
    return (
        "(kicad_pcb\n"
        '\t(footprint "Lib:LDO"\n'
        '\t\t(layer "F.Cu")\n'
        '\t\t(uuid "aaaa")\n'
        "\t\t(at 10 10)\n"
        '\t\t(property "Reference" "U_LDO1")\n'
        '\t\t(property "Value" "AP2112K")\n'
        '\t\t(pad "1" smd roundrect\n'
        "\t\t\t(at 0 0)\n"
        f"\t\t\t{net_line_a}\n"
        "\t\t)\n"
        '\t\t(pad "2" smd roundrect\n'
        "\t\t\t(at 1 0)\n"
        f"\t\t\t{net_line_b}\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )


def test_name_only_net_detected():
    r = _audit_power_tree(_board('(net "+5V")', '(net "+5V")'), 10.0)
    assert r["success"] is True
    assert r["n_power_nets"] >= 1
    assert "+5V" in r["rails"]


def test_number_and_name_net_detected():
    r = _audit_power_tree(_board('(net 5 "+5V")', '(net 5 "+5V")'), 10.0)
    assert r["success"] is True
    assert "+5V" in r["rails"]


def test_unconnected_pads_yield_no_power_net():
    r = _audit_power_tree(_board('(net 0 "")', '(net "")'), 10.0)
    assert r["success"] is True
    assert r["n_power_nets"] == 0
