# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the pure placement scorer (utils/placement_eval) behind Entwirren.

All headless — no KiCad. The scorer is the non-mutating notepad the agent
reasons against, so its geometry (segment crossings, per-net MST ratsnest,
overlaps, power-net exclusion) must be exactly right."""

from __future__ import annotations

from kicad_mcp.utils import placement_eval as pe
from kicad_mcp.utils.pcb_geometry import pcb_local_to_world


# --- segment crossing --------------------------------------------------------

def test_segments_properly_cross():
    assert pe.segments_cross((0, 0), (10, 10), (0, 10), (10, 0)) is True


def test_segments_touching_endpoint_is_not_a_crossing():
    # they meet at (10,10) — a shared endpoint, not a proper crossing
    assert pe.segments_cross((0, 0), (10, 10), (10, 10), (20, 0)) is False


def test_parallel_segments_do_not_cross():
    assert pe.segments_cross((0, 0), (10, 0), (0, 5), (10, 5)) is False


# --- MST (ratsnest) ----------------------------------------------------------

def test_mst_two_points_one_edge():
    assert pe.mst_edges([(0, 0), (3, 4)]) == [(0, 1)]


def test_mst_has_n_minus_1_edges_and_picks_short_ones():
    pts = [(0, 0), (1, 0), (2, 0), (10, 0)]  # a line
    edges = pe.mst_edges(pts)
    assert len(edges) == 3
    # total length is the line length (1+1+8), not a star from node 0
    total = sum(pe._seg_len(pts[i], pts[j]) for i, j in edges)
    assert abs(total - 10.0) < 1e-9


# --- power-net detection -----------------------------------------------------

def test_power_names_detected():
    for n in ("GND", "gnd", "/GND", "VCC", "+3V3", "3V3", "VDD_IO", "VBUS", "5V"):
        assert pe.is_power_net(n), n


def test_signal_names_not_power():
    for n in ("SDA", "SCL", "MOSI", "LED_EN", "NET1", "CLK"):
        assert not pe.is_power_net(n), n


def test_high_fanout_net_counts_as_power():
    # a net touching most footprints is a rail whatever its name
    assert pe.is_power_net("MYSTERY", pad_count=8, footprint_count=10)
    assert not pe.is_power_net("MYSTERY", pad_count=2, footprint_count=10)


# --- count_crossings ---------------------------------------------------------

def test_count_crossings_counts_distinct_pad_pairs():
    aw = [
        (("A", "1"), ("B", "1"), (0, 0), (10, 10)),
        (("C", "1"), ("D", "1"), (0, 10), (10, 0)),
    ]
    assert pe.count_crossings(aw) == 1


def test_count_crossings_skips_shared_pad():
    # both airwires share pad A.1 → they meet there, not a crossing
    aw = [
        (("A", "1"), ("B", "1"), (0, 0), (10, 10)),
        (("A", "1"), ("C", "1"), (0, 0), (10, 0)),
    ]
    assert pe.count_crossings(aw) == 0


# --- pad world positions -----------------------------------------------------

def test_pad_world_no_rotation_is_offset():
    fps = [{"ref": "R1", "x": 10.0, "y": 20.0,
            "pads": [{"name": "1", "lx": 1.0, "ly": 0.0}]}]
    got = pe.pad_world_positions(fps)[("R1", "1")]
    assert got == (11.0, 20.0)


def test_pad_world_uses_footgun_safe_helper():
    fps = [{"ref": "U1", "x": 5.0, "y": 7.0, "rot": 90.0,
            "pads": [{"name": "1", "lx": 2.0, "ly": 0.0}]}]
    got = pe.pad_world_positions(fps)[("U1", "1")]
    assert got == pcb_local_to_world((5.0, 7.0), 90.0, 2.0, 0.0, False)


# --- evaluate_layout (the scorer) --------------------------------------------

def _pad(ref, x, y, bbox=(1.0, 1.0)):
    return {"ref": ref, "x": x, "y": y, "bbox": list(bbox),
            "pads": [{"name": "1", "lx": 0.0, "ly": 0.0}]}


def test_evaluate_detects_one_crossing():
    # two nets whose airwires form an X
    fps = [_pad("A", 0, 0), _pad("B", 10, 10),
           _pad("C", 10, 0), _pad("D", 0, 10)]
    nets = {"N1": [["A", "1"], ["B", "1"]], "N2": [["C", "1"], ["D", "1"]]}
    out = pe.evaluate_layout(fps, nets)
    assert out["signal_crossings"] == 1
    assert out["signal_nets"] == 2
    assert out["overlaps"] == 0


def test_evaluate_untangled_has_no_crossing():
    # same nets, but laid out as two parallel horizontal airwires
    fps = [_pad("A", 0, 0), _pad("B", 10, 0),
           _pad("C", 0, 5), _pad("D", 10, 5)]
    nets = {"N1": [["A", "1"], ["B", "1"]], "N2": [["C", "1"], ["D", "1"]]}
    assert pe.evaluate_layout(fps, nets)["signal_crossings"] == 0


def test_evaluate_excludes_power_nets():
    fps = [_pad("A", 0, 0), _pad("B", 10, 10),
           _pad("C", 10, 0), _pad("D", 0, 10)]
    # the crossing pair is now on GND → excluded → no counted crossing
    nets = {"GND": [["A", "1"], ["B", "1"]], "N2": [["C", "1"], ["D", "1"]]}
    out = pe.evaluate_layout(fps, nets)
    assert out["signal_crossings"] == 0
    assert "GND" in out["excluded_power_nets"]
    assert out["signal_nets"] == 1


def test_evaluate_counts_overlap():
    fps = [_pad("A", 0, 0, bbox=(4, 4)), _pad("B", 1, 1, bbox=(4, 4))]
    nets = {"N1": [["A", "1"], ["B", "1"]]}
    assert pe.evaluate_layout(fps, nets)["overlaps"] == 1


def test_evaluate_wirelength_sums_airwires():
    fps = [_pad("A", 0, 0), _pad("B", 3, 4)]
    nets = {"N1": [["A", "1"], ["B", "1"]]}
    assert pe.evaluate_layout(fps, nets)["wirelength_mm"] == 5.0
