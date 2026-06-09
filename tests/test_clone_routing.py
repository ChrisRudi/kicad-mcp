# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``clone_routing`` — clone tracks/arcs/vias from one anchor's
region onto sibling anchors with a pad-correspondence-fitted transform
(rotation or reflection)."""

from __future__ import annotations

import asyncio
import re

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt
from kicad_mcp.utils.pcb_geometry import pcb_local_to_world

# Four pads at the local unit-square corners — >=3 pads so the Procrustes
# fit is well-determined.
_PADS_LOCAL = {"1": (-1.0, -1.0), "2": (1.0, -1.0),
               "3": (1.0, 1.0), "4": (-1.0, 1.0)}


def _fp(ref: str, x: float, y: float, rot: float,
        layer: str = "F.Cu", n_pads: int = 4) -> str:
    uid = f"{abs(hash(ref)) % (16**12):012x}"
    pads = ""
    for i in range(1, n_pads + 1):
        lx, ly = _PADS_LOCAL[str(i)]
        pads += (
            f'\t\t(pad "{i}" smd rect\n'
            f'\t\t\t(at {lx} {ly} 0)\n'
            f'\t\t\t(size 0.5 0.5)\n'
            f'\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
            f'\t\t)\n'
        )
    return (
        f'\t(footprint "Test:quad"\n'
        f'\t\t(layer "{layer}")\n'
        f'\t\t(uuid "00000000-0000-0000-0000-{uid}")\n'
        f'\t\t(at {x} {y} {rot})\n'
        f'\t\t(property "Reference" "{ref}"\n'
        f'\t\t\t(at 0 0 0)\n\t\t\t(layer "F.SilkS")\n\t\t)\n'
        f'\t\t(property "Value" "X"\n'
        f'\t\t\t(at 0 0 0)\n\t\t\t(layer "F.Fab")\n\t\t)\n'
        f'{pads}'
        f'\t)\n'
    )


def _seg(x1: float, y1: float, x2: float, y2: float, net: str) -> str:
    return (
        f'\t(segment\n'
        f'\t\t(start {x1} {y1})\n'
        f'\t\t(end {x2} {y2})\n'
        f'\t\t(width 0.2)\n'
        f'\t\t(layer "F.Cu")\n'
        f'\t\t(net "{net}")\n'
        f'\t\t(uuid "11111111-0000-0000-0000-000000000001")\n'
        f'\t)\n'
    )


def _via(x: float, y: float, net: str) -> str:
    return (
        f'\t(via\n'
        f'\t\t(at {x} {y})\n'
        f'\t\t(size 0.6)\n'
        f'\t\t(drill 0.3)\n'
        f'\t\t(layers "F.Cu" "B.Cu")\n'
        f'\t\t(net "{net}")\n'
        f'\t\t(uuid "22222222-0000-0000-0000-000000000001")\n'
        f'\t)\n'
    )


def _build(footprints: str, routing: str = "") -> str:
    return (
        '(kicad_pcb\n'
        '\t(version 20240108)\n'
        '\t(generator "test")\n'
        '\t(general\n\t\t(thickness 1.6)\n\t)\n'
        '\t(layers\n'
        '\t\t(0 "F.Cu" signal)\n'
        '\t\t(31 "B.Cu" signal)\n'
        '\t)\n'
        + footprints + routing + ')\n'
    )


def _world(ref_x, ref_y, rot, pad, flipped=False):
    lx, ly = _PADS_LOCAL[pad]
    return pcb_local_to_world((ref_x, ref_y), rot, lx, ly, flipped)


@pytest.fixture
def pcb_path(tmp_path):
    # Source U1 at (50,50,0); target U2 rotated 90°; target U3 on B.Cu
    # (mirror). One segment pad1->pad3 and one via on pad2 of U1.
    fps = (
        _fp("U1", 50.0, 50.0, 0.0)
        + _fp("U2", 100.0, 50.0, 90.0)
        + _fp("U3", 100.0, 100.0, 0.0, layer="B.Cu")
    )
    s1, s3 = _world(50, 50, 0, "1"), _world(50, 50, 0, "3")
    s2 = _world(50, 50, 0, "2")
    routing = (
        _seg(s1[0], s1[1], s3[0], s3[1], "NET_A")
        + _via(s2[0], s2[1], "NET_A")
    )
    p = tmp_path / "board.kicad_pcb"
    p.write_text(_build(fps, routing), encoding="utf-8")
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


def _seg_endpoints(text: str, net: str):
    for m in re.finditer(r"\(segment\b(.*?)\n\t\)", text, re.DOTALL):
        b = m.group(1)
        if f'(net "{net}")' not in b:
            continue
        s = re.search(r"\(start ([\d.\-]+) ([\d.\-]+)\)", b)
        e = re.search(r"\(end ([\d.\-]+) ([\d.\-]+)\)", b)
        return ((float(s.group(1)), float(s.group(2))),
                (float(e.group(1)), float(e.group(2))))
    return None


# ---------------------------------------------------------------------------
# Happy path — rotation
# ---------------------------------------------------------------------------


class TestCloneRotation:
    def test_clones_onto_rotated_anchor(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_routing",
            pcb_path=pcb_path,
            source_anchor="U1",
            target_anchors=[{"anchor_ref": "U2",
                             "net_map": {"NET_A": "NET_B"}}],
            radius_mm=10.0,
        )
        assert out["success"] is True
        assert out["cloned_total"] == 2          # one segment + one via
        det = out["details"][0]
        assert det["transform"] == "rotation"
        assert det["fit_rms_mm"] < 1e-6          # exact pad fit
        assert det["pads_fitted"] == 4

        text = open(pcb_path, encoding="utf-8").read()
        ep = _seg_endpoints(text, "NET_B")
        assert ep is not None
        # cloned segment must land on U2's pad1 and pad3
        u2p1 = _world(100, 50, 90, "1")
        u2p3 = _world(100, 50, 90, "3")
        got = {(round(ep[0][0], 3), round(ep[0][1], 3)),
               (round(ep[1][0], 3), round(ep[1][1], 3))}
        want = {(round(u2p1[0], 3), round(u2p1[1], 3)),
                (round(u2p3[0], 3), round(u2p3[1], 3))}
        assert got == want

    def test_net_substitution_and_source_kept(self, mcp_server, pcb_path):
        _call(
            mcp_server, "clone_routing",
            pcb_path=pcb_path, source_anchor="U1",
            target_anchors=[{"anchor_ref": "U2",
                             "net_map": {"NET_A": "NET_B"}}],
            radius_mm=10.0,
        )
        text = open(pcb_path, encoding="utf-8").read()
        # source NET_A routing still present, new NET_B added
        assert text.count('(net "NET_A")') == 2
        assert text.count('(net "NET_B")') == 2


# ---------------------------------------------------------------------------
# Mirror + dry-run
# ---------------------------------------------------------------------------


class TestCloneMirror:
    def test_reflection_detected_for_flipped_anchor(self, mcp_server,
                                                    pcb_path):
        out = _call(
            mcp_server, "clone_routing",
            pcb_path=pcb_path, source_anchor="U1",
            target_anchors=[{"anchor_ref": "U3",
                             "net_map": {"NET_A": "NET_C"}}],
            radius_mm=10.0,
        )
        assert out["success"] is True
        det = out["details"][0]
        assert det["transform"] == "reflection"
        assert det["fit_rms_mm"] < 1e-6
        text = open(pcb_path, encoding="utf-8").read()
        ep = _seg_endpoints(text, "NET_C")
        u3p1 = _world(100, 100, 0, "1", flipped=True)
        u3p3 = _world(100, 100, 0, "3", flipped=True)
        got = {(round(ep[0][0], 3), round(ep[0][1], 3)),
               (round(ep[1][0], 3), round(ep[1][1], 3))}
        want = {(round(u3p1[0], 3), round(u3p1[1], 3)),
                (round(u3p3[0], 3), round(u3p3[1], 3))}
        assert got == want

    def test_dry_run_leaves_file_untouched(self, mcp_server, pcb_path):
        before = open(pcb_path, encoding="utf-8").read()
        out = _call(
            mcp_server, "clone_routing",
            pcb_path=pcb_path, source_anchor="U1",
            target_anchors=[{"anchor_ref": "U2",
                             "net_map": {"NET_A": "NET_B"}}],
            radius_mm=10.0, dry_run=True,
        )
        assert out["success"] is True and out["dry_run"] is True
        assert open(pcb_path, encoding="utf-8").read() == before

    def test_clear_target_idempotent(self, mcp_server, pcb_path):
        # Two identical calls with clear_target=True → still exactly one
        # cloned segment + via on the target net (old clone cleared).
        for _ in range(2):
            _call(
                mcp_server, "clone_routing",
                pcb_path=pcb_path, source_anchor="U1",
                target_anchors=[{"anchor_ref": "U2",
                                 "net_map": {"NET_A": "NET_B"}}],
                radius_mm=10.0, clear_target=True,
            )
        text = open(pcb_path, encoding="utf-8").read()
        assert text.count('(net "NET_B")') == 2


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestCloneErrors:
    def test_missing_source_anchor(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_routing",
            pcb_path=pcb_path, source_anchor="U_NOPE",
            target_anchors=[{"anchor_ref": "U2"}],
        )
        assert out["success"] is False
        assert "Source anchor not found" in out["error"]

    def test_missing_target_anchor(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_routing",
            pcb_path=pcb_path, source_anchor="U1",
            target_anchors=[{"anchor_ref": "U_NOPE"}],
            radius_mm=10.0,
        )
        assert out["success"] is False
        assert "Target anchor not found" in out["error"]

    def test_empty_target_list(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_routing",
            pcb_path=pcb_path, source_anchor="U1", target_anchors=[],
        )
        assert out["success"] is False

    def test_missing_pcb_file(self, mcp_server, tmp_path):
        out = _call(
            mcp_server, "clone_routing",
            pcb_path=str(tmp_path / "no.kicad_pcb"),
            source_anchor="U1", target_anchors=[{"anchor_ref": "U2"}],
        )
        assert out["success"] is False
        assert "not found" in out["error"]
