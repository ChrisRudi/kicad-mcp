# SPDX-License-Identifier: GPL-3.0-or-later
# test_ipc_live_diff.py
# Unit tests for the PURE diff/attribution/summary engine of the IPC live
# layer. No kipy / no running KiCad -> runs in plain CI (DoD #6).

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kicad_mcp.tools.ipc_live_diff import (  # noqa: E402
    attribute,
    cas_conflict,
    diff_snapshots,
    fp_signature,
    make_record,
    summarize_user,
    track_signature,
    via_signature,
)

_MM = 1_000_000


def _fp(kiid, x_mm, y_mm, rot=0.0, layer="F.Cu"):
    x, y = int(x_mm * _MM), int(y_mm * _MM)
    return kiid, make_record("footprint", fp_signature(x, y, rot, layer),
                             x=x, y=y, layer=layer)


def _track(kiid, sx, sy, ex, ey, layer="B.Cu", net="GND", w=0.25):
    sx, sy, ex, ey, w = (int(v * _MM) for v in (sx, sy, ex, ey, w))
    return kiid, make_record("track", track_signature(sx, sy, ex, ey, w, layer, net),
                             x=sx, y=sy, layer=layer, net=net)


def _via(kiid, x_mm, y_mm, net="GND"):
    x, y = int(x_mm * _MM), int(y_mm * _MM)
    return kiid, make_record("via", via_signature(x, y, 600000, 300000, net, "through"),
                             x=x, y=y, layer="via", net=net)


def _snap(*items):
    return dict(items)


# --- diff_snapshots ---------------------------------------------------------


def test_no_change_empty_diff():
    snap = _snap(_fp("a", 10, 10), _fp("b", 20, 20))
    d = diff_snapshots(snap, snap)
    assert d == {"added": [], "removed": [], "changed": []}


def test_added_removed_changed():
    old = _snap(_fp("a", 10, 10), _fp("b", 20, 20))
    new = _snap(_fp("a", 10, 10), _fp("b", 25, 20), _fp("c", 30, 30))
    d = diff_snapshots(old, new)
    assert d["added"] == ["c"]
    assert d["removed"] == []
    assert d["changed"] == ["b"]


def test_removed_detected():
    old = _snap(_fp("a", 10, 10), _fp("b", 20, 20))
    new = _snap(_fp("a", 10, 10))
    assert diff_snapshots(old, new)["removed"] == ["b"]


def test_rotation_is_a_change():
    old = _snap(_fp("a", 10, 10, rot=0.0))
    new = _snap(_fp("a", 10, 10, rot=90.0))
    assert diff_snapshots(old, new)["changed"] == ["a"]


def test_track_net_change_detected():
    old = _snap(_track("t", 0, 0, 1, 1, net="GND"))
    new = _snap(_track("t", 0, 0, 1, 1, net="VCC"))
    assert diff_snapshots(old, new)["changed"] == ["t"]


# --- attribution + self-write masking --------------------------------------


def test_agent_self_write_masked():
    # agent moved 'a' to (15,10); user moved 'b'. Only 'b' should be 'user'.
    old = _snap(_fp("a", 10, 10), _fp("b", 20, 20))
    new = _snap(_fp("a", 15, 10), _fp("b", 25, 20))
    expected = dict([_fp("a", 15, 10)])  # what the agent intended
    d = diff_snapshots(old, new)
    att = attribute(d, new, expected)
    assert att["agent"]["changed"] == ["a"]
    assert att["user"]["changed"] == ["b"]


def test_no_expected_all_user():
    old = _snap(_fp("a", 10, 10))
    new = _snap(_fp("a", 15, 10))
    att = attribute(diff_snapshots(old, new), new, None)
    assert att["user"]["changed"] == ["a"]
    assert att["agent"]["changed"] == []


def test_agent_added_masked():
    old = _snap()
    new = _snap(_via("v1", 5, 5))
    expected = dict([_via("v1", 5, 5)])
    att = attribute(diff_snapshots(old, new), new, expected)
    assert att["agent"]["added"] == ["v1"]
    assert att["user"]["added"] == []


def test_agent_removal_masked():
    old = _snap(_fp("a", 10, 10))
    new = _snap()
    expected = {"a": make_record("footprint", (), layer="F.Cu")}
    expected["a"]["sig"] = None  # sentinel: agent-deleted
    att = attribute(diff_snapshots(old, new), new, expected)
    assert att["agent"]["removed"] == ["a"]
    assert att["user"]["removed"] == []


# --- summary ----------------------------------------------------------------


def test_summary_empty():
    s = summarize_user({"added": [], "removed": [], "changed": []}, {}, {}, 0, 0)
    assert "No user changes" in s


def test_summary_groups_and_regions():
    # center at (0,0); 'a' upper-left, 'b' lower-right.
    old = _snap(_fp("a", -10, -10), _fp("b", 10, 10))
    new = _snap(_fp("a", -12, -10), _fp("b", 12, 10))
    d = diff_snapshots(old, new)
    s = summarize_user(d, old, new, 0, 0)
    assert s.startswith("User ")
    assert "footprint" in s
    assert "upper-left" in s and "lower-right" in s


def test_summary_pluralization_and_verbs():
    old = _snap(_track("t1", -1, -1, -2, -2), _track("t2", -1, -1, -3, -3))
    new = _snap(_track("t1", -1, -1, -2, -5), _track("t2", -1, -1, -3, -9))
    d = diff_snapshots(old, new)
    s = summarize_user(d, old, new, 0, 0)
    assert "re-routed 2 tracks" in s


class TestCasConflict:
    """Optimistic-concurrency gate for live collaboration: a write is refused
    ONLY when the user moved the item since the agent planned the move."""

    def test_no_baseline_never_conflicts(self):
        sig = fp_signature(1 * _MM, 2 * _MM, 0.0, 4)
        assert cas_conflict(sig, None) is False

    def test_unchanged_since_plan_is_safe(self):
        sig = fp_signature(1 * _MM, 2 * _MM, 90.0, 4)
        assert cas_conflict(sig, list(sig)) is False

    def test_user_moved_it_conflicts(self):
        baseline = fp_signature(1 * _MM, 2 * _MM, 0.0, 4)
        live = fp_signature(5 * _MM, 2 * _MM, 0.0, 4)  # user dragged it away
        assert cas_conflict(live, list(baseline)) is True

    def test_agent_self_write_is_not_a_conflict(self):
        baseline = fp_signature(1 * _MM, 2 * _MM, 0.0, 4)
        live = fp_signature(5 * _MM, 2 * _MM, 0.0, 4)
        pending = make_record("footprint", live)  # the agent's own last write
        assert cas_conflict(live, list(baseline), pending) is False

    def test_json_int_float_drift_is_tolerated(self):
        # baseline orientation left as 90.0, comes back over MCP as int 90
        live = fp_signature(1 * _MM, 2 * _MM, 90.0, 4)
        baseline = [1 * _MM, 2 * _MM, 90, 4]
        assert cas_conflict(live, baseline) is False


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(["pytest", "-q", __file__]))
