# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ipc_markup_to_tracks: markup graphics (User.9) → copper tracks.

kipy / a running KiCad are mocked. The tool reuses real layer-name resolution
(_layer_to_enum) but the board + shapes + Track/ArcTrack are fakes so the
mapping, commit-wrapping, filtering and counts are exercised headless.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

import kicad_mcp.tools.ipc_markup_tools as mod
from kicad_mcp.tools.ipc_tools import _layer_to_enum


# --- fake kipy board_types (Track/ArcTrack with settable attrs) --------------

class _Track:
    def __init__(self):
        self.start = self.end = self.width = self.layer = self.net = None


class _ArcTrack:
    def __init__(self):
        self.start = self.mid = self.end = self.width = self.layer = None
        self.net = None


@pytest.fixture
def fake_kipy(monkeypatch):
    bt = types.ModuleType("kipy.board_types")
    bt.Track = _Track
    bt.ArcTrack = _ArcTrack
    kipy = sys.modules.get("kipy") or types.ModuleType("kipy")
    monkeypatch.setitem(sys.modules, "kipy", kipy)
    monkeypatch.setitem(sys.modules, "kipy.board_types", bt)
    return bt


def _vec(x_nm, y_nm):
    return SimpleNamespace(x=x_nm, y=y_nm)


def _seg(layer, x1, y1, x2, y2):
    s = SimpleNamespace(layer=layer, start=_vec(x1, y1), end=_vec(x2, y2))
    s.__class__.__name__ = "BoardSegment"
    return s


class _Seg:
    __slots__ = ("layer", "start", "end")

    def __init__(self, layer, x1, y1, x2, y2):
        self.layer, self.start, self.end = layer, _vec(x1, y1), _vec(x2, y2)


class _Arc:
    __slots__ = ("layer", "start", "mid", "end")

    def __init__(self, layer):
        self.layer = layer
        self.start, self.mid, self.end = _vec(0, 0), _vec(5, 5), _vec(10, 0)


class _Poly:
    __slots__ = ("layer",)

    def __init__(self, layer):
        self.layer = layer


# give the fakes the class names the tool dispatches on
_Seg.__name__ = "BoardSegment"
_Arc.__name__ = "BoardArc"
_Poly.__name__ = "BoardPolygon"


class _Board:
    def __init__(self, shapes):
        self._shapes = shapes
        self.created = None
        self.commits = []
        self.dropped = 0

    def get_shapes(self):
        return list(self._shapes)

    def begin_commit(self):
        return object()

    def create_items(self, items):
        self.created = list(items)
        return self.created

    def push_commit(self, _commit, msg):
        self.commits.append(msg)

    def drop_commit(self, _commit):
        self.dropped += 1


def _patch_board(monkeypatch, board):
    monkeypatch.setattr(mod, "_connect_kicad", lambda: (object(), board),
                        raising=False)
    # _connect_kicad is imported inside the tool from ipc_tools; patch there
    import kicad_mcp.tools.ipc_tools as ipct
    monkeypatch.setattr(ipct, "_connect_kicad", lambda: (object(), board))


def _call(server, **kw):
    import asyncio
    result = asyncio.run(server.call_tool("ipc_markup_to_tracks", kw))
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return result


@pytest.fixture
def server(fake_kipy):
    s = FastMCP("test")
    mod.register_ipc_markup_tools(s)
    return s


USER9 = _layer_to_enum("User.9")
FCU = _layer_to_enum("F.Cu")


class TestMapping:
    def test_segment_becomes_track_netless_on_target(self, server, monkeypatch):
        board = _Board([_Seg(USER9, 0, 0, 1_000_000, 0)])
        _patch_board(monkeypatch, board)
        r = _call(server, width_mm=0.3)
        assert r["success"] and r["created"] == 1
        assert r["by_type"] == {"segments": 1, "arcs": 0}
        track = board.created[0]
        assert track.layer == FCU and track.net is None      # netless
        assert track.width == 300000                          # 0.3 mm → nm
        assert track.start.x == 0 and track.end.x == 1_000_000  # nm preserved
        assert board.commits == ["kicad-mcp ipc_markup_to_tracks"]  # 1 undo

    def test_arc_becomes_arctrack(self, server, monkeypatch):
        board = _Board([_Arc(USER9)])
        _patch_board(monkeypatch, board)
        r = _call(server, width_mm=0.25)
        assert r["created"] == 1 and r["by_type"]["arcs"] == 1
        assert isinstance(board.created[0], _ArcTrack)

    def test_other_layer_ignored(self, server, monkeypatch):
        board = _Board([_Seg(FCU, 0, 0, 1, 0)])  # not on the source layer
        _patch_board(monkeypatch, board)
        r = _call(server, source_layer="User.9")
        assert r["created"] == 0 and "note" in r

    def test_polygon_skipped_counted(self, server, monkeypatch):
        board = _Board([_Poly(USER9), _Seg(USER9, 0, 0, 1, 0)])
        _patch_board(monkeypatch, board)
        r = _call(server)
        assert r["created"] == 1 and r["skipped"] == 1


class TestGuards:
    def test_dry_run_creates_nothing(self, server, monkeypatch):
        board = _Board([_Seg(USER9, 0, 0, 1, 0)])
        _patch_board(monkeypatch, board)
        r = _call(server, dry_run=True)
        assert r["dry_run"] and r["created"] == 1
        assert board.created is None and board.commits == []

    def test_unknown_source_layer(self, server, monkeypatch):
        board = _Board([])
        _patch_board(monkeypatch, board)
        r = _call(server, source_layer="Nope.123")
        assert r["success"] is False and "source layer" in r["error"]

    def test_bad_width(self, server, monkeypatch):
        board = _Board([])
        _patch_board(monkeypatch, board)
        r = _call(server, width_mm=0)
        assert r["success"] is False and "width_mm" in r["error"]
