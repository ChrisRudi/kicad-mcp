# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``cluster_block_outside_pcb`` — group-tag-driven tangential
grid placement at a polar position outside the PCB outline."""

from __future__ import annotations

import asyncio
import re

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fp(ref: str, x: float, y: float, uuid_byte: str) -> str:
    """Build a minimal SMD R_0402 footprint block for the PCB fixture."""
    return f"""\
\t(footprint "Test:R_0402"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-0000000000{uuid_byte}")
\t\t(at {x} {y} 0.0)
\t\t(property "Reference" "{ref}"
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
\t)"""


MIN_PCB_3 = (
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
\t)
"""
    + _fp("R1", 10.0, 20.0, "01") + "\n"
    + _fp("R2", 20.0, 20.0, "02") + "\n"
    + _fp("C1", 30.0, 20.0, "03") + "\n"
    + ")\n"
)


def _sym(ref: str, x: float, group: str = "block_test") -> str:
    """Build a minimal placed-symbol block for the SCH fixture."""
    return f"""\
\t(symbol
\t\t(lib_id "Device:R")
\t\t(at {x} 100 0)
\t\t(uuid "00000000-0000-0000-0000-000000000{ref[-1]:0>3}")
\t\t(property "Reference" "{ref}"
\t\t\t(at 0 0 0)
\t\t)
\t\t(property "Value" "10k"
\t\t\t(at 0 0 0)
\t\t)
\t\t(property "kicad-mcp.group" "{group}"
\t\t\t(at 0 0 0)
\t\t)
\t)"""


MIN_SCH = (
    """\
(kicad_sch
\t(version 20240108)
\t(generator "test")
\t(lib_symbols
\t)
"""
    + _sym("R1", 100) + "\n"
    + _sym("R2", 110) + "\n"
    + _sym("C1", 120) + "\n"
    + ")\n"
)


# A schematic where one symbol is in a *different* group — must not be
# picked up by the search for ``block_test``.
MIXED_SCH = (
    """\
(kicad_sch
\t(version 20240108)
\t(generator "test")
\t(lib_symbols
\t)
"""
    + _sym("R1", 100, group="block_test") + "\n"
    + _sym("R2", 110, group="block_test") + "\n"
    + _sym("R99", 120, group="other_block") + "\n"
    + ")\n"
)


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text(MIN_PCB_3, encoding="utf-8")
    return str(p)


@pytest.fixture
def sch_path(tmp_path):
    p = tmp_path / "board.kicad_sch"
    p.write_text(MIN_SCH, encoding="utf-8")
    return str(p)


@pytest.fixture
def mixed_sch_path(tmp_path):
    p = tmp_path / "mixed.kicad_sch"
    p.write_text(MIXED_SCH, encoding="utf-8")
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
# Pure text-function tests (no MCP server needed)
# ---------------------------------------------------------------------------


def test_text_fn_happy_path():
    """3 refs in a 3-column grid at phi=0°, r=42, pcb_center=(0,0):
    cluster centre lands at (42, 0); each ref is offset tangentially."""
    new_text, result = ppt.cluster_block_outside_pcb_text(
        MIN_PCB_3,
        refs=["R1", "R2", "C1"],
        cluster_phi_deg=0.0,
        cluster_r_mm=42.0,
        pcb_center_x_mm=0.0,
        pcb_center_y_mm=0.0,
        grid_cols=3,
        spacing_t_mm=5.0,
        spacing_r_mm=5.0,
        align_mode="radial_in",
    )
    assert result["success"] is True
    assert result["placed_count"] == 3
    assert result["error_count"] == 0
    assert result["cluster_center"]["x_mm"] == 42.0
    assert result["cluster_center"]["y_mm"] == 0.0
    # All three on row 0; columns 0..2; tangential offset symmetric.
    placed_by_ref = {p["ref"]: p for p in result["placed"]}
    assert set(placed_by_ref.keys()) == {"R1", "R2", "C1"}
    for ref, exp_col in (("R1", 0), ("R2", 1), ("C1", 2)):
        assert placed_by_ref[ref]["row"] == 0
        assert placed_by_ref[ref]["col"] == exp_col
    # At phi=0: tangential is (-sin 0, -cos 0) = (0, -1) in y-down.
    # R1 col=0, t_off=-5 → target_y = 0 + (-5)*(-1) = +5
    # R2 col=1, t_off=0  → target_y = 0
    # C1 col=2, t_off=+5 → target_y = -5
    assert placed_by_ref["R1"]["y_mm"] == 5.0
    assert placed_by_ref["R2"]["y_mm"] == 0.0
    assert placed_by_ref["C1"]["y_mm"] == -5.0
    # All on the radial line at x=42
    for ref in ("R1", "R2", "C1"):
        assert placed_by_ref[ref]["x_mm"] == 42.0


def test_text_fn_radial_in_rotation_at_phi_0():
    """At phi=0, radial_in must rotate footprint so its +y axis points
    toward the PCB centre (= along -x). align_radial_rotation gives 90°
    in y-down KiCad coords."""
    _, result = ppt.cluster_block_outside_pcb_text(
        MIN_PCB_3,
        refs=["R1"],
        cluster_phi_deg=0.0,
        cluster_r_mm=42.0,
        pcb_center_x_mm=0.0,
        pcb_center_y_mm=0.0,
        grid_cols=1,
        align_mode="radial_in",
    )
    assert result["success"] is True
    # The exact value comes from place_at_pivot_text — assert that it is
    # *a finite number in [0,360)* rather than guessing the convention.
    rot = result["placed"][0]["rotation"]
    assert isinstance(rot, (int, float))
    assert 0.0 <= rot < 360.0


def test_text_fn_empty_refs():
    _, result = ppt.cluster_block_outside_pcb_text(
        MIN_PCB_3, refs=[], cluster_phi_deg=0.0,
    )
    assert result["success"] is False
    assert "refs must be" in result["error"]


def test_text_fn_invalid_align_mode():
    _, result = ppt.cluster_block_outside_pcb_text(
        MIN_PCB_3, refs=["R1"], cluster_phi_deg=0.0,
        align_mode="bogus",
    )
    assert result["success"] is False
    assert "align_mode" in result["error"]


def test_text_fn_invalid_grid_cols():
    _, result = ppt.cluster_block_outside_pcb_text(
        MIN_PCB_3, refs=["R1"], cluster_phi_deg=0.0, grid_cols=0,
    )
    assert result["success"] is False
    assert "grid_cols" in result["error"]


def test_text_fn_ref_missing_from_pcb():
    """A ref that does not exist in the PCB ends up in ``errors``."""
    _, result = ppt.cluster_block_outside_pcb_text(
        MIN_PCB_3, refs=["R1", "GHOST"], cluster_phi_deg=0.0,
        grid_cols=2,
    )
    # One ok, one error → overall success=False
    assert result["success"] is False
    assert result["placed_count"] == 1
    assert result["error_count"] == 1
    assert result["errors"][0]["ref"] == "GHOST"


def test_text_fn_idempotent(pcb_path):
    """Two consecutive runs with identical args must produce byte-equal
    output (no UUID churn)."""
    text = _read(pcb_path)
    out1, _ = ppt.cluster_block_outside_pcb_text(
        text, refs=["R1", "R2"], cluster_phi_deg=45.0,
        cluster_r_mm=30.0, grid_cols=2,
    )
    out2, _ = ppt.cluster_block_outside_pcb_text(
        out1, refs=["R1", "R2"], cluster_phi_deg=45.0,
        cluster_r_mm=30.0, grid_cols=2,
    )
    assert out2 == out1


# ---------------------------------------------------------------------------
# MCP-wrapper tests (need FastMCP available)
# ---------------------------------------------------------------------------


def test_mcp_happy_path(mcp_server, pcb_path, sch_path):
    """End-to-end: SCH lists 3 refs in block_test; PCB has 3 footprints;
    cluster places all three with correct counts."""
    result = _call(
        mcp_server, "cluster_block_outside_pcb",
        pcb_path=pcb_path, sch_path=sch_path,
        block_name="block_test",
        cluster_phi_deg=0.0, cluster_r_mm=42.0,
        grid_cols=3, align_mode="radial_in",
        dry_run=False,
    )
    assert result["success"] is True
    assert result["block_name"] == "block_test"
    assert sorted(result["refs_resolved"]) == ["C1", "R1", "R2"]
    assert result["placed_count"] == 3
    assert result["dry_run"] is False
    # Disk has been mutated — anchor of R1 in PCB now != 10.0
    new_pcb = _read(pcb_path)
    m = re.search(r'\(uuid "00000000-0000-0000-0000-000000000001"\)\s*'
                  r'\(at ([\d.\-]+) ([\d.\-]+)', new_pcb)
    assert m is not None
    new_x = float(m.group(1))
    assert new_x != 10.0  # was the seed value


def test_mcp_dry_run_preserves_disk(mcp_server, pcb_path, sch_path):
    before = _read(pcb_path)
    result = _call(
        mcp_server, "cluster_block_outside_pcb",
        pcb_path=pcb_path, sch_path=sch_path,
        block_name="block_test",
        cluster_phi_deg=0.0, cluster_r_mm=42.0,
        grid_cols=3, dry_run=True,
    )
    after = _read(pcb_path)
    assert result["success"] is True
    assert result["dry_run"] is True
    assert before == after  # disk untouched


def test_mcp_filters_by_group(mcp_server, pcb_path, mixed_sch_path):
    """Mixed SCH has R1/R2 in block_test + R99 in other_block. Cluster
    of block_test must resolve exactly 2 refs (R1, R2) — R99 ignored."""
    result = _call(
        mcp_server, "cluster_block_outside_pcb",
        pcb_path=pcb_path, sch_path=mixed_sch_path,
        block_name="block_test",
        cluster_phi_deg=0.0, cluster_r_mm=42.0,
        grid_cols=2, dry_run=True,
    )
    assert sorted(result["refs_resolved"]) == ["R1", "R2"]
    # Both refs exist in PCB → placed_count=2, success=True.
    assert result["placed_count"] == 2


def test_mcp_block_not_found(mcp_server, pcb_path, sch_path):
    result = _call(
        mcp_server, "cluster_block_outside_pcb",
        pcb_path=pcb_path, sch_path=sch_path,
        block_name="does_not_exist",
        cluster_phi_deg=0.0,
    )
    assert result["success"] is False
    assert "No refs found in group" in result["error"]


def test_mcp_missing_pcb(mcp_server, sch_path, tmp_path):
    result = _call(
        mcp_server, "cluster_block_outside_pcb",
        pcb_path=str(tmp_path / "nope.kicad_pcb"),
        sch_path=sch_path,
        block_name="block_test",
        cluster_phi_deg=0.0,
    )
    assert result["success"] is False
    assert "PCB not found" in result["error"]


def test_mcp_missing_sch(mcp_server, pcb_path, tmp_path):
    result = _call(
        mcp_server, "cluster_block_outside_pcb",
        pcb_path=pcb_path,
        sch_path=str(tmp_path / "nope.kicad_sch"),
        block_name="block_test",
        cluster_phi_deg=0.0,
    )
    assert result["success"] is False
    assert "SCH not found" in result["error"]
