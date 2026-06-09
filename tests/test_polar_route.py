# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the ``polar_grid`` ``route`` op (pin-to-pin polar routing)."""

from __future__ import annotations

import asyncio
import re

import pytest

from kicad_mcp.tools import polar_grid_tools as pgt

# Default reference polar config: centre (148.5, 105), arc=In1.Cu, radial=In2.Cu.
CX, CY = 148.5, 105.0


def _fp(ref, x, y, pads, layer="F.Cu"):
    """Build a minimal footprint block. ``pads`` = list of
    ``(name, rel_x, rel_y, net_id, net_name, layers_str)``."""
    pad_txt = ""
    for name, rx, ry, nid, nname, players in pads:
        pad_txt += (
            f'\t\t(pad "{name}" smd roundrect (at {rx} {ry}) '
            f'(size 0.5 0.5) (layers {players}) '
            f'(net {nid} "{nname}"))\n'
        )
    return (
        f'\t(footprint "lib:{ref}_fp"\n'
        f'\t\t(layer "{layer}")\n'
        f'\t\t(at {x} {y} 0)\n'
        f'\t\t(property "Reference" "{ref}" (at 0 0 0))\n'
        f'{pad_txt}'
        f'\t)\n'
    )


def _make_pcb():
    smd = '"F.Cu" "F.Mask" "F.Paste"'
    tht = '"*.Cu" "*.Mask"'
    # U1 pad on the +X axis at r=20 (on ring r_mm=20); U2 at -X, r=20.
    u1 = _fp("U1", CX + 20.0, CY, [("1", 0, 0, 1, "NET_X", smd)])
    u2 = _fp("U2", CX - 20.0, CY, [("1", 0, 0, 1, "NET_X", smd)])
    # U3 SMD pad off-ring at r=25 on the +Y axis (different angle than U1
    # so the arc is non-degenerate); needs a radial stub to reach r=20.
    u3 = _fp("U3", CX, CY + 25.0, [("1", 0, 0, 1, "NET_X", smd)])
    # C9 THT coil pad (*.Cu) off-ring at r=25 on the -Y axis — stub but
    # NO pad via.
    c9 = _fp("C9", CX, CY - 25.0, [("1", 0, 0, 1, "NET_X", tht)])
    # U4 on a different net, for the mismatch test.
    u4 = _fp("U4", CX - 25.0, CY, [("1", 0, 0, 2, "NET_Y", smd)])
    header = (
        "(kicad_pcb\n"
        "\t(version 20240108)\n"
        '\t(generator "test")\n'
        "\t(layers\n"
        '\t\t(0 "F.Cu" signal)\n'
        '\t\t(31 "B.Cu" signal)\n'
        '\t\t(1 "In1.Cu" signal)\n'
        '\t\t(2 "In2.Cu" signal)\n'
        "\t)\n"
        '\t(net 0 "")\n'
        '\t(net 1 "NET_X")\n'
        '\t(net 2 "NET_Y")\n'
    )
    return header + u1 + u2 + u3 + c9 + u4 + ")\n"


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "polar.kicad_pcb"
    p.write_text(_make_pcb(), encoding="utf-8")
    return str(p)


@pytest.fixture
def mcp_server():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    pgt.register_polar_grid_tools(mcp)
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
# Pad-on-ring → arc + 2 vias, no stub
# ---------------------------------------------------------------------------


class TestOnRing:
    def test_two_smd_pads_on_ring(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "polar_grid", op="route", pcb_path=pcb_path,
            from_ref_pad="U1.1", to_ref_pad="U2.1", r_mm=20.0,
        )
        assert out["success"] is True
        assert out["wrote"] is True
        assert out["arcs"] == 1
        assert out["segments"] == 0          # both pads sit on the ring
        assert out["vias"] == 2              # one F.Cu→In1 drop per pad
        text = _read(pcb_path)
        assert '(arc' in text and '(layer "In1.Cu")' in text
        # Arc is net-bound to NET_X. Track/arc/via tags use the numeric
        # index form ``(net 1)`` (the "name" form is pad-only); net id 1
        # == NET_X in this PCB's net table.
        arc = re.search(r'\(arc[\s\S]*?\)\n\t\)', text)
        assert arc and re.search(r'\(net 1\)', arc.group(0))

    def test_dry_run_does_not_write(self, mcp_server, pcb_path):
        before = _read(pcb_path)
        out = _call(
            mcp_server, "polar_grid", op="route", pcb_path=pcb_path,
            from_ref_pad="U1.1", to_ref_pad="U2.1", r_mm=20.0, dry_run=True,
        )
        assert out["success"] is True
        assert out["wrote"] is False
        assert out["arcs"] == 1 and out["vias"] == 2
        assert _read(pcb_path) == before    # untouched


# ---------------------------------------------------------------------------
# Off-ring pads → radial stubs; THT pad needs no pad-via
# ---------------------------------------------------------------------------


class TestStubs:
    def test_smd_offring_gets_stub_and_padvia(self, mcp_server, pcb_path):
        # U3 at r=25 → stub on In2 + F.Cu→In2 pad via + In2→In1 ring via.
        # U1 at r=20 (on ring) → one F.Cu→In1 via.
        out = _call(
            mcp_server, "polar_grid", op="route", pcb_path=pcb_path,
            from_ref_pad="U3.1", to_ref_pad="U1.1", r_mm=20.0,
        )
        assert out["success"] is True
        assert out["segments"] == 1          # one stub (U3 side)
        # U3: pad via + ring via = 2 ; U1: 1 = 3 total
        assert out["vias"] == 3
        text = _read(pcb_path)
        assert '(layer "In2.Cu")' in text    # the radial stub

    def test_tht_pad_needs_no_pad_via(self, mcp_server, pcb_path):
        # C9 is a *.Cu THT pad off-ring → stub but NO pad via (already
        # reaches In2). U1 on-ring → 1 via. So vias = 1 (U1) + 1 (ring via
        # on C9 side) = 2, segments = 1 (C9 stub).
        out = _call(
            mcp_server, "polar_grid", op="route", pcb_path=pcb_path,
            from_ref_pad="C9.1", to_ref_pad="U1.1", r_mm=20.0,
        )
        assert out["success"] is True
        assert out["segments"] == 1
        assert out["vias"] == 2


# ---------------------------------------------------------------------------
# Net auto + validation
# ---------------------------------------------------------------------------


class TestNetAndValidation:
    def test_net_mismatch_rejected(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "polar_grid", op="route", pcb_path=pcb_path,
            from_ref_pad="U1.1", to_ref_pad="U4.1", r_mm=20.0,
        )
        assert out["success"] is False
        assert out["results"][0]["success"] is False
        assert "different nets" in out["results"][0]["error"].lower()

    def test_pad_not_found(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "polar_grid", op="route", pcb_path=pcb_path,
            from_ref_pad="U1.9", to_ref_pad="U2.1", r_mm=20.0,
        )
        assert out["success"] is False
        assert "not found" in out["results"][0]["error"].lower()

    def test_missing_endpoints(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "polar_grid", op="route", pcb_path=pcb_path,
            r_mm=20.0,
        )
        assert out["success"] is False
        assert "from_ref_pad" in out["error"]


# ---------------------------------------------------------------------------
# Batch + collision warning
# ---------------------------------------------------------------------------


class TestBatch:
    def test_batch_single_write_and_counts(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "polar_grid", op="route", pcb_path=pcb_path,
            connections=[
                {"from": "U1.1", "to": "U2.1", "r_mm": 20.0},
                {"from": "U3.1", "to": "C9.1", "r_mm": 22.0},
            ],
        )
        assert out["success"] is True
        assert out["count"] == 2
        assert out["arcs"] == 2

    def test_same_ring_overlap_warns(self, mcp_server, pcb_path):
        # Two arcs both on r=20 covering overlapping angular spans
        # (U1↔U2 is a 180° sweep through +Y; U3↔C9 also crosses +Y).
        out = _call(
            mcp_server, "polar_grid", op="route", pcb_path=pcb_path,
            connections=[
                {"from": "U1.1", "to": "U2.1", "r_mm": 20.0},
                {"from": "U3.1", "to": "C9.1", "r_mm": 20.0},
            ],
        )
        assert out["success"] is True
        assert any("overlap" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# Disk persistence — every editing op must actually write the file, not
# only update the in-memory cache. ``_read`` opens the file directly
# (bypassing the cache), so a cache-only "write" would fail these.
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_add_polar_arc_persists(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "polar_grid", op="add_polar_arc", pcb_path=pcb_path,
            net_name="NET_X", ring=5, theta_start_deg=0.0,
            theta_end_deg=90.0, width_mm=0.5,
        )
        assert out["success"] is True
        assert "(arc" in _read(pcb_path)

    def test_add_radial_segment_persists(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "polar_grid", op="add_radial_segment",
            pcb_path=pcb_path, net_name="NET_X", theta_deg=0.0,
            ring_from=1, ring_to=5, width_mm=0.5,
        )
        assert out["success"] is True
        disk = _read(pcb_path)
        assert "(segment" in disk and '(layer "In2.Cu")' in disk

    def test_add_polar_via_persists(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "polar_grid", op="add_polar_via", pcb_path=pcb_path,
            net_name="NET_X", ring=5, spoke_deg=0.0,
        )
        assert out["success"] is True
        assert "(via" in _read(pcb_path)

    def test_place_on_ring_persists(self, mcp_server, pcb_path):
        before = _read(pcb_path)
        out = _call(
            mcp_server, "polar_grid", op="place_on_ring", pcb_path=pcb_path,
            ref="U1", ring=5, theta_deg=45.0,
        )
        assert out["success"] is True
        assert _read(pcb_path) != before    # footprint pose rewritten on disk
