# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the generic ``pcb_batch`` tool — chain multiple file-edit
operations in one open/write cycle."""

from __future__ import annotations

import asyncio
import textwrap

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt
from kicad_mcp.tools import pcb_geometry_tools as pgt


PCB_FIXTURE = textwrap.dedent(
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
    \t(footprint "Test:R_0402"
    \t\t(layer "F.Cu")
    \t\t(uuid "00000000-0000-0000-0000-000000000001")
    \t\t(at 10.0 10.0 0.0)
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
    \t(footprint "Test:R_0402"
    \t\t(layer "F.Cu")
    \t\t(uuid "00000000-0000-0000-0000-000000000002")
    \t\t(at 20.0 20.0 0.0)
    \t\t(property "Reference" "R2"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.SilkS")
    \t\t)
    \t\t(property "Value" "1k"
    \t\t\t(at 0 0 0)
    \t\t\t(layer "F.Fab")
    \t\t)
    \t\t(pad "1" smd rect
    \t\t\t(at -0.5 0)
    \t\t\t(size 0.5 0.6)
    \t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
    \t\t)
    \t)
    \t(segment (start 0 0) (end 5 0) (width 0.25) (layer "F.Cu") (net 0) (uuid "00000000-0000-0000-0000-aaaaaaaaaaaa"))
    )
    """
)


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "batch.kicad_pcb"
    p.write_text(PCB_FIXTURE, encoding="utf-8")
    return str(p)


@pytest.fixture
def mcp_server():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    ppt.register_pcb_patch_tools(mcp)
    pgt.register_pcb_geometry_tools(mcp)
    return mcp


def _call(mcp, name, **kwargs):
    async def _do():
        result = await mcp.call_tool(name, kwargs)
        if isinstance(result, tuple):
            return result[1] if len(result) > 1 else result[0]
        return result
    return asyncio.run(_do())


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestBatch:
    def test_chains_place_and_arc(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "pcb_batch",
            pcb_path=pcb_path,
            operations=[
                {"tool": "place_at_pivot",
                 "args": {"ref": "R1",
                          "target_x_mm": 50.0, "target_y_mm": 60.0}},
                {"tool": "add_arc_to_pcb",
                 "args": {"start_x_mm": 0.0, "start_y_mm": -10.0,
                          "end_x_mm": 10.0, "end_y_mm": 0.0,
                          "center_x_mm": 0.0, "center_y_mm": 0.0,
                          "layer": "In2.Cu", "net_name": "FOO",
                          "width_mm": 0.3}},
            ],
        )
        assert out["success"] is True
        assert out["count"] == 2
        text = _read(pcb_path)
        # R1 moved
        assert "(at 50.000000 60.000000 0)" in text
        # Arc inserted
        assert "(arc" in text
        # Net FOO inserted into net table
        assert '"FOO"' in text

    def test_dry_run_does_not_write(self, mcp_server, pcb_path):
        before = _read(pcb_path)
        out = _call(
            mcp_server, "pcb_batch",
            pcb_path=pcb_path,
            operations=[
                {"tool": "place_at_pivot",
                 "args": {"ref": "R1",
                          "target_x_mm": 100.0, "target_y_mm": 200.0}},
            ],
            dry_run=True,
        )
        assert out["success"] is True
        assert out["dry_run"] is True
        assert _read(pcb_path) == before


# ---------------------------------------------------------------------------
# Error semantics
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_halt_on_first_failure(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "pcb_batch",
            pcb_path=pcb_path,
            operations=[
                {"tool": "place_at_pivot",
                 "args": {"ref": "R1",
                          "target_x_mm": 50.0, "target_y_mm": 60.0}},
                # Unknown ref → individual tool returns success=False
                {"tool": "place_at_pivot",
                 "args": {"ref": "NOPE",
                          "target_x_mm": 1.0, "target_y_mm": 2.0}},
                {"tool": "place_at_pivot",
                 "args": {"ref": "R2",
                          "target_x_mm": 30.0, "target_y_mm": 40.0}},
            ],
            halt_on_error=True,
        )
        assert out["success"] is False
        assert out["count"] == 2     # third op skipped
        assert out["results"][0]["success"] is True
        assert out["results"][1]["success"] is False

    def test_continue_on_error(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "pcb_batch",
            pcb_path=pcb_path,
            operations=[
                {"tool": "place_at_pivot",
                 "args": {"ref": "R1",
                          "target_x_mm": 50.0, "target_y_mm": 60.0}},
                {"tool": "place_at_pivot",
                 "args": {"ref": "NOPE",
                          "target_x_mm": 1.0, "target_y_mm": 2.0}},
                {"tool": "place_at_pivot",
                 "args": {"ref": "R2",
                          "target_x_mm": 30.0, "target_y_mm": 40.0}},
            ],
            halt_on_error=False,
        )
        # Overall success=False because one op failed, but all three ran.
        assert out["success"] is False
        assert out["count"] == 3

    def test_unknown_tool_rejected_up_front(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "pcb_batch",
            pcb_path=pcb_path,
            operations=[
                {"tool": "place_at_pivot",
                 "args": {"ref": "R1",
                          "target_x_mm": 50.0, "target_y_mm": 60.0}},
                {"tool": "nope", "args": {}},
            ],
        )
        assert out["success"] is False
        assert "nope" in out["unknown_tools"]
        # File was not modified.

    def test_argument_mismatch_caught(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "pcb_batch",
            pcb_path=pcb_path,
            operations=[
                {"tool": "place_at_pivot",
                 "args": {"ref": "R1"}},   # missing target_x_mm / target_y_mm
            ],
        )
        assert out["success"] is False
        assert "argument mismatch" in out["results"][0]["error"].lower()

    def test_empty_operations_rejected(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "pcb_batch",
            pcb_path=pcb_path,
            operations=[],
        )
        assert out["success"] is False
        assert "non-empty list" in out["error"]

    def test_missing_pcb(self, mcp_server, tmp_path):
        out = _call(
            mcp_server, "pcb_batch",
            pcb_path=str(tmp_path / "nope.kicad_pcb"),
            operations=[
                {"tool": "place_at_pivot",
                 "args": {"ref": "R1",
                          "target_x_mm": 1.0, "target_y_mm": 2.0}},
            ],
        )
        assert out["success"] is False
        assert "not found" in out["error"].lower()
