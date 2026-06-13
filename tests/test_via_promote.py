# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``via_promote`` (blind/buried → through optimiser)."""

from __future__ import annotations

import importlib.util

import pytest

from kicad_mcp.tools.via_promote_tools import (
    _promote_layers_text,
    _resize_via_text,
    _retype_via_text,
    via_promote_impl,
)
from kicad_mcp.tools import via_promote_worker as worker

_HAS_PCBNEW = importlib.util.find_spec("pcbnew") is not None
_needs_pcbnew = pytest.mark.skipif(
    not _HAS_PCBNEW, reason="pcbnew not importable (run under KiCad Python)")

# Valid UUIDs (KiCad regenerates invalid ones on load).
U_BLOCKED = "22222222-2222-2222-2222-222222222222"
U_FREE = "33333333-3333-3333-3333-333333333333"
U_POFV = "55555555-5555-5555-5555-555555555555"


def _board():
    """4-layer board: one buried In1↔In2 via under a foreign-net F.Cu pad
    (blocked), one in open space (promotable)."""
    return (
        '(kicad_pcb (version 20240108) (generator "pcbnew") '
        '(generator_version "9.0")\n'
        ' (general (thickness 1.6)) (paper "A4")\n'
        ' (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "In2.Cu" signal) '
        '(31 "B.Cu" signal) (44 "Edge.Cuts" user))\n'
        ' (net 0 "") (net 1 "NET_A") (net 2 "NET_B")\n'
        ' (footprint "lib:pad" (layer "F.Cu") (at 150 105) '
        '(uuid "11111111-1111-1111-1111-111111111111")\n'
        '   (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
        '(net 2 "NET_B")))\n'
        f' (via blind (at 150 105) (size 0.45) (drill 0.2) '
        f'(layers "In1.Cu" "In2.Cu") (net 1) (uuid "{U_BLOCKED}"))\n'
        f' (via blind (at 170 105) (size 0.45) (drill 0.2) '
        f'(layers "In1.Cu" "In2.Cu") (net 1) (uuid "{U_FREE}"))\n'
        ')\n'
    )


@pytest.fixture
def pcb_path(tmp_path):
    p = tmp_path / "vp.kicad_pcb"
    p.write_text(_board(), encoding="utf-8")
    return str(p)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Text-patch helper (pure, no pcbnew)
# ---------------------------------------------------------------------------


class TestPromoteLayersText:
    def test_only_named_via_changes(self):
        pcb = (
            '(kicad_pcb '
            '(via (at 1 2) (size 0.45) (drill 0.2) '
            '(layers "In1.Cu" "In2.Cu") (net 3) (uuid "aaa")) '
            '(via (at 3 4) (size 0.45) (drill 0.2) '
            '(layers "In1.Cu" "In2.Cu") (net 3) (uuid "bbb")) )'
        )
        out, n = _promote_layers_text(pcb, ["aaa"])
        assert n == 1
        assert '(layers "F.Cu" "B.Cu")' in out
        # bbb untouched
        assert out.count('(layers "In1.Cu" "In2.Cu")') == 1

    def test_strips_blind_buried_type_token(self):
        # The promote must remove the blind/buried/micro token too — KiCad
        # treats it as authoritative over (layers), so leaving it makes the
        # via still blind/buried at fab despite the F/B rewrite.
        pcb = (
            '(kicad_pcb '
            '(via blind (at 1 2) (size 0.45) (drill 0.2) '
            '(layers "F.Cu" "In2.Cu") (net 3) (uuid "aaa")) '
            '(via buried (at 3 4) (size 0.45) (drill 0.2) '
            '(layers "In1.Cu" "In3.Cu") (net 3) (uuid "bbb")) )'
        )
        out, n = _promote_layers_text(pcb, ["aaa", "bbb"])
        assert n == 2
        assert "(via blind" not in out and "(via buried" not in out
        assert out.count('(layers "F.Cu" "B.Cu")') == 2
        assert out.count("(via (at") == 2

    def test_through_via_promote_is_noop_on_token(self):
        # A via with no type token stays token-less (no spurious change).
        pcb = '(kicad_pcb (via (at 1 2) (layers "In1.Cu" "In2.Cu") (uuid "x")))'
        out, n = _promote_layers_text(pcb, ["x"])
        assert n == 1
        assert "(via (at 1 2)" in out and '(layers "F.Cu" "B.Cu")' in out

    def test_empty_set_is_noop(self):
        pcb = '(kicad_pcb (via (layers "In1.Cu" "In2.Cu") (uuid "x")))'
        out, n = _promote_layers_text(pcb, [])
        assert n == 0 and out == pcb

    def test_absent_uuid_noop(self):
        pcb = '(kicad_pcb (via (layers "In1.Cu" "In2.Cu") (uuid "x")))'
        out, n = _promote_layers_text(pcb, ["not-here"])
        assert n == 0 and out == pcb


# ---------------------------------------------------------------------------
# Worker analysis (needs pcbnew)
# ---------------------------------------------------------------------------


@_needs_pcbnew
class TestWorkerAnalysis:
    def test_detects_promotable_and_blocked(self, pcb_path):
        r = worker.run(pcb_path, 0.2)
        assert r["success"] is True
        assert r["zones_filled"] is True
        assert r["total_vias"] == 2
        assert r["promotable_count"] == 1
        assert r["blocked_count"] == 1
        assert r["promotable"][0]["uuid"] == U_FREE
        assert r["promotable"][0]["adds_layers"] == ["F.Cu", "B.Cu"]
        blk = r["blocked"][0]
        assert blk["uuid"] == U_BLOCKED
        assert blk["blocked_on"][0]["layer"] == "F.Cu"
        assert blk["blocked_on"][0]["blocker_net"] == "NET_B"

    def test_tighter_clearance_same_result(self, pcb_path):
        # The blocked via physically overlaps the pad, so even clearance 0
        # blocks it; the free via is far away.
        r = worker.run(pcb_path, 0.0)
        assert r["promotable_count"] == 1 and r["blocked_count"] == 1


# ---------------------------------------------------------------------------
# Full impl: spawn worker + apply text-patch (needs pcbnew)
# ---------------------------------------------------------------------------


@_needs_pcbnew
class TestImplApply:
    def test_dry_run_reports_without_writing(self, pcb_path):
        before = _read(pcb_path)
        out = via_promote_impl(pcb_path, clearance_mm=0.2, dry_run=True)
        assert out["success"] is True
        assert out["promotable_count"] == 1
        assert out["dry_run"] is True
        assert out["wrote"] is False
        assert out["applied"] == 0
        assert _read(pcb_path) == before

    def test_apply_promotes_only_free_via(self, pcb_path):
        out = via_promote_impl(pcb_path, clearance_mm=0.2, dry_run=False)
        assert out["success"] is True
        assert out["wrote"] is True
        assert out["applied"] == 1
        text = _read(pcb_path)
        # The free via's block is now F.Cu/B.Cu; the blocked via still
        # In1/In2. Exactly one (layers "In1.Cu" "In2.Cu") remains.
        assert text.count('(layers "F.Cu" "B.Cu")') == 1
        assert text.count('(layers "In1.Cu" "In2.Cu")') == 1


# ---------------------------------------------------------------------------
# Via-in-pad (POFV) classification + tier summary  (needs pcbnew)
# ---------------------------------------------------------------------------


def _board_pofv():
    """4-layer board: a buried In1↔In2 via sitting inside its OWN-net F.Cu
    SMD pad (→ needs POFV when promoted), plus one clean free via."""
    return (
        '(kicad_pcb (version 20240108) (generator "pcbnew") '
        '(generator_version "9.0")\n'
        ' (general (thickness 1.6)) (paper "A4")\n'
        ' (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "In2.Cu" signal) '
        '(31 "B.Cu" signal) (44 "Edge.Cuts" user))\n'
        ' (net 0 "") (net 1 "NET_A")\n'
        ' (footprint "lib:pad" (layer "F.Cu") (at 150 105) '
        '(uuid "44444444-4444-4444-4444-444444444444")\n'
        '   (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
        '(net 1 "NET_A")))\n'
        f' (via blind (at 150 105) (size 0.45) (drill 0.2) '
        f'(layers "In1.Cu" "In2.Cu") (net 1) (uuid "{U_POFV}"))\n'
        f' (via blind (at 170 105) (size 0.45) (drill 0.2) '
        f'(layers "In1.Cu" "In2.Cu") (net 1) (uuid "{U_FREE}"))\n'
        ')\n'
    )


@pytest.fixture
def pofv_path(tmp_path):
    p = tmp_path / "vp_pofv.kicad_pcb"
    p.write_text(_board_pofv(), encoding="utf-8")
    return str(p)


@_needs_pcbnew
class TestPofvAndTier:
    def test_own_net_smd_pad_is_needs_pofv(self, pofv_path):
        r = worker.run(pofv_path, 0.2)
        assert r["success"] is True
        # one clean promotable (free via), one needs_pofv (sits in own pad)
        assert r["promotable_count"] == 1
        assert r["needs_pofv_count"] == 1
        assert r["blocked_count"] == 0
        assert r["promotable"][0]["uuid"] == U_FREE
        pofv = r["needs_pofv"][0]
        assert pofv["uuid"] == U_POFV
        assert pofv["in_pads"][0]["layer"] == "F.Cu"
        assert pofv["in_pads"][0]["pad"].endswith(".1")

    def test_tier_summary_counts(self, pofv_path):
        r = worker.run(pofv_path, 0.2)
        assert r["tier_before"]["blind_buried_vias"] == 2
        assert r["tier_before"]["blind_buried_types"] == 1
        # promoting only the clean one leaves 1 blind/buried
        assert r["tier_after_promotable"]["blind_buried_vias"] == 1
        # promoting with POFV clears all blind/buried
        assert r["tier_after_with_pofv"]["blind_buried_vias"] == 0
        assert r["tier_after_with_pofv"]["blind_buried_types"] == 0

    def test_apply_with_pofv_promotes_both(self, pofv_path):
        out = via_promote_impl(pofv_path, dry_run=False, pofv_ok=True)
        assert out["success"] is True and out["applied"] == 2
        text = _read(pofv_path)
        assert text.count('(layers "F.Cu" "B.Cu")') == 2
        assert '(layers "In1.Cu" "In2.Cu")' not in text

    def test_apply_without_pofv_skips_in_pad_via(self, pofv_path):
        out = via_promote_impl(pofv_path, dry_run=False, pofv_ok=False)
        assert out["success"] is True and out["applied"] == 1
        text = _read(pofv_path)
        # only the clean free via promoted; the in-pad via stays blind
        assert text.count('(layers "F.Cu" "B.Cu")') == 1
        assert text.count('(layers "In1.Cu" "In2.Cu")') == 1


class TestRetype:
    """Pure-text via-type retype (no pcbnew)."""

    _TWO_VIAS = (
        '(via micro\n\t\t(at 1 1)\n\t\t(size 0.4) (drill 0.2)'
        ' (layers "F.Cu" "In1.Cu")\n\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000001"))\n'
        '(via\n\t\t(at 2 2)\n\t\t(size 0.45) (drill 0.2)'
        ' (layers "F.Cu" "B.Cu")\n\t\t(uuid "bbbbbbbb-0000-0000-0000-000000000002"))\n'
    )

    def test_micro_to_blind_only_targeted(self):
        new, n = _retype_via_text(
            self._TWO_VIAS, ["aaaaaaaa-0000-0000-0000-000000000001"], "blind")
        assert n == 1
        assert '(via blind\n' in new
        assert '(via micro' not in new
        # the through via is untouched, layers/size/drill intact everywhere
        assert new.count('(layers "F.Cu" "In1.Cu")') == 1
        assert new.count('(layers "F.Cu" "B.Cu")') == 1
        assert '(via\n\t\t(at 2 2)' in new

    def test_same_length_when_micro_to_blind(self):
        new, _ = _retype_via_text(
            self._TWO_VIAS, ["aaaaaaaa-0000-0000-0000-000000000001"], "blind")
        assert len(new) == len(self._TWO_VIAS)  # 'micro' and 'blind' are 5 chars

    def test_unknown_uuid_is_noop(self):
        new, n = _retype_via_text(self._TWO_VIAS, ["does-not-exist"], "blind")
        assert n == 0 and new == self._TWO_VIAS

    def test_invalid_type_rejected(self):
        new, n = _retype_via_text(
            self._TWO_VIAS, ["aaaaaaaa-0000-0000-0000-000000000001"], "bogus")
        assert n == 0 and new == self._TWO_VIAS

    def test_through_drops_token(self):
        new, n = _retype_via_text(
            self._TWO_VIAS, ["aaaaaaaa-0000-0000-0000-000000000001"], "through")
        assert n == 1
        assert '(via\n\t\t(at 1 1)' in new  # token gone
        assert '(via micro' not in new


class TestResize:
    """Pure-text via size/drill standardisation (no pcbnew)."""

    _MIX = (
        '(via micro\n\t\t(at 1 1)\n\t\t(size 0.45) (drill 0.2)'
        ' (layers "F.Cu" "In1.Cu")\n\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000001"))\n'
        '(via\n\t\t(at 2 2)\n\t\t(size 0.6) (drill 0.3)'
        ' (layers "F.Cu" "B.Cu")\n\t\t(uuid "bbbbbbbb-0000-0000-0000-000000000002"))\n'
        '(via\n\t\t(at 3 3)\n\t\t(size 0.4) (drill 0.2)'
        ' (layers "F.Cu" "B.Cu")\n\t\t(uuid "cccccccc-0000-0000-0000-000000000003"))\n'
    )

    def test_all_vias_standardised(self):
        new, n = _resize_via_text(self._MIX, 0.4, 0.2, None)
        assert n == 2  # the 0.45 and 0.6 change; the already-0.4 is a no-op
        assert new.count('(size 0.4)') == 3
        assert '(size 0.45)' not in new and '(size 0.6)' not in new
        assert new.count('(drill 0.2)') == 3 and '(drill 0.3)' not in new
        # type token + layers untouched
        assert '(via micro' in new
        assert new.count('(layers "F.Cu" "In1.Cu")') == 1

    def test_size_only_keeps_drill(self):
        new, n = _resize_via_text(self._MIX, 0.4, None, None)
        assert n == 2
        assert '(drill 0.3)' in new  # drill left alone when drill=None

    def test_targeted_by_uuid(self):
        new, n = _resize_via_text(
            self._MIX, 0.4, 0.2, ["bbbbbbbb-0000-0000-0000-000000000002"])
        assert n == 1
        assert '(size 0.45)' in new  # the micro via untouched

    def test_noop_when_already_target(self):
        new, n = _resize_via_text(
            self._MIX, 0.4, 0.2, ["cccccccc-0000-0000-0000-000000000003"])
        assert n == 0 and new == self._MIX


# ---------------------------------------------------------------------------
# Warm daemon: board cache (read-only analysis reuses the loaded+filled board)
# ---------------------------------------------------------------------------
@_needs_pcbnew
class TestWarmDaemon:
    def test_second_analysis_is_cache_hit(self, pcb_path):
        from kicad_mcp.tools.via_promote_tools import _DAEMON
        _DAEMON.request({"op": "reset"}, timeout=10.0)
        r1 = via_promote_impl(pcb_path, dry_run=True)
        r2 = via_promote_impl(pcb_path, dry_run=True)
        assert r1["success"] and r2["success"]
        # First analysis after a reset loads cold; the immediate repeat is warm.
        assert r1["cache_hit"] is False
        assert r2["cache_hit"] is True
        assert r1["promotable_count"] == r2["promotable_count"] == 1

    def test_apply_invalidates_cache_on_mtime_change(self, pcb_path):
        from kicad_mcp.tools.via_promote_tools import _DAEMON
        _DAEMON.request({"op": "reset"}, timeout=10.0)
        via_promote_impl(pcb_path, dry_run=True)             # warm the cache
        via_promote_impl(pcb_path, dry_run=False)            # apply → rewrites the file
        after = via_promote_impl(pcb_path, dry_run=True)
        assert after["success"] is True
        # The patched file has a new mtime → the daemon reloads (cache miss),
        # and the promoted via is now through, so nothing remains promotable.
        assert after["cache_hit"] is False
        assert after["promotable_count"] == 0

    def test_status_lists_cached_board(self, pcb_path):
        from kicad_mcp.tools.via_promote_tools import _DAEMON
        _DAEMON.request({"op": "reset"}, timeout=10.0)
        via_promote_impl(pcb_path, dry_run=True)
        st = _DAEMON.request({"op": "status"}, timeout=10.0)
        assert st["ok"] is True
        assert isinstance(st["loads"], int) and st["loads"] >= 1
        assert any(pcb_path in c["path"] for c in st["cached"])
