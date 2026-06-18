# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for the multi-sheet intersheet → hierarchical_label
flow in ``build_schematic`` (added 2026-05-23).

Before the fix, ``generation_tools.py`` built each sub-sheet without
handing the ``intersheet_nets`` set to ``build_schematic`` — so signal
nets that crossed sheet boundaries were emitted as plain local
``(label …)`` on the sub-sheet, while the root sheet-symbol expected a
matching ``(hierarchical_label …)``. KiCad's ERC reported "no
connection" for every cross-sheet net.

The tests rely on the ``len(pins) == 1`` branch in
``_emit_wires_and_labels``: an intersheet net has, by construction,
exactly **one pin on a given sub-sheet** (its remaining connections
sit on other sub-sheets). That branch always emits a label-with-stub,
so we can verify hierarchical-vs-local decisions without depending on
the placement-dependent wire-routing heuristics.
"""
from __future__ import annotations

from kicad_mcp.generators.schematic.builder import build_schematic
from kicad_mcp.generators.schematic.route import _place_label_with_stub
from kicad_mcp.generators.sexpr import SExpr


# Two parts. Three nets:
#   • SIG_X      — one pin on this sheet (intersheet candidate)
#   • SIG_Y      — one pin on this sheet (intersheet candidate)
#   • PRIVATE    — two pins, fully on this sheet (local label or wire)
PARTS = [
    {"ref": "R1", "name": "Device:R", "value": "10k",
     "footprint": "Resistor_SMD:R_0805_2012Metric",
     "pins": [{"num": "1", "name": "~", "type": "passive"},
              {"num": "2", "name": "~", "type": "passive"}]},
    {"ref": "R2", "name": "Device:R", "value": "1k",
     "footprint": "Resistor_SMD:R_0805_2012Metric",
     "pins": [{"num": "1", "name": "~", "type": "passive"},
              {"num": "2", "name": "~", "type": "passive"}]},
]

NETS = [
    {"name": "SIG_X", "type": "signal", "connections": ["R1:1"]},
    {"name": "SIG_Y", "type": "signal", "connections": ["R2:1"]},
    {"name": "PRIVATE", "type": "signal", "connections": ["R1:2", "R2:2"]},
]


class TestPlaceLabelWithStubHierarchical:
    """Lowest-level test: the `_place_label_with_stub` helper must emit
    the right S-expression token for each label kind."""

    def test_hierarchical_kind_emits_hierarchical_label(self):
        s = SExpr()
        s.open("kicad_sch")
        _place_label_with_stub(
            s, "NET_FOO", 10.0, 20.0,
            lbl_uid="aaaa-bbbb", wire_uid="cccc-dddd",
            is_hierarchical=True, direction="right",
        )
        s.close()
        out = s.render()
        assert '(hierarchical_label "NET_FOO"' in out
        assert '(global_label "NET_FOO"' not in out
        assert '(label "NET_FOO"' not in out

    def test_hierarchical_takes_precedence_over_global(self):
        """If both flags happen to be set, hierarchical wins — the
        sub-sheet must produce the form the root sheet-symbol pin
        expects."""
        s = SExpr()
        s.open("kicad_sch")
        _place_label_with_stub(
            s, "NET_FOO", 10.0, 20.0,
            lbl_uid="aaaa-bbbb", wire_uid="cccc-dddd",
            is_global=True, is_hierarchical=True, direction="right",
        )
        s.close()
        out = s.render()
        assert '(hierarchical_label "NET_FOO"' in out
        assert '(global_label "NET_FOO"' not in out

    def test_default_is_local_label(self):
        s = SExpr()
        s.open("kicad_sch")
        _place_label_with_stub(
            s, "NET_FOO", 10.0, 20.0,
            lbl_uid="aaaa-bbbb", wire_uid="cccc-dddd",
            direction="right",
        )
        s.close()
        out = s.render()
        assert '(label "NET_FOO"' in out
        assert '(hierarchical_label "NET_FOO"' not in out
        assert '(global_label "NET_FOO"' not in out


class TestBuildSchematicIntersheet:
    """End-to-end: build_schematic with intersheet_nets must emit
    hierarchical_label for the named nets and leave others local."""

    def test_intersheet_net_emits_hierarchical_label(self):
        intersheet = [NETS[0]]  # only SIG_X
        sch = build_schematic(
            PARTS, NETS, project_name="proj", intersheet_nets=intersheet,
        )
        assert '(hierarchical_label "SIG_X"' in sch, (
            "SIG_X is flagged as intersheet but no hierarchical_label "
            "was emitted on the sub-sheet"
        )

    def test_non_intersheet_single_pin_stays_local(self):
        intersheet = [NETS[0]]  # only SIG_X — SIG_Y stays local
        sch = build_schematic(
            PARTS, NETS, project_name="proj", intersheet_nets=intersheet,
        )
        # SIG_Y is single-pin (label path) but NOT in intersheet → local label.
        assert '(hierarchical_label "SIG_Y"' not in sch
        # SIG_Y must show up as a plain (label …) somewhere on the sheet.
        assert '(label "SIG_Y"' in sch

    def test_no_intersheet_arg_emits_no_hierarchical(self):
        """Single-sheet default — none of the nets are hierarchical."""
        sch = build_schematic(PARTS, NETS, project_name="proj")
        assert "hierarchical_label" not in sch

    def test_empty_intersheet_list_is_no_op(self):
        sch = build_schematic(
            PARTS, NETS, project_name="proj", intersheet_nets=[],
        )
        assert "hierarchical_label" not in sch

    def test_both_intersheet_nets_emit_hierarchical(self):
        intersheet = [NETS[0], NETS[1]]  # SIG_X and SIG_Y
        sch = build_schematic(
            PARTS, NETS, project_name="proj", intersheet_nets=intersheet,
        )
        assert '(hierarchical_label "SIG_X"' in sch
        assert '(hierarchical_label "SIG_Y"' in sch
