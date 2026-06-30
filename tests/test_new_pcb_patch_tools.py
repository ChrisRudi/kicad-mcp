# SPDX-License-Identifier: GPL-3.0-or-later
"""Happy-path tests for the 5 PCB-edit tools added 2026-05-20:

    * add_segment
    * delete_footprint
    * add_footprint_text
    * set_footprint_3d_model
    * set_footprint_property_visibility
"""

from __future__ import annotations

import asyncio

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt


# ---------------------------------------------------------------------------
# Fixture: small PCB with a single footprint that already has a Reference
# property, a Value property, a (model) override, and one segment + via at
# top level. Mirrors the structure used by tiny_2pin but trimmed down.
# ---------------------------------------------------------------------------


PCB_FIXTURE = """\
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
\t(footprint "Resistor_SMD:R_0402_1005Metric"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-aaaaaaaaaaaa")
\t\t(at 50 50 0)
\t\t(property "Reference" "R1"
\t\t\t(at 0 -2 0)
\t\t\t(unlocked yes)
\t\t\t(layer "F.SilkS")
\t\t\t(uuid "11111111-1111-1111-1111-111111111111")
\t\t\t(effects (font (size 1 1) (thickness 0.15)))
\t\t)
\t\t(property "Value" "10k"
\t\t\t(at 0 2 0)
\t\t\t(layer "F.Fab")
\t\t\t(uuid "22222222-2222-2222-2222-222222222222")
\t\t\t(effects (font (size 1 1) (thickness 0.15)))
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at -0.5 0)
\t\t\t(size 0.5 0.6)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t\t(net 1 "VCC")
\t\t)
\t\t(pad "2" smd rect
\t\t\t(at 0.5 0)
\t\t\t(size 0.5 0.6)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t\t(net 2 "GND")
\t\t)
\t\t(model "${KICAD10_3DMODEL_DIR}/Resistor_SMD.3dshapes/old.step"
\t\t\t(offset
\t\t\t\t(xyz 0 0 0)
\t\t\t)
\t\t\t(scale
\t\t\t\t(xyz 1 1 1)
\t\t\t)
\t\t\t(rotate
\t\t\t\t(xyz 0 0 0)
\t\t\t)
\t\t)
\t)
\t(footprint "TestPoint:TestPoint_Pad_D2.0mm"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-bbbbbbbbbbbb")
\t\t(at 60 60 0)
\t\t(property "Reference" "TP_GND2"
\t\t\t(at 0 -2 0)
\t\t\t(layer "F.SilkS")
\t\t\t(uuid "33333333-3333-3333-3333-333333333333")
\t\t\t(effects (font (size 1 1) (thickness 0.15)))
\t\t)
\t\t(property "Value" "GND"
\t\t\t(at 0 2 0)
\t\t\t(layer "F.Fab")
\t\t\t(uuid "44444444-4444-4444-4444-444444444444")
\t\t\t(effects (font (size 1 1) (thickness 0.15)))
\t\t)
\t\t(pad "1" smd circle
\t\t\t(at 0 0)
\t\t\t(size 2 2)
\t\t\t(layers "F.Cu" "F.Mask")
\t\t\t(net 2 "GND")
\t\t)
\t)
\t(segment (start 10.0 10.0) (end 20.0 10.0) (width 0.25) (layer "F.Cu") (net 1) (uuid "55555555-5555-5555-5555-555555555555"))
)
"""


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "fixture.kicad_pcb"
    p.write_text(PCB_FIXTURE, encoding="utf-8")
    return str(p)


@pytest.fixture
def mcp_server():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test_new_pcb")
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
# 1. add_segment
# ---------------------------------------------------------------------------


def test_add_segment_happy_path(mcp_server, pcb_path):
    out = _call(
        mcp_server, "add_segment",
        pcb_path=pcb_path,
        start_x_mm=100.0, start_y_mm=100.0,
        end_x_mm=110.0, end_y_mm=100.0,
        layer="F.Cu", net_name="VCC", width_mm=0.30,
    )
    assert out["success"] is True
    assert out["segment_added"]["start"] == [100.0, 100.0]
    assert out["segment_added"]["end"] == [110.0, 100.0]
    assert out["segment_added"]["layer"] == "F.Cu"
    assert out["segment_added"]["net"] == "VCC"
    assert out["segment_added"]["net_id"] == 1  # existing VCC id
    assert out["segment_added"]["width"] == 0.30
    text = _read(pcb_path)
    # New segment is present.
    assert "(start 100.000000 100.000000)" in text
    assert "(end 110.000000 100.000000)" in text
    # Original segment still present.
    assert '"55555555-5555-5555-5555-555555555555"' in text


def test_add_segment_creates_new_net(mcp_server, pcb_path):
    out = _call(
        mcp_server, "add_segment",
        pcb_path=pcb_path,
        start_x_mm=0.0, start_y_mm=0.0,
        end_x_mm=1.0, end_y_mm=0.0,
        layer="In1.Cu", net_name="VBUS_NEW", width_mm=0.25,
    )
    assert out["success"] is True
    assert out["segment_added"]["net_id"] == 3  # next after GND=2
    text = _read(pcb_path)
    assert '(net 3 "VBUS_NEW")' in text


def test_add_segment_rejects_non_copper_layer(mcp_server, pcb_path):
    out = _call(
        mcp_server, "add_segment",
        pcb_path=pcb_path,
        start_x_mm=0.0, start_y_mm=0.0,
        end_x_mm=1.0, end_y_mm=0.0,
        layer="F.SilkS", net_name="VCC",
    )
    assert out["success"] is False
    assert "copper" in out["error"].lower()


def test_add_segment_dry_run_does_not_write(mcp_server, pcb_path):
    before = _read(pcb_path)
    out = _call(
        mcp_server, "add_segment",
        pcb_path=pcb_path,
        start_x_mm=200.0, start_y_mm=200.0,
        end_x_mm=210.0, end_y_mm=200.0,
        layer="F.Cu", net_name="VCC", width_mm=0.25,
        dry_run=True,
    )
    assert out["success"] is True
    assert out["dry_run"] is True
    assert _read(pcb_path) == before


# ---------------------------------------------------------------------------
# 2. delete_footprint
# ---------------------------------------------------------------------------


def test_delete_footprint_happy_path(mcp_server, pcb_path):
    before = _read(pcb_path)
    assert '"TP_GND2"' in before
    out = _call(
        mcp_server, "delete_footprint",
        pcb_path=pcb_path, ref="TP_GND2",
    )
    assert out["success"] is True
    assert out["deleted"]["ref"] == "TP_GND2"
    assert out["deleted"]["lib_id"] == "TestPoint:TestPoint_Pad_D2.0mm"
    assert out["deleted"]["position"][:2] == [60.0, 60.0]
    after = _read(pcb_path)
    assert '"TP_GND2"' not in after
    # R1 still present.
    assert '"R1"' in after
    # Top-level segment still present.
    assert '"55555555-5555-5555-5555-555555555555"' in after


def test_delete_footprint_unknown_ref(mcp_server, pcb_path):
    out = _call(
        mcp_server, "delete_footprint",
        pcb_path=pcb_path, ref="NOPE",
    )
    assert out["success"] is False
    assert "not found" in out["error"].lower()


# ---------------------------------------------------------------------------
# 3. add_footprint_text
# ---------------------------------------------------------------------------


def test_add_footprint_text_happy_path(mcp_server, pcb_path):
    out = _call(
        mcp_server, "add_footprint_text",
        pcb_path=pcb_path,
        ref="R1", text="pin1",
        local_x_mm=-1.0, local_y_mm=0.0,
        layer="F.SilkS",
        font_size_mm=0.5, font_thickness_mm=0.08,
    )
    assert out["success"] is True
    assert out["text_added"]["ref"] == "R1"
    assert out["text_added"]["text"] == "pin1"
    assert out["text_added"]["layer"] == "F.SilkS"
    assert out["text_added"]["local_xy"] == [-1.0, 0.0]
    assert out["text_added"]["rotation"] == 0
    text = _read(pcb_path)
    # Find the R1 block by ref.
    r1_idx = text.find('"R1"')
    fp_start = text.rfind("(footprint", 0, r1_idx)
    # The next top-level (footprint after R1 marks its end region; we want
    # the fp_text inserted before R1's closing ')'.
    next_fp_start = text.find("(footprint", r1_idx + 1)
    region = text[fp_start:next_fp_start] if next_fp_start > 0 else text[fp_start:]
    assert '(fp_text user "pin1"' in region
    assert '(at -1.000000 0.000000)' in region
    assert '(layer "F.SilkS")' in region
    assert '(size 0.500000 0.500000)' in region
    assert '(thickness 0.080000)' in region


def test_add_footprint_text_b_side_gets_mirror(mcp_server, pcb_path):
    out = _call(
        mcp_server, "add_footprint_text",
        pcb_path=pcb_path,
        ref="R1", text="bottom-label",
        local_x_mm=0.0, local_y_mm=1.0,
        layer="B.SilkS",
    )
    assert out["success"] is True
    text = _read(pcb_path)
    # Just look for the mirror token in the file (only one fp_text added).
    assert "(justify mirror)" in text


def test_add_footprint_text_unknown_ref(mcp_server, pcb_path):
    out = _call(
        mcp_server, "add_footprint_text",
        pcb_path=pcb_path,
        ref="NOPE", text="x",
        local_x_mm=0.0, local_y_mm=0.0,
        layer="F.SilkS",
    )
    assert out["success"] is False


# ---------------------------------------------------------------------------
# 4. set_footprint_3d_model
# ---------------------------------------------------------------------------


def test_set_footprint_3d_model_replace(mcp_server, pcb_path):
    new_path = "${KIPRJMOD}/3d/MyResistor.step"
    out = _call(
        mcp_server, "set_footprint_3d_model",
        pcb_path=pcb_path,
        ref="R1", model_path=new_path,
        offset_xyz=[0.1, 0.2, 0.3],
        scale_xyz=[1.0, 1.0, 1.0],
        rotate_xyz=[0.0, 0.0, 90.0],
        replace_existing=True,
    )
    assert out["success"] is True
    assert out["model_set"]["ref"] == "R1"
    assert out["model_set"]["model_path"] == new_path
    assert out["model_set"]["replaced_existing"] is True
    assert "ipc_revert" in out["note"]
    text = _read(pcb_path)
    # Old model gone, new model present.
    assert "old.step" not in text
    assert "MyResistor.step" in text
    assert "(xyz 0.1 0.2 0.3)" in text
    assert "(xyz 0.0 0.0 90.0)" in text


def test_set_footprint_3d_model_insert_new(mcp_server, pcb_path):
    # TP_GND2 has no (model ...) block.
    out = _call(
        mcp_server, "set_footprint_3d_model",
        pcb_path=pcb_path,
        ref="TP_GND2", model_path="${KIPRJMOD}/3d/tp.step",
    )
    assert out["success"] is True
    assert out["model_set"]["replaced_existing"] is False
    text = _read(pcb_path)
    # New model is now inside TP_GND2 footprint.
    tp_idx = text.find('"TP_GND2"')
    fp_start = text.rfind("(footprint", 0, tp_idx)
    next_fp_start = text.find("(footprint", tp_idx + 1)
    region = text[fp_start:next_fp_start] if next_fp_start > 0 else text[fp_start:]
    assert "tp.step" in region


def test_set_footprint_3d_model_unknown_ref(mcp_server, pcb_path):
    out = _call(
        mcp_server, "set_footprint_3d_model",
        pcb_path=pcb_path, ref="NOPE",
        model_path="x.step",
    )
    assert out["success"] is False


# ---------------------------------------------------------------------------
# 5. set_footprint_property_visibility
# ---------------------------------------------------------------------------


def test_set_visibility_hide_true_inserts_token(mcp_server, pcb_path):
    out = _call(
        mcp_server, "set_footprint_property_visibility",
        pcb_path=pcb_path,
        ref="R1", property_name="Value", hide=True,
    )
    assert out["success"] is True
    assert out["property"]["name"] == "Value"
    assert out["property"]["hide_before"] is False
    assert out["property"]["hide_after"] is True
    text = _read(pcb_path)
    # The Value property of R1 should now have (hide yes).
    # Find the Value property block via balanced-paren walk.
    val_start = text.find('(property "Value"')
    assert val_start != -1
    val_end = ppt._find_block_end(text, val_start)
    block = text[val_start:val_end]
    assert "(hide yes)" in block


def test_set_visibility_hide_false_strips_token(mcp_server, pcb_path):
    # First insert it, then strip.
    _call(
        mcp_server, "set_footprint_property_visibility",
        pcb_path=pcb_path,
        ref="R1", property_name="Reference", hide=True,
    )
    out = _call(
        mcp_server, "set_footprint_property_visibility",
        pcb_path=pcb_path,
        ref="R1", property_name="Reference", hide=False,
    )
    assert out["success"] is True
    assert out["property"]["hide_before"] is True
    assert out["property"]["hide_after"] is False
    text = _read(pcb_path)
    ref_start = text.find('(property "Reference"')
    assert ref_start != -1
    ref_end = ppt._find_block_end(text, ref_start)
    block = text[ref_start:ref_end]
    assert "(hide yes)" not in block


def test_set_visibility_hide_true_idempotent(mcp_server, pcb_path):
    """Second identical call must not duplicate (hide yes)."""
    _call(
        mcp_server, "set_footprint_property_visibility",
        pcb_path=pcb_path,
        ref="R1", property_name="Value", hide=True,
    )
    first = _read(pcb_path)
    _call(
        mcp_server, "set_footprint_property_visibility",
        pcb_path=pcb_path,
        ref="R1", property_name="Value", hide=True,
    )
    second = _read(pcb_path)
    assert first == second
    assert first.count("(hide yes)") == second.count("(hide yes)")


def test_set_visibility_unknown_property(mcp_server, pcb_path):
    out = _call(
        mcp_server, "set_footprint_property_visibility",
        pcb_path=pcb_path,
        ref="R1", property_name="NoSuchProperty", hide=True,
    )
    assert out["success"] is False


# Bug 11: single-line property block must not get a bare footprint-level
# (hide yes) — that produced an unparsable .kicad_pcb.
_SINGLE_LINE_PCB = (
    '(kicad_pcb\n'
    '  (footprint "iFloat:FFC8-1MM-RA" (layer "B.Cu")\n'
    '    (attr smd)\n'
    '    (property "Reference" "J_FFC_A" (at 0 -2.5 150) (layer "B.SilkS")'
    ' (effects (font (size 1 1))))\n'
    '  )\n'
    ')\n'
)


def test_set_visibility_single_line_property_inside_parens():
    out, meta = ppt.set_footprint_property_visibility_text(
        _SINGLE_LINE_PCB, "J_FFC_A", "Reference", True)
    assert meta["success"] is True
    assert meta["property"]["hide_after"] is True
    # (hide yes) lands INSIDE the property block ...
    pstart = out.find('(property "Reference"')
    pend = ppt._find_block_end(out, pstart)
    assert "(hide yes)" in out[pstart:pend]
    # ... and NEVER as a bare footprint-level token (the Bug 11 corruption).
    assert not any(ln.strip() == "(hide yes)" for ln in out.splitlines())
    # parens stay balanced (file still parses).
    assert out.count("(") == out.count(")")


def test_set_visibility_multiline_property_not_regressed():
    # A property whose (layer …) is on its own line keeps the existing
    # behaviour: (hide yes) inside the block, parens balanced.
    pcb = (
        '(kicad_pcb\n'
        '  (footprint "Lib:X" (layer "F.Cu")\n'
        '    (property "Reference" "X" (at 0 0 0))\n'
        '    (property "Value" "100n"\n'
        '      (at 0 1 0)\n'
        '      (layer "F.Fab")\n'
        '      (effects (font (size 1 1)))\n'
        '    )\n'
        '  )\n'
        ')\n'
    )
    out, meta = ppt.set_footprint_property_visibility_text(
        pcb, "X", "Value", True)
    assert meta["success"] is True
    pstart = out.find('(property "Value"')
    pend = ppt._find_block_end(out, pstart)
    assert "(hide yes)" in out[pstart:pend]
    assert out.count("(") == out.count(")")
