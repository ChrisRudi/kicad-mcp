# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the pinout MCP tools + the match pipeline (disambiguation)."""
from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools.pinout_tools import register_pinout_tools
from kicad_mcp.generators.pinout import datasheet_pins as dp
from kicad_mcp.generators.pinout import search as srch


# Two candidates with the same pin count; only GOODVAR matches the datasheet.
_LIB = '''\
(kicad_symbol_lib
  (symbol "GOODVAR"
    (symbol "GOODVAR_1_1"
      (pin power_in line (at 0 1 0) (length 2.54)
        (name "VM" (effects (font (size 1 1)))) (number "1" (effects (font (size 1 1)))))
      (pin output line (at 0 2 0) (length 2.54)
        (name "OUT1" (effects (font (size 1 1)))) (number "2" (effects (font (size 1 1)))))
      (pin power_in line (at 0 3 0) (length 2.54)
        (name "GND" (effects (font (size 1 1)))) (number "3" (effects (font (size 1 1)))))
    )
  )
  (symbol "BADVAR"
    (symbol "BADVAR_1_1"
      (pin output line (at 0 1 0) (length 2.54)
        (name "OUT1" (effects (font (size 1 1)))) (number "1" (effects (font (size 1 1)))))
      (pin power_in line (at 0 2 0) (length 2.54)
        (name "VM" (effects (font (size 1 1)))) (number "2" (effects (font (size 1 1)))))
      (pin power_in line (at 0 3 0) (length 2.54)
        (name "GND" (effects (font (size 1 1)))) (number "3" (effects (font (size 1 1)))))
    )
  )
)
'''

_DS_TABLE = {
    "page": 1, "index": 0,
    "rows": [
        ["Pin", "Name", "Type"],
        ["1", "VM", "P"],
        ["2", "OUT1", "O"],
        ["3", "GND", "G"],
    ],
}


@pytest.fixture()
def server() -> FastMCP:
    m = FastMCP("test-pinout")
    register_pinout_tools(m)
    return m


@pytest.fixture()
def lib_path(tmp_path, monkeypatch):
    p = tmp_path / "VarLib.kicad_sym"
    p.write_text(_LIB, encoding="utf-8")
    monkeypatch.setattr(
        srch, "_iter_local_libs", lambda: [("VarLib", str(p), "stock")]
    )
    monkeypatch.setattr(
        dp, "extract_tables_safe",
        lambda pdf_path, pages=None: {"success": True, "tables": [_DS_TABLE]},
    )
    # The pinout PDF must exist for the tool's isfile() gate.
    pdf = tmp_path / "ds.pdf"
    pdf.write_text("dummy", encoding="utf-8")
    return str(p), str(pdf)


def _call(server: FastMCP, name: str, **kwargs):
    result = asyncio.run(server.call_tool(name, kwargs))
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return result


def test_search_symbol_tool(server, lib_path):
    res = _call(server, "search_symbol", query="GOODVAR")
    assert res["success"] is True
    assert any(c["lib_id"] == "VarLib:GOODVAR" for c in res["candidates"])


def test_validate_pinout_match(server, lib_path):
    sym, pdf = lib_path
    res = _call(server, "validate_pinout",
                sym_path=sym, symbol_name="GOODVAR", pdf_path=pdf)
    assert res["success"] is True
    assert res["diff"]["match"] is True


def test_validate_pinout_mismatch(server, lib_path):
    sym, pdf = lib_path
    res = _call(server, "validate_pinout",
                sym_path=sym, symbol_name="BADVAR", pdf_path=pdf)
    assert res["success"] is True
    assert res["diff"]["match"] is False


def test_validate_pinout_missing_pdf(server, lib_path):
    sym, _pdf = lib_path
    res = _call(server, "validate_pinout",
                sym_path=sym, symbol_name="GOODVAR",
                pdf_path="/nonexistent/audit.pdf")
    assert res["success"] is False
    assert "not found" in res["error"].lower()


def test_validate_pinout_bad_pages_json(server, lib_path):
    sym, pdf = lib_path
    res = _call(server, "validate_pinout",
                sym_path=sym, symbol_name="GOODVAR", pdf_path=pdf,
                pages="{not json")
    assert res["success"] is False
    assert "json" in res["error"].lower()


def test_match_disambiguates_variant(server, lib_path):
    _sym, pdf = lib_path
    res = _call(server, "match_symbol_to_datasheet",
                query="VAR", pdf_path=pdf, expected_pin_count=3)
    assert res["success"] is True
    cands = res["candidates"]
    assert cands, "expected at least the two variants"
    # The correct variant ranks first with zero mismatches.
    assert cands[0]["lib_id"] == "VarLib:GOODVAR"
    assert cands[0]["match"] is True
    assert cands[0]["mismatches"] == 0
    bad = next(c for c in cands if c["lib_id"] == "VarLib:BADVAR")
    assert bad["match"] is False
    assert bad["mismatches"] > 0
