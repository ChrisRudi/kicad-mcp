# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``center_item_clearance`` (live-editor via centering).

kipy / a running KiCad are mocked: ``_require_editor`` / ``_connect_kicad`` are
patched to hand the tool a fake board, so the obstacle build, the solver
dispatch and the via+stub drag are what's under test. The actual point move
uses real ``kipy.geometry.Vector2`` (installed in the test env), exactly like
the sibling ``ipc_move_items`` tests.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mcp.server.fastmcp import FastMCP

import kicad_mcp.tools.ipc_interact_tools as mod
from kicad_mcp.tools.ipc_interact_tools import register_ipc_interact_tools


# --- fake kipy item types (class name drives _friendly_type) ----------------

class Via(SimpleNamespace):
    pass


class Track(SimpleNamespace):
    pass


class FootprintInstance(SimpleNamespace):
    pass


def _vec(x_nm, y_nm):
    return SimpleNamespace(x=int(x_nm), y=int(y_nm))


def _kiid(val):
    return SimpleNamespace(value=val)


def _net(name):
    return SimpleNamespace(name=name, code=1)


def _via(uuid, net, x_mm, y_mm, dia_nm=600_000, layer="F.Cu"):
    return Via(id=_kiid(uuid), net=_net(net), layer=layer,
              position=_vec(x_mm * 1_000_000, y_mm * 1_000_000), diameter=dia_nm)


def _track(uuid, net, x1, y1, x2, y2, w_mm=0.2, layer="In1.Cu"):
    return Track(id=_kiid(uuid), net=_net(net), layer=layer,
                 start=_vec(x1 * 1_000_000, y1 * 1_000_000),
                 end=_vec(x2 * 1_000_000, y2 * 1_000_000),
                 width=int(w_mm * 1_000_000))


class FakeBoard:
    """Minimal board exposing the getters/commit surface the tool touches."""

    def __init__(self, vias=None, tracks=None, pads=None, footprints=None,
                 selection=None):
        self._vias = vias or []
        self._tracks = tracks or []
        self._pads = pads or []
        self._fps = footprints or []
        self._selection = selection or []
        self.updated: list = []
        self.commits = 0

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)

    def get_pads(self):
        return list(self._pads)

    def get_footprints(self):
        return list(self._fps)

    def get_zones(self):
        return []

    def get_shapes(self):
        return []

    def get_text(self):
        return []

    def get_selection(self):
        return list(self._selection)

    def get_layer_name(self, layer):
        return str(layer)

    def get_item_bounding_box(self, item):
        pos = getattr(item, "position", None) or _vec(0, 0)
        return SimpleNamespace(
            pos=_vec(pos.x - 250_000, pos.y - 250_000),
            size=_vec(500_000, 500_000))

    def get_project(self):
        nc = SimpleNamespace(name="Default", clearance=200_000,
                             via_diameter=600_000, via_drill=300_000)
        return SimpleNamespace(get_net_classes=lambda: [nc])

    def begin_commit(self):
        return object()

    def push_commit(self, commit, msg):
        self.commits += 1

    def update_items(self, items):
        items = items if isinstance(items, (list, tuple)) else [items]
        self.updated.extend(items)
        return list(items)


@pytest.fixture
def server():
    m = FastMCP("test")
    register_ipc_interact_tools(m)
    return m


def _call(server: FastMCP, name: str, **kwargs):
    result = asyncio.run(server.call_tool(name, kwargs))
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return result


@pytest.fixture
def patch_board(monkeypatch):
    def _install(board: FakeBoard):
        monkeypatch.setattr(mod, "_require_editor", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_connect_kicad", lambda: (object(), board))
        return board
    return _install


def _corridor_board():
    """Via off-centre (y=5.0) in a corridor between walls at y=4.5 and y=6.0,
    with a same-net stub coincident with the via centre."""
    own = _via("v1", "SIG", 10.0, 5.0)
    wall1 = _track("w1", "GND", 8, 4.5, 12, 4.5)
    wall2 = _track("w2", "GND", 8, 6.0, 12, 6.0)
    stub = _track("s1", "SIG", 10, 5, 10, 3)
    return FakeBoard(vias=[own], tracks=[wall1, wall2, stub]), own, stub


# --- equalize ----------------------------------------------------------------

class TestEqualize:
    def test_centers_via_and_drags_stub(self, server, patch_board):
        board, own, stub = _corridor_board()
        patch_board(board)
        r = _call(server, "center_item_clearance", uuid="v1",
                  search_radius_mm=2.0, mode="equalize")
        assert r["success"] is True and r["moved"] is True
        # exact corridor midpoint, x unchanged
        assert r["new_position_mm"] == [10.0, 5.25]
        assert r["neighbor_count"] == 2 and r["stubs_followed"] == 1
        # via actually moved on the board
        assert round(own.position.x / 1e6, 3) == 10.0
        assert round(own.position.y / 1e6, 3) == 5.25
        # stub's near end followed, far end anchored
        assert round(stub.start.y / 1e6, 3) == 5.25
        assert round(stub.end.y / 1e6, 3) == 3.0
        # clearances equalised and the tightest grew
        assert abs(r["neighbors"][0]["clearance_after_mm"]
                   - r["neighbors"][1]["clearance_after_mm"]) < 1e-3
        assert r["min_clearance_after_mm"] >= r["min_clearance_before_mm"]
        assert r["required_clearance_mm"] == 0.2 and r["meets_rule"] is True
        assert board.commits == 1

    def test_same_net_copper_is_not_an_obstacle(self, server, patch_board):
        # only a same-net stub nearby → nothing foreign to centre against
        own = _via("v1", "SIG", 10.0, 5.0)
        stub = _track("s1", "SIG", 10, 5, 10, 3)
        board = patch_board(FakeBoard(vias=[own], tracks=[stub]))
        r = _call(server, "center_item_clearance", uuid="v1")
        assert r["success"] is True and r["moved"] is False
        assert r["neighbor_count"] == 0
        assert "no foreign copper" in r["note"].lower()
        assert board.commits == 0


# --- dry_run -----------------------------------------------------------------

class TestDryRun:
    def test_computes_without_moving(self, server, patch_board):
        board, own, stub = _corridor_board()
        patch_board(board)
        r = _call(server, "center_item_clearance", uuid="v1", dry_run=True)
        assert r["success"] is True
        assert r["dry_run"] is True and r["moved"] is False
        assert r["new_position_mm"] == [10.0, 5.25]      # predicted
        # nothing on the board moved
        assert round(own.position.y / 1e6, 3) == 5.0
        assert round(stub.start.y / 1e6, 3) == 5.0
        assert board.commits == 0 and r["stubs_followed"] == 0


# --- maximize ----------------------------------------------------------------

class TestMaximize:
    def test_maximize_also_centers_corridor(self, server, patch_board):
        board, _own, _stub = _corridor_board()
        patch_board(board)
        r = _call(server, "center_item_clearance", uuid="v1", mode="maximize",
                  search_radius_mm=2.0)
        assert r["success"] is True and r["moved"] is True
        assert abs(r["new_position_mm"][1] - 5.25) < 0.05


# --- layer scoping -----------------------------------------------------------

class TestLayers:
    def test_layers_filter_excludes_other_copper(self, server, patch_board):
        # walls are on In1.Cu; restricting the scan to In2.Cu hides them
        board, _own, _stub = _corridor_board()
        patch_board(board)
        r = _call(server, "center_item_clearance", uuid="v1",
                  layers=["In2.Cu"])
        assert r["success"] is True and r["moved"] is False
        assert r["neighbor_count"] == 0


# --- resolution + validation -------------------------------------------------

class TestResolution:
    def test_uses_single_selected_via_when_no_uuid(self, server, patch_board):
        board, own, _stub = _corridor_board()
        board._selection = [own]
        patch_board(board)
        r = _call(server, "center_item_clearance")
        assert r["success"] is True and r["uuid"] == "v1"

    def test_selection_must_be_exactly_one_via(self, server, patch_board):
        board, _own, _stub = _corridor_board()
        board._selection = []
        patch_board(board)
        r = _call(server, "center_item_clearance")
        assert r["success"] is False and "exactly one via" in r["error"]

    def test_uuid_not_found(self, server, patch_board):
        patch_board(FakeBoard(vias=[_via("v1", "SIG", 10, 5)]))
        r = _call(server, "center_item_clearance", uuid="nope")
        assert r["success"] is False and "found" in r["error"].lower()

    def test_non_via_item_rejected(self, server, patch_board):
        patch_board(FakeBoard(tracks=[_track("t1", "GND", 0, 0, 5, 0)]))
        r = _call(server, "center_item_clearance", uuid="t1")
        assert r["success"] is False and "via" in r["error"].lower()

    def test_bad_mode_rejected(self, server, patch_board):
        patch_board(FakeBoard(vias=[_via("v1", "SIG", 10, 5)]))
        r = _call(server, "center_item_clearance", uuid="v1", mode="spin")
        assert r["success"] is False and "mode" in r["error"].lower()

    def test_zero_radius_rejected(self, server, patch_board):
        patch_board(FakeBoard(vias=[_via("v1", "SIG", 10, 5)]))
        r = _call(server, "center_item_clearance", uuid="v1", search_radius_mm=0)
        assert r["success"] is False and "search_radius_mm" in r["error"]
