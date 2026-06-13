# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for buried-via support (``add_track_to_pcb`` via_layers param)
and the new ``add_via_to_pcb`` standalone tool."""

from __future__ import annotations

import asyncio
import re
import textwrap

import pytest

from kicad_mcp.tools import pcb_geometry_tools as pgt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


PCB_WITH_PADS = textwrap.dedent(
    """\
    (kicad_pcb
    \t(version 20240108)
    \t(generator "test")
    \t(general
    \t\t(thickness 1.6)
    \t)
    \t(layers
    \t\t(0 "F.Cu" signal)
    \t\t(1 "In1.Cu" signal)
    \t\t(2 "In2.Cu" signal)
    \t\t(31 "B.Cu" signal)
    \t)
    \t(net 0 "")
    \t(footprint "Test:R_0402"
    \t\t(layer "F.Cu")
    \t\t(uuid "00000000-0000-0000-0000-000000000001")
    \t\t(at 10.0 10.0 0.0)
    \t\t(property "Reference" "U1"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.SilkS")
    \t\t)
    \t\t(property "Value" "X"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.Fab")
    \t\t)
    \t\t(pad "1" smd rect
    \t\t\t(at 0 0)
    \t\t\t(size 0.5 0.6)
    \t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
    \t\t)
    \t)
    \t(footprint "Test:R_0402"
    \t\t(layer "B.Cu")
    \t\t(uuid "00000000-0000-0000-0000-000000000002")
    \t\t(at 20.0 20.0 0.0)
    \t\t(property "Reference" "U2"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "B.SilkS")
    \t\t)
    \t\t(property "Value" "X"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "B.Fab")
    \t\t)
    \t\t(pad "1" smd rect
    \t\t\t(at 0 0)
    \t\t\t(size 0.5 0.6)
    \t\t\t(layers "B.Cu" "B.Mask" "B.Paste")
    \t\t)
    \t)
    )
    """
)


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "buried.kicad_pcb"
    p.write_text(PCB_WITH_PADS, encoding="utf-8")
    return str(p)


@pytest.fixture
def mcp_server():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    pgt.register_pcb_geometry_tools(mcp)
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
# add_track_to_pcb with via_layers
# ---------------------------------------------------------------------------


class TestTrackViaLayers:
    def test_default_via_is_through(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_track_to_pcb",
            pcb_path=pcb_path, ref1="U1", pin1="1", ref2="U2", pin2="1",
            net_name="NET_A",
        )
        assert out["success"] is True
        assert out["vias_added"] == 1
        assert out["via_layers"] == ["F.Cu", "B.Cu"]
        text = _read(pcb_path)
        assert re.search(r'\(layers\s+"F\.Cu"\s+"B\.Cu"\)', text)

    def test_custom_buried_via(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_track_to_pcb",
            pcb_path=pcb_path, ref1="U1", pin1="1", ref2="U2", pin2="1",
            net_name="NET_B",
            via_layers=["In1.Cu", "In2.Cu"],
        )
        assert out["success"] is True
        assert out["via_layers"] == ["In1.Cu", "In2.Cu"]
        text = _read(pcb_path)
        assert re.search(r'\(layers\s+"In1\.Cu"\s+"In2\.Cu"\)', text)
        # The default F.Cu/B.Cu layer pair must NOT be present (we only
        # added the buried via, not a through one).
        assert not re.search(
            r'\(via[\s\S]*?\(layers\s+"F\.Cu"\s+"B\.Cu"\)', text,
        )

    def test_custom_via_size_drill(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_track_to_pcb",
            pcb_path=pcb_path, ref1="U1", pin1="1", ref2="U2", pin2="1",
            net_name="X",
            via_layers=["In1.Cu", "In2.Cu"],
            via_size_mm=0.5, via_drill_mm=0.25,
        )
        assert out["success"] is True
        text = _read(pcb_path)
        assert re.search(r'\(via[\s\S]*?\(size 0\.500000\)', text)
        assert re.search(r'\(via[\s\S]*?\(drill 0\.250000\)', text)

    def test_via_layers_validation_length(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_track_to_pcb",
            pcb_path=pcb_path, ref1="U1", pin1="1", ref2="U2", pin2="1",
            net_name="X",
            via_layers=["F.Cu"],            # not two
        )
        assert out["success"] is False
        assert "via_layers" in out["error"].lower()

    def test_via_layers_validation_distinct(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_track_to_pcb",
            pcb_path=pcb_path, ref1="U1", pin1="1", ref2="U2", pin2="1",
            net_name="X",
            via_layers=["F.Cu", "F.Cu"],
        )
        assert out["success"] is False
        assert "distinct" in out["error"].lower()


# ---------------------------------------------------------------------------
# add_via_to_pcb standalone
# ---------------------------------------------------------------------------


class TestStandaloneVia:
    def test_through_via_default(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_via_to_pcb",
            pcb_path=pcb_path,
            x_mm=50.0, y_mm=50.0,
            net_name="STANDALONE",
        )
        assert out["success"] is True
        assert out["layer_pair"] == ["F.Cu", "B.Cu"]
        assert out["net_id"] > 0
        text = _read(pcb_path)
        assert re.search(
            r'\(via[\s\S]*?\(at 50\.000000 50\.000000\)'
            r'[\s\S]*?\(layers\s+"F\.Cu"\s+"B\.Cu"\)', text,
        )

    def test_buried_via_inner_only(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_via_to_pcb",
            pcb_path=pcb_path,
            x_mm=100.0, y_mm=100.0,
            net_name="/JUNCT_P4",
            layer_pair=["In1.Cu", "In2.Cu"],
            size_mm=0.5, drill_mm=0.3,
        )
        assert out["success"] is True
        assert out["layer_pair"] == ["In1.Cu", "In2.Cu"]
        assert out["size_mm"] == 0.5
        assert out["drill_mm"] == 0.3
        text = _read(pcb_path)
        assert re.search(
            r'\(via[\s\S]*?\(at 100\.000000 100\.000000\)'
            r'[\s\S]*?\(layers\s+"In1\.Cu"\s+"In2\.Cu"\)', text,
        )

    def test_empty_net_uses_zero(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_via_to_pcb",
            pcb_path=pcb_path,
            x_mm=10.0, y_mm=10.0, net_name="",
        )
        assert out["success"] is True
        assert out["net_id"] == 0

    def test_layer_pair_validation_length(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_via_to_pcb",
            pcb_path=pcb_path, x_mm=0.0, y_mm=0.0, net_name="X",
            layer_pair=["F.Cu"],
        )
        assert out["success"] is False
        assert "layer_pair" in out["error"]

    def test_layer_pair_validation_distinct(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_via_to_pcb",
            pcb_path=pcb_path, x_mm=0.0, y_mm=0.0, net_name="X",
            layer_pair=["In2.Cu", "In2.Cu"],
        )
        assert out["success"] is False
        assert "distinct" in out["error"].lower()

    def test_missing_pcb(self, mcp_server, tmp_path):
        out = _call(
            mcp_server, "add_via_to_pcb",
            pcb_path=str(tmp_path / "no.kicad_pcb"),
            x_mm=0.0, y_mm=0.0, net_name="X",
        )
        assert out["success"] is False
        assert "not found" in out["error"].lower()


# ---------------------------------------------------------------------------
# Via-type token after "(via" — KiCad reads the type from this token, NOT
# from the (layers ...) pair. A buried/blind via written as a plain "(via"
# loads as a through via in KiCad (regression fixed 2026-06-05).
# ---------------------------------------------------------------------------


class TestViaTypeToken:
    def test_block_through_has_no_token(self):
        blk = pgt._via_block(
            (1.0, 2.0), '(net 0 "")', layer_pair=("F.Cu", "B.Cu"),
        )
        assert blk.startswith("\t(via\n")

    def test_block_buried_inner_inner(self):
        blk = pgt._via_block(
            (1.0, 2.0), '(net 0 "")', layer_pair=("In1.Cu", "In2.Cu"),
        )
        assert blk.startswith("\t(via buried\n")

    @pytest.mark.parametrize(
        "pair",
        [("In1.Cu", "B.Cu"), ("In2.Cu", "B.Cu"), ("F.Cu", "In1.Cu")],
    )
    def test_block_blind_one_outer(self, pair):
        blk = pgt._via_block((1.0, 2.0), '(net 0 "")', layer_pair=pair)
        assert blk.startswith("\t(via blind\n")

    def test_tool_writes_buried_token(self, mcp_server, pcb_path):
        _call(
            mcp_server, "add_via_to_pcb",
            pcb_path=pcb_path, x_mm=30.0, y_mm=30.0,
            net_name="BUR", layer_pair=["In1.Cu", "In2.Cu"],
        )
        text = _read(pcb_path)
        assert re.search(
            r'\(via buried[\s\S]*?\(at 30\.000000 30\.000000\)'
            r'[\s\S]*?\(layers\s+"In1\.Cu"\s+"In2\.Cu"\)', text,
        )

    def test_tool_writes_blind_token(self, mcp_server, pcb_path):
        _call(
            mcp_server, "add_via_to_pcb",
            pcb_path=pcb_path, x_mm=40.0, y_mm=40.0,
            net_name="BLI", layer_pair=["In1.Cu", "B.Cu"],
        )
        text = _read(pcb_path)
        assert re.search(
            r'\(via blind[\s\S]*?\(at 40\.000000 40\.000000\)'
            r'[\s\S]*?\(layers\s+"In1\.Cu"\s+"B\.Cu"\)', text,
        )

    def test_tool_through_has_no_token(self, mcp_server, pcb_path):
        _call(
            mcp_server, "add_via_to_pcb",
            pcb_path=pcb_path, x_mm=60.0, y_mm=60.0, net_name="THRU",
        )
        text = _read(pcb_path)
        # The through via must be a plain "(via" with no blind/buried token.
        assert re.search(
            r'\t\(via\n\t\t\(at 60\.000000 60\.000000\)'
            r'[\s\S]*?\(layers\s+"F\.Cu"\s+"B\.Cu"\)', text,
        )
