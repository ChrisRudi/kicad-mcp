# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``clone_layout_around_pivot`` — replicate a manually-placed
peripheral group around N other anchors with the same relative layout."""

from __future__ import annotations

import asyncio

import pytest

from kicad_mcp.tools import pcb_patch_tools as ppt


# ---------------------------------------------------------------------------
# Fixture: 4 anchors at 90° around the origin, each with one peripheral
# (R_n) placed at a known offset relative to its anchor.
# ---------------------------------------------------------------------------


def _fp(ref: str, x: float, y: float, rot: float, layer: str = "F.Cu") -> str:
    uid = f'{abs(hash(ref)) % (16**12):012x}'
    return (
        f'\t(footprint "Test:0402"\n'
        f'\t\t(layer "{layer}")\n'
        f'\t\t(uuid "00000000-0000-0000-0000-{uid}")\n'
        f'\t\t(at {x} {y} {rot})\n'
        f'\t\t(property "Reference" "{ref}"\n'
        f'\t\t\t(at 0 0 0)\n'
        f'\t\t\t(layer "F.SilkS")\n'
        f'\t\t)\n'
        f'\t\t(property "Value" "X"\n'
        f'\t\t\t(at 0 0 0)\n'
        f'\t\t\t(layer "F.Fab")\n'
        f'\t\t)\n'
        f'\t\t(pad "1" smd rect\n'
        f'\t\t\t(at -0.5 0 0)\n'
        f'\t\t\t(size 0.5 0.6)\n'
        f'\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
        f'\t\t)\n'
        f'\t\t(pad "2" smd rect\n'
        f'\t\t\t(at 0.5 0 0)\n'
        f'\t\t\t(size 0.5 0.6)\n'
        f'\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
        f'\t\t)\n'
        f'\t)\n'
    )


def _build_pcb(refs_xy_rot: list[tuple[str, float, float, float]]) -> str:
    """Assemble a minimal .kicad_pcb with the given footprints."""
    head = (
        '(kicad_pcb\n'
        '\t(version 20240108)\n'
        '\t(generator "test")\n'
        '\t(general\n\t\t(thickness 1.6)\n\t)\n'
        '\t(layers\n'
        '\t\t(0 "F.Cu" signal)\n'
        '\t\t(31 "B.Cu" signal)\n'
        '\t)\n'
    )
    body = "".join(_fp(*x) for x in refs_xy_rot)
    return head + body + ')\n'


@pytest.fixture
def pcb_path(tmp_path):
    # Source: U2 at (60, 0, 90°) with peripheral R2 placed 3 mm to the east
    # in the LOCAL frame (because U2 is rotated 90°, R2 is visually NORTH).
    # The same template, cloned onto U1/U3/U4 anchors, must place R1/R3/R4
    # along each anchor's local-east axis.
    refs = [
        # Anchors at 4 cardinal points, each rotated to point radially OUT
        # (anchor's local +X = outward direction).
        ("U1", 60.0,   0.0,   0.0),   # east, rotation 0 → +X = east
        ("U2",  0.0, -60.0,  90.0),   # north (smaller y), +X visually north
        ("U3", -60.0,  0.0, 180.0),   # west, +X visually west
        ("U4",  0.0,  60.0, 270.0),   # south, +X visually south
        # Peripherals (only R2 is "manually" placed; R1, R3, R4 sit at
        # bogus origin and get re-placed by clone_layout_around_pivot).
        ("R2",  0.0, -63.0,  90.0),   # 3 mm visually north of U2 = local +X
        ("R1",  0.0,   0.0,   0.0),
        ("R3",  0.0,   0.0,   0.0),
        ("R4",  0.0,   0.0,   0.0),
    ]
    p = tmp_path / "ring.kicad_pcb"
    p.write_text(_build_pcb(refs), encoding="utf-8")
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


def _read_pose(text: str, ref: str) -> tuple[float, float, float]:
    """Read footprint header ``(at x y rot)`` for ``ref``. Uses
    ``_find_footprint_block`` from the module under test to avoid the
    "greedy regex matches an earlier footprint" pitfall."""
    span = ppt._find_footprint_block(text, ref)
    assert span is not None, f"footprint {ref} not found"
    return ppt._read_fp_pose(text[span[0]:span[1]])


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRadialClone:
    def test_clones_onto_three_targets(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path,
            source_ref="U2",
            source_peripherals=["R2"],
            target_pivots=[
                {"anchor_ref": "U1", "peripheral_refs": ["R1"]},
                {"anchor_ref": "U3", "peripheral_refs": ["R3"]},
                {"anchor_ref": "U4", "peripheral_refs": ["R4"]},
            ],
        )
        assert out["success"] is True
        assert out["placed"] == 3
        assert out["targets"] == 3

        text = open(pcb_path, encoding="utf-8").read()

        # U2 was at (0, -60, 90°); R2 at (0, -63, 90°) — 3 mm "north"
        # of U2, which in U2's local frame is local +X (the visual rotation
        # by 90° puts local-+X pointing visually up = smaller y). So the
        # template's local offset is +3 mm along local +X, 0 along local +Y.

        # U1 (east-anchor, rot 0°): local +X is world +X.
        # R1 should land 3 mm east of U1 → (63, 0, 0°).
        x, y, rot = _read_pose(text, "R1")
        assert (round(x, 3), round(y, 3), int(rot)) == (63.0, 0.0, 0)

        # U3 (west-anchor, rot 180°): local +X is world -X.
        # R3 should land 3 mm west of U3 → (-63, 0, 180°).
        x, y, rot = _read_pose(text, "R3")
        assert (round(x, 3), round(y, 3), int(rot)) == (-63.0, 0.0, 180)

        # U4 (south-anchor, rot 270°): local +X visually south = +y.
        # R4 should land 3 mm south of U4 → (0, 63, 270°).
        x, y, rot = _read_pose(text, "R4")
        assert (round(x, 3), round(y, 3), int(rot)) == (0.0, 63.0, 270)

    def test_source_anchor_unchanged(self, mcp_server, pcb_path):
        before = _read_pose(open(pcb_path, encoding="utf-8").read(), "U2")
        _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path,
            source_ref="U2",
            source_peripherals=["R2"],
            target_pivots=[
                {"anchor_ref": "U1", "peripheral_refs": ["R1"]},
            ],
        )
        after = _read_pose(open(pcb_path, encoding="utf-8").read(), "U2")
        assert before == after

    def test_pads_rotate_with_body(self, mcp_server, pcb_path):
        # R3 lands rotated 180° onto U3. Its pad lokal-rot should match.
        _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path,
            source_ref="U2",
            source_peripherals=["R2"],
            target_pivots=[
                {"anchor_ref": "U3", "peripheral_refs": ["R3"]},
            ],
        )
        import re
        text = open(pcb_path, encoding="utf-8").read()
        span = ppt._find_footprint_block(text, "R3")
        assert span is not None
        block = text[span[0]:span[1]]
        pad_rots = re.findall(
            r'\(pad\s+"[^"]*"\s+\w+\s+\w+\s*'
            r'(?:[^()]|\([^()]*\))*?'
            r'\(at\s+[\d.\-]+\s+[\d.\-]+\s+([\d.\-]+)\)',
            block,
        )
        assert pad_rots == ["180", "180"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_pcb(self, mcp_server, tmp_path):
        out = _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=str(tmp_path / "no.kicad_pcb"),
            source_ref="U2",
            source_peripherals=["R2"],
            target_pivots=[{"anchor_ref": "U1", "peripheral_refs": ["R1"]}],
        )
        assert out["success"] is False
        assert "not found" in out["error"].lower()

    def test_unknown_source(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path, source_ref="UNKNOWN",
            source_peripherals=["R2"],
            target_pivots=[{"anchor_ref": "U1", "peripheral_refs": ["R1"]}],
        )
        assert out["success"] is False
        assert "source anchor not found" in out["error"].lower()

    def test_unknown_source_peripheral(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path, source_ref="U2",
            source_peripherals=["RX"],
            target_pivots=[{"anchor_ref": "U1", "peripheral_refs": ["R1"]}],
        )
        assert out["success"] is False
        assert "source peripheral not found" in out["error"].lower()

    def test_unknown_target_anchor(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path, source_ref="U2",
            source_peripherals=["R2"],
            target_pivots=[{"anchor_ref": "UX", "peripheral_refs": ["R1"]}],
        )
        assert out["success"] is False
        assert "target anchor not found" in out["error"].lower()

    def test_unknown_target_peripheral(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path, source_ref="U2",
            source_peripherals=["R2"],
            target_pivots=[{"anchor_ref": "U1", "peripheral_refs": ["RX"]}],
        )
        assert out["success"] is False
        assert "target peripheral not found" in out["error"].lower()

    def test_empty_source_peripherals(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path, source_ref="U2",
            source_peripherals=[],
            target_pivots=[{"anchor_ref": "U1", "peripheral_refs": []}],
        )
        assert out["success"] is False
        assert "source_peripherals" in out["error"]

    def test_empty_target_pivots(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path, source_ref="U2",
            source_peripherals=["R2"],
            target_pivots=[],
        )
        assert out["success"] is False
        assert "target_pivots" in out["error"]

    def test_length_mismatch(self, mcp_server, pcb_path):
        out = _call(
            mcp_server, "clone_layout_around_pivot",
            pcb_path=pcb_path, source_ref="U2",
            source_peripherals=["R2"],
            target_pivots=[{
                "anchor_ref": "U1",
                "peripheral_refs": ["R1", "EXTRA"],  # wrong length
            }],
        )
        assert out["success"] is False
        assert "length" in out["error"].lower()
