# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``place_at_pivot`` — radial/clustered placement with pad-shape
rotation propagation."""

from __future__ import annotations

import asyncio
import re

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


MIN_PCB = """\
(kicad_pcb
\t(version 20240108)
\t(generator "test")
\t(general
\t\t(thickness 1.6)
\t)
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(31 "B.Cu" signal)
\t)
\t(footprint "Test:R_0402"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-000000000001")
\t\t(at 10.0 20.0 0.0)
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
\t\t(pad "2" smd rect
\t\t\t(at 0.5 0)
\t\t\t(size 0.5 0.6)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t)
\t)
)
"""


MIN_MOD = """\
(module Test:R_0402 (layer F.Cu)
  (fp_line (start -1.1 -0.7) (end 1.1 -0.7) (layer "F.Fab"))
  (fp_line (start  1.1 -0.7) (end 1.1  0.7) (layer "F.Fab"))
  (pad "1" smd rect (at -0.5 0) (size 0.5 0.6) (layers "F.Cu"))
  (pad "2" smd rect (at  0.5 0) (size 0.5 0.6) (layers "F.Cu"))
)
"""


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text(MIN_PCB, encoding="utf-8")
    return str(p)


@pytest.fixture
def mod_path(tmp_path):
    p = tmp_path / "R_0402.kicad_mod"
    p.write_text(MIN_MOD, encoding="utf-8")
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


def _header_at(text: str, ref: str) -> tuple[float, float, int]:
    """Pull ``(at x y rot)`` from the footprint header for ``ref``."""
    fp_re = re.compile(
        r'\(footprint[\s\S]*?\(property\s+"Reference"\s+"' + re.escape(ref) + r'"'
    )
    m = fp_re.search(text)
    assert m, f"footprint for {ref} not found"
    span_start = m.start()
    head_re = re.compile(
        r'\)\s*\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+([\d.\-]+))?\)'
    )
    m2 = head_re.search(text, span_start)
    assert m2, f"header (at) for {ref} not found"
    return float(m2.group(1)), float(m2.group(2)), int(float(m2.group(3) or 0))


def _pad_rots(text: str, ref: str) -> list[int]:
    """Pull every pad's lokal-rot from the footprint block of ``ref``."""
    block_re = re.compile(
        r'\(footprint[\s\S]*?\(property\s+"Reference"\s+"' + re.escape(ref) + r'"'
        r'[\s\S]*?(?=\(footprint|\Z)'
    )
    m = block_re.search(text)
    assert m, f"footprint for {ref} not found"
    block = m.group(0)
    pad_re = re.compile(
        r'\(pad\s+"[^"]*"\s+\w+\s+\w+\s*'
        r'(?:[^()]|\([^()]*\))*?'
        r'\(at\s+[\d.\-]+\s+[\d.\-]+(?:\s+([\d.\-]+))?\)'
    )
    return [int(float(m.group(1) or 0)) for m in pad_re.finditer(block)]


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestAnchorPivot:
    def test_translate_only(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=50.0, target_y_mm=60.0,
            pivot_kind="anchor",
        )
        assert out["success"] is True
        assert out["anchor"] == {"x_mm": 50.0, "y_mm": 60.0}
        x, y, rot = _header_at(_read(pcb_path), "R1")
        assert (x, y, rot) == (50.0, 60.0, 0)

    def test_rotation_propagates_to_pads(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=10.0, target_y_mm=20.0,
            pivot_kind="anchor",
            rotation_deg=90.0,
        )
        assert out["success"] is True
        assert out["rotation"] == 90.0
        assert out["pads_updated"] == 2
        text = _read(pcb_path)
        _, _, hdr_rot = _header_at(text, "R1")
        assert hdr_rot == 90
        assert _pad_rots(text, "R1") == [90, 90]


class TestPadPivot:
    def test_pad_lands_at_target(self, mcp_server, pcb_path):
        # Pad "2" has local (0.5, 0). With anchor=(10,20) rot=0, its
        # world pos is (10.5, 20). Moving pad "2" to (100, 100) at the
        # same rotation must place the anchor at (99.5, 100).
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=100.0, target_y_mm=100.0,
            pivot_kind="pad", pivot_arg="2",
        )
        assert out["success"] is True
        x, y, _ = _header_at(_read(pcb_path), "R1")
        assert x == pytest.approx(99.5)
        assert y == pytest.approx(100.0)

    def test_pad_pivot_with_rotation(self, mcp_server, pcb_path):
        # Pad "2" local (0.5, 0). At rot=90°, the pad world pos relative
        # to anchor is (0, -0.5) by the CW transform. So anchor =
        # target − (0, -0.5) = (100, 100.5).
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=100.0, target_y_mm=100.0,
            pivot_kind="pad", pivot_arg="2",
            rotation_deg=90.0,
        )
        assert out["success"] is True
        x, y, rot = _header_at(_read(pcb_path), "R1")
        assert (round(x, 4), round(y, 4), rot) == (100.0, 100.5, 90)


class TestBboxCenterPivot:
    def test_centre_at_target(self, mcp_server, pcb_path, mod_path):
        # MIN_MOD has a symmetric bbox (pads + fp_line). Centre should
        # be near (0, 0) → anchor lands at target.
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=42.0, target_y_mm=58.0,
            pivot_kind="bbox_center", mod_path=mod_path,
        )
        assert out["success"] is True
        x, y, _ = _header_at(_read(pcb_path), "R1")
        # bbox of MIN_MOD: x∈[-1.1, 1.1], y∈[-0.7, 0.7] → centre (0, 0)
        # → anchor must equal target.
        assert (round(x, 4), round(y, 4)) == (42.0, 58.0)


class TestAutoRotation:
    def test_radial_out_east(self, mcp_server, pcb_path):
        # Target east of (148.5, 105): expected rotation 0°.
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=171.0, target_y_mm=105.0,
            pivot_kind="anchor",
            auto_rotation="radial_out",
            center_x_mm=148.5, center_y_mm=105.0,
        )
        assert out["success"] is True
        assert out["rotation"] == pytest.approx(0.0, abs=1e-6)

    def test_tangential_ccw_overrides_rotation_deg(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=171.0, target_y_mm=105.0,
            pivot_kind="anchor",
            rotation_deg=37.0,                   # must be ignored
            auto_rotation="tangential_ccw",
            center_x_mm=148.5, center_y_mm=105.0,
        )
        assert out["success"] is True
        assert out["rotation"] == pytest.approx(90.0)


class TestLayerSwap:
    def test_move_to_b_cu(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=10.0, target_y_mm=20.0,
            pivot_kind="anchor",
            layer="B.Cu",
        )
        assert out["success"] is True
        assert out["layer"] == "B.Cu"
        assert '(layer "B.Cu")' in _read(pcb_path)

    def test_bcu_pad_pivot_no_double_flip(self, mcp_server, pcb_path):
        # place_at_pivot does NOT mirror pad-local coords when changing
        # layer (it only rewrites the (layer) tag and pad-local rotations
        # — see ``_patch_fp_pose``). Therefore the anchor-from-pivot
        # calculation must use ``flipped=False`` regardless of layer, or
        # the pivot lands at the wrong world position.
        # Pad "2" local (0.5, 0). At rot=0, after place_at_pivot to B.Cu
        # with pad-pivot "2" → target (100, 100), the anchor must end up
        # at (99.5, 100) — exactly the same as the F.Cu case (because
        # the function doesn't flip pad coords).
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=100.0, target_y_mm=100.0,
            pivot_kind="pad", pivot_arg="2",
            layer="B.Cu",
        )
        assert out["success"] is True
        x, y, _ = _header_at(_read(pcb_path), "R1")
        assert x == pytest.approx(99.5)
        assert y == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    def test_missing_pcb(self, mcp_server, tmp_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=str(tmp_path / "nope.kicad_pcb"),
            ref="R1",
            target_x_mm=0.0, target_y_mm=0.0,
        )
        assert out["success"] is False
        assert "not found" in out["error"].lower()

    def test_unknown_ref(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="Q42",
            target_x_mm=0.0, target_y_mm=0.0,
        )
        assert out["success"] is False
        assert "footprint not found" in out["error"].lower()

    def test_unknown_pad_pivot(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=0.0, target_y_mm=0.0,
            pivot_kind="pad", pivot_arg="99",
        )
        assert out["success"] is False
        assert "pad" in out["error"].lower()

    def test_bbox_center_missing_mod(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=0.0, target_y_mm=0.0,
            pivot_kind="bbox_center",
        )
        assert out["success"] is False
        assert "mod_path" in out["error"]

    def test_invalid_layer(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=0.0, target_y_mm=0.0,
            layer="In1.Cu",
        )
        assert out["success"] is False
        assert "layer" in out["error"].lower()

    def test_invalid_auto_rotation(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=10.0, target_y_mm=20.0,
            auto_rotation="diagonal",
        )
        assert out["success"] is False
        assert "mode" in out["error"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_two_calls_same_args_byte_identical(self, mcp_server, pcb_path):
        out1 = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=42.0, target_y_mm=37.0,
            pivot_kind="anchor", rotation_deg=45.0,
        )
        assert out1["success"]
        snapshot1 = _read(pcb_path)
        out2 = _call(
            mcp_server, "place_at_pivot",
            pcb_path=pcb_path, ref="R1",
            target_x_mm=42.0, target_y_mm=37.0,
            pivot_kind="anchor", rotation_deg=45.0,
        )
        assert out2["success"]
        assert _read(pcb_path) == snapshot1
