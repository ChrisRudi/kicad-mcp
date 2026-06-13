# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for kicad_mcp.tools.connectivity_tools.

Exercises the pcbnew-backed connectivity / ratsnest check against the
committed ``medium_8pin_routed`` fixture. The whole module is skipped when
``pcbnew`` is not importable (i.e. not running under KiCad's bundled
Python) — the same convention the IPC-dependent tests use.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pcbnew")

from kicad_mcp.tools.connectivity_tools import check_connectivity_impl as run  # noqa: E402

FIXTURE = str(Path(__file__).parent / "PCB" / "medium_8pin" / "medium_8pin_routed.kicad_pcb")


def test_overview_happy() -> None:
    """overview returns the unconnected count + fragmented-net breakdown."""
    res = run(FIXTURE, "overview")
    assert res["success"] is True
    assert res["mode"] == "overview"
    assert isinstance(res["unconnected_items"], int)
    assert res["net_count"] >= 1
    # The routed fixture is intentionally incomplete → at least one net splits.
    names = {row["net"] for row in res["fragmented_nets"]}
    assert "GND" in names
    for row in res["fragmented_nets"]:
        assert row["clusters"] == len(row["group_sizes"])


def test_pad_mode_returns_cluster() -> None:
    """pad mode reports the electrical cluster of a known pad."""
    res = run(FIXTURE, "pad", ref_pad="D1.1")
    assert res["success"] is True
    assert res["net"] == "GND"
    assert res["cluster_pad_count"] >= 1
    assert "D1.1" in res["cluster_pads"]
    assert isinstance(res["cluster_items_by_class"], dict)


def test_whatif_structure_and_load_bearing_flag() -> None:
    """whatif removes the nearest track/via in memory and reports orphans."""
    # A VCC track start coordinate taken from the fixture.
    res = run(FIXTURE, "whatif", x_mm=149.64, y_mm=105.96)
    assert res["success"] is True
    assert res["mode"] == "whatif"
    assert res["net"] == "VCC"
    assert res["distance_mm"] is not None and res["distance_mm"] < 0.5
    assert isinstance(res["load_bearing"], bool)
    assert isinstance(res["orphaned_pads"], list)
    # load_bearing must agree with the orphan list (no silent disagreement).
    assert res["load_bearing"] == bool(res["orphaned_pads"])


def test_whatif_is_read_only() -> None:
    """whatif must not modify the file on disk (in-memory removal only)."""
    before = Path(FIXTURE).read_bytes()
    run(FIXTURE, "whatif", x_mm=149.64, y_mm=105.96)
    assert Path(FIXTURE).read_bytes() == before


def test_pad_mode_missing_pad_errors() -> None:
    """Unknown ref_pad yields a clean error dict, not an exception."""
    res = run(FIXTURE, "pad", ref_pad="NOPE.99")
    assert res["success"] is False
    assert "error" in res


def test_whatif_requires_coordinates() -> None:
    """whatif without coordinates returns a clear error."""
    res = run(FIXTURE, "whatif")
    assert res["success"] is False
    assert "x_mm" in res["error"]


def test_unknown_mode_errors() -> None:
    res = run(FIXTURE, "bogus")
    assert res["success"] is False
    assert "unknown mode" in res["error"]


def test_missing_file_errors() -> None:
    res = run("/nonexistent/board.kicad_pcb", "overview")
    assert res["success"] is False
    assert "not found" in res["error"].lower()


# ---------------------------------------------------------------------------
# Warm-daemon behaviour (① warm board cache, ② scoped / optional fill)
# ---------------------------------------------------------------------------
from kicad_mcp.tools.connectivity_tools import _DAEMON  # noqa: E402


def test_overview_reports_warm_cache_hit() -> None:
    """Two consecutive overviews on the same board: the second is a cache
    hit (the board is loaded + filled once, then reused)."""
    _DAEMON.request({"op": "reset"}, timeout=10.0)
    first = run(FIXTURE, "overview")
    second = run(FIXTURE, "overview")
    assert first["success"] and second["success"]
    # First call after a reset loads cold; the immediate repeat is warm.
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    # The warm result is identical to the cold one (same engine, same board).
    assert first["unconnected_items"] == second["unconnected_items"]
    assert first["fragmented_net_count"] == second["fragmented_net_count"]


def test_overview_fill_true_fills_zones() -> None:
    """Default overview fills zones and reports it."""
    _DAEMON.request({"op": "reset"}, timeout=10.0)
    res = run(FIXTURE, "overview", fill=True)
    assert res["success"] is True
    assert res["zones_filled"] is True


def test_overview_fill_false_is_pour_blind() -> None:
    """``fill=False`` skips the zone fill (fast, pour-blind) on a freshly
    loaded board — zones_filled is False and the structure is still valid."""
    _DAEMON.request({"op": "reset"}, timeout=10.0)
    res = run(FIXTURE, "overview", fill=False)
    assert res["success"] is True
    assert res["mode"] == "overview"
    assert res["zones_filled"] is False
    assert isinstance(res["fragmented_nets"], list)


def test_pad_reuses_warm_board() -> None:
    """A pad query after an overview reuses the warm board (cache hit) and
    still returns the correct net/cluster (scoped fill of the pad's net)."""
    _DAEMON.request({"op": "reset"}, timeout=10.0)
    run(FIXTURE, "overview")            # warm + fill-all
    res = run(FIXTURE, "pad", ref_pad="D1.1")
    assert res["success"] is True
    assert res["cache_hit"] is True
    assert res["net"] == "GND"
    assert "D1.1" in res["cluster_pads"]


def test_whatif_drops_board_from_cache() -> None:
    """``whatif`` mutates the in-memory board, so it must NOT stay cached:
    the next query reloads a pristine board (cache miss)."""
    _DAEMON.request({"op": "reset"}, timeout=10.0)
    run(FIXTURE, "overview")            # warm
    run(FIXTURE, "whatif", x_mm=149.64, y_mm=105.96)   # mutates → evict + recycle
    after = run(FIXTURE, "overview")
    assert after["success"] is True
    assert after["cache_hit"] is False  # pristine reload, not the mutated board


def test_status_op_lists_cached_boards() -> None:
    """The daemon's status op reports cached boards and load count."""
    _DAEMON.request({"op": "reset"}, timeout=10.0)
    run(FIXTURE, "overview")
    status = _DAEMON.request({"op": "status"}, timeout=10.0)
    assert status["ok"] is True
    assert isinstance(status["loads"], int) and status["loads"] >= 1
    assert any(FIXTURE in c["path"] for c in status["cached"])
