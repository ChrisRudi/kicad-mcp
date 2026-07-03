# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the parsed-extraction cache in pcb_tools._extract_all.

The read tools (list_pcb_footprints / analyze_pcb_nets / find_tracks_by_net)
all funnel through _extract_all; a typical "look at this board" flow calls
several back-to-back on the same unchanged file. The cache must:
  * serve a repeat read of an unchanged board without re-parsing,
  * reload when the on-disk fingerprint (mtime_ns, size) changes,
  * bound its size (LRU),
  * never wedge on a missing file.
"""

from __future__ import annotations

import asyncio
import os

import pytest

import kicad_mcp.tools.pcb_tools as pcb_tools


@pytest.fixture(autouse=True)
def _clear_cache():
    with pcb_tools._EXTRACT_LOCK:
        pcb_tools._EXTRACT_CACHE.clear()
        pcb_tools._EXTRACT_ORDER.clear()
    yield
    with pcb_tools._EXTRACT_LOCK:
        pcb_tools._EXTRACT_CACHE.clear()
        pcb_tools._EXTRACT_ORDER.clear()


def _make_board(tmp_path, name="b.kicad_pcb", body="(kicad_pcb)"):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def test_repeat_read_hits_cache(tmp_path, monkeypatch):
    board = _make_board(tmp_path)
    calls = {"n": 0}

    def _counting(path):
        calls["n"] += 1
        return {"backend": "fake", "footprints": [], "nets": [], "tracks": [],
                "vias": [], "dimensions": {}, "zones": 0}

    monkeypatch.setattr(pcb_tools, "_extract_all_uncached", _counting)

    a = pcb_tools._extract_all(board)
    b = pcb_tools._extract_all(board)
    assert calls["n"] == 1          # second call served from cache
    assert a is b                   # same shared (read-only) instance


def test_fingerprint_change_reloads(tmp_path, monkeypatch):
    board = _make_board(tmp_path)
    calls = {"n": 0}
    monkeypatch.setattr(pcb_tools, "_extract_all_uncached",
                        lambda p: {"n": (calls.__setitem__("n", calls["n"] + 1) or calls["n"])})

    pcb_tools._extract_all(board)
    assert calls["n"] == 1

    # Change size AND bump mtime so the fingerprint differs even on coarse clocks.
    with open(board, "a", encoding="utf-8") as fh:
        fh.write("  ")
    st = os.stat(board)
    os.utime(board, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    pcb_tools._extract_all(board)
    assert calls["n"] == 2          # fingerprint changed → fresh parse


def test_lru_eviction(tmp_path, monkeypatch):
    monkeypatch.setattr(pcb_tools, "_extract_all_uncached",
                        lambda p: {"path": p})
    boards = [_make_board(tmp_path, f"b{i}.kicad_pcb") for i in range(pcb_tools._EXTRACT_MAX + 2)]
    for b in boards:
        pcb_tools._extract_all(b)
    assert len(pcb_tools._EXTRACT_CACHE) == pcb_tools._EXTRACT_MAX
    # the oldest board was evicted, the newest retained
    assert os.path.realpath(boards[0]) not in pcb_tools._EXTRACT_CACHE
    assert os.path.realpath(boards[-1]) in pcb_tools._EXTRACT_CACHE


def test_missing_file_falls_through(tmp_path, monkeypatch):
    seen = {"path": None}

    def _uncached(path):
        seen["path"] = path
        raise FileNotFoundError(path)  # what the real sexpr parse would raise

    monkeypatch.setattr(pcb_tools, "_extract_all_uncached", _uncached)
    with pytest.raises(FileNotFoundError):
        pcb_tools._extract_all(str(tmp_path / "nope.kicad_pcb"))
    assert seen["path"] is not None   # reached the real parser, no cache wedge


def test_read_tools_offload_and_cache(tmp_path, monkeypatch):
    """The async read tools await to_thread and share the cache: two calls on
    the same board parse once."""
    from mcp.server.fastmcp import FastMCP

    board = _make_board(tmp_path)
    calls = {"n": 0}

    def _counting(path):
        calls["n"] += 1
        return {"backend": "fake", "footprints": [
                    {"reference": "R1", "value": "10k", "footprint_id": "x",
                     "layer": "F.Cu", "position": {}, "rotation": 0, "pad_count": 2}],
                "nets": [], "tracks": [], "vias": [], "dimensions": {}, "zones": 0}

    monkeypatch.setattr(pcb_tools, "_extract_all_uncached", _counting)

    registered = {}
    mcp = FastMCP("test")
    orig_tool = mcp.tool

    def _capture(*a, **k):
        deco = orig_tool(*a, **k)
        def wrap(fn):
            registered[fn.__name__] = fn
            return deco(fn)
        return wrap

    monkeypatch.setattr(mcp, "tool", _capture)
    pcb_tools.register_pcb_tools(mcp)

    list_fps = registered["list_pcb_footprints"]
    r1 = asyncio.run(list_fps(board))
    r2 = asyncio.run(list_fps(board))
    assert r1["success"] and r1["count"] == 1
    assert r2["success"]
    assert calls["n"] == 1            # cached across the two tool calls
