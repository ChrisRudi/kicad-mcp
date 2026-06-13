# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for kicad_mcp.tools.pcb_geometry_tools.

Synthesises a small ``.kicad_pcb`` with two footprints (one on F.Cu, one
flipped to B.Cu) so we can verify the world-coordinate transform handles
rotation + flip correctly. The test fixture also has a pre-existing
``(layers …)`` block and ``(net 0 "")`` declaration so net insertion is
exercised on a realistic structure.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from kicad_mcp.tools import pcb_geometry_tools as pgt


# ---------------------------------------------------------------------------
# Fixture PCB: two SOIC-like footprints, one normal, one flipped to B.Cu.
# ---------------------------------------------------------------------------


PCB_TEMPLATE = textwrap.dedent(
    """\
    (kicad_pcb
    \t(version 20240108)
    \t(generator "test")
    \t(general
    \t\t(thickness 1.6)
    \t)
    \t(layers
    \t\t(0 "F.Cu" signal)
    \t\t(31 "B.Cu" signal)
    \t\t(44 "Edge.Cuts" user)
    \t)
    \t(net 0 "")
    \t(footprint "Test:U_FCU"
    \t\t(layer "F.Cu")
    \t\t(uuid "00000000-0000-0000-0000-00000000aaaa")
    \t\t(at 10.0 5.0 90.0)
    \t\t(property "Reference" "U1"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.SilkS")
    \t\t)
    \t\t(property "Value" "FCU"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.Fab")
    \t\t)
    \t\t(pad "1" smd rect
    \t\t\t(at 1.0 0.0)
    \t\t\t(size 0.6 0.6)
    \t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
    \t\t)
    \t)
    \t(footprint "Test:U_BCU"
    \t\t(layer "B.Cu")
    \t\t(uuid "00000000-0000-0000-0000-00000000bbbb")
    \t\t(at -10.0 -5.0 0.0)
    \t\t(property "Reference" "U2"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "B.SilkS")
    \t\t)
    \t\t(property "Value" "BCU"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "B.Fab")
    \t\t)
    \t\t(pad "1" smd rect
    \t\t\t(at 1.0 0.0)
    \t\t\t(size 0.6 0.6)
    \t\t\t(layers "B.Cu" "B.Mask" "B.Paste")
    \t\t)
    \t)
    )
    """
)


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "geom.kicad_pcb"
    p.write_text(PCB_TEMPLATE, encoding="utf-8")
    return str(p)


@pytest.fixture
def mcp_with_geometry():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    pgt.register_pcb_geometry_tools(mcp)
    return mcp


def _call(mcp, name, **kwargs):
    import asyncio

    async def _do():
        result = await mcp.call_tool(name, kwargs)
        if isinstance(result, tuple):
            return result[1] if len(result) > 1 else result[0]
        return result

    return asyncio.run(_do())


# ---------------------------------------------------------------------------
# World-coordinate transform
# ---------------------------------------------------------------------------


class TestPadWorldTransform:
    def test_fcu_rotation(self):
        # Footprint at (10,5) rotated 90° (CCW visually, math-CW in y-down
        # screen coords — matches KiCad's RotatePoint). Pad-rel (1,0) is
        # the local +X axis, which after a visual-CCW rotation by 90°
        # points UP on screen = smaller y in screen coords. World →
        # (10, 4).
        x, y = pgt._transform_pad_world(
            fp_x=10.0, fp_y=5.0, fp_rot=90.0,
            fp_layer="F.Cu", pad_rel_x=1.0, pad_rel_y=0.0,
        )
        assert x == pytest.approx(10.0, abs=1e-6)
        assert y == pytest.approx(4.0, abs=1e-6)

    def test_bcu_pad_coords_are_post_flip(self):
        # KiCad's FOOTPRINT::Flip mirrors PAD::m_pos.X in-place when the
        # footprint is placed on B.Cu. By the time the file is written,
        # the pad-relative (at lx ly) value is ALREADY mirrored. Reading
        # the file and applying another mirror produces an answer that
        # disagrees with what DRC and pcbnew report.
        # → therefore for B.Cu footprints, the file value MUST be used
        # as-is (no additional flip in the transform). For a B.Cu
        # footprint at fp_origin=(-10,-5), file-stored pad rel (1, 0)
        # lands at world (-10 + 1, -5) = (-9, -5).
        x, y = pgt._transform_pad_world(
            fp_x=-10.0, fp_y=-5.0, fp_rot=0.0,
            fp_layer="B.Cu", pad_rel_x=1.0, pad_rel_y=0.0,
        )
        assert x == pytest.approx(-9.0, abs=1e-6)
        assert y == pytest.approx(-5.0, abs=1e-6)

    def test_bcu_with_rotation(self):
        # B.Cu pad-rel (1, 0) — file-stored, no further flip. Apply
        # math-CW rotation by 90°:
        #   wx = 1·cos(90°) + 0·sin(90°) = 0
        #   wy = -1·sin(90°) + 0·cos(90°) = -1
        # Translate by (1, 1) → (1, 0).
        x, y = pgt._transform_pad_world(
            fp_x=1.0, fp_y=1.0, fp_rot=90.0,
            fp_layer="B.Cu", pad_rel_x=1.0, pad_rel_y=0.0,
        )
        assert x == pytest.approx(1.0, abs=1e-6)
        assert y == pytest.approx(0.0, abs=1e-6)

    def test_bcu_realistic_soic_pin1(self):
        # Reproduces the reference-Mainboard U_597 case: SOIC-16 placed on
        # B.Cu via place_at_pivot, file stores pad 1 at the library-
        # canonical (-2.475, -4.445). User-visible pin 1 (per pcbnew
        # hover and DRC violation report) is at world (134.166, 96.037)
        # for fp at (129.102, 96.525) rot=-113.6°. The transform must
        # match — applying a redundant B.Cu X-mirror produces world
        # (132.184, 100.573) which is where the OPPOSITE pad (pin 16,
        # +3V3 on this footprint) sits and creates a routing snapping
        # disaster when the LLM trusts the wrong coordinate.
        x, y = pgt._transform_pad_world(
            fp_x=129.102, fp_y=96.525, fp_rot=-113.6,
            fp_layer="B.Cu", pad_rel_x=-2.474996, pad_rel_y=-4.444996,
        )
        assert x == pytest.approx(134.166, abs=5e-3)
        assert y == pytest.approx(96.037, abs=5e-3)


# ---------------------------------------------------------------------------
# compute_pad_world_positions
# ---------------------------------------------------------------------------


class TestComputePadPositions:
    def test_extracts_world_pads(self, mcp_with_geometry, pcb_path):
        out = _call(
            mcp_with_geometry, "compute_pad_world_positions", pcb_path=pcb_path,
        )
        assert out["success"] is True
        assert out["footprint_count"] == 2
        u1 = out["footprints"]["U1"][0]
        # U1 at (10,5), 90° (visual-CCW) → pad 1 (rel 1,0) → world (10, 4).
        # The local +X axis points visually up after a 90° CCW turn,
        # which in y-down screen coords is smaller y.
        assert u1["x_mm"] == pytest.approx(10.0)
        assert u1["y_mm"] == pytest.approx(4.0)
        assert u1["primary_layer"] == "F.Cu"

        u2 = out["footprints"]["U2"][0]
        # U2 at (-10,-5) on B.Cu, 0° → pad-rel (1, 0) is file-stored
        # (post-flip by KiCad's FOOTPRINT::Flip) and must be used as-is.
        # World = (-10 + 1, -5) = (-9, -5).
        assert u2["x_mm"] == pytest.approx(-9.0)
        assert u2["y_mm"] == pytest.approx(-5.0)
        assert u2["primary_layer"] == "B.Cu"

    def test_missing_file(self, mcp_with_geometry, tmp_path):
        out = _call(
            mcp_with_geometry, "compute_pad_world_positions",
            pcb_path=str(tmp_path / "no.kicad_pcb"),
        )
        assert out["success"] is False


# ---------------------------------------------------------------------------
# add_track_to_pcb
# ---------------------------------------------------------------------------


class TestAddTrack:
    def test_adds_segment_with_via_when_layers_differ(
        self, mcp_with_geometry, pcb_path,
    ):
        out = _call(
            mcp_with_geometry, "add_track_to_pcb",
            pcb_path=pcb_path,
            ref1="U1", pin1="1", ref2="U2", pin2="1",
            net_name="VCC",
        )
        assert out["success"] is True
        assert out["segments_added"] == 1
        assert out["vias_added"] == 1
        # Endpoints must match the world-coord transform (math-CW for
        # the visual-CCW rotation convention KiCad uses; B.Cu pad coords
        # are file-stored = post-flip, no further mirror).
        assert out["from"]["x_mm"] == pytest.approx(10.0)
        assert out["from"]["y_mm"] == pytest.approx(4.0)
        assert out["to"]["x_mm"] == pytest.approx(-9.0)
        assert out["to"]["y_mm"] == pytest.approx(-5.0)
        # File on disk must contain a (segment …) and a (via …) block now.
        text = Path(pcb_path).read_text(encoding="utf-8")
        assert "(segment" in text
        assert "(via" in text
        # Net "VCC" must have been inserted.
        assert '"VCC"' in text

    def test_no_via_for_same_layer(self, mcp_with_geometry, tmp_path):
        # Both footprints on F.Cu — no via.
        body = PCB_TEMPLATE.replace('(layer "B.Cu")', '(layer "F.Cu")', 1)
        body = body.replace(
            '(layers "B.Cu" "B.Mask" "B.Paste")',
            '(layers "F.Cu" "F.Mask" "F.Paste")',
            1,
        )
        p = tmp_path / "same.kicad_pcb"
        p.write_text(body, encoding="utf-8")
        out = _call(
            mcp_with_geometry, "add_track_to_pcb",
            pcb_path=str(p), ref1="U1", pin1="1", ref2="U2", pin2="1",
            net_name="DAT",
        )
        assert out["success"] is True
        assert out["vias_added"] == 0

    def test_missing_pad_rejected(self, mcp_with_geometry, pcb_path):
        out = _call(
            mcp_with_geometry, "add_track_to_pcb",
            pcb_path=pcb_path,
            ref1="U1", pin1="99", ref2="U2", pin2="1",
        )
        assert out["success"] is False
        assert "Pad not found" in out["error"]


# ---------------------------------------------------------------------------
# add_zone_pour_to_pcb
# ---------------------------------------------------------------------------


class TestAddZonePour:
    def test_adds_zone_block(self, mcp_with_geometry, pcb_path):
        out = _call(
            mcp_with_geometry, "add_zone_pour_to_pcb",
            pcb_path=pcb_path, net_name="GND", layer="B.Cu",
            polygon_xy_mm=[[-15, -10], [15, -10], [15, 10], [-15, 10]],
        )
        assert out["success"] is True
        assert out["vertices"] == 4
        text = Path(pcb_path).read_text(encoding="utf-8")
        assert "(zone" in text
        assert '(net_name "GND")' in text
        # GND net was created on the fly.
        assert '"GND"' in text

    def test_polygon_too_small(self, mcp_with_geometry, pcb_path):
        out = _call(
            mcp_with_geometry, "add_zone_pour_to_pcb",
            pcb_path=pcb_path, net_name="GND", layer="B.Cu",
            polygon_xy_mm=[[0, 0], [1, 0]],
        )
        assert out["success"] is False
        assert "at least 3" in out["error"]


# ---------------------------------------------------------------------------
# Net helpers
# ---------------------------------------------------------------------------


class TestNetHelpers:
    def test_ensure_net_idempotent(self, pcb_path):
        text = Path(pcb_path).read_text(encoding="utf-8")
        # First call creates net 1.
        new_text, nid = pgt._ensure_net(text, "VCC")
        assert nid == 1
        # Second call (on the now-patched text) returns the same id.
        same_text, same_nid = pgt._ensure_net(new_text, "VCC")
        assert same_nid == 1
        assert same_text == new_text   # nothing else changed


# ---------------------------------------------------------------------------
# String-form PCBs (KiCad 10 short net tag, no top-level net table).
# The original emitters wrote ``(net 0)`` on such files because their
# ``_ensure_net`` indexed-lookup found no table and synthesised one with
# index 0. Verify the fix:
#  * tracks/vias/arcs/zones carry ``(net "name")`` literally;
#  * the file never grows a synthetic ``(net 0 "name")`` line at the top;
#  * the result-dict reports ``net_format="string"`` and ``net_id=None``.
# ---------------------------------------------------------------------------


STRING_FORM_PCB = textwrap.dedent(
    """\
    (kicad_pcb
    \t(version 20240108)
    \t(generator "test")
    \t(general
    \t\t(thickness 1.6)
    \t)
    \t(layers
    \t\t(0 "F.Cu" signal)
    \t\t(31 "B.Cu" signal)
    \t\t(44 "Edge.Cuts" user)
    \t)
    \t(footprint "Test:U_FCU"
    \t\t(layer "F.Cu")
    \t\t(uuid "00000000-0000-0000-0000-00000000aaaa")
    \t\t(at 10.0 5.0 0.0)
    \t\t(property "Reference" "U1"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.SilkS")
    \t\t)
    \t\t(property "Value" "FCU"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.Fab")
    \t\t)
    \t\t(pad "1" smd rect
    \t\t\t(at 1.0 0.0)
    \t\t\t(size 0.6 0.6)
    \t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
    \t\t\t(net "DRIVE_P1_A")
    \t\t)
    \t)
    \t(footprint "Test:U_FCU2"
    \t\t(layer "F.Cu")
    \t\t(uuid "00000000-0000-0000-0000-00000000bbbb")
    \t\t(at 30.0 5.0 0.0)
    \t\t(property "Reference" "U2"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.SilkS")
    \t\t)
    \t\t(property "Value" "FCU"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.Fab")
    \t\t)
    \t\t(pad "1" smd rect
    \t\t\t(at 1.0 0.0)
    \t\t\t(size 0.6 0.6)
    \t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
    \t\t\t(net "DRIVE_P1_A")
    \t\t)
    \t)
    )
    """
)


@pytest.fixture
def string_pcb_path(tmp_path):
    p = tmp_path / "string.kicad_pcb"
    p.write_text(STRING_FORM_PCB, encoding="utf-8")
    return str(p)


class TestStringFormPcb:
    def test_via_on_string_pcb_writes_named_tag(
        self, mcp_with_geometry, string_pcb_path,
    ):
        out = _call(
            mcp_with_geometry, "add_via_to_pcb",
            pcb_path=string_pcb_path,
            x_mm=15.0, y_mm=5.0,
            net_name="DRIVE_P1_A",
        )
        assert out["success"] is True
        assert out["net_format"] == "string"
        assert out["net_id"] is None
        text = Path(string_pcb_path).read_text(encoding="utf-8")
        assert '(net "DRIVE_P1_A")' in text
        # No synthetic table line at the top.
        assert '(net 0 "DRIVE_P1_A")' not in text
        # And the via must carry the named tag, not (net 0):
        via_block = text[text.find("(via"):text.find("(via") + 200]
        assert '(net "DRIVE_P1_A")' in via_block
        assert "(net 0)" not in via_block

    def test_track_on_string_pcb_uses_pad_net(
        self, mcp_with_geometry, string_pcb_path,
    ):
        # No net_name → falls back to the source pad's net (DRIVE_P1_A).
        out = _call(
            mcp_with_geometry, "add_track_to_pcb",
            pcb_path=string_pcb_path,
            ref1="U1", pin1="1", ref2="U2", pin2="1",
        )
        assert out["success"] is True
        assert out["net_format"] == "string"
        assert out["net_id"] is None
        assert out["net_name"] == "DRIVE_P1_A"
        text = Path(string_pcb_path).read_text(encoding="utf-8")
        seg_block = text[text.find("(segment"):text.find("(segment") + 250]
        assert '(net "DRIVE_P1_A")' in seg_block
        assert "(net 0)" not in seg_block

    def test_arc_on_string_pcb_writes_named_tag(
        self, mcp_with_geometry, string_pcb_path,
    ):
        out = _call(
            mcp_with_geometry, "add_arc_to_pcb",
            pcb_path=string_pcb_path,
            start_x_mm=10.0, start_y_mm=5.0,
            end_x_mm=20.0, end_y_mm=5.0,
            center_x_mm=15.0, center_y_mm=5.0,
            layer="In1.Cu", net_name="DRIVE_P1_A",
        )
        assert out["success"] is True
        assert out["net_format"] == "string"
        text = Path(string_pcb_path).read_text(encoding="utf-8")
        arc_block = text[text.find("(arc"):text.find("(arc") + 300]
        assert '(net "DRIVE_P1_A")' in arc_block
        assert "(net 0)" not in arc_block
        # No table pollution.
        assert '(net 0 "DRIVE_P1_A")' not in text

    def test_zone_on_string_pcb_omits_net_name_redundancy(
        self, mcp_with_geometry, string_pcb_path,
    ):
        out = _call(
            mcp_with_geometry, "add_zone_pour_to_pcb",
            pcb_path=string_pcb_path,
            net_name="GND", layer="B.Cu",
            polygon_xy_mm=[[0, 0], [10, 0], [10, 10], [0, 10]],
        )
        assert out["success"] is True
        assert out["net_format"] == "string"
        text = Path(string_pcb_path).read_text(encoding="utf-8")
        zone_block = text[text.find("(zone"):text.find("(zone") + 400]
        assert '(net "GND")' in zone_block
        # String-form PCBs drop the redundant ``(net_name "GND")`` line.
        assert '(net_name "GND")' not in zone_block

    def test_string_pcb_format_stays_string_after_edits(
        self, mcp_with_geometry, string_pcb_path,
    ):
        # After several edits, the PCB must still parse as a string-form
        # file — no synthetic ``(net N "name")`` lines should have leaked
        # in at the top of the document.
        for name, kwargs in [
            ("add_via_to_pcb",
             dict(x_mm=15.0, y_mm=5.0, net_name="DRIVE_P1_A")),
            ("add_arc_to_pcb",
             dict(start_x_mm=10.0, start_y_mm=5.0,
                  end_x_mm=20.0, end_y_mm=5.0,
                  center_x_mm=15.0, center_y_mm=5.0,
                  layer="In1.Cu", net_name="DRIVE_P1_A")),
            ("add_zone_pour_to_pcb",
             dict(net_name="GND", layer="B.Cu",
                  polygon_xy_mm=[[0, 0], [10, 0], [10, 10], [0, 10]])),
        ]:
            out = _call(mcp_with_geometry, name,
                        pcb_path=string_pcb_path, **kwargs)
            assert out["success"] is True
            assert out["net_format"] == "string"

        text = Path(string_pcb_path).read_text(encoding="utf-8")
        # Indexed-form table lines must not appear at the top.
        from kicad_mcp.utils.pcb_net_format import pcb_net_format
        assert pcb_net_format(text) == "string"
