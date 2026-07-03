# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for fab preferred-parts (utils/fab_parts + suggest_preferred_parts).
Headless — real JLCPCB seed snapshot, no KiCad."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils import fab_parts as fp


# --- util ------------------------------------------------------------------ #

def test_provider_registry_and_snapshot_loads():
    assert "jlcpcb" in fp.provider_keys()
    data = fp.load_snapshot("jlcpcb")
    assert data["tier_name"] == "Basic"
    assert data["snapshot_date"]
    assert data["disclaimer"]
    assert data["_index"]           # built index


def test_unknown_provider_raises():
    try:
        fp.load_snapshot("nosuchfab")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_extract_package():
    assert fp.extract_package("Resistor_SMD:R_0402_1005Metric") == "0402"
    assert fp.extract_package("Capacitor_SMD:C_0603_1608Metric") == "0603"
    assert fp.extract_package("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm") == ""


def test_lookup_matches_infix_and_decimal_notation():
    data = fp.load_snapshot("jlcpcb")
    # 4k7 and 4.7k must hit the same "4.7k" 0402 row
    from kicad_mcp.utils import bom_consolidate as bc
    a = fp.lookup(data, "R", bc.normalize_value("4k7", "R"), "0402")
    b = fp.lookup(data, "R", bc.normalize_value("4.7k", "R"), "0402")
    assert a is not None and a is b


def test_suggest_counts_and_savings():
    items = [
        {"ref": "R1", "cls": "R", "si": 10_000.0, "package": "0402"},   # basic
        {"ref": "R2", "cls": "R", "si": 10_000.0, "package": "0402"},   # dup type
        {"ref": "C1", "cls": "C", "si": 100e-9, "package": "0402"},     # basic
        {"ref": "R9", "cls": "R", "si": 12_345.0, "package": "0402"},   # not basic
    ]
    rep = fp.suggest(items, "jlcpcb")
    assert rep["distinct_types"] == 3          # 10k/0402, 100nF/0402, 12.345k/0402
    assert rep["types_with_preferred"] == 2
    assert rep["types_without_preferred"] == 1
    assert rep["potential_saving_usd"] == 6.0  # 2 types × $3
    # the 10k row folds both R1 and R2
    tenk = next(r for r in rep["types"] if r["value"] == "10k")
    assert tenk["has_preferred"] and tenk["part"] == "C25744"
    assert tenk["refs"] == ["R1", "R2"] and tenk["count"] == 2


# --- tool ------------------------------------------------------------------ #

def _fp(ref, value, fpid):
    return (f'\t(footprint "{fpid}" (layer "F.Cu")\n\t\t(uuid "{ref}")\n'
            f'\t\t(at 10 10 0)\n'
            f'\t\t(property "Reference" "{ref}" (at 0 0 0))\n'
            f'\t\t(property "Value" "{value}" (at 0 0 0))\n'
            f'\t\t(pad "1" smd rect (at 0 0) (layers "F.Cu") (net 1 "N"))\n\t)')


def _board(*fps):
    return "(kicad_pcb (version 20240108)\n" + "\n".join(fps) + "\n)\n"


def _register(monkeypatch, pcb_text):
    from kicad_mcp.tools.fab_parts_tools import register_fab_parts_tools
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
    register_fab_parts_tools(mcp)
    import kicad_mcp.tools.fab_parts_tools as t
    monkeypatch.setattr(t, "get_text", lambda _p: pcb_text)
    monkeypatch.setattr(t.os.path, "isfile", lambda _p: True)
    return captured["suggest_preferred_parts"]


def test_tool_end_to_end(monkeypatch, tmp_path):
    board = _board(
        _fp("R1", "10k", "Resistor_SMD:R_0402_1005Metric"),
        _fp("C1", "100nF", "Capacitor_SMD:C_0402_1005Metric"),
        _fp("R9", "12.3k", "Resistor_SMD:R_0402_1005Metric"),   # not basic
        _fp("U1", "MCU", "Package_QFP:LQFP-48"))               # ignored
    tool = _register(monkeypatch, board)
    out = tool(str(tmp_path / "b.kicad_pcb"))
    assert out["success"]
    rep = out["report"]
    assert rep["types_with_preferred"] == 2
    assert rep["potential_saving_usd"] == 6.0
    assert rep["snapshot_date"] and rep["disclaimer"]
    assert out["available_providers"] == ["jlcpcb"]
    # R9's value has no basic row → reported, not skipped (it has a package)
    r9row = next(r for r in rep["types"] if r["value"] == "12.3k")
    assert r9row["has_preferred"] is False


def test_tool_unknown_provider(monkeypatch, tmp_path):
    tool = _register(monkeypatch, _board(
        _fp("R1", "10k", "Resistor_SMD:R_0402_1005Metric")))
    out = tool(str(tmp_path / "b.kicad_pcb"), provider="bogus")
    assert out["success"] is False and "unknown provider" in out["error"]


def test_tool_scoped_and_skips_no_package(monkeypatch, tmp_path):
    board = _board(
        _fp("R1", "10k", "Resistor_SMD:R_0402_1005Metric"),
        _fp("R2", "10k", "Resistor_THT:R_Axial_DIN0207"))   # no SMD size → skip
    tool = _register(monkeypatch, board)
    out = tool(str(tmp_path / "b.kicad_pcb"))
    assert out["report"]["distinct_types"] == 1
    assert {s["ref"] for s in out["skipped"]} == {"R2"}


def test_tool_missing_file():
    from kicad_mcp.tools.fab_parts_tools import register_fab_parts_tools
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
    register_fab_parts_tools(mcp)
    out = captured["suggest_preferred_parts"]("/no/such.kicad_pcb")
    assert out["success"] is False
