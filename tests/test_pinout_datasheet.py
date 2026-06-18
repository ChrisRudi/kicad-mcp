# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for datasheet pinout extraction (pdfplumber path + LLM fallback)."""
from __future__ import annotations

from kicad_mcp.generators.pinout import datasheet_pins as dp


def _patch_tables(monkeypatch, tables):
    monkeypatch.setattr(
        dp, "extract_tables_safe",
        lambda pdf_path, pages=None: {"success": True, "tables": tables},
    )


_PINOUT_TABLE = {
    "page": 5, "index": 0,
    "rows": [
        ["Pin", "Name", "Type"],
        ["1", "VM", "P"],
        ["2", "OUT1", "O"],
        ["3", "GND", "G"],
    ],
}


def test_pdfplumber_table_parsed(monkeypatch):
    _patch_tables(monkeypatch, [_PINOUT_TABLE])
    res = dp.extract_datasheet_pins("x.pdf")
    assert res["success"] is True
    assert res["source"] == "pdfplumber"
    assert res["fallback_used"] is False
    pins = {p["num"]: p for p in res["pins"]}
    assert pins["1"]["type"] == "power_in"
    assert pins["2"]["type"] == "output"
    assert pins["3"]["type"] == "power_in"


def test_missing_type_column_is_unclassifiable(monkeypatch):
    table = {
        "page": 1, "index": 0,
        "rows": [["Pin", "Name"], ["1", "VM"], ["2", "OUT1"]],
    }
    _patch_tables(monkeypatch, [table])
    res = dp.extract_datasheet_pins("x.pdf")
    assert res["success"] is True
    assert len(res["unclassifiable"]) == 2
    assert all(p["type"] is None for p in res["pins"])


def test_fallback_not_triggered_when_clean(monkeypatch):
    _patch_tables(monkeypatch, [_PINOUT_TABLE])
    called = {"n": 0}

    def _llm(_pdf, _pages):
        called["n"] += 1
        return {"pins": []}

    res = dp.extract_datasheet_pins("x.pdf", llm_extract=_llm, expected_pin_count=3)
    assert res["fallback_used"] is False
    assert called["n"] == 0


def test_fallback_triggers_on_count_mismatch(monkeypatch):
    _patch_tables(monkeypatch, [_PINOUT_TABLE])  # table has 3 pins

    def _llm(_pdf, _pages):
        return {"pins": [
            {"num": "1", "name": "VM", "type": "P"},
            {"num": "2", "name": "OUT1", "type": "O"},
        ]}

    res = dp.extract_datasheet_pins(
        "x.pdf", llm_extract=_llm, expected_pin_count=2  # expect 2, table has 3
    )
    assert res["fallback_used"] is True
    assert res["source"] == "llm"
    assert len(res["pins"]) == 2


def test_fallback_triggers_on_duplicate_numbers(monkeypatch):
    dup_table = {
        "page": 1, "index": 0,
        "rows": [["Pin", "Name", "Type"], ["1", "A", "I"], ["1", "B", "O"]],
    }
    _patch_tables(monkeypatch, [dup_table])

    def _llm(_pdf, _pages):
        return {"pins": [{"num": "1", "name": "A", "type": "I"},
                         {"num": "2", "name": "B", "type": "O"}]}

    res = dp.extract_datasheet_pins("x.pdf", llm_extract=_llm)
    assert res["fallback_used"] is True


def test_no_table_no_llm_best_effort_empty(monkeypatch):
    _patch_tables(monkeypatch, [])
    res = dp.extract_datasheet_pins("x.pdf")
    assert res["success"] is True
    assert res["pins"] == []
    assert res["fallback_used"] is False


def test_extraction_failure_propagates(monkeypatch):
    monkeypatch.setattr(
        dp, "extract_tables_safe",
        lambda pdf_path, pages=None: {"success": False, "error": "pdfplumber missing"},
    )
    res = dp.extract_datasheet_pins("x.pdf")
    assert res["success"] is False
    assert "pdfplumber" in res["error"]
