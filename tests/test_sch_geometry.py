# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for ``kicad_mcp.utils.sch_geometry``.

Pure-math helpers — no KiCad/kipy dependency.
"""

from __future__ import annotations


import pytest

from kicad_mcp.utils.sch_geometry import (
    bbox_center,
    bbox_of_points,
    needs_half_grid_offset,
    pin_world_xy,
    residual_after_snap,
    rotate_point,
    snap_for_pin_grid,
    snap_to_90,
)


class TestRotatePoint:
    def test_rotate_zero_is_identity(self):
        assert rotate_point(3.0, 4.0, 0.0) == pytest.approx((3.0, 4.0))

    def test_rotate_90_unit_vector(self):
        x, y = rotate_point(1.0, 0.0, 90.0)
        assert x == pytest.approx(0.0, abs=1e-9)
        assert y == pytest.approx(1.0, abs=1e-9)

    def test_rotate_around_pivot(self):
        # rotating (10, 5) by 90° around (5, 5) -> (5, 10)
        x, y = rotate_point(10.0, 5.0, 90.0, pivot=(5.0, 5.0))
        assert x == pytest.approx(5.0, abs=1e-9)
        assert y == pytest.approx(10.0, abs=1e-9)

    def test_rotate_360_is_identity(self):
        x, y = rotate_point(7.5, -2.3, 360.0)
        assert x == pytest.approx(7.5, abs=1e-9)
        assert y == pytest.approx(-2.3, abs=1e-9)


class TestSnapTo90:
    @pytest.mark.parametrize(
        "angle,expected",
        [
            (0, 0),
            (45, 90),
            (44, 0),
            (89, 90),
            (90, 90),
            (180, 180),
            (270, 270),
            (360, 0),
            (-90, 270),
            (450, 90),  # wraps
        ],
    )
    def test_known_angles(self, angle, expected):
        assert snap_to_90(angle) == expected


class TestResidualAfterSnap:
    def test_clean_90_residual_zero(self):
        for a in (0, 90, 180, 270):
            assert residual_after_snap(a) == pytest.approx(0.0, abs=1e-9)

    def test_residual_in_window(self):
        # residual lives in (-45, 45]
        for a in [10, 30, 45, -10, -30, -45, 50, 100, 137]:
            r = residual_after_snap(a)
            assert -45.0 < r <= 45.0


class TestPinWorldXY:
    def test_no_rotation_no_mirror(self):
        # Symbol at (10, 10), pin local (2.54, 0) -> y flips on conversion,
        # then rotation 0 keeps it.
        x, y = pin_world_xy(10.0, 10.0, 0, None, 2.54, 0.0)
        assert x == pytest.approx(12.54)
        assert y == pytest.approx(10.0)

    def test_rotation_90_moves_pin_along_y(self):
        # Pin local (2.54, 0) -> after 90° symbol rotation is below origin
        x, y = pin_world_xy(10.0, 10.0, 90, None, 2.54, 0.0)
        assert x == pytest.approx(10.0)
        assert y == pytest.approx(12.54)

    def test_rotation_180_negates_x(self):
        x, y = pin_world_xy(10.0, 10.0, 180, None, 2.54, 0.0)
        assert x == pytest.approx(7.46)
        assert y == pytest.approx(10.0)

    def test_mirror_y_flips_x(self):
        # mirror y inverts the x component before rotation
        x, y = pin_world_xy(0.0, 0.0, 0, "y", 2.54, 0.0)
        assert x == pytest.approx(-2.54)
        assert y == pytest.approx(0.0)

    def test_mirror_x_flips_y(self):
        # mirror x flips the (already y-flipped) y
        x, y = pin_world_xy(0.0, 0.0, 0, "x", 0.0, 2.54)
        # local (0,2.54) -> after schematic y-flip -> (0,-2.54)
        # after mirror x -> (0, +2.54)
        assert x == pytest.approx(0.0)
        assert y == pytest.approx(2.54)


class TestBBox:
    def test_bbox_of_points_simple(self):
        bb = bbox_of_points([(1, 2), (5, 0), (-1, 3)])
        assert bb == (-1, 0, 5, 3)

    def test_bbox_center(self):
        cx, cy = bbox_center((-1.0, 0.0, 5.0, 4.0))
        assert cx == pytest.approx(2.0)
        assert cy == pytest.approx(2.0)

    def test_bbox_empty_raises(self):
        with pytest.raises(ValueError):
            bbox_of_points([])


class TestSnapForPinGrid:
    """Bug 8 — half-pitch passive symbols need a 1.27 mm centre offset."""

    def test_non_passive_unchanged(self):
        x, y, snapped = snap_for_pin_grid(50.0, 50.0, "Switch:SW_Push", 0)
        assert (x, y, snapped) == (50.0, 50.0, False)

    def test_unknown_lib_unchanged(self):
        x, y, snapped = snap_for_pin_grid(12.34, 56.78, "Connector:Conn_01x02", 90)
        assert (x, y, snapped) == (12.34, 56.78, False)

    def test_c_small_vertical_snaps_y(self):
        # rotation 0 → pins on Y axis → adjust Y to (N+0.5)*2.54.
        # 50/2.54 ≈ 19.69 → nearest half-step is 19.5 → 19.5*2.54 = 49.53
        x, y, snapped = snap_for_pin_grid(50.0, 50.0, "Device:C_Small", 0)
        assert x == pytest.approx(50.0)
        assert y == pytest.approx(49.53)
        assert snapped is True

    def test_c_small_horizontal_snaps_x(self):
        x, y, snapped = snap_for_pin_grid(50.0, 50.0, "Device:C_Small", 90)
        assert y == pytest.approx(50.0)
        assert x == pytest.approx(49.53)
        assert snapped is True

    def test_already_on_pin_grid_no_snap(self):
        # 1.27 + N*2.54 is already correct (here 21.59 = 8.5*2.54)
        x, y, snapped = snap_for_pin_grid(20.0, 21.59, "Device:R_Small", 0)
        assert x == pytest.approx(20.0)
        assert y == pytest.approx(21.59)
        assert snapped is False

    def test_rotation_180_snaps_y(self):
        # 180° still has pins on Y axis. 20/2.54 ≈ 7.87 → 7.5*2.54 = 19.05
        _, y, snapped = snap_for_pin_grid(20.0, 20.0, "Device:L_Small", 180)
        assert snapped is True
        assert y == pytest.approx(19.05)

    def test_rotation_270_snaps_x(self):
        x, _, snapped = snap_for_pin_grid(20.0, 20.0, "Device:R_Small", 270)
        assert snapped is True
        assert x == pytest.approx(19.05)

    def test_full_pitch_device_c_also_snaps(self):
        # Device:C has pin pitch 7.62 = 3*2.54 → same off-grid problem.
        _, y, snapped = snap_for_pin_grid(50.0, 50.0, "Device:C", 0)
        assert snapped is True
        assert y == pytest.approx(49.53)

    def test_negative_rotation_normalised(self):
        # rotation -90 is equivalent to 270 → snap X
        x, _, snapped = snap_for_pin_grid(20.0, 20.0, "Device:R_Small", -90)
        assert snapped is True
        assert x == pytest.approx(19.05)


class TestNeedsHalfGridOffset:
    def test_known_passives(self):
        for lib in ("Device:C", "Device:C_Small", "Device:R", "Device:R_Small",
                    "Device:L", "Device:L_Small", "Device:CP", "Device:CP_Small",
                    "Device:D", "Device:LED"):
            assert needs_half_grid_offset(lib), lib

    def test_other_symbols(self):
        for lib in ("Switch:SW_Push", "Connector:Conn_01x02",
                    "Device:Q_NPN_BCE", "power:VCC"):
            assert not needs_half_grid_offset(lib), lib
