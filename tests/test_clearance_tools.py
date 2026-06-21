# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the shared clearance engine (``clearance_tools`` +
``clearance_worker``) and its wiring into the copper-mutating tools.

The pure spec builders and the graceful-degradation path run everywhere
(no pcbnew needed — the case in CI). The actual ``SHAPE.Collide`` geometry
is exercised under ``@_needs_pcbnew`` only, mirroring ``test_via_promote``.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from kicad_mcp.tools import clearance_worker as worker
from kicad_mcp.tools.clearance_tools import (
    arc_specs,
    attach_clearance,
    check_clearance_impl,
    register_clearance_tools,
    seg_spec,
    via_spec,
)

_HAS_PCBNEW = importlib.util.find_spec("pcbnew") is not None
_needs_pcbnew = pytest.mark.skipif(
    not _HAS_PCBNEW, reason="pcbnew not importable (run under KiCad Python)")


# ---------------------------------------------------------------------------
# Pure spec builders (no pcbnew)
# ---------------------------------------------------------------------------


class TestSpecBuilders:
    def test_via_spec_fields(self):
        s = via_spec(10, 5, "GND", ["F.Cu", "B.Cu"], 0.6)
        assert s == {"kind": "via", "x_mm": 10.0, "y_mm": 5.0, "net": "GND",
                     "layers": ["F.Cu", "B.Cu"], "diameter_mm": 0.6}

    def test_via_spec_defaults_layer_pair(self):
        s = via_spec(1, 2, "", None, 0.4)
        assert s["layers"] == ["F.Cu", "B.Cu"]
        assert s["net"] == ""

    def test_seg_spec_fields(self):
        s = seg_spec(0, 0, 1, 1, "VCC", "In1.Cu", 0.25)
        assert s["kind"] == "seg"
        assert (s["x1_mm"], s["y1_mm"], s["x2_mm"], s["y2_mm"]) == (0.0, 0.0, 1.0, 1.0)
        assert s["layer"] == "In1.Cu" and s["width_mm"] == 0.25

    def test_arc_specs_two_chords(self):
        specs = arc_specs((0, 0), (1, 1), (2, 0), "N", "F.Cu", 0.3)
        assert len(specs) == 2
        assert all(s["kind"] == "seg" for s in specs)
        assert specs[0]["x2_mm"] == 1.0 and specs[1]["x1_mm"] == 1.0


# ---------------------------------------------------------------------------
# attach_clearance — total contract (never raises, never flips success)
# ---------------------------------------------------------------------------


class TestAttachClearance:
    def test_disabled_records_reason(self):
        res = {"success": True}
        out = attach_clearance(res, "/whatever.kicad_pcb", [], enabled=False)
        assert out is res  # mutates in place
        assert out["success"] is True
        assert out["clearance"] == {"checked": False, "reason": "disabled"}

    def test_missing_file_does_not_flip_success(self):
        res = {"success": True, "count": 3}
        out = attach_clearance(res, "/nope/missing.kicad_pcb",
                               [via_spec(1, 1, "N", None, 0.6)], enabled=True)
        assert out["success"] is True          # mutation result untouched
        assert out["clearance"]["checked"] is False
        assert "reason" in out["clearance"]

    @pytest.mark.skipif(_HAS_PCBNEW, reason="degradation path only without pcbnew")
    def test_no_pcbnew_is_advisory_only(self):
        res = {"success": True}
        out = attach_clearance(res, "/nope/missing.kicad_pcb", None, enabled=True)
        assert out["success"] is True
        assert out["clearance"]["checked"] is False


# ---------------------------------------------------------------------------
# check_clearance_impl — validation (runs without pcbnew)
# ---------------------------------------------------------------------------


class TestImplValidation:
    def test_missing_file_structured_error(self):
        out = check_clearance_impl("/nope/missing.kicad_pcb")
        assert out["success"] is False
        assert "not found" in out["error"].lower()

    @pytest.mark.skipif(_HAS_PCBNEW, reason="no-pcbnew branch only")
    def test_no_pcbnew_reports_checked_false(self, tmp_path):
        p = tmp_path / "b.kicad_pcb"
        p.write_text("(kicad_pcb)\n", encoding="utf-8")
        out = check_clearance_impl(str(p))
        assert out["success"] is False
        assert out["checked"] is False


# ---------------------------------------------------------------------------
# MCP tool surface — missing path + bad JSON (no pcbnew needed)
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_with_clearance():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    register_clearance_tools(mcp)
    return mcp


def _call(mcp, name, **kwargs):
    async def _do():
        result = await mcp.call_tool(name, kwargs)
        if isinstance(result, tuple):
            return result[1] if len(result) > 1 else result[0]
        return result
    return asyncio.run(_do())


class TestCheckClearanceTool:
    def test_missing_path(self, mcp_with_clearance):
        out = _call(mcp_with_clearance, "check_clearance",
                    pcb_path="/nope/missing.kicad_pcb")
        assert out["success"] is False
        assert "not found" in out["error"].lower()

    def test_bad_json_items(self, mcp_with_clearance, tmp_path):
        p = tmp_path / "b.kicad_pcb"
        p.write_text("(kicad_pcb)\n", encoding="utf-8")
        out = _call(mcp_with_clearance, "check_clearance",
                    pcb_path=str(p), items="{not json")
        assert out["success"] is False
        assert "json" in out["error"].lower()


# ---------------------------------------------------------------------------
# Geometry-tool wiring — clearance echo present, mutation still succeeds
# (no-pcbnew env: echo is {checked: False}; the edit itself must still work)
# ---------------------------------------------------------------------------


_TINY_PCB = (
    '(kicad_pcb (version 20240108) (generator "test")\n'
    ' (general (thickness 1.6)) (paper "A4")\n'
    ' (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))\n'
    ' (net 0 "")\n'
    ')\n'
)


@pytest.fixture
def geom_mcp():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    from kicad_mcp.tools import pcb_geometry_tools as pgt
    mcp = fastmcp.FastMCP("test")
    pgt.register_pcb_geometry_tools(mcp)
    return mcp


class TestGeometryWiring:
    def test_add_via_attaches_clearance(self, geom_mcp, tmp_path):
        p = tmp_path / "g.kicad_pcb"
        p.write_text(_TINY_PCB, encoding="utf-8")
        out = _call(geom_mcp, "add_via_to_pcb", pcb_path=str(p),
                    x_mm=10.0, y_mm=10.0, net_name="GND")
        assert out["success"] is True
        assert "clearance" in out
        assert "checked" in out["clearance"]
        # the via was actually written regardless of the clearance backend
        assert '(via' in p.read_text(encoding="utf-8")

    def test_dry_run_skips_clearance(self, geom_mcp, tmp_path):
        p = tmp_path / "g.kicad_pcb"
        p.write_text(_TINY_PCB, encoding="utf-8")
        out = _call(geom_mcp, "add_via_to_pcb", pcb_path=str(p),
                    x_mm=10.0, y_mm=10.0, net_name="GND", dry_run=True)
        assert out["success"] is True
        assert out["clearance"]["checked"] is False
        assert '(via' not in p.read_text(encoding="utf-8")

    def test_check_clearance_false_skips(self, geom_mcp, tmp_path):
        p = tmp_path / "g.kicad_pcb"
        p.write_text(_TINY_PCB, encoding="utf-8")
        out = _call(geom_mcp, "add_via_to_pcb", pcb_path=str(p),
                    x_mm=10.0, y_mm=10.0, net_name="GND", check_clearance=False)
        assert out["success"] is True
        assert out["clearance"] == {"checked": False, "reason": "disabled"}


# ---------------------------------------------------------------------------
# Worker collision geometry (needs pcbnew)
# ---------------------------------------------------------------------------


def _board_pad_netB():
    """2-layer board with one F.Cu SMD pad on NET_B at (150,105)."""
    return (
        '(kicad_pcb (version 20240108) (generator "pcbnew") '
        '(generator_version "9.0")\n'
        ' (general (thickness 1.6)) (paper "A4")\n'
        ' (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))\n'
        ' (net 0 "") (net 1 "NET_A") (net 2 "NET_B")\n'
        ' (footprint "lib:pad" (layer "F.Cu") (at 150 105) '
        '(uuid "11111111-1111-1111-1111-111111111111")\n'
        '   (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
        '(net 2 "NET_B")))\n'
        ')\n'
    )


@pytest.fixture
def pad_board(tmp_path):
    p = tmp_path / "clr.kicad_pcb"
    p.write_text(_board_pad_netB(), encoding="utf-8")
    return str(p)


@_needs_pcbnew
class TestWorkerTargeted:
    def test_via_over_foreign_pad_is_violation(self, pad_board):
        # NET_A via dropped on top of the NET_B pad → short.
        r = worker.run(pad_board, [via_spec(150, 105, "NET_A",
                                            ["F.Cu", "B.Cu"], 0.6)], 0.2)
        assert r["success"] is True and r["mode"] == "targeted"
        assert r["ok"] is False
        assert r["violation_count"] >= 1
        assert r["violations"][0]["blocker_net"] == "NET_B"

    def test_via_in_open_space_is_clean(self, pad_board):
        r = worker.run(pad_board, [via_spec(170, 105, "NET_A",
                                            ["F.Cu", "B.Cu"], 0.6)], 0.2)
        assert r["success"] is True and r["ok"] is True
        assert r["violation_count"] == 0

    def test_same_net_via_is_not_a_short(self, pad_board):
        # A NET_B via on the NET_B pad is the same net → not a violation.
        r = worker.run(pad_board, [via_spec(150, 105, "NET_B",
                                            ["F.Cu", "B.Cu"], 0.6)], 0.2)
        assert r["success"] is True and r["ok"] is True

    def test_track_over_foreign_pad_is_violation(self, pad_board):
        # A NET_A track crossing the NET_B pad → short. Exercises the
        # SHAPE_SEGMENT subject + Collide(segment, clr) path.
        r = worker.run(pad_board, [seg_spec(149, 105, 151, 105,
                                            "NET_A", "F.Cu", 0.25)], 0.2)
        assert r["success"] is True and r["ok"] is False
        assert r["violations"][0]["item_kind"] == "seg"
        assert r["violations"][0]["blocker_net"] == "NET_B"


def _board_short():
    """Board where a NET_A track physically crosses the NET_B pad → a real
    different-net short for the board-wide scan to find."""
    return (
        '(kicad_pcb (version 20240108) (generator "pcbnew") '
        '(generator_version "9.0")\n'
        ' (general (thickness 1.6)) (paper "A4")\n'
        ' (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))\n'
        ' (net 0 "") (net 1 "NET_A") (net 2 "NET_B")\n'
        ' (footprint "lib:pad" (layer "F.Cu") (at 150 105) '
        '(uuid "11111111-1111-1111-1111-111111111111")\n'
        '   (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
        '(net 2 "NET_B")))\n'
        ' (segment (start 149 105) (end 151 105) (width 0.25) '
        '(layer "F.Cu") (net 1) '
        '(uuid "22222222-2222-2222-2222-222222222222"))\n'
        ')\n'
    )


@pytest.fixture
def short_board(tmp_path):
    p = tmp_path / "short.kicad_pcb"
    p.write_text(_board_short(), encoding="utf-8")
    return str(p)


@_needs_pcbnew
class TestWorkerBoardWide:
    def test_clean_board_has_no_violations(self, pad_board):
        r = worker.run(pad_board, None, 0.2)
        assert r["success"] is True and r["mode"] == "board"
        assert r["ok"] is True and r["violation_count"] == 0

    def test_detects_different_net_short(self, short_board):
        r = worker.run(short_board, None, 0.2)
        assert r["success"] is True and r["mode"] == "board"
        assert r["ok"] is False and r["violation_count"] >= 1
        v = r["violations"][0]
        assert {v["a_net"], v["b_net"]} == {"NET_A", "NET_B"}


_U_VIA = "33333333-3333-3333-3333-333333333333"


def _board_via_uuid():
    """NET_A through-via (known uuid) sitting on the NET_B pad — the
    ``via_uuid`` path (used by via_retype / via_resize) must resolve the via,
    read ITS net (NET_A) and flag the NET_B pad."""
    return (
        '(kicad_pcb (version 20240108) (generator "pcbnew") '
        '(generator_version "9.0")\n'
        ' (general (thickness 1.6)) (paper "A4")\n'
        ' (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))\n'
        ' (net 0 "") (net 1 "NET_A") (net 2 "NET_B")\n'
        ' (footprint "lib:pad" (layer "F.Cu") (at 150 105) '
        '(uuid "11111111-1111-1111-1111-111111111111")\n'
        '   (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
        '(net 2 "NET_B")))\n'
        f' (via (at 150 105) (size 0.6) (drill 0.3) '
        f'(layers "F.Cu" "B.Cu") (net 1) (uuid "{_U_VIA}"))\n'
        ')\n'
    )


@pytest.fixture
def viauuid_board(tmp_path):
    p = tmp_path / "viauuid.kicad_pcb"
    p.write_text(_board_via_uuid(), encoding="utf-8")
    return str(p)


@_needs_pcbnew
class TestWorkerViaUuid:
    def test_via_uuid_resolves_and_collides(self, viauuid_board):
        r = worker.run(viauuid_board, [{"kind": "via_uuid", "uuid": _U_VIA}], 0.2)
        assert r["success"] is True and r["ok"] is False
        assert r["violations"][0]["blocker_net"] == "NET_B"

    def test_unknown_via_uuid_is_clean(self, viauuid_board):
        # A uuid not on the board resolves to nothing → no subject, no error.
        r = worker.run(viauuid_board, [{"kind": "via_uuid", "uuid": "deadbeef"}], 0.2)
        assert r["success"] is True and r["ok"] is True
        assert r["violation_count"] == 0
