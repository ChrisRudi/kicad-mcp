# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the strict three-field pinout diff."""
from __future__ import annotations

from kicad_mcp.generators.pinout.diff import diff_pinout


def _sym(num, name, typ):
    return {"num": num, "name": name, "type": typ}


def _ds(num, name, typ, type_raw=""):
    return {"num": num, "name": name, "type": typ, "type_raw": type_raw}


def test_full_match():
    sym = [_sym("1", "VM", "power_in"), _sym("2", "OUT1", "output")]
    ds = [_ds("1", "VM", "power_in"), _ds("2", "OUT1", "output")]
    res = diff_pinout(sym, ds, strict=True)
    assert res["match"] is True
    assert res["summary"]["matched"] == 2
    assert all(r["status"] == "match" for r in res["rows"])


def test_name_mismatch():
    sym = [_sym("1", "VM", "power_in")]
    ds = [_ds("1", "VCC", "power_in")]
    res = diff_pinout(sym, ds)
    assert res["match"] is False
    assert res["rows"][0]["status"] == "name_mismatch"
    assert res["summary"]["name_mismatch"] == 1


def test_active_low_name_matches_across_notations():
    sym = [_sym("3", "~{RESET}", "input")]
    ds = [_ds("3", "nRESET", "input")]
    res = diff_pinout(sym, ds)
    assert res["rows"][0]["status"] == "match"


def test_type_mismatch():
    sym = [_sym("2", "OUT1", "input")]
    ds = [_ds("2", "OUT1", "output")]
    res = diff_pinout(sym, ds)
    assert res["rows"][0]["status"] == "type_mismatch"
    assert res["summary"]["type_mismatch"] == 1


def test_unclassifiable_strict_fails():
    sym = [_sym("5", "FOO", "passive")]
    ds = [_ds("5", "FOO", None, type_raw="ZZZ")]
    res = diff_pinout(sym, ds, strict=True)
    assert res["rows"][0]["status"] == "unclassifiable"
    assert res["match"] is False


def test_unclassifiable_lenient_passes_on_name():
    sym = [_sym("5", "FOO", "passive")]
    ds = [_ds("5", "FOO", None, type_raw="ZZZ")]
    res = diff_pinout(sym, ds, strict=False)
    assert res["rows"][0]["status"] == "match"
    assert res["match"] is True


def test_missing_in_datasheet():
    sym = [_sym("1", "VM", "power_in"), _sym("9", "EXTRA", "passive")]
    ds = [_ds("1", "VM", "power_in")]
    res = diff_pinout(sym, ds)
    statuses = {r["num"]: r["status"] for r in res["rows"]}
    assert statuses["9"] == "missing_in_datasheet"
    assert res["summary"]["missing_in_datasheet"] == 1


def test_missing_in_symbol():
    sym = [_sym("1", "VM", "power_in")]
    ds = [_ds("1", "VM", "power_in"), _ds("2", "OTHER", "output")]
    res = diff_pinout(sym, ds)
    statuses = {r["num"]: r["status"] for r in res["rows"]}
    assert statuses["2"] == "missing_in_symbol"
    assert res["summary"]["missing_in_symbol"] == 1


def test_ep_number_alias_bridges():
    # Symbol labels EP "29"; datasheet labels it "EP" — must pair, not flag.
    sym = [_sym("29", "EP", "power_in")]
    ds = [_ds("EP", "EP", "power_in")]
    res = diff_pinout(sym, ds)
    assert res["rows"][0]["status"] == "match"
    assert res["match"] is True


def test_empty_inputs_not_a_match():
    res = diff_pinout([], [])
    assert res["match"] is False
