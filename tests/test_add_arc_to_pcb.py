# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``add_arc_to_pcb``."""

from __future__ import annotations

import asyncio
import math
import re
import textwrap

import pytest

from kicad_mcp.tools import pcb_geometry_tools as pgt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


MIN_PCB = textwrap.dedent(
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
    \t\t(1 "In1.Cu" signal)
    \t\t(2 "In2.Cu" signal)
    \t)
    \t(net 0 "")
    )
    """
)


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "arc.kicad_pcb"
    p.write_text(MIN_PCB, encoding="utf-8")
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
# Center mode (short_arc_mid_xy)
# ---------------------------------------------------------------------------


class TestCenterMode:
    def test_quarter_arc(self, mcp_server, pcb_path):
        # Start at (10, 0), end at (0, 10), centre at origin.
        # Short-arc mid is at 45° → (7.071, 7.071).
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=10.0, start_y_mm=0.0,
            end_x_mm=0.0,    end_y_mm=10.0,
            center_x_mm=0.0, center_y_mm=0.0,
            layer="In2.Cu", net_name="ARC_TEST", width_mm=0.3,
        )
        assert out["success"] is True
        assert out["arc_added"] == 1
        assert out["mid"]["x_mm"] == pytest.approx(
            10.0 * math.cos(math.radians(45)), abs=1e-3,
        )
        assert out["mid"]["y_mm"] == pytest.approx(
            10.0 * math.sin(math.radians(45)), abs=1e-3,
        )
        assert out["radius_mm"] == pytest.approx(10.0, abs=1e-3)
        # File on disk has the (arc ...) block
        text = _read(pcb_path)
        assert "(arc" in text
        assert '(layer "In2.Cu")' in text
        assert '"ARC_TEST"' in text

    def test_short_mid_not_long_way(self, mcp_server, pcb_path):
        # Reproduces the V12 P0 "long way around" bug pattern:
        # start φ=2.4°, end φ=351.4° on a R=24.95 circle around (148.5, 105).
        # Expected mid lies at φ≈356.9°, NOT diametrically opposite at 177°.
        cx, cy = 148.5, 105.0
        r = 24.95
        sx = cx + r * math.cos(math.radians(2.4))
        # KiCad PCB y-down: pad angles use atan2(-(y-cy), x-cx).
        sy = cy - r * math.sin(math.radians(2.4))
        ex = cx + r * math.cos(math.radians(351.4))
        ey = cy - r * math.sin(math.radians(351.4))
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=sx, start_y_mm=sy,
            end_x_mm=ex, end_y_mm=ey,
            center_x_mm=cx, center_y_mm=cy,
            layer="In1.Cu", net_name="/JUNCT_P0",
        )
        assert out["success"] is True
        # The short-mid lies at ~356.9° → roughly (cx + r·cos, cy - r·sin)
        # Most importantly: distance from start to mid must be SHORT
        # (under r·sin(angular-gap), not r·sin(180-gap)).
        sm = (out["mid"]["x_mm"], out["mid"]["y_mm"])
        d_short = math.hypot(sm[0] - sx, sm[1] - sy)
        # If long-way arc were chosen, distance would be ~2·r·sin(~89.5°)
        # ≈ 49.9 mm. Short-way distance is ~2.3 mm.
        assert d_short < 5.0, (
            f"mid {sm} too far from start ({sx}, {sy}); "
            "looks like the long arc was selected."
        )

    def test_unequal_radii_rejected(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=10.0, start_y_mm=0.0,
            end_x_mm=20.0, end_y_mm=0.0,           # twice the radius
            center_x_mm=0.0, center_y_mm=0.0,
            layer="F.Cu", net_name="X",
        )
        assert out["success"] is False
        assert "equidistant" in out["error"].lower()

    def test_slightly_unequal_radii_accepted_mean_radius(
        self, mcp_server, pcb_path,
    ):
        # Endpoints snapped to real pads: start at r=10.000, end at
        # r=10.030 (30 µm mismatch, within the ±50 µm tolerance).
        # Quarter arc around origin; the arc must be placed on the MEAN
        # radius (10.015), not the start radius.
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=10.000, start_y_mm=0.0,
            end_x_mm=0.0,      end_y_mm=10.030,
            center_x_mm=0.0,   center_y_mm=0.0,
            layer="In1.Cu", net_name="JUNCT",
        )
        assert out["success"] is True
        assert out["radius_mm"] == pytest.approx(10.015, abs=1e-3)
        # Mid sits at 45° on the mean-radius circle.
        assert out["mid"]["x_mm"] == pytest.approx(
            10.015 * math.cos(math.radians(45)), abs=1e-3,
        )
        assert out["mid"]["y_mm"] == pytest.approx(
            10.015 * math.sin(math.radians(45)), abs=1e-3,
        )

    def test_unequal_radii_beyond_tol_rejected(self, mcp_server, pcb_path):
        # 100 µm mismatch — beyond ±50 µm → rejected, error reports µm.
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=10.000, start_y_mm=0.0,
            end_x_mm=0.0,      end_y_mm=10.100,
            center_x_mm=0.0,   center_y_mm=0.0,
            layer="In1.Cu", net_name="X",
        )
        assert out["success"] is False
        assert "equidistant" in out["error"].lower()
        assert "µm" in out["error"]


# ---------------------------------------------------------------------------
# Explicit-mid mode
# ---------------------------------------------------------------------------


class TestExplicitMidMode:
    def test_uses_supplied_mid(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=0.0, start_y_mm=0.0,
            end_x_mm=10.0, end_y_mm=0.0,
            mid_x_mm=5.0, mid_y_mm=3.0,
            layer="F.Cu", net_name="MIDDED",
        )
        assert out["success"] is True
        assert out["mid"] == {"x_mm": 5.0, "y_mm": 3.0}
        # Circumradius of (0,0)-(5,3)-(10,0) is sqrt(25 + (4/6 + 9/6 − ...)²)
        # → roughly 4.83 mm (specifically (5² + (something)²) = computed)
        assert out["radius_mm"] > 0.0

    def test_collinear_mid_returns_zero_radius(self, mcp_server, pcb_path):
        # Collinear points → circumradius "0" (sentinel)
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=0.0, start_y_mm=0.0,
            end_x_mm=10.0, end_y_mm=0.0,
            mid_x_mm=5.0, mid_y_mm=0.0,         # collinear
            layer="F.Cu", net_name="X",
        )
        assert out["success"] is True   # arc inserted anyway
        assert out["radius_mm"] == 0.0  # diagnostic sentinel


# ---------------------------------------------------------------------------
# Mode validation
# ---------------------------------------------------------------------------


class TestModeValidation:
    def test_both_center_and_mid_rejected(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=0.0, start_y_mm=0.0,
            end_x_mm=10.0, end_y_mm=0.0,
            center_x_mm=5.0, center_y_mm=5.0,
            mid_x_mm=5.0, mid_y_mm=3.0,
            layer="F.Cu", net_name="X",
        )
        assert out["success"] is False
        assert "exactly one" in out["error"].lower()

    def test_neither_center_nor_mid_rejected(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=0.0, start_y_mm=0.0,
            end_x_mm=10.0, end_y_mm=0.0,
            layer="F.Cu", net_name="X",
        )
        assert out["success"] is False
        assert "exactly one" in out["error"].lower()

    def test_degenerate_arc_rejected(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=1.0, start_y_mm=1.0,
            end_x_mm=1.0, end_y_mm=1.0,         # coincident
            center_x_mm=0.0, center_y_mm=0.0,
            layer="F.Cu", net_name="X",
        )
        assert out["success"] is False
        assert "degenerate" in out["error"].lower()


# ---------------------------------------------------------------------------
# Net handling
# ---------------------------------------------------------------------------


class TestNetHandling:
    def test_new_net_added_to_table(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=10.0, start_y_mm=0.0,
            end_x_mm=0.0, end_y_mm=10.0,
            center_x_mm=0.0, center_y_mm=0.0,
            layer="In2.Cu", net_name="BRAND_NEW",
        )
        assert out["success"] is True
        assert out["net_id"] == 1
        text = _read(pcb_path)
        assert re.search(r'\(net\s+1\s+"BRAND_NEW"\)', text)

    def test_existing_net_reused(self, mcp_server, pcb_path):
        # Seed a net manually
        seeded = MIN_PCB.replace(
            '(net 0 "")', '(net 0 "")\n\t(net 7 "FOO")',
        )
        with open(pcb_path, "w", encoding="utf-8") as fh:
            fh.write(seeded)
        out = _call(
            mcp_server, "add_arc_to_pcb",
            pcb_path=pcb_path,
            start_x_mm=10.0, start_y_mm=0.0,
            end_x_mm=0.0, end_y_mm=10.0,
            center_x_mm=0.0, center_y_mm=0.0,
            layer="In2.Cu", net_name="FOO",
        )
        assert out["success"] is True
        assert out["net_id"] == 7
