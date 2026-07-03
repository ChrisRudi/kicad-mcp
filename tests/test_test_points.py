# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Test-Punkt-Wächter (utils/test_points + audit_test_points).
Headless — board text → context → coverage, no KiCad."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils import design_rules as dr
from kicad_mcp.utils import test_points as tp


def _fp(ref, value, pads, fpid="X"):
    pad_lines = "\n".join(
        f'\t\t(pad "{pn}" smd rect (at {i} 0) (layers "F.Cu") (net {i+1} "{net}"))'
        for i, (pn, net) in enumerate(pads))
    return (f'\t(footprint "{fpid}" (layer "F.Cu")\n\t\t(uuid "{ref}")\n'
            f'\t\t(at 10 10 0)\n'
            f'\t\t(property "Reference" "{ref}" (at 0 0 0))\n'
            f'\t\t(property "Value" "{value}" (at 0 0 0))\n{pad_lines}\n\t)')


def _board(*fps):
    return "(kicad_pcb (version 20240108)\n" + "\n".join(fps) + "\n)\n"


def test_ranks_and_flags_blind_critical_nets():
    # +3V3 rail reaches U1 only (no test point) → blind power net.
    board = _board(
        _fp("U1", "MCU", [("1", "+3V3"), ("2", "GND"), ("3", "NRST"),
                          ("4", "SDA"), ("5", "SCL")]),
        _fp("U2", "SNS", [("1", "SDA"), ("2", "SCL")]))
    rep = tp.evaluate_test_points(dr.build_context(board))
    # critical nets: +3V3 (power), NRST (reset), SDA+SCL (bus) = 4, all blind
    assert rep["critical_total"] == 4
    assert rep["critical_covered"] == 0
    assert rep["coverage_pct"] == 0.0
    blind = {b["net"] for b in rep["blind_nets"]}
    assert blind == {"+3V3", "NRST", "SDA", "SCL"}


def test_test_point_and_connector_cover_nets():
    board = _board(
        _fp("U1", "MCU", [("1", "+3V3"), ("2", "GND"), ("3", "NRST")]),
        _fp("TP1", "3V3", [("1", "+3V3")]),               # test point on rail
        _fp("J1", "HDR", [("1", "NRST"), ("2", "GND")]))  # connector on reset
    rep = tp.evaluate_test_points(dr.build_context(board))
    assert rep["critical_total"] == 2         # +3V3, NRST
    assert rep["critical_covered"] == 2
    assert rep["coverage_pct"] == 100.0
    v33 = next(r for r in rep["nets"] if r["net"] == "+3V3")
    assert v33["covered"] and v33["via"] == ["TP1"]


def test_testpoint_by_fpid_keyword():
    board = _board(
        _fp("U1", "MCU", [("1", "CLK"), ("2", "GND")]),
        _fp("X1", "", [("1", "CLK")], fpid="TestPoint:TestPoint_Pad_D1.5mm"))
    rep = tp.evaluate_test_points(dr.build_context(board))
    clk = next(r for r in rep["nets"] if r["net"] == "CLK")
    assert clk["priority"] == "clock" and clk["covered"]


def test_ground_not_audited_and_signals_optional():
    board = _board(
        _fp("U1", "MCU", [("1", "+5V"), ("2", "GND"), ("3", "LED_A")]))
    ctx = dr.build_context(board)
    base = tp.evaluate_test_points(ctx)
    # GND skipped; LED_A is a plain signal → excluded by default
    assert all(n["net"] != "GND" for n in base["nets"])
    assert all(n["net"] != "LED_A" for n in base["nets"])
    withsig = tp.evaluate_test_points(ctx, include_signals=True)
    assert any(n["net"] == "LED_A" for n in withsig["nets"])
    # but the signal never changes the critical coverage
    assert withsig["critical_total"] == base["critical_total"]


# --- tool ------------------------------------------------------------------ #

def _register(monkeypatch, pcb_text):
    from kicad_mcp.tools.test_points_tools import register_test_points_tools
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
    register_test_points_tools(mcp)
    import kicad_mcp.tools.test_points_tools as t
    monkeypatch.setattr(t, "get_text", lambda _p: pcb_text)
    monkeypatch.setattr(t.os.path, "isfile", lambda _p: True)
    return captured["audit_test_points"]


def test_tool_end_to_end_and_scoping(monkeypatch, tmp_path):
    board = _board(
        _fp("U1", "MCU", [("1", "+3V3"), ("2", "NRST")]),
        _fp("U2", "PMIC", [("1", "+12V"), ("2", "GND")]),
        _fp("TP1", "3V3", [("1", "+3V3")]))
    tool = _register(monkeypatch, board)
    out = tool(str(tmp_path / "b.kicad_pcb"))
    assert out["success"]
    # +3V3 covered, NRST + +12V blind
    assert out["report"]["critical_total"] == 3
    assert out["report"]["critical_covered"] == 1

    # scope to U2 only → just +12V is audited (and blind)
    scoped = tool(str(tmp_path / "b.kicad_pcb"), refs="U2")
    nets = {n["net"] for n in scoped["report"]["nets"]}
    assert nets == {"+12V"}
    assert scoped["report"]["coverage_pct"] == 0.0


def test_tool_missing_file():
    from kicad_mcp.tools.test_points_tools import register_test_points_tools
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
    register_test_points_tools(mcp)
    out = captured["audit_test_points"]("/no/such.kicad_pcb")
    assert out["success"] is False
