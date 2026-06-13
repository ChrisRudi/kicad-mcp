# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the cropped-region PCB renderer (pcb_render_tools).

Skipped when cairosvg / kicad-cli aren't usable. Verifies Edge.Cuts bbox
parsing, the crop pipeline, and PNG output.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from kicad_mcp.tools.pcb_render_tools import _edge_bbox, _render

FIXTURE = str(Path(__file__).parent / "PCB" / "medium_8pin" / "medium_8pin_routed.kicad_pcb")


def _png_magic(path):
    with open(path, "rb") as f:
        return f.read(8) == b"\x89PNG\r\n\x1a\n"


def test_edge_bbox_parses():
    bb = _edge_bbox(Path(FIXTURE).read_text(encoding="utf-8"))
    assert bb is not None
    minx, miny, maxx, maxy = bb
    assert maxx > minx and maxy > miny


def test_render_produces_png(tmp_path):
    try:
        from kicad_mcp.tools.pcb_render_tools import _load_cairosvg
        _load_cairosvg()
    except Exception:
        pytest.skip("cairosvg/cairo not available in this environment")
    bb = _edge_bbox(Path(FIXTURE).read_text(encoding="utf-8"))
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    out = str(tmp_path / "r.png")
    try:
        res = _render(FIXTURE, cx, cy, 8.0, 400, "F.Cu,B.Cu,Edge.Cuts", out)
    except RuntimeError as e:
        if "kicad-cli" in str(e):
            pytest.skip("kicad-cli not available")
        raise
    assert os.path.isfile(res["png_path"])
    assert _png_magic(res["png_path"])
    assert res["size_bytes"] > 0
    assert res["region_board_mm"]["x"][0] < cx < res["region_board_mm"]["x"][1]


def test_render_missing_file_via_tool(tmp_path):
    # the MCP tool body's guard (no pcbnew/cairo needed)
    from kicad_mcp.tools import pcb_render_tools as m

    class _Mcp:
        def tool(self):
            def deco(fn):
                self.fn = fn
                return fn
            return deco
    mcp = _Mcp()
    m.register_pcb_render_tools(mcp)
    r = mcp.fn("/nope/none.kicad_pcb", 0, 0)
    assert r["success"] is False and "not found" in r["error"].lower()
