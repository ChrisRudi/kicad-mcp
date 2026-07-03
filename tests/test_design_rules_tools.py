# SPDX-License-Identifier: GPL-3.0-or-later
"""Design-Wächter tests: semantic checks KiCad's ERC lacks (I²C pull-ups).
Headless — text parse + bus inference, no KiCad."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools.design_rules_tools import register_design_rules_tools


def _fp(ref, value, pads):
    """pads: list of (padname, netname)."""
    pad_lines = "\n".join(
        f'\t\t(pad "{pn}" smd rect (at {i} 0) (layers "F.Cu") (net {i+1} "{net}"))'
        for i, (pn, net) in enumerate(pads)
    )
    return (f'\t(footprint "X" (layer "F.Cu")\n'
            f'\t\t(uuid "{ref}")\n\t\t(at 10 10 0)\n'
            f'\t\t(property "Reference" "{ref}" (at 0 0 0))\n'
            f'\t\t(property "Value" "{value}" (at 0 0 0))\n'
            f'{pad_lines}\n\t)')


def _board(*fps):
    return "(kicad_pcb (version 20240108)\n" + "\n".join(fps) + "\n)\n"


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
    import kicad_mcp.tools.design_rules_tools as dr
    monkeypatch.setattr(dr, "get_text", lambda _p: pcb_text)
    monkeypatch.setattr(dr.os.path, "isfile", lambda _p: True)
    return captured["audit_bus_rules"]


# I²C with proper pull-ups: R1 SDA↔VCC, R2 SCL↔VCC → no issue.
_WITH_PULLUPS = _board(
    _fp("U1", "MCU", [("1", "SDA"), ("2", "SCL")]),
    _fp("U2", "SENSOR", [("1", "SDA"), ("2", "SCL")]),
    _fp("R1", "4k7", [("1", "SDA"), ("2", "VCC")]),
    _fp("R2", "4k7", [("1", "SCL"), ("2", "VCC")]),
)

# Same board, no pull-up resistors → SDA and SCL both flagged.
_NO_PULLUPS = _board(
    _fp("U1", "MCU", [("1", "SDA"), ("2", "SCL")]),
    _fp("U2", "SENSOR", [("1", "SDA"), ("2", "SCL")]),
)


def test_pullups_present_no_issue(monkeypatch, tmp_path):
    tool = _register(monkeypatch, _WITH_PULLUPS)
    out = tool(str(tmp_path / "b.kicad_pcb"))
    assert out["success"] and out["issues"] == []
    assert "I2C" in out["buses_checked"]


def test_missing_pullups_flagged(monkeypatch, tmp_path):
    tool = _register(monkeypatch, _NO_PULLUPS)
    out = tool(str(tmp_path / "b.kicad_pcb"))
    assert out["success"]
    flagged = {i["net"] for i in out["issues"]}
    assert flagged == {"SDA", "SCL"}
    assert all(i["rule"] == "i2c_missing_pullup" for i in out["issues"])


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
    out = captured["audit_bus_rules"]("/no/such.kicad_pcb")
    assert out["success"] is False
