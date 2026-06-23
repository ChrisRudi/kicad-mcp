# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the pure clearance-centering geometry (``utils.pcb_clearance``).

Headless: no KiCad / kipy. Exercises the obstacle probes and the two target
solvers (equalize closed form + maximize soft-min ascent) on hand-checkable
corridors / corners.
"""

from __future__ import annotations

import math

from kicad_mcp.utils.pcb_clearance import (
    CircleObstacle,
    RectObstacle,
    SegmentObstacle,
    clearances_at,
    min_clearance,
    nearest_two,
    solve_equalize,
    solve_maximize,
    solve_target,
)


# --- obstacle probes ---------------------------------------------------------

class TestProbes:
    def test_segment_gap_and_direction(self):
        # horizontal centreline y=0, half-width 0.1; point 0.5 above
        seg = SegmentObstacle(-5, 0, 5, 0, 0.1)
        gap, ux, uy = seg.probe(0.0, 0.5)
        assert round(gap, 6) == 0.4            # 0.5 - 0.1
        assert (round(ux, 6), round(uy, 6)) == (0.0, 1.0)

    def test_segment_endpoint_region(self):
        # point beyond the segment end clamps to the endpoint
        seg = SegmentObstacle(0, 0, 0, 0, 0.0)   # degenerate → a point at origin
        gap, ux, uy = seg.probe(3.0, 4.0)
        assert round(gap, 6) == 5.0
        assert round(math.hypot(ux, uy), 6) == 1.0

    def test_circle_gap(self):
        via = CircleObstacle(0, 0, 0.3)
        gap, ux, uy = via.probe(1.0, 0.0)
        assert round(gap, 6) == 0.7            # 1.0 - 0.3
        assert (round(ux, 6), round(uy, 6)) == (1.0, 0.0)

    def test_rect_outside_face(self):
        pad = RectObstacle(0, 0, 0.5, 0.5)
        gap, ux, uy = pad.probe(2.0, 0.0)
        assert round(gap, 6) == 1.5            # 2.0 - 0.5
        assert (round(ux, 6), round(uy, 6)) == (1.0, 0.0)

    def test_rect_inside_is_negative(self):
        pad = RectObstacle(0, 0, 0.5, 0.5)
        gap, _ux, _uy = pad.probe(0.1, 0.0)
        assert gap < 0


# --- equalize (centre between two walls) ------------------------------------

class TestEqualize:
    def _corridor(self):
        # two horizontal walls, centrelines at y=0 and y=2, half-width 0.1
        return [
            SegmentObstacle(-5, 0, 5, 0, 0.1, net="A", uuid="bottom"),
            SegmentObstacle(-5, 2, 5, 2, 0.1, net="B", uuid="top"),
        ]

    def test_centres_between_parallel_walls(self):
        obs = self._corridor()
        x, y = solve_equalize(0.0, 0.5, obs, max_step=2.0)
        assert round(x, 4) == 0.0
        assert abs(y - 1.0) < 1e-3             # exact midpoint of the corridor

    def test_equal_clearances_after(self):
        obs = self._corridor()
        x, y = solve_equalize(0.0, 0.7, obs, max_step=2.0)
        cs = clearances_at(obs, x, y, via_radius=0.3)
        assert abs(cs[0]["clearance_mm"] - cs[1]["clearance_mm"]) < 1e-3

    def test_step_budget_caps_displacement(self):
        obs = self._corridor()
        # need to travel 0.5 to centre, but only allow 0.2
        _x, y = solve_equalize(0.0, 0.5, obs, max_step=0.2)
        assert abs(y - 0.7) < 1e-3             # moved exactly the budget, toward centre

    def test_corner_falls_back_without_crash(self):
        # bottom wall + left wall meet at the origin region → not a corridor
        obs = [
            SegmentObstacle(-5, 0, 5, 0, 0.1, uuid="bottom"),
            SegmentObstacle(0, -5, 0, 5, 0.1, uuid="left"),
        ]
        before = min_clearance(obs, 0.5, 0.5, via_radius=0.1)
        x, y = solve_equalize(0.5, 0.5, obs, max_step=2.0)
        after = min_clearance(obs, x, y, via_radius=0.1)
        assert after >= before                 # moved away from the corner


# --- maximize ----------------------------------------------------------------

class TestMaximize:
    def test_centres_corridor(self):
        obs = [
            SegmentObstacle(-5, 0, 5, 0, 0.1),
            SegmentObstacle(-5, 2, 5, 2, 0.1),
        ]
        _x, y = solve_maximize(0.0, 0.6, obs, max_step=2.0)
        assert abs(y - 1.0) < 0.05

    def test_single_wall_escapes_by_budget(self):
        obs = [SegmentObstacle(-5, 0, 5, 0, 0.1)]
        _x, y = solve_maximize(0.0, 0.5, obs, max_step=0.4)
        assert abs(y - 0.9) < 1e-2             # moved away by the full budget

    def test_monotone_never_worsens(self):
        obs = [
            CircleObstacle(0, 0, 0.2, uuid="a"),
            CircleObstacle(3, 0, 0.2, uuid="b"),
            CircleObstacle(1.5, 2.0, 0.2, uuid="c"),
        ]
        before = min_clearance(obs, 1.4, 0.6, via_radius=0.1)
        x, y = solve_maximize(1.4, 0.6, obs, max_step=1.5)
        after = min_clearance(obs, x, y, via_radius=0.1)
        assert after >= before


# --- dispatch + reporting ----------------------------------------------------

class TestDispatchAndReport:
    def test_solve_target_unknown_mode(self):
        try:
            solve_target(0, 0, [], "spin", 1.0)
        except ValueError as exc:
            assert "mode" in str(exc)
        else:
            raise AssertionError("expected ValueError for unknown mode")

    def test_no_obstacles_is_noop(self):
        assert solve_maximize(1.0, 2.0, [], max_step=1.0) == (1.0, 2.0)
        assert min_clearance([], 1.0, 2.0, 0.3) is None

    def test_clearances_sorted_tightest_first(self):
        obs = [
            CircleObstacle(0, 0, 0.2, net="A", uuid="far"),
            CircleObstacle(0.8, 0, 0.2, net="B", uuid="near"),
        ]
        cs = clearances_at(obs, 0.0, 0.0, via_radius=0.1)
        # point sits on 'far' centre (clearance very negative) → it is tightest
        assert cs[0]["uuid"] == "far"
        assert cs[0]["clearance_mm"] <= cs[1]["clearance_mm"]

    def test_nearest_two_orders_by_gap(self):
        obs = [
            CircleObstacle(5, 0, 0.2, uuid="far"),
            CircleObstacle(1, 0, 0.2, uuid="near"),
        ]
        (g1, _, _2), (g2, _3, _4) = nearest_two(obs, 0.0, 0.0)
        assert g1 <= g2
