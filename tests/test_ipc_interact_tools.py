# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the live selection tools (G1 read, G2 set).

kipy / a running KiCad are mocked — these run headless. The module's
connection helpers (``_connect_kicad`` / ``_require_editor``) are patched to
hand the tools a fake board, so the serialisation and filter logic is what's
under test.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from mcp.server.fastmcp import FastMCP

import kicad_mcp.tools.ipc_interact_tools as mod
from kicad_mcp.tools.ipc_interact_tools import register_ipc_interact_tools


# --- fake kipy item types (class name drives _friendly_type) ----------------

class FootprintInstance(SimpleNamespace):
    pass


class Via(SimpleNamespace):
    pass


class Track(SimpleNamespace):
    pass


class BoardText(SimpleNamespace):
    pass


def _vec(x_nm, y_nm):
    return SimpleNamespace(x=x_nm, y=y_nm)


def _kiid(val):
    return SimpleNamespace(value=val)


def _net(name):
    return SimpleNamespace(name=name, code=1)


def _boardtext(value):
    # Mirror live kipy: Field.text is a BoardText whose .value holds the string.
    return SimpleNamespace(value=value)


def _fp(ref, value, uuid, x_mm, y_mm, layer="F.Cu", pad_nets=None):
    defn = None
    if pad_nets is not None:
        defn = SimpleNamespace(pads=[
            SimpleNamespace(number=num, net=_net(net) if net else None)
            for num, net in pad_nets
        ])
    return FootprintInstance(
        id=_kiid(uuid),
        reference_field=SimpleNamespace(text=_boardtext(ref)),
        value_field=SimpleNamespace(text=_boardtext(value)),
        layer=layer,
        position=_vec(x_mm * 1_000_000, y_mm * 1_000_000),
        definition=defn,
    )


def _via(uuid, net, x_mm, y_mm, layer="F.Cu"):
    return Via(
        id=_kiid(uuid),
        net=_net(net),
        layer=layer,
        position=_vec(x_mm * 1_000_000, y_mm * 1_000_000),
    )


def _track(uuid, net, x1, y1, x2, y2, w_mm=0.25, layer="In1.Cu"):
    return Track(
        id=_kiid(uuid),
        net=_net(net),
        layer=layer,
        start=_vec(x1 * 1_000_000, y1 * 1_000_000),
        end=_vec(x2 * 1_000_000, y2 * 1_000_000),
        width=w_mm * 1_000_000,
    )


class FakeBoard:
    def __init__(self, footprints=None, tracks=None, vias=None):
        self._fps = footprints or []
        self._tracks = tracks or []
        self._vias = vias or []
        self._selection: list = []
        self.add_calls: list = []
        self.cleared = 0

    # collections
    def get_footprints(self):
        return list(self._fps)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_zones(self):
        return []

    def get_shapes(self):
        return []

    def get_text(self):
        return []

    # selection
    def get_selection(self):
        return list(self._selection)

    def clear_selection(self):
        self.cleared += 1
        self._selection = []

    def add_to_selection(self, items):
        self.add_calls.append(list(items))
        self._selection.extend(items)

    # misc
    def get_layer_name(self, layer):
        return str(layer)

    def get_item_bounding_box(self, item):
        return SimpleNamespace(pos=_vec(0, 0), size=_vec(1_000_000, 1_000_000))

    def get_connected_items(self, item):
        return []


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
    """Return a setter that installs a FakeBoard behind the tool helpers."""
    def _install(board: FakeBoard):
        monkeypatch.setattr(mod, "_require_editor", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_connect_kicad", lambda: (object(), board))
        return board
    return _install


# --- G1: read ----------------------------------------------------------------

class TestGetSelection:
    def test_empty_selection_is_note_not_error(self, server, patch_board):
        patch_board(FakeBoard())
        r = _call(server, "ipc_get_selection")
        assert r["success"] is True
        assert r["count"] == 0
        assert r["items"] == []
        assert "selektiert" in r["note"].lower()

    def test_serializes_footprint_and_via(self, server, patch_board):
        board = FakeBoard(
            footprints=[_fp("R12", "10k", "uuid-r12", 100.0, 50.0)],
            vias=[_via("uuid-v1", "GND", 101.0, 51.0)],
        )
        board._selection = board.get_footprints() + board.get_vias()
        patch_board(board)
        r = _call(server, "ipc_get_selection")
        assert r["success"] and r["count"] == 2
        fp = next(i for i in r["items"] if i["type"] == "footprint")
        assert fp["reference"] == "R12"
        assert fp["value"] == "10k"
        assert fp["uuid"] == "uuid-r12"
        assert fp["position_mm"] == [100.0, 50.0]
        via = next(i for i in r["items"] if i["type"] == "via")
        assert via["net"] == "GND"
        assert via["position_mm"] == [101.0, 51.0]

    def test_track_start_end_width(self, server, patch_board):
        board = FakeBoard(tracks=[_track("t1", "In1", 0, 0, 5, 0, w_mm=0.5)])
        board._selection = board.get_tracks()
        patch_board(board)
        r = _call(server, "ipc_get_selection")
        t = r["items"][0]
        assert t["type"] == "track"
        assert t["start_mm"] == [0.0, 0.0] and t["end_mm"] == [5.0, 0.0]
        assert t["width_mm"] == 0.5


class TestInspectItem:
    def test_find_by_reference(self, server, patch_board):
        patch_board(FakeBoard(footprints=[_fp("U1", "ESP32", "u1", 10, 10)]))
        r = _call(server, "ipc_inspect_item", ref_or_uuid="U1")
        assert r["success"] is True
        assert r["item"]["reference"] == "U1"
        assert r["connected"] == []

    def test_find_by_uuid(self, server, patch_board):
        patch_board(FakeBoard(vias=[_via("uuid-xyz", "VBUS", 1, 2)]))
        r = _call(server, "ipc_inspect_item", ref_or_uuid="uuid-xyz")
        assert r["success"] is True
        assert r["item"]["net"] == "VBUS"

    def test_footprint_reports_pad_nets(self, server, patch_board):
        # A footprint's connectivity is its pad->net map (get_connected_items
        # rejects a footprint arg). Mirrors the live U_589 case.
        fp = _fp("U_589", "74HC589", "u589", 126.75, 95.0, layer="B.Cu",
                 pad_nets=[("1", "nFAULT_DRV1"), ("7", "GND"), ("16", "+3V3")])
        patch_board(FakeBoard(footprints=[fp]))
        r = _call(server, "ipc_inspect_item", ref_or_uuid="U_589")
        assert r["success"] is True
        assert r["item"]["reference"] == "U_589"
        assert r["item"]["value"] == "74HC589"
        assert {"number": "1", "net": "nFAULT_DRV1"} in r["pads"]
        assert r["nets"] == ["+3V3", "GND", "nFAULT_DRV1"]
        assert "connected" not in r  # footprint path, not get_connected_items

    def test_unknown_errors(self, server, patch_board):
        patch_board(FakeBoard())
        r = _call(server, "ipc_inspect_item", ref_or_uuid="NOPE")
        assert r["success"] is False
        assert "found" in r["error"].lower()

    def test_blank_arg_errors(self, server, patch_board):
        patch_board(FakeBoard())
        r = _call(server, "ipc_inspect_item", ref_or_uuid="  ")
        assert r["success"] is False


# --- G2: set -----------------------------------------------------------------

class TestSelectItems:
    def test_requires_a_filter(self, server, patch_board):
        patch_board(FakeBoard())
        r = _call(server, "ipc_select_items")
        assert r["success"] is False
        assert "filter" in r["error"].lower()

    def test_select_by_ref(self, server, patch_board):
        board = FakeBoard(footprints=[
            _fp("R1", "1k", "r1", 0, 0), _fp("R2", "2k", "r2", 1, 1),
        ])
        patch_board(board)
        r = _call(server, "ipc_select_items", refs=["R2"])
        assert r["success"] and r["selected_count"] == 1
        assert board.cleared == 1
        assert board.add_calls and len(board.add_calls[0]) == 1

    def test_select_by_net(self, server, patch_board):
        board = FakeBoard(vias=[
            _via("v1", "GND", 0, 0), _via("v2", "GND", 1, 1),
            _via("v3", "VCC", 2, 2),
        ])
        patch_board(board)
        r = _call(server, "ipc_select_items", net="GND")
        assert r["selected_count"] == 2

    def test_select_by_type(self, server, patch_board):
        board = FakeBoard(
            footprints=[_fp("R1", "1k", "r1", 0, 0)],
            vias=[_via("v1", "GND", 0, 0)],
        )
        patch_board(board)
        r = _call(server, "ipc_select_items", item_type="via")
        assert r["selected_count"] == 1

    def test_no_match_is_clean(self, server, patch_board):
        patch_board(FakeBoard(footprints=[_fp("R1", "1k", "r1", 0, 0)]))
        r = _call(server, "ipc_select_items", refs=["NOPE"])
        assert r["success"] is True
        assert r["selected_count"] == 0


class TestClearSelection:
    def test_clears(self, server, patch_board):
        board = FakeBoard()
        patch_board(board)
        r = _call(server, "ipc_clear_selection")
        assert r["success"] is True
        assert board.cleared == 1


# --- G3: markers -------------------------------------------------------------

class _BoardText(SimpleNamespace):
    pass


class FakeMarkerBoard:
    """Captures create/remove + classifies items by type for marker tests.

    Uses real kipy item objects built by ``_build_marker_items`` (they
    construct headless), so it exercises the real shape/text construction.
    Starts with the marker layer DISABLED so the enable path is covered.
    """

    def __init__(self):
        self._shapes: list = []
        self._texts: list = []
        self.enabled = [3, 34]      # F.Cu, B.Cu — no user layer yet
        self.visible = [3, 34]
        self.commits = 0

    # layer management
    def get_enabled_layers(self):
        return list(self.enabled)

    def get_copper_layer_count(self):
        return 2

    def set_enabled_layers(self, copper_count, layers):
        self.enabled = list(layers)

    def get_visible_layers(self):
        return list(self.visible)

    def set_visible_layers(self, layers):
        self.visible = list(layers)

    # commits + items
    def begin_commit(self):
        return object()

    def push_commit(self, commit, msg):
        self.commits += 1

    def create_items(self, items):
        for it in items:
            if type(it).__name__ == "BoardText":
                self._texts.append(it)
            else:
                self._shapes.append(it)
        return list(items)

    def remove_items(self, items):
        idset = set(id(i) for i in items)
        self._texts = [t for t in self._texts if id(t) not in idset]
        self._shapes = [s for s in self._shapes if id(s) not in idset]

    def get_text(self):
        return list(self._texts)

    def get_shapes(self):
        return list(self._shapes)


@pytest.fixture
def patch_marker_board(monkeypatch):
    def _install(board):
        monkeypatch.setattr(mod, "_require_editor", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_connect_kicad", lambda: (object(), board))
        return board
    return _install


class TestDrawMarkers:
    def test_draw_assigns_ids_and_enables_layer(self, server, patch_marker_board):
        board = patch_marker_board(FakeMarkerBoard())
        r = _call(server, "ipc_draw_markers", markers=json.dumps([
            {"x_mm": 100, "y_mm": 60, "type": "circle"},
            {"x_mm": 105, "y_mm": 60, "type": "cross", "label_text": "tight"},
            {"x_mm": 110, "y_mm": 60, "type": "label", "label_text": "note"},
        ]))
        assert r["success"] is True, r
        assert r["count"] == 3
        assert [d["id"] for d in r["drawn"]] == ["M1", "M2", "M3"]
        assert r["layer_enabled"] is True          # was disabled, got enabled
        # 1 circle + 2 cross-segments = 3 shapes; 3 ID texts
        assert len(board.get_shapes()) == 3
        assert len(board.get_text()) == 3

    def test_ids_continue_from_existing(self, server, patch_marker_board):
        patch_marker_board(FakeMarkerBoard())
        _call(server, "ipc_draw_markers",
              markers=json.dumps([{"x_mm": 1, "y_mm": 1, "type": "label"}]))
        r = _call(server, "ipc_draw_markers",
                  markers=json.dumps([{"x_mm": 2, "y_mm": 2, "type": "label"}]))
        assert r["drawn"][0]["id"] == "M2"

    def test_bad_type_rejected(self, server, patch_marker_board):
        patch_marker_board(FakeMarkerBoard())
        r = _call(server, "ipc_draw_markers",
                  markers=json.dumps([{"x_mm": 1, "y_mm": 1, "type": "blob"}]))
        assert r["success"] is False
        assert "circle/cross/label" in r["error"]

    def test_empty_list_errors(self, server, patch_marker_board):
        patch_marker_board(FakeMarkerBoard())
        r = _call(server, "ipc_draw_markers", markers="[]")
        assert r["success"] is False


class TestListAndClearMarkers:
    def _seed(self, server, board):
        _call(server, "ipc_draw_markers", markers=json.dumps([
            {"x_mm": 100, "y_mm": 60, "type": "circle", "label_text": "a"},
            {"x_mm": 105, "y_mm": 60, "type": "label", "label_text": "b"},
        ]))

    def test_list(self, server, patch_marker_board):
        board = patch_marker_board(FakeMarkerBoard())
        self._seed(server, board)
        r = _call(server, "ipc_list_markers")
        assert r["count"] == 2
        assert [m["id"] for m in r["markers"]] == ["M1", "M2"]
        assert r["markers"][0]["label"] == "M1: a"

    def test_clear_all(self, server, patch_marker_board):
        board = patch_marker_board(FakeMarkerBoard())
        self._seed(server, board)
        r = _call(server, "ipc_clear_markers")
        assert r["success"] is True
        assert r["removed_count"] == 2
        assert _call(server, "ipc_list_markers")["count"] == 0

    def test_clear_by_id(self, server, patch_marker_board):
        board = patch_marker_board(FakeMarkerBoard())
        self._seed(server, board)
        r = _call(server, "ipc_clear_markers", ids=["M1"])
        assert r["removed_ids"] == ["M1"]
        remaining = _call(server, "ipc_list_markers")
        assert [m["id"] for m in remaining["markers"]] == ["M2"]

    def test_check_before_save_warns(self, server, patch_marker_board):
        board = patch_marker_board(FakeMarkerBoard())
        self._seed(server, board)
        r = _call(server, "ipc_check_markers_before_save")
        assert r["warn"] is True
        assert r["marker_count"] == 2
        _call(server, "ipc_clear_markers")
        r2 = _call(server, "ipc_check_markers_before_save")
        assert r2["warn"] is False


# --- G4/G5/G6: edits, DRC session, status ------------------------------------

class Zone(SimpleNamespace):
    pass


class FakeEditBoard(FakeMarkerBoard):
    """Marker board + by-uuid items + commit capture for edit/DRC tests."""

    def __init__(self, footprints=None, tracks=None, vias=None, nets=("GND",),
                 selection=None):
        super().__init__()
        self._fps = footprints or []
        self._tracks = tracks or []
        self._vias = vias or []
        self._nets = [_net(n) for n in nets]
        self._selection = selection or []
        self.updated = []
        self.removed = []
        self.saved = 0

    def get_footprints(self):
        return list(self._fps)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_zones(self):
        return []

    def get_nets(self):
        return list(self._nets)

    def get_selection(self):
        return list(self._selection)

    def update_items(self, items):
        items = items if isinstance(items, (list, tuple)) else [items]
        self.updated.extend(items)
        return list(items)

    def remove_items(self, items):
        self.removed.extend(items)
        super().remove_items(items)

    def save(self):
        self.saved += 1


def _trk(uuid, width_mm=0.2):
    return Track(id=_kiid(uuid), width=int(width_mm * 1_000_000),
                 start=_vec(0, 0), end=_vec(5_000_000, 0), net=_net("GND"),
                 layer="In1.Cu")


class TestSetTrackWidth:
    def test_changes_track_width(self, server, patch_marker_board):
        board = FakeEditBoard(tracks=[_trk("t1"), _trk("t2")])
        patch_marker_board(board)
        r = _call(server, "ipc_set_track_width", uuids=["t1", "t2"], width_mm=0.5)
        assert r["success"] and r["changed"] == 2
        assert all(t.width == 500000 for t in board._tracks)

    def test_skips_non_width_items(self, server, patch_marker_board):
        board = FakeEditBoard(footprints=[_fp("R1", "1k", "r1", 0, 0)])
        patch_marker_board(board)
        r = _call(server, "ipc_set_track_width", uuids=["r1"], width_mm=0.5)
        assert r["changed"] == 0 and "r1" in r["skipped"]

    def test_rejects_bad_width(self, server, patch_marker_board):
        patch_marker_board(FakeEditBoard())
        r = _call(server, "ipc_set_track_width", uuids=["t1"], width_mm=0)
        assert r["success"] is False


class TestMoveItems:
    def test_moves_track_endpoints(self, server, patch_marker_board):
        board = FakeEditBoard(tracks=[_trk("t1")])
        patch_marker_board(board)
        r = _call(server, "ipc_move_items", uuids=["t1"], dx_mm=1.0, dy_mm=2.0)
        assert r["success"] and r["moved"] == 1
        t = board._tracks[0]
        assert round(t.start.x / 1e6, 3) == 1.0 and round(t.end.y / 1e6, 3) == 2.0

    def test_zero_delta_errors(self, server, patch_marker_board):
        patch_marker_board(FakeEditBoard(tracks=[_trk("t1")]))
        r = _call(server, "ipc_move_items", uuids=["t1"])
        assert r["success"] is False

    def test_reports_not_found(self, server, patch_marker_board):
        patch_marker_board(FakeEditBoard(tracks=[_trk("t1")]))
        r = _call(server, "ipc_move_items", uuids=["nope"], dx_mm=1)
        assert r["moved"] == 0 and r["not_found"] == ["nope"]


class TestRemoveItems:
    def test_removes(self, server, patch_marker_board):
        board = FakeEditBoard(tracks=[_trk("t1"), _trk("t2")])
        patch_marker_board(board)
        r = _call(server, "ipc_remove_items", uuids=["t1"])
        assert r["success"] and r["removed"] == 1
        assert len(board.removed) == 1

    def test_empty_errors(self, server, patch_marker_board):
        patch_marker_board(FakeEditBoard())
        r = _call(server, "ipc_remove_items", uuids=[])
        assert r["success"] is False


class TestCreateViaErrors:
    def test_net_not_found(self, server, patch_marker_board):
        patch_marker_board(FakeEditBoard(nets=("GND",)))
        r = _call(server, "ipc_create_via", x_mm=10, y_mm=10, net="NOPE")
        assert r["success"] is False
        assert "not found" in r["error"].lower()


class TestAcceptMarkersErrors:
    def test_empty_ids(self, server, patch_marker_board):
        patch_marker_board(FakeEditBoard())
        r = _call(server, "ipc_accept_markers", ids=[], net="GND")
        assert r["success"] is False

    def test_unknown_ids(self, server, patch_marker_board):
        patch_marker_board(FakeEditBoard())
        r = _call(server, "ipc_accept_markers", ids=["M9"], net="GND")
        assert r["success"] is False
        assert "found" in r["error"].lower()


class TestSessionStatus:
    def test_reports_markers_and_selection(self, server, patch_marker_board):
        board = FakeEditBoard(selection=[_trk("t1"), _via("v1", "GND", 0, 0)])
        patch_marker_board(board)
        _call(server, "ipc_draw_markers",
              markers=json.dumps([{"x_mm": 1, "y_mm": 1, "type": "label"}]))
        r = _call(server, "ipc_session_status")
        assert r["success"] is True
        assert r["markers"]["count"] == 1 and r["markers"]["ids"] == ["M1"]
        assert r["selection"]["count"] == 2
        assert r["selection"]["types"]["track"] == 1

    def test_nothing_pending_hint(self, server, patch_marker_board):
        patch_marker_board(FakeEditBoard())
        r = _call(server, "ipc_session_status")
        assert r["markers"]["count"] == 0
        assert any("Nothing pending" in h for h in r["next_steps"])


class TestDrcSession:
    _REPORT = {
        "violations": [
            {"severity": "error", "type": "clearance",
             "items": [{"uuid": "u1", "pos": {"x": 10.0, "y": 20.0}}]},
            {"severity": "warning", "type": "silk_overlap",
             "items": [{"uuid": "u2", "pos": {"x": 30.0, "y": 40.0}}]},
        ],
        "unconnected_items": [
            {"severity": "error", "type": "unconnected_items",
             "items": [{"uuid": "u3", "pos": {"x": 50.0, "y": 60.0}}]},
        ],
        "schematic_parity": [],
    }

    def test_runs_and_marks(self, server, patch_marker_board, monkeypatch, tmp_path):
        pcb = tmp_path / "b.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        board = patch_marker_board(FakeEditBoard())
        monkeypatch.setattr(mod, "_run_cli_drc", lambda p: dict(self._REPORT))
        r = _call(server, "ipc_drc_session_start", pcb_path=str(pcb))
        assert r["success"] is True, r
        assert board.saved == 1                      # board was saved for DRC
        assert r["total"] == 3
        assert r["severity_counts"]["error"] == 2
        assert r["marked"] == 3
        # error violations marked first
        assert r["markers"][0]["severity"] == "error"
        assert r["markers"][0]["item_uuids"] == ["u1"]
        # markers actually drawn on the board (3 crosses = 6 segments + 3 texts)
        assert len(board.get_text()) == 3

    def test_respects_max_markers(self, server, patch_marker_board, monkeypatch, tmp_path):
        pcb = tmp_path / "b.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        patch_marker_board(FakeEditBoard())
        monkeypatch.setattr(mod, "_run_cli_drc", lambda p: dict(self._REPORT))
        r = _call(server, "ipc_drc_session_start", pcb_path=str(pcb), max_markers=1)
        assert r["marked"] == 1
        assert r["total"] == 3            # total still reports everything

    def test_drc_error_surfaced(self, server, patch_marker_board, monkeypatch, tmp_path):
        pcb = tmp_path / "b.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        patch_marker_board(FakeEditBoard())
        monkeypatch.setattr(mod, "_run_cli_drc", lambda p: {"error": "kicad-cli not found."})
        r = _call(server, "ipc_drc_session_start", pcb_path=str(pcb))
        assert r["success"] is False
        assert "kicad-cli" in r["error"]


class TestBoardDefaultVia:
    def test_reads_default_netclass(self):
        nc = SimpleNamespace(name="Default", via_diameter=400000, via_drill=200000)
        board = SimpleNamespace(
            get_project=lambda: SimpleNamespace(get_net_classes=lambda: [nc])
        )
        assert mod._board_default_via_nm(board) == (400000, 200000)

    def test_falls_back_when_no_netclass(self):
        board = SimpleNamespace(
            get_project=lambda: SimpleNamespace(get_net_classes=lambda: [])
        )
        assert mod._board_default_via_nm(board) == (400_000, 200_000)

    def test_falls_back_on_error(self):
        def _boom():
            raise RuntimeError("no project")
        board = SimpleNamespace(get_project=_boom)
        assert mod._board_default_via_nm(board) == (400_000, 200_000)


class TestSketchLegend:
    def test_draws_legend_and_survives_clear(self, server, patch_marker_board):
        board = patch_marker_board(FakeMarkerBoard())
        r = _call(server, "ipc_draw_sketch_legend")
        assert r["success"] is True
        assert len(r["lines"]) >= 3
        n_legend = len(board.get_text())
        assert n_legend >= 3
        # add markers, then clear all — legend must remain
        _call(server, "ipc_draw_markers",
              markers=json.dumps([{"x_mm": 5, "y_mm": 5, "type": "label"}]))
        _call(server, "ipc_clear_markers")
        # markers gone, legend lines still present
        assert _call(server, "ipc_list_markers")["count"] == 0
        from kicad_mcp.tools.ipc_interact_tools import _LEGEND_TAG
        remaining = [t for t in board.get_text()
                     if str(getattr(t, "value", "")).startswith(_LEGEND_TAG)]
        assert len(remaining) == n_legend

    def test_rerun_replaces_legend(self, server, patch_marker_board):
        board = patch_marker_board(FakeMarkerBoard())
        _call(server, "ipc_draw_sketch_legend")
        first = len(board.get_text())
        _call(server, "ipc_draw_sketch_legend")
        # replaced, not doubled
        assert len(board.get_text()) == first


class TestPresenceBeacon:
    def test_first_contact_enables_layer_and_draws_legend(self, monkeypatch):
        monkeypatch.delenv("KICAD_MCP_SKETCH_PRESENCE", raising=False)
        mod._reset_presence_for_tests()
        board = FakeEditBoard(footprints=[_fp("R1", "1k", "r1", 50, 50)])
        # before: marker layer not enabled, no legend
        from kicad_mcp.tools.ipc_tools import _layer_to_enum
        L = _layer_to_enum(mod.DEFAULT_MARKER_LAYER)
        assert L not in board.enabled
        mod.ensure_mcp_presence(board)
        assert L in board.enabled and L in board.visible
        assert len(mod._legend_items_on_layer(board, L)) == len(mod._LEGEND_LINES)

    def test_runs_only_once_per_process(self, monkeypatch):
        monkeypatch.delenv("KICAD_MCP_SKETCH_PRESENCE", raising=False)
        mod._reset_presence_for_tests()
        board = FakeEditBoard()
        mod.ensure_mcp_presence(board)
        n = len(board.get_text())
        mod.ensure_mcp_presence(board)   # second call: no-op
        assert len(board.get_text()) == n

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("KICAD_MCP_SKETCH_PRESENCE", "0")
        mod._reset_presence_for_tests()
        board = FakeEditBoard()
        mod.ensure_mcp_presence(board)
        from kicad_mcp.tools.ipc_tools import _layer_to_enum
        L = _layer_to_enum(mod.DEFAULT_MARKER_LAYER)
        assert L not in board.enabled
        assert len(board.get_text()) == 0

    def test_does_not_redraw_existing_legend(self, monkeypatch):
        monkeypatch.delenv("KICAD_MCP_SKETCH_PRESENCE", raising=False)
        from kicad_mcp.tools.ipc_tools import _layer_to_enum
        L = _layer_to_enum(mod.DEFAULT_MARKER_LAYER)
        board = FakeEditBoard()
        # pre-existing legend
        board.create_items(mod._build_legend_items(L, 10, 10, mod._LEGEND_LINES, 1.0))
        n = len(board.get_text())
        mod._reset_presence_for_tests()
        mod.ensure_mcp_presence(board)
        assert len(board.get_text()) == n   # not duplicated
