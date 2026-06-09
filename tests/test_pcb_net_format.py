# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``kicad_mcp.utils.pcb_net_format``.

The helper centralises the index- vs. string-net-tag decision that was
previously duplicated (and broken on string-form PCBs) across
``pcb_geometry_tools`` and ``pcb_patch_tools``.
"""

from __future__ import annotations

import textwrap

from kicad_mcp.utils.pcb_net_format import ensure_net_tag, pcb_net_format


# ---------------------------------------------------------------------------
# Fixture PCB skeletons. Both are valid KiCad 10 PCBs; the difference is the
# net-tag convention. ``pcbnew`` accepts and round-trips both.
# ---------------------------------------------------------------------------

INDEX_PCB = textwrap.dedent(
    """\
    (kicad_pcb
    \t(version 20240108)
    \t(generator "test")
    \t(layers
    \t\t(0 "F.Cu" signal)
    \t\t(31 "B.Cu" signal)
    \t)
    \t(net 0 "")
    \t(net 1 "GND")
    \t(segment (start 0 0) (end 1 0) (width 0.25) (layer "F.Cu") (net 1))
    )
    """
)

STRING_PCB = textwrap.dedent(
    """\
    (kicad_pcb
    \t(version 20240108)
    \t(generator "test")
    \t(layers
    \t\t(0 "F.Cu" signal)
    \t\t(31 "B.Cu" signal)
    \t)
    \t(segment (start 0 0) (end 1 0) (width 0.25) (layer "F.Cu") (net "GND"))
    )
    """
)

EMPTY_PCB = textwrap.dedent(
    """\
    (kicad_pcb
    \t(version 20240108)
    \t(generator "test")
    \t(layers
    \t\t(0 "F.Cu" signal)
    \t\t(31 "B.Cu" signal)
    \t)
    )
    """
)


class TestPcbNetFormat:
    def test_index_form_detected(self):
        assert pcb_net_format(INDEX_PCB) == "index"

    def test_string_form_detected(self):
        assert pcb_net_format(STRING_PCB) == "string"

    def test_empty_pcb_defaults_to_index(self):
        # Brand-new boards have no net tags at all; the SWIG writer's
        # default is the indexed form, so that is the safer fall-through.
        assert pcb_net_format(EMPTY_PCB) == "index"


class TestEnsureNetTag:
    def test_index_existing_net_returns_existing_index(self):
        new_text, tag, fmt, idx = ensure_net_tag(INDEX_PCB, "GND")
        assert fmt == "index"
        assert idx == 1
        assert tag == "(net 1)"
        # No table mutation when the net already exists.
        assert new_text == INDEX_PCB

    def test_index_new_net_gets_fresh_index_and_table_entry(self):
        new_text, tag, fmt, idx = ensure_net_tag(INDEX_PCB, "VCC")
        assert fmt == "index"
        assert idx == 2  # next after existing 0,1
        assert tag == "(net 2)"
        assert '(net 2 "VCC")' in new_text

    def test_string_form_returns_inline_tag_and_does_not_touch_table(self):
        new_text, tag, fmt, idx = ensure_net_tag(STRING_PCB, "DRIVE_P1_A")
        assert fmt == "string"
        assert idx is None
        assert tag == '(net "DRIVE_P1_A")'
        # The file body must be untouched: no synthetic
        # (net 0 "DRIVE_P1_A") at the top.
        assert new_text == STRING_PCB
        assert '"DRIVE_P1_A"' not in new_text  # not yet inserted by us

    def test_empty_net_name_emits_no_connect_tag(self):
        for src in (INDEX_PCB, STRING_PCB, EMPTY_PCB):
            new_text, tag, _fmt, idx = ensure_net_tag(src, "")
            assert tag == "(net 0)"
            assert idx == 0
            assert new_text == src

    def test_index_form_repeated_call_is_idempotent(self):
        t1, _tag, _fmt, idx1 = ensure_net_tag(INDEX_PCB, "VCC")
        t2, _tag2, _fmt2, idx2 = ensure_net_tag(t1, "VCC")
        assert idx1 == idx2
        assert t1 == t2

    def test_string_form_repeated_call_is_idempotent(self):
        t1, _tag, _fmt, _idx = ensure_net_tag(STRING_PCB, "DRIVE_P1_A")
        t2, _tag2, _fmt2, _idx2 = ensure_net_tag(t1, "DRIVE_P1_A")
        assert t1 == t2
