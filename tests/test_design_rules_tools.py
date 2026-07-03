# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end test for the Design-Wächter tool (audit_design). Headless."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools.design_rules_tools import register_design_rules_tools


def _fp(ref, value, pads):
    pad_lines = "\n".join(
        f'\t\t(pad "{pn}" smd rect (at {i} 0) (layers "F.Cu") (net {i+1} "{net}"))'
        for i, (pn, net) in enumerate(pads))
    return (f'\t(footprint "X" (layer "F.Cu")\n\t\t(uuid "{ref}")\n'
            f'\t\t(at 10 10 0)\n'
            f'\t\t(property "Reference" "{ref}" (at 0 0 0))\n'
            f'\t\t(property "Value" "{value}" (at 0 0 0))\n{pad_lines}\n\t)')


def _board(*fps):
    return "(kicad_pcb (version 20240108)\n" + "\n".join(fps) + "\n)\n"


_NO_PULLUPS = _board(
    _fp("U1", "MCU", [("1", "SDA"), ("2", "SCL")]),
    _fp("U2", "SNS", [("1", "SDA"), ("2", "SCL")]))


def _register(monkeypatch, pcb_text):
    captured = {}
    mcp = FastMCP("t")
    orig = mcp.tool

    def _cap(*a, **k):
        deco = orig(*a, **k)
        def wrap(fn):
            captured[fn.__name__] = fn
            return deco(fn)
        return wrap
    monkeypatch.setattr(mcp, "tool", _cap)
    register_design_rules_tools(mcp)
    import kicad_mcp.tools.design_rules_tools as t
    monkeypatch.setattr(t, "get_text", lambda _p: pcb_text)
    monkeypatch.setattr(t.os.path, "isfile", lambda _p: True)
    return captured["audit_design"]


def test_audit_design_flags_and_lists_rules(monkeypatch, tmp_path):
    tool = _register(monkeypatch, _NO_PULLUPS)
    out = tool(str(tmp_path / "b.kicad_pcb"))
    assert out["success"]
    assert {i["net"] for i in out["issues"]} == {"SDA", "SCL"}
    assert out["summary"]["warnings"] == 2
    keys = {r["key"] for r in out["available_rules"]}
    assert {"i2c_pullups", "crystal_load_caps"} <= keys


def test_audit_design_rule_subset(monkeypatch, tmp_path):
    tool = _register(monkeypatch, _NO_PULLUPS)
    # run only the crystal rule → the I²C issues are not reported
    out = tool(str(tmp_path / "b.kicad_pcb"), rules="crystal_load_caps")
    assert out["success"] and out["issues"] == []


def test_missing_file():
    captured = {}
    mcp = FastMCP("t")
    orig = mcp.tool

    def _cap(*a, **k):
        deco = orig(*a, **k)
        def wrap(fn):
            captured[fn.__name__] = fn
            return deco(fn)
        return wrap
    mcp.tool = _cap  # type: ignore
    register_design_rules_tools(mcp)
    out = captured["audit_design"]("/no/such.kicad_pcb")
    assert out["success"] is False
