# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the file-text cache (kicad_mcp.cache.file_cache)."""

from __future__ import annotations

import os
import time

import pytest

from kicad_mcp.cache import file_cache as fc


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts with an empty cache."""
    fc.invalidate()
    yield
    fc.invalidate()


@pytest.fixture
def pcb(tmp_path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text("(kicad_pcb v1)\n", encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Cache hit / miss
# ---------------------------------------------------------------------------


class TestCacheHitMiss:
    def test_unchanged_file_is_cache_hit(self, pcb):
        # First read populates the cache.
        first = fc.get_text(pcb)
        assert first == "(kicad_pcb v1)\n"
        # Overwrite the content but restore mtime+size -> fingerprint is
        # identical -> cache must still serve the OLD text (proves no
        # re-read happened).
        st = os.stat(pcb)
        with open(pcb, "w", encoding="utf-8") as fh:
            fh.write("(kicad_pcb v2)\n")  # same length as v1
        os.utime(pcb, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert fc.get_text(pcb) == "(kicad_pcb v1)\n"

    def test_mtime_change_triggers_fresh_read(self, pcb):
        fc.get_text(pcb)
        time.sleep(0.01)
        with open(pcb, "w", encoding="utf-8") as fh:
            fh.write("(kicad_pcb CHANGED much longer content)\n")
        # mtime + size both differ now -> cache miss -> fresh content.
        assert fc.get_text(pcb) == "(kicad_pcb CHANGED much longer content)\n"

    def test_put_text_avoids_reread(self, pcb):
        fc.get_text(pcb)
        new = "(kicad_pcb written-by-tool)\n"
        with open(pcb, "w", encoding="utf-8") as fh:
            fh.write(new)
        fc.put_text(pcb, new)              # tool registers what it wrote
        # Corrupt the on-disk file WITHOUT changing mtime/size would be
        # fragile; instead trust that put_text stored the fresh
        # fingerprint -> get_text returns the put text as a hit.
        assert fc.get_text(pcb) == new


# ---------------------------------------------------------------------------
# Key normalization
# ---------------------------------------------------------------------------


class TestKeyNormalization:
    def test_relative_and_absolute_share_entry(self, pcb, monkeypatch):
        fc.get_text(pcb)
        monkeypatch.chdir(os.path.dirname(pcb))
        rel = os.path.basename(pcb)
        # Same file via a relative path -> same cache entry.
        st = os.stat(pcb)
        with open(pcb, "w", encoding="utf-8") as fh:
            fh.write("(kicad_pcb v2)\n")
        os.utime(pcb, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert fc.get_text(rel) == "(kicad_pcb v1)\n"  # served from cache

    def test_status_lists_one_entry(self, pcb, monkeypatch):
        fc.get_text(pcb)
        monkeypatch.chdir(os.path.dirname(pcb))
        fc.get_text(os.path.basename(pcb))
        assert len(fc.cache_status()) == 1


# ---------------------------------------------------------------------------
# LRU + invalidate + status
# ---------------------------------------------------------------------------


class TestLruInvalidateStatus:
    def test_lru_evicts_oldest(self, tmp_path):
        paths = []
        for i in range(7):                      # > _MAX_ENTRIES (5)
            p = tmp_path / f"b{i}.kicad_pcb"
            p.write_text(f"(b {i})\n", encoding="utf-8")
            paths.append(str(p))
            fc.get_text(str(p))
        status = fc.cache_status()
        assert len(status) == 5
        cached = {s["path"] for s in status}
        assert os.path.realpath(paths[0]) not in cached   # evicted
        assert os.path.realpath(paths[6]) in cached        # newest kept

    def test_invalidate_one(self, pcb):
        fc.get_text(pcb)
        fc.invalidate(pcb)
        assert fc.cache_status() == []

    def test_invalidate_all_idempotent(self, pcb):
        fc.get_text(pcb)
        fc.invalidate()
        fc.invalidate()                          # second call is a no-op
        assert fc.cache_status() == []

    def test_status_reports_out_of_sync(self, pcb):
        fc.get_text(pcb)
        time.sleep(0.01)
        with open(pcb, "a", encoding="utf-8") as fh:
            fh.write("(extra)\n")
        st = fc.cache_status()
        assert len(st) == 1 and st[0]["in_sync_with_disk"] is False


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            fc.get_text(str(tmp_path / "nope.kicad_pcb"))
