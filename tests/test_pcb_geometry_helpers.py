# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for :mod:`kicad_mcp.utils.pcb_geometry`.

Pure math + S-expression text helpers used by ``place_at_pivot`` and
future placement / routing tools. No KiCad runtime required.
"""

from __future__ import annotations

import math

import pytest

from kicad_mcp.utils.pcb_geometry import (
    align_radial_rotation,
    bbox_center,
    compute_fp_bbox,
    pcb_local_to_world,
    pcb_world_to_local,
    phi_short,
    short_arc_mid_xy,
    short_mid_phi,
    wrap_signed,
)


class TestAngleUtilities:
    def test_wrap_signed_inside_range(self):
        assert wrap_signed(0.0) == 0.0
        assert wrap_signed(90.0) == 90.0
        assert wrap_signed(-90.0) == -90.0

    def test_wrap_signed_wraps_full_turns(self):
        assert wrap_signed(370.0) == pytest.approx(10.0)
        assert wrap_signed(-200.0) == pytest.approx(160.0)
        assert wrap_signed(720.0) == pytest.approx(0.0)

    def test_wrap_signed_180_canonicalises_positive(self):
        # The half-open interval is (-180, 180], so 180 maps to 180 and
        # -180 maps to 180 (so callers can compare deterministically).
        assert wrap_signed(180.0) == 180.0
        assert wrap_signed(-180.0) == 180.0

    def test_phi_short_chooses_shortest_route(self):
        assert phi_short(350.0, 10.0) == pytest.approx(20.0)
        assert phi_short(10.0, 350.0) == pytest.approx(-20.0)
        assert phi_short(0.0, 180.0) == pytest.approx(180.0)
        assert phi_short(45.0, 225.0) == pytest.approx(180.0)

    def test_short_mid_phi_does_not_wrap_around(self):
        # Bug pattern from radial routing: pad at φ=2.4°, via at φ=351.4°
        # — the mid must be at ~357°, not the diametrically-opposite 177°.
        assert short_mid_phi(2.4, 351.4) == pytest.approx(356.9, abs=0.05)

    def test_short_mid_phi_symmetric(self):
        # Mid of (a, b) and (b, a) is the same point modulo direction.
        m1 = short_mid_phi(20.0, 80.0)
        m2 = short_mid_phi(80.0, 20.0)
        assert m1 == pytest.approx(50.0)
        assert m2 == pytest.approx(50.0)


class TestLocalWorldTransforms:
    def test_zero_rotation_is_identity_translation(self):
        wx, wy = pcb_local_to_world((100.0, 200.0), 0.0, 1.0, 2.0)
        assert (wx, wy) == pytest.approx((101.0, 202.0))

    def test_90_degree_cw_screen_convention(self):
        # KiCad PCB y-down: a 90° rotation maps local (+x) to world (−y)
        # (visually "up"). Earlier text-only routing got this wrong by
        # applying MATH-CCW, which placed pads 0.4 mm off for a 0402.
        wx, wy = pcb_local_to_world((100.0, 100.0), 90.0, 1.0, 0.0)
        assert (wx, wy) == pytest.approx((100.0, 99.0))
        wx, wy = pcb_local_to_world((100.0, 100.0), 90.0, 0.0, 1.0)
        assert (wx, wy) == pytest.approx((101.0, 100.0))

    def test_180_rotation_negates_both(self):
        wx, wy = pcb_local_to_world((10.0, 20.0), 180.0, 3.0, 4.0)
        assert (wx, wy) == pytest.approx((7.0, 16.0))

    def test_round_trip_at_arbitrary_rotation(self):
        anchor = (12.5, 7.25)
        rot = 37.5
        lx_in, ly_in = -3.0, 4.5
        wx, wy = pcb_local_to_world(anchor, rot, lx_in, ly_in)
        lx_out, ly_out = pcb_world_to_local(anchor, rot, wx, wy)
        assert lx_out == pytest.approx(lx_in)
        assert ly_out == pytest.approx(ly_in)

    def test_flip_mirrors_x_axis(self):
        # B.Cu footprint: local X is mirrored before rotation+translate.
        wx, wy = pcb_local_to_world((0.0, 0.0), 0.0, 1.0, 2.0, flipped=True)
        assert (wx, wy) == pytest.approx((-1.0, 2.0))


class TestRadialAlignment:
    @pytest.mark.parametrize(
        "mode, target, expected",
        [
            # Target east of centre — radial axis runs along +X.
            ("radial_out", (171.0, 105.0), 0.0),
            ("radial_in", (171.0, 105.0), 180.0),
            ("tangential_ccw", (171.0, 105.0), 90.0),
            ("tangential_cw", (171.0, 105.0), 270.0),
            # Target north of centre (KiCad y-down: smaller y is "up").
            ("radial_out", (148.5, 85.0), 90.0),
            ("radial_in", (148.5, 85.0), 270.0),
        ],
    )
    def test_cardinal_directions(self, mode, target, expected):
        rot = align_radial_rotation(target, (148.5, 105.0), mode)
        assert rot == pytest.approx(expected, abs=1e-6)

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            align_radial_rotation((1, 2), (0, 0), "diagonal_anywhere")


class TestShortArcMid:
    def test_arc_mid_takes_short_route(self):
        # Start at 270° (0,-10), end at 0° (10,0), centre origin: the
        # short arc midpoint is at 315° = (7.07, -7.07).
        mx, my = short_arc_mid_xy((0.0, -10.0), (10.0, 0.0), (0.0, 0.0))
        assert (mx, my) == pytest.approx(
            (10.0 * math.cos(math.radians(-45)),
             10.0 * math.sin(math.radians(-45))),
            abs=1e-9,
        )

    def test_arc_mid_for_diametrical_endpoints_picks_one_side(self):
        # 180° apart: a (0°) → b (180°). Either side is geometrically
        # equal-length; phi_short maps the boundary to +180 so the mid
        # sits at +90° = (0, +R).
        mx, my = short_arc_mid_xy((10.0, 0.0), (-10.0, 0.0), (0.0, 0.0))
        assert (mx, my) == pytest.approx((0.0, 10.0), abs=1e-9)


class TestFootprintBbox:
    def test_empty_text_returns_zero_bbox(self):
        assert compute_fp_bbox("") == (0.0, 0.0, 0.0, 0.0)

    def test_pads_define_bbox(self):
        mod = (
            '(module Test (layer F.Cu)\n'
            '  (pad "1" smd rect (at -1.0 -0.5) (size 0.5 0.5) (layers "F.Cu"))\n'
            '  (pad "2" smd rect (at  1.0  0.5) (size 0.5 0.5) (layers "F.Cu"))\n'
            ')'
        )
        bbox = compute_fp_bbox(mod)
        assert bbox == pytest.approx((-1.25, -0.75, 1.25, 0.75))

    def test_fab_lines_extend_bbox(self):
        mod = (
            '(module Test (layer F.Cu)\n'
            '  (pad "1" smd rect (at 0 0) (size 0.2 0.2) (layers "F.Cu"))\n'
            '  (fp_line (start -5 -3) (end 5 -3) (layer "F.Fab"))\n'
            ')'
        )
        bbox = compute_fp_bbox(mod)
        assert bbox[0] == pytest.approx(-5.0)
        assert bbox[2] == pytest.approx(5.0)
        assert bbox[1] == pytest.approx(-3.0)

    def test_bbox_center_midpoint(self):
        cx, cy = bbox_center((-2.0, -4.0, 6.0, 8.0))
        assert cx == pytest.approx(2.0)
        assert cy == pytest.approx(2.0)
