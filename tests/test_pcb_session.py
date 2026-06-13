# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the warm-board pcb_eval session (kicad_mcp.tools.pcb_session_tools).

Covers edge cases: cold→warm reuse, mtime invalidation, in-memory what-if
not touching disk, helper-library correctness, error/empty/missing-file
paths, result truncation, stdout capture, ctx persistence, timeout +
daemon recycle, status/reset, and the real analysis patterns used during
routing. Skipped when pcbnew is unavailable.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("pcbnew")

from kicad_mcp.tools.pcb_session_tools import _DAEMON, _eval_impl  # noqa: E402

FIXTURE = str(Path(__file__).parent / "PCB" / "medium_8pin" / "medium_8pin_routed.kicad_pcb")


@pytest.fixture(autouse=True)
def _fresh_daemon():
    """Each test starts and ends with a clean daemon."""
    _DAEMON._kill()
    yield
    _DAEMON._kill()


def ev(path, code, **kw):
    return _eval_impl(path, code, **kw)


# --- happy path + warmth ---------------------------------------------------
def test_cold_then_warm_reuse():
    r1 = ev(FIXTURE, "result = len(nets())")
    assert r1["success"] and r1["cache_hit"] is False
    assert isinstance(r1["result"], int) and r1["result"] >= 1
    r2 = ev(FIXTURE, "result = unconnected()")
    assert r2["success"] and r2["cache_hit"] is True       # warm reuse
    assert r2["elapsed_ms"] < 800                          # ms-fast, not a reload


def test_result_and_stdout_capture():
    r = ev(FIXTURE, "print('hi'); print('there'); result = 6*7")
    assert r["success"]
    assert r["result"] == 42
    assert "hi" in r["stdout"] and "there" in r["stdout"]


def test_helpers_world_pos_and_pads():
    r = ev(FIXTURE, "result = {'pads': len(fp_pads('U1')), 'pos': world_pos('U1','1')}")
    assert r["success"]
    assert r["result"]["pads"] >= 1
    assert isinstance(r["result"]["pos"], list) and len(r["result"]["pos"]) == 2


def test_helper_cluster_of_is_list_or_none():
    r = ev(FIXTURE, "p = fp_pads('U1')[0]['pad']; result = cluster_of('U1', p)")
    assert r["success"]
    assert r["result"] is None or isinstance(r["result"], list)


def test_what_touches_returns_sorted():
    r = ev(FIXTURE, "result = what_touches(0,0,r=500)")  # large r → catch something
    assert r["success"] and isinstance(r["result"], list)
    dists = [h["dist"] for h in r["result"]]
    assert dists == sorted(dists)


def test_ctx_persists_across_calls():
    ev(FIXTURE, "ctx['n'] = 123")
    r = ev(FIXTURE, "result = ctx.get('n')")
    assert r["success"] and r["result"] == 123


# --- error paths -----------------------------------------------------------
def test_exception_in_code():
    r = ev(FIXTURE, "result = 1/0")
    assert r["success"] is False
    assert "ZeroDivisionError" in r["error"]
    assert "traceback" in r


def test_empty_code():
    r = ev(FIXTURE, "   ")
    assert r["success"] is False and "empty" in r["error"]


def test_missing_file():
    r = ev("/nope/none.kicad_pcb", "result = 1")
    assert r["success"] is False and "not found" in r["error"].lower()


# --- caching / invalidation / read-only ------------------------------------
def test_mtime_invalidation(tmp_path):
    p = tmp_path / "b.kicad_pcb"
    shutil.copy(FIXTURE, p)
    r1 = ev(str(p), "result = 1")
    assert r1["cache_hit"] is False
    r2 = ev(str(p), "result = 1")
    assert r2["cache_hit"] is True
    time.sleep(0.01)
    p.write_bytes(p.read_bytes())          # rewrite → new mtime
    r3 = ev(str(p), "result = 1")
    assert r3["cache_hit"] is False        # reloaded after external change


def test_mutation_then_read_recovers_same_session(tmp_path):
    """Regression: a what-if mutation poisons the pcbnew interpreter; the
    daemon must recycle so a SUBSEQUENT read in the SAME session works."""
    p = tmp_path / "b.kicad_pcb"
    shutil.copy(FIXTURE, p)
    r1 = ev(str(p), "result = unconnected()")
    assert r1["success"]
    rm = ev(str(p), "t = next((x for x in board.GetTracks() if x.GetClass()=='PCB_VIA'), None)\n"
                    "board.Remove(t) if t else None; board.BuildConnectivity(); result = 'mut'")
    assert rm["success"] and rm.get("mutated") is True
    # next read must succeed (fresh reload after recycle), not SwigPyObject-fail
    r2 = ev(str(p), "result = len(fp_pads('U1'))")
    assert r2["success"] and isinstance(r2["result"], int)
    r3 = ev(str(p), "result = unconnected()")
    assert r3["success"] and r3["cache_hit"] is True   # warm again


def test_whatif_does_not_write_disk(tmp_path):
    p = tmp_path / "b.kicad_pcb"
    shutil.copy(FIXTURE, p)
    before = p.read_bytes()
    r = ev(str(p), "board.GetTracks()[0] and board.Remove(board.GetTracks()[0]); "
                   "board.BuildConnectivity(); result = 'mutated'")
    assert r["success"] and r["result"] == "mutated"
    assert p.read_bytes() == before        # in-memory only, disk untouched


# --- truncation ------------------------------------------------------------
def test_result_truncation():
    r = ev(FIXTURE, "result = list(range(100000))", max_chars=2000)
    assert r["success"] and r["result_truncated"] is True


# --- session mgmt ----------------------------------------------------------
def test_status_and_reset():
    ev(FIXTURE, "result = 1")
    st = _DAEMON.request({"op": "status"}, 10.0)
    assert st["ok"] and any(c["path"].endswith(".kicad_pcb") for c in st["cached"])
    rs = _DAEMON.request({"op": "reset"}, 10.0)
    assert rs["ok"]
    st2 = _DAEMON.request({"op": "status"}, 10.0)
    assert st2["cached"] == []
    # cache cleared → next eval reloads
    r = ev(FIXTURE, "result = 1")
    assert r["cache_hit"] is False


# --- timeout + recycle -----------------------------------------------------
def test_timeout_recycles_and_recovers():
    r = ev(FIXTURE, "import time as _t; _t.sleep(3); result = 1", timeout_s=0.7)
    assert r["success"] is False and "timed out" in r["error"]
    # daemon was killed; a fresh eval must still work (respawn)
    r2 = ev(FIXTURE, "result = 'alive'")
    assert r2["success"] and r2["result"] == "alive"


# --- real routing analysis patterns (must run error-free) ------------------
def test_pattern_clearance_scan():
    """A path-clearance scan like the stub-placement work."""
    code = """
import math
worst = 9.0
for i in range(21):
    x = 140 + i*0.5
    h = nearest_copper(x, 100.0)
    if h and h['dist'] < worst:
        worst = h['dist']
result = {'worst_clearance': round(worst,3)}
"""
    r = ev(FIXTURE, code)
    assert r["success"] and "worst_clearance" in r["result"]


def test_pattern_whatif_remove_via_connectivity():
    """The load-bearing check pattern: remove nearest via, recompute."""
    code = """
conn = board.GetConnectivity()
before = conn.GetUnconnectedCount(False)
via = next((t for t in board.GetTracks() if t.GetClass()=='PCB_VIA'), None)
if via is not None:
    board.Remove(via); board.BuildConnectivity()
    after = board.GetConnectivity().GetUnconnectedCount(False)
    result = {'had_via': True, 'before': before, 'after': after}
else:
    result = {'had_via': False}
"""
    r = ev(FIXTURE, code)
    assert r["success"] and "had_via" in r["result"]
