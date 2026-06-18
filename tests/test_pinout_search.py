# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ranked symbol-candidate search."""
from __future__ import annotations

import pytest

from kicad_mcp.generators.pinout import search as srch


_LIB_A = '''\
(kicad_symbol_lib
  (symbol "DRV8313"
    (symbol "DRV8313_1_1"
''' + "".join(
    f'      (pin input line (at 0 {i} 0) (length 2.54)'
    f' (name "P{i}" (effects (font (size 1 1))))'
    f' (number "{i}" (effects (font (size 1 1)))))\n'
    for i in range(1, 29)
) + '''    )
  )
  (symbol "DRV8313PWP"
    (symbol "DRV8313PWP_1_1"
''' + "".join(
    f'      (pin input line (at 0 {i} 0) (length 2.54)'
    f' (name "P{i}" (effects (font (size 1 1))))'
    f' (number "{i}" (effects (font (size 1 1)))))\n'
    for i in range(1, 29)
) + '''    )
  )
  (symbol "UNRELATED_CHIP"
    (symbol "UNRELATED_CHIP_1_1"
      (pin input line (at 0 0 0) (length 2.54)
        (name "X" (effects (font (size 1 1))))
        (number "1" (effects (font (size 1 1)))))
    )
  )
)
'''


@pytest.fixture()
def fake_libs(tmp_path, monkeypatch):
    p = tmp_path / "MyLib.kicad_sym"
    p.write_text(_LIB_A, encoding="utf-8")
    monkeypatch.setattr(
        srch, "_iter_local_libs",
        lambda: [("MyLib", str(p), "stock")],
    )
    return str(p)


def test_returns_all_matching_candidates(fake_libs):
    cands = srch.search_symbol_candidates("DRV8313")
    lib_ids = {c["lib_id"] for c in cands}
    assert "MyLib:DRV8313" in lib_ids
    assert "MyLib:DRV8313PWP" in lib_ids  # variant kept, not collapsed
    assert "MyLib:UNRELATED_CHIP" not in lib_ids


def test_ranking_exact_name_first(fake_libs):
    cands = srch.search_symbol_candidates("DRV8313")
    assert cands[0]["lib_id"] == "MyLib:DRV8313"
    assert cands[0]["score"] >= cands[1]["score"]


def test_pin_count_filter(fake_libs):
    cands = srch.search_symbol_candidates("DRV8313", expected_pin_count=28)
    assert cands and all(c["pin_count"] == 28 for c in cands)
    none = srch.search_symbol_candidates("DRV8313", expected_pin_count=99)
    assert none == []


def test_pin_count_enrichment(fake_libs):
    cands = srch.search_symbol_candidates("DRV8313")
    by_id = {c["lib_id"]: c for c in cands}
    assert by_id["MyLib:DRV8313"]["pin_count"] == 28


def test_empty_index_returns_empty(monkeypatch):
    monkeypatch.setattr(srch, "_iter_local_libs", lambda: [])
    assert srch.search_symbol_candidates("DRV8313") == []


def test_empty_query_returns_empty(fake_libs):
    assert srch.search_symbol_candidates("") == []
