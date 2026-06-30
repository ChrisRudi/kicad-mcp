# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the footprint-resync tools (schematic parser + the two text-patch
tools + the replace wrapper). The pcbnew swap itself is KiCad-only and not
exercised here; the wrapper's resolution / guard / no-op paths are.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_mcp.tools import footprint_resync_tools as frt
from kicad_mcp.utils import sch_inspect


SCH = """(kicad_sch
 (lib_symbols
   (symbol "iFloat:DRV8313"
     (pin (number "1") (name "CPL"))
     (pin (number "2") (name "GND"))
   )
 )
 (symbol
   (lib_id "iFloat:DRV8313")
   (property "Reference" "U_DRV4")
   (property "Footprint" "iFloat:DRV8313")
 )
 (symbol
   (lib_id "Device:R")
   (property "Reference" "R5")
   (property "Footprint" "Resistor_SMD:R_0402")
 )
)
"""

PCB = """(kicad_pcb
 (footprint "DRV8313"
   (property "Reference" "U_DRV4")
   (pad "1" smd roundrect (layer "F.Cu") (net 3 "CPL"))
   (pad "2" smd roundrect (layer "F.Cu") (net "GND") (pinfunction "OLD"))
 )
 (footprint "Resistor_SMD:R_0402"
   (property "Reference" "R5")
   (pad "1" smd (layer "F.Cu") (net 1 "VCC"))
 )
)
"""


@pytest.fixture
def board(tmp_path):
    pcb = tmp_path / "b.kicad_pcb"
    sch = tmp_path / "b.kicad_sch"
    pcb.write_text(PCB, encoding="utf-8")
    sch.write_text(SCH, encoding="utf-8")
    return str(pcb), str(sch)


# --- schematic parser --------------------------------------------------------

class TestSchInspect:
    def test_footprint_map(self):
        m = sch_inspect.schematic_footprint_map(SCH)
        assert m == {"U_DRV4": "iFloat:DRV8313",
                     "R5": "Resistor_SMD:R_0402"}

    def test_pin_names_resolved_via_lib_cache(self):
        pins = sch_inspect.schematic_pin_names(SCH)
        assert pins["U_DRV4"] == {"1": "CPL", "2": "GND"}
        assert "R5" not in pins  # Device:R not in lib_symbols cache


# --- Tool 1 ------------------------------------------------------------------

class TestNormalizeLibId:
    def test_dry_run_reports_only_bare_match(self, board):
        pcb, sch = board
        r = frt.normalize_footprint_libid_impl(pcb, sch, dry_run=True)
        assert r["success"] and r["count"] == 1
        assert r["normalized"] == [
            {"ref": "U_DRV4", "from": "DRV8313", "to": "iFloat:DRV8313"}]
        assert "DRV8313\"" in Path(pcb).read_text(encoding="utf-8")  # not written in dry_run
        assert '(footprint "iFloat:DRV8313"' not in Path(pcb).read_text(encoding="utf-8")

    def test_apply_and_idempotent(self, board):
        pcb, sch = board
        r1 = frt.normalize_footprint_libid_impl(pcb, sch, dry_run=False)
        assert r1["count"] == 1
        assert '(footprint "iFloat:DRV8313"' in Path(pcb).read_text(encoding="utf-8")
        r2 = frt.normalize_footprint_libid_impl(pcb, sch, dry_run=False)
        assert r2["count"] == 0  # already qualified → idempotent

    def test_qualified_footprint_untouched(self, board):
        # R5 already has a namespace → never touched
        pcb, sch = board
        r = frt.normalize_footprint_libid_impl(pcb, sch, dry_run=False)
        assert all(e["ref"] != "R5" for e in r["normalized"])

    def test_refs_filter(self, board):
        pcb, sch = board
        r = frt.normalize_footprint_libid_impl(pcb, sch, refs=["R5"],
                                               dry_run=False)
        assert r["count"] == 0  # U_DRV4 excluded


# --- Tool 2 ------------------------------------------------------------------

class TestRefreshPinfunctions:
    def test_insert_and_replace_both_net_forms(self, board):
        pcb, sch = board
        r = frt.refresh_pinfunctions_impl(pcb, sch, dry_run=False)
        assert r["success"]
        assert set(r["changed"]) == {"U_DRV4.1->CPL", "U_DRV4.2->GND"}
        text = Path(pcb).read_text(encoding="utf-8")
        assert '(pinfunction "CPL")' in text   # inserted after (net 3 "CPL")
        assert '(pinfunction "GND")' in text   # replaced "OLD"
        assert '(pinfunction "OLD")' not in text

    def test_idempotent(self, board):
        pcb, sch = board
        frt.refresh_pinfunctions_impl(pcb, sch, dry_run=False)
        r2 = frt.refresh_pinfunctions_impl(pcb, sch, dry_run=False)
        assert r2["count"] == 0

    def test_dry_run_writes_nothing(self, board):
        pcb, sch = board
        before = Path(pcb).read_text(encoding="utf-8")
        r = frt.refresh_pinfunctions_impl(pcb, sch, dry_run=True)
        assert r["count"] == 2 and Path(pcb).read_text(encoding="utf-8") == before


# --- Tool 3 wrapper (no pcbnew needed) ---------------------------------------

class TestReplaceCanonicalWrapper:
    def test_unresolved_refs_when_no_lib(self, board, monkeypatch):
        # no fp-lib-table + no kicad_lib_root → nothing resolves → no jobs
        monkeypatch.setattr(frt, "kicad_lib_root", lambda: "")
        pcb, sch = board
        r = frt.replace_footprint_canonical_impl(pcb, sch, ["U_DRV4"],
                                                 dry_run=True)
        assert r["success"] and r["done"] == []
        assert "U_DRV4" in r["unresolved"]

    def test_empty_refs_rejected(self, board):
        pcb, sch = board
        r = frt.replace_footprint_canonical_impl(pcb, sch, [], dry_run=True)
        assert r["success"] is False and "refs" in r["error"]

    def test_blocked_when_board_open(self, board, monkeypatch):
        from kicad_mcp.utils import board_open_guard
        monkeypatch.setattr(board_open_guard, "is_pcb_open_in_gui",
                            lambda p, factory=None: True)
        pcb, sch = board
        r = frt.replace_footprint_canonical_impl(pcb, sch, ["U_DRV4"],
                                                 dry_run=False)
        assert r["success"] is False and "geoeffnet" in r["error"].lower()

    def test_resolves_pretty_dir_from_lib_table(self, tmp_path):
        # a project-local fp-lib-table pointing at an existing .pretty dir
        pretty = tmp_path / "iFloat.pretty"
        pretty.mkdir()
        (tmp_path / "fp-lib-table").write_text(
            '(fp_lib_table\n (lib (name "iFloat")(type "KiCad")'
            '(uri "${KIPRJMOD}/iFloat.pretty")(options "")(descr ""))\n)\n',
            encoding="utf-8")
        got = frt._resolve_pretty_dir("iFloat", str(tmp_path))
        assert got == str(pretty)


# --- force flag (B.Cu-flip override) ----------------------------------------
# The pcbnew swap itself is KiCad-only; here we pin the wrapper contract that
# `force` reaches the worker payload (the worker's gate is `if drift and not
# force`). The pad-drift gate must stay armed by DEFAULT (force=False).

class TestForceFlag:
    def _patch(self, monkeypatch, captured):
        import json as _json
        # make the ref resolvable without a real .pretty dir on disk
        monkeypatch.setattr(frt, "_resolve_pretty_dir",
                            lambda nick, d: "/fake.pretty")

        class FakeProc:
            stdout = (frt.MARK
                      + '{"done":["U_DRV4"],"errors":[],"saved":false}'
                      + frt.MARK_END)
            stderr = ""

        def fake_run(cmd, input=None, **kw):
            captured["payload"] = _json.loads(input)
            return FakeProc()

        monkeypatch.setattr(frt.subprocess, "run", fake_run)

    def test_force_true_propagates(self, board, monkeypatch):
        pcb, sch = board
        cap: dict = {}
        self._patch(monkeypatch, cap)
        r = frt.replace_footprint_canonical_impl(
            pcb, sch, ["U_DRV4"], dry_run=True, force=True)
        assert r["success"] is True
        assert cap["payload"]["force"] is True

    def test_force_defaults_false(self, board, monkeypatch):
        pcb, sch = board
        cap: dict = {}
        self._patch(monkeypatch, cap)
        frt.replace_footprint_canonical_impl(pcb, sch, ["U_DRV4"], dry_run=True)
        assert cap["payload"]["force"] is False
