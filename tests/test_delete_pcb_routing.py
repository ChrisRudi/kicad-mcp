# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``delete_pcb_routing``."""

from __future__ import annotations

import asyncio

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt


# ---------------------------------------------------------------------------
# Fixture: PCB with assorted routing (segments + arcs + vias) on 3 nets and
# 2 layers, plus one footprint that contains an internal "(segment …)"-like
# string so we can verify the top-level filter.
# ---------------------------------------------------------------------------


PCB_WITH_ROUTING = """\
(kicad_pcb
\t(version 20240108)
\t(generator "test")
\t(general
\t\t(thickness 1.6)
\t)
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(31 "B.Cu" signal)
\t\t(1 "In1.Cu" signal)
\t\t(2 "In2.Cu" signal)
\t)
\t(net 0 "")
\t(net 1 "VCC")
\t(net 2 "GND")
\t(net 3 "/JUNCT_P0")
\t(footprint "Test:R_0402"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-aaaaaaaaaaaa")
\t\t(at 0 0 0)
\t\t(property "Reference" "R1"
\t\t\t(at 0 0 0)
\t\t\t(layer "F.SilkS")
\t\t)
\t\t(property "Value" "10k"
\t\t\t(at 0 0 0)
\t\t\t(layer "F.Fab")
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at -0.5 0)
\t\t\t(size 0.5 0.6)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t)
\t)
\t(segment (start 10.0 10.0) (end 20.0 10.0) (width 0.25) (layer "F.Cu") (net 1) (uuid "11111111-1111-1111-1111-111111111111"))
\t(segment (start 20.0 10.0) (end 30.0 10.0) (width 0.25) (layer "F.Cu") (net 1) (uuid "11111111-1111-1111-1111-111111111112"))
\t(segment (start 50.0 50.0) (end 60.0 50.0) (width 0.25) (layer "B.Cu") (net 2) (uuid "22222222-2222-2222-2222-222222222221"))
\t(arc (start 100.0 100.0) (mid 105.0 95.0) (end 110.0 100.0) (width 0.3) (layer "In2.Cu") (net 3) (uuid "33333333-3333-3333-3333-333333333331"))
\t(arc (start 110.0 100.0) (mid 115.0 95.0) (end 120.0 100.0) (width 0.3) (layer "In2.Cu") (net 3) (uuid "33333333-3333-3333-3333-333333333332"))
\t(via (at 30.0 10.0) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "44444444-4444-4444-4444-444444444441"))
\t(via (at 100.0 100.0) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 3) (uuid "44444444-4444-4444-4444-444444444442"))
)
"""


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "routing.kicad_pcb"
    p.write_text(PCB_WITH_ROUTING, encoding="utf-8")
    return str(p)


@pytest.fixture
def mcp_server():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    ppt.register_pcb_patch_tools(mcp)
    return mcp


def _call(mcp, name, **kwargs):
    async def _do():
        result = await mcp.call_tool(name, kwargs)
        if isinstance(result, tuple):
            return result[1] if len(result) > 1 else result[0]
        return result
    return asyncio.run(_do())


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Filter combinations
# ---------------------------------------------------------------------------


class TestNetFilter:
    def test_delete_one_net(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, net_name="/JUNCT_P0",
        )
        assert out["success"] is True
        assert out["deleted"] == 3
        assert out["by_kind"] == {"segment": 0, "arc": 2, "via": 1}
        text = _read(pcb_path)
        assert '"33333333-3333-3333-3333-333333333331"' not in text
        assert '"33333333-3333-3333-3333-333333333332"' not in text
        # Other nets untouched
        assert '"11111111-1111-1111-1111-111111111111"' in text

    def test_delete_all_nets(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing", pcb_path=pcb_path,
        )
        assert out["success"] is True
        assert out["deleted"] == 7
        text = _read(pcb_path)
        assert "(segment " not in text
        assert "(arc " not in text
        # Top-level via gone, but footprints survive.
        assert "(via " not in text
        assert '(property "Reference" "R1"' in text

    def test_unknown_net_errors(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, net_name="NOPE",
        )
        assert out["success"] is False
        assert "not found" in out["error"].lower()


class TestLayerFilter:
    def test_layer_filters_tracks_only(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, layer="B.Cu",
            element_kinds=["segment"],
        )
        assert out["success"] is True
        assert out["deleted"] == 1
        assert out["by_kind"] == {"segment": 1, "arc": 0, "via": 0}
        text = _read(pcb_path)
        assert '"22222222-2222-2222-2222-222222222221"' not in text
        # F.Cu segments preserved.
        assert '"11111111-1111-1111-1111-111111111111"' in text

    def test_via_matched_by_layer_pair(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, layer="F.Cu",
            element_kinds=["via"],
        )
        assert out["success"] is True
        assert out["deleted"] == 2

    def test_layer_no_match_returns_zero(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, layer="In1.Cu",
        )
        assert out["success"] is True
        assert out["deleted"] == 0


class TestBboxFilter:
    def test_bbox_keeps_outside_elements(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path,
            bbox_xy_mm=[90.0, 90.0, 130.0, 110.0],
        )
        assert out["success"] is True
        # arcs (2) + via at (100,100) — all in bbox.
        assert out["by_kind"] == {"segment": 0, "arc": 2, "via": 1}

    def test_bbox_malformed(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path,
            bbox_xy_mm=[1.0, 2.0],   # wrong length
        )
        assert out["success"] is False
        assert "bbox_xy_mm" in out["error"]


class TestKindFilter:
    def test_only_vias(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, element_kinds=["via"],
        )
        assert out["success"] is True
        assert out["by_kind"] == {"segment": 0, "arc": 0, "via": 2}

    def test_invalid_kind_rejected(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, element_kinds=["pad"],
        )
        assert out["success"] is False
        assert "element_kinds" in out["error"]


# ---------------------------------------------------------------------------
# Dry run + idempotency
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_modify(self, mcp_server, pcb_path):
        before = _read(pcb_path)
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, net_name="VCC", dry_run=True,
        )
        assert out["success"] is True
        assert out["dry_run"] is True
        assert out["deleted"] == 3       # 2 segments + 1 via
        assert "preview" in out
        assert _read(pcb_path) == before

    def test_second_call_zero_deletions(self, mcp_server, pcb_path):
        _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, net_name="VCC",
        )
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=pcb_path, net_name="VCC",
        )
        assert out["success"] is True
        assert out["deleted"] == 0


class TestTopLevelOnly:
    def test_footprint_internals_not_deleted(self, mcp_server, pcb_path):
        # The fixture's R1 footprint has its own (pad …) but no nested
        # (segment) — we just verify nothing inside the footprint block
        # was touched when we delete a net the footprint references via
        # pad-level (net) markers. Run a full-scope delete and assert
        # the footprint block content is unchanged.
        before = _read(pcb_path)
        # Slice out the footprint block from the original
        fp_span = ppt._find_footprint_block(before, "R1")
        assert fp_span is not None
        fp_before = before[fp_span[0]:fp_span[1]]
        _call(
            mcp_server, "delete_pcb_routing", pcb_path=pcb_path,
        )
        after = _read(pcb_path)
        fp_span2 = ppt._find_footprint_block(after, "R1")
        fp_after = after[fp_span2[0]:fp_span2[1]]
        assert fp_after == fp_before


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_pcb(self, mcp_server, tmp_path):
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=str(tmp_path / "no.kicad_pcb"),
        )
        assert out["success"] is False
        assert "not found" in out["error"].lower()


# ---------------------------------------------------------------------------
# String-form PCB (KiCad 10 short net tag, no top-level table) — must
# still resolve net_name to the matching blocks. Pre-2026-05-23 the
# name-to-id map was built only from `(net N "name")` table entries, so
# delete_pcb_routing(net_name="…") on a string-form PCB always errored
# "Net not found".
# ---------------------------------------------------------------------------


PCB_STRING_FORM_ROUTING = """\
(kicad_pcb
\t(version 20240108)
\t(generator "test")
\t(general (thickness 1.6))
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(31 "B.Cu" signal)
\t)
\t(segment (start 10.0 10.0) (end 20.0 10.0) (width 0.25) (layer "F.Cu") (net "VCC") (uuid "11111111-1111-1111-1111-111111111111"))
\t(segment (start 50.0 50.0) (end 60.0 50.0) (width 0.25) (layer "B.Cu") (net "GND") (uuid "22222222-2222-2222-2222-222222222221"))
\t(via (at 30.0 10.0) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "VCC") (uuid "44444444-4444-4444-4444-444444444441"))
)
"""


class TestStringFormPcb:
    def test_delete_one_named_net(self, mcp_server, tmp_path):
        p = tmp_path / "string_routing.kicad_pcb"
        p.write_text(PCB_STRING_FORM_ROUTING, encoding="utf-8")
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=str(p), net_name="VCC",
        )
        assert out["success"] is True
        # One F.Cu segment + one via, both on net "VCC".
        assert out["deleted"] == 2
        assert out["by_kind"]["segment"] == 1
        assert out["by_kind"]["via"] == 1
        text = _read(str(p))
        # GND segment must survive.
        assert '(net "GND")' in text
        # VCC must be gone.
        assert '(net "VCC")' not in text

    def test_unknown_net_errors_on_string_form(self, mcp_server, tmp_path):
        p = tmp_path / "string_routing.kicad_pcb"
        p.write_text(PCB_STRING_FORM_ROUTING, encoding="utf-8")
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=str(p), net_name="DOES_NOT_EXIST",
        )
        assert out["success"] is False
        assert "not found" in out["error"].lower()

    def test_delete_all_nets_on_string_form(self, mcp_server, tmp_path):
        p = tmp_path / "string_routing.kicad_pcb"
        p.write_text(PCB_STRING_FORM_ROUTING, encoding="utf-8")
        out = _call(
            mcp_server, "delete_pcb_routing",
            pcb_path=str(p),
        )
        assert out["success"] is True
        # 2 segments + 1 via
        assert out["deleted"] == 3
