# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for BOM-Konsolidierung (utils/bom_consolidate + consolidate_bom tool).
Headless — no KiCad."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils import bom_consolidate as bc


# --- value parsing --------------------------------------------------------- #

@pytest.mark.parametrize("raw,cls,si", [
    ("10k", "R", 10_000.0),
    ("4.7k", "R", 4_700.0),
    ("4k7", "R", 4_700.0),        # infix notation
    ("100R", "R", 100.0),
    ("100", "R", 100.0),
    ("1M", "R", 1_000_000.0),
    ("2R2", "R", 2.2),
    ("100nF", "C", 100e-9),
    ("4n7", "C", 4.7e-9),         # infix notation
    ("4.7uF", "C", 4.7e-6),
    ("22pF", "C", 22e-12),
])
def test_normalize_value(raw, cls, si):
    assert bc.normalize_value(raw, cls) == pytest.approx(si)


def test_normalize_rejects_dnp_and_unitless_cap():
    assert bc.normalize_value("DNP", "R") is None
    assert bc.normalize_value("", "R") is None
    assert bc.normalize_value("100", "C") is None   # cap needs a unit


def test_format_value_roundtrips():
    assert bc.format_value(4_700, "R") == "4.7k"
    assert bc.format_value(100, "R") == "100R"
    assert bc.format_value(1_000_000, "R") == "1M"
    assert bc.format_value(100e-9, "C") == "100nF"
    assert bc.format_value(22e-12, "C") == "22pF"


def test_ref_class():
    assert bc.ref_class("R12") == "R"
    assert bc.ref_class("C3") == "C"
    assert bc.ref_class("U1") is None
    assert bc.ref_class("") is None


# --- E-series snapping ----------------------------------------------------- #

def test_nearest_eseries_snaps_and_reports_shift():
    snap, shift = bc.nearest_eseries(10_200, "E24")   # → 10k
    assert snap == pytest.approx(10_000)
    # shift is relative to the part's actual value: |10000-10200|/10200 ≈ 1.96%
    assert shift == pytest.approx(1.96, abs=0.02)

    snap, shift = bc.nearest_eseries(10_000, "E24")   # already standard
    assert snap == pytest.approx(10_000)
    assert shift == pytest.approx(0.0)


def test_nearest_eseries_crosses_decade():
    snap, _ = bc.nearest_eseries(0.98, "E24")   # → 1.0 (mantissa 10 @ decade -1)
    assert snap == pytest.approx(1.0)


# --- consolidation --------------------------------------------------------- #

def test_consolidate_merges_near_duplicates():
    items = [
        {"ref": "R1", "cls": "R", "si": 10_000.0},
        {"ref": "R2", "cls": "R", "si": 10_200.0},   # 2% → 10k
        {"ref": "R3", "cls": "R", "si": 9_900.0},    # 1% → 10k
    ]
    rep = bc.consolidate(items, series="E24", max_shift_pct=5.0)
    r = rep["classes"]["R"]
    assert r["distinct_before"] == 3
    assert r["distinct_after"] == 1
    assert r["feeders_saved"] == 2
    assert len(r["merges"]) == 1
    m = r["merges"][0]
    assert m["to"] == "10k"
    assert sorted(m["refs"]) == ["R1", "R2", "R3"]
    assert rep["feeders_saved"] == 2


def test_consolidate_keeps_out_of_tolerance():
    # 10k and 12k are both E24 standards >5% apart → no merge, no shift
    items = [
        {"ref": "R1", "cls": "R", "si": 10_000.0},
        {"ref": "R2", "cls": "R", "si": 12_000.0},
    ]
    rep = bc.consolidate(items, series="E24", max_shift_pct=5.0)
    r = rep["classes"]["R"]
    assert r["distinct_before"] == 2 and r["distinct_after"] == 2
    assert r["merges"] == []


def test_consolidate_reports_unmergeable_when_far_from_series():
    # 10.5k in E6 (10,15,22,…): nearest is 10k, 4.76% shift; with a 2% cap it
    # cannot be safely snapped → unmergeable.
    items = [{"ref": "R1", "cls": "R", "si": 10_500.0}]
    rep = bc.consolidate(items, series="E6", max_shift_pct=2.0)
    r = rep["classes"]["R"]
    assert r["merges"] == []
    assert r["unmergeable"] and r["unmergeable"][0]["refs"] == ["R1"]


def test_consolidate_separates_classes():
    items = [
        {"ref": "R1", "cls": "R", "si": 10_000.0},
        {"ref": "R2", "cls": "R", "si": 10_100.0},
        {"ref": "C1", "cls": "C", "si": 100e-9},
        {"ref": "C2", "cls": "C", "si": 102e-9},
    ]
    rep = bc.consolidate(items, series="E24")
    assert rep["classes"]["R"]["feeders_saved"] == 1
    assert rep["classes"]["C"]["feeders_saved"] == 1
    assert rep["feeders_saved"] == 2


# --- tool ------------------------------------------------------------------ #

def _fp(ref, value):
    return (f'\t(footprint "X" (layer "F.Cu")\n\t\t(uuid "{ref}")\n'
            f'\t\t(at 10 10 0)\n'
            f'\t\t(property "Reference" "{ref}" (at 0 0 0))\n'
            f'\t\t(property "Value" "{value}" (at 0 0 0))\n'
            f'\t\t(pad "1" smd rect (at 0 0) (layers "F.Cu") (net 1 "N"))\n\t)')


def _board(*fps):
    return "(kicad_pcb (version 20240108)\n" + "\n".join(fps) + "\n)\n"


def _register(monkeypatch, pcb_text):
    from kicad_mcp.tools.bom_consolidate_tools import register_bom_consolidate_tools
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
    register_bom_consolidate_tools(mcp)
    import kicad_mcp.tools.bom_consolidate_tools as t
    monkeypatch.setattr(t, "get_text", lambda _p: pcb_text)
    monkeypatch.setattr(t.os.path, "isfile", lambda _p: True)
    return captured["consolidate_bom"]


def test_tool_end_to_end(monkeypatch, tmp_path):
    board = _board(
        _fp("R1", "10k"), _fp("R2", "10.2k"), _fp("R3", "9.9k"),
        _fp("C1", "100nF"), _fp("U1", "MCU"), _fp("R9", "weird"))
    tool = _register(monkeypatch, board)
    out = tool(str(tmp_path / "b.kicad_pcb"))
    assert out["success"]
    assert out["report"]["classes"]["R"]["feeders_saved"] == 2
    # U1 ignored (not R/C); R9 unparseable → skipped
    assert {s["ref"] for s in out["skipped"]} == {"R9"}


def test_tool_scoped_to_refs(monkeypatch, tmp_path):
    board = _board(_fp("R1", "10k"), _fp("R2", "10.2k"), _fp("R3", "9.9k"))
    tool = _register(monkeypatch, board)
    out = tool(str(tmp_path / "b.kicad_pcb"), refs="R1,R2")
    # only R1+R2 in scope → still merge to one, saving one feeder
    assert out["report"]["classes"]["R"]["distinct_before"] == 2
    assert out["report"]["classes"]["R"]["feeders_saved"] == 1


def test_tool_missing_file():
    from kicad_mcp.tools.bom_consolidate_tools import register_bom_consolidate_tools
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
    register_bom_consolidate_tools(mcp)
    out = captured["consolidate_bom"]("/no/such.kicad_pcb")
    assert out["success"] is False
