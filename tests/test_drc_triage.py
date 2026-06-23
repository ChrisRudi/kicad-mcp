# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the DRC triage + select tools (drc_triage / drc_select_group).

``kicad-cli`` is mocked (``_run_cli_drc`` patched) so these run headless; the
grouping, the fix-tool suggestion, the net/layer enrichment and the live
selection are what's under test. A fake board carries items whose uuids match
the synthetic DRC report.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mcp.server.fastmcp import FastMCP

import kicad_mcp.tools.ipc_interact_tools as mod
from kicad_mcp.tools.ipc_interact_tools import (
    _suggest_fix_tool,
    register_ipc_interact_tools,
)


# --- fake kipy item types (class name drives _friendly_type) ----------------

class Via(SimpleNamespace):
    pass


class Track(SimpleNamespace):
    pass


class BoardText(SimpleNamespace):
    pass


class Pad(SimpleNamespace):
    pass


def _kiid(val):
    return SimpleNamespace(value=val)


def _net(name):
    return SimpleNamespace(name=name, code=1)


def _via(uuid, net, layer="F.Cu"):
    return Via(id=_kiid(uuid), net=_net(net), layer=layer)


def _track(uuid, net, layer="In1.Cu"):
    return Track(id=_kiid(uuid), net=_net(net), layer=layer)


def _text(uuid, layer="F.SilkS"):
    return BoardText(id=_kiid(uuid), value="REF**", layer=layer)


def _pad(uuid, net, layer="F.Cu"):
    return Pad(id=_kiid(uuid), net=_net(net), layer=layer)


class FakeBoard:
    def __init__(self):
        self._vias = [_via("via1", "GND"), _via("via2", "VCC")]
        self._tracks = [_track("trk1", "GND")]
        self._texts = [_text("txt1")]
        self._pads = [_pad("pad1", "")]
        self.saved = 0
        self.cleared = 0
        self.added: list = []

    def get_footprints(self):
        return []

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_zones(self):
        return []

    def get_shapes(self):
        return []

    def get_text(self):
        return list(self._texts)

    def get_pads(self):
        return list(self._pads)

    def get_layer_name(self, layer):
        return str(layer)

    def save(self):
        self.saved += 1

    def clear_selection(self):
        self.cleared += 1

    def add_to_selection(self, items):
        self.added.append(list(items))


# Synthetic kicad-cli DRC report: clearance (×2, via-involved), annular,
# silk and an unconnected pad — five violations across four types.
_REPORT = {
    "violations": [
        {"type": "clearance", "severity": "error", "description": "Clearance (via)",
         "items": [{"uuid": "via1", "pos": {"x": 10.0, "y": 10.0}},
                   {"uuid": "trk1", "pos": {"x": 10.2, "y": 10.0}}]},
        {"type": "clearance", "severity": "error", "description": "Clearance 2",
         "items": [{"uuid": "via2", "pos": {"x": 20.0, "y": 12.0}}]},
        {"type": "annular_width", "severity": "warning", "description": "Annular small",
         "items": [{"uuid": "via1", "pos": {"x": 10.0, "y": 10.0}}]},
        {"type": "silk_over_copper", "severity": "warning", "description": "Silk on pad",
         "items": [{"uuid": "txt1", "pos": {"x": 5.0, "y": 5.0}}]},
    ],
    "unconnected_items": [
        {"type": "unconnected_items", "severity": "error", "description": "Unconnected",
         "items": [{"uuid": "pad1", "pos": {"x": 30.0, "y": 30.0}}]},
    ],
    "schematic_parity": [],
}


@pytest.fixture(autouse=True)
def _reset_cache():
    mod._reset_drc_triage_cache()
    yield
    mod._reset_drc_triage_cache()


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
def patch_drc(monkeypatch):
    """Install a fake board + a mocked kicad-cli DRC report."""
    def _install(board, report=None):
        report = _REPORT if report is None else report
        monkeypatch.setattr(mod, "_require_editor", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_connect_kicad", lambda: (object(), board))
        monkeypatch.setattr(mod, "_run_cli_drc", lambda p: dict(report))
        return board
    return _install


def _pcb(tmp_path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text("(kicad_pcb)")
    return str(p)


# --- _suggest_fix_tool unit --------------------------------------------------

class TestSuggestFixTool:
    def test_clearance_with_via_is_center_item_clearance(self):
        assert _suggest_fix_tool("clearance", {"via", "track"}) == "center_item_clearance"

    def test_clearance_track_only_is_width(self):
        assert _suggest_fix_tool("clearance", {"track"}) == "ipc_set_track_width / reroute"

    def test_annular_is_via_resize(self):
        assert _suggest_fix_tool("annular_width", {"via"}) == "via_resize"

    def test_hole_is_via_resize(self):
        assert _suggest_fix_tool("hole_clearance", set()) == "via_resize"

    def test_track_width_is_set_track_width(self):
        assert _suggest_fix_tool("track_width", {"track"}) == "ipc_set_track_width"

    def test_unconnected_is_router(self):
        assert _suggest_fix_tool("unconnected_items", set()) == "ipc_route_pin_to_pin"

    def test_silk_and_edge_are_move(self):
        assert _suggest_fix_tool("silk_over_copper", set()) == "ipc_move_items"
        assert _suggest_fix_tool("copper_edge_clearance", set()) == "ipc_move_items"

    def test_unknown_is_manual(self):
        assert _suggest_fix_tool("some_new_rule", set()) == "manuelle Prüfung"


# --- drc_triage --------------------------------------------------------------

class TestDrcTriage:
    def test_groups_by_type_with_suggestions(self, server, patch_drc, tmp_path):
        board = patch_drc(FakeBoard())
        r = _call(server, "drc_triage", pcb_path=_pcb(tmp_path))
        assert r["success"] is True
        assert board.saved == 1                       # saved before DRC
        assert r["group_count"] == 4 and r["total"] == 5
        # errors first; clearance has the highest count
        assert r["groups"][0]["type"] == "clearance"
        assert r["groups"][0]["severity"] == "error"
        assert r["groups"][0]["count"] == 2
        assert r["groups"][0]["suggested_tool"] == "center_item_clearance"
        by_type = {g["type"]: g for g in r["groups"]}
        assert by_type["annular_width"]["suggested_tool"] == "via_resize"
        assert by_type["unconnected_items"]["suggested_tool"] == "ipc_route_pin_to_pin"
        assert by_type["silk_over_copper"]["suggested_tool"] == "ipc_move_items"
        # enrichment: clearance touches both nets and both layers, deduped uuids
        cl = by_type["clearance"]
        assert set(cl["nets"]) == {"GND", "VCC"}
        assert set(cl["layers"]) == {"F.Cu", "In1.Cu"}
        assert sorted(cl["item_uuids"]) == ["trk1", "via1", "via2"]
        assert cl["centroid_mm"] is not None and cl["bbox_mm"] is not None
        assert r["by_severity"]["error"] == 3 and r["by_severity"]["warning"] == 2

    def test_exclude_warnings(self, server, patch_drc, tmp_path):
        patch_drc(FakeBoard())
        r = _call(server, "drc_triage", pcb_path=_pcb(tmp_path), include_warnings=False)
        types = {g["type"] for g in r["groups"]}
        assert types == {"clearance", "unconnected_items"}   # errors only

    def test_exclude_unconnected(self, server, patch_drc, tmp_path):
        patch_drc(FakeBoard())
        r = _call(server, "drc_triage", pcb_path=_pcb(tmp_path), include_unconnected=False)
        types = {g["type"] for g in r["groups"]}
        assert "unconnected_items" not in types and r["group_count"] == 3

    def test_cli_error_surfaced(self, server, patch_drc, tmp_path):
        patch_drc(FakeBoard(), report={"error": "kicad-cli not found."})
        r = _call(server, "drc_triage", pcb_path=_pcb(tmp_path))
        assert r["success"] is False and "kicad-cli" in r["error"]


# --- drc_select_group --------------------------------------------------------

class TestDrcSelectGroup:
    def test_select_by_type_highlights_items(self, server, patch_drc, tmp_path):
        board = patch_drc(FakeBoard())
        r = _call(server, "drc_select_group", group_type="clearance",
                  pcb_path=_pcb(tmp_path))
        assert r["success"] is True
        assert r["selected_count"] == 3          # via1, trk1, via2 all resolved
        assert r["suggested_tool"] == "center_item_clearance"
        assert board.cleared == 1 and len(board.added[0]) == 3

    def test_select_by_index(self, server, patch_drc, tmp_path):
        patch_drc(FakeBoard())
        r = _call(server, "drc_select_group", index=0, pcb_path=_pcb(tmp_path))
        assert r["success"] is True and r["group_type"] == "clearance"

    def test_select_pad_level_violation(self, server, patch_drc, tmp_path):
        # pad uuids aren't in the main collections — resolved via get_pads()
        patch_drc(FakeBoard())
        r = _call(server, "drc_select_group", group_type="unconnected_items",
                  pcb_path=_pcb(tmp_path))
        assert r["success"] is True and r["selected_count"] == 1

    def test_unknown_type_lists_available(self, server, patch_drc, tmp_path):
        patch_drc(FakeBoard())
        r = _call(server, "drc_select_group", group_type="nope",
                  pcb_path=_pcb(tmp_path))
        assert r["success"] is False
        assert "clearance" in r["error"]          # lists what's available

    def test_requires_a_selector(self, server, patch_drc, tmp_path):
        patch_drc(FakeBoard())
        r = _call(server, "drc_select_group", pcb_path=_pcb(tmp_path))
        assert r["success"] is False and "group_type" in r["error"]

    def test_cache_avoids_second_drc_run(self, server, patch_drc, tmp_path, monkeypatch):
        board = FakeBoard()
        monkeypatch.setattr(mod, "_require_editor", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_connect_kicad", lambda: (object(), board))
        calls = {"n": 0}

        def _counting(_p):
            calls["n"] += 1
            return dict(_REPORT)

        monkeypatch.setattr(mod, "_run_cli_drc", _counting)
        pcb = _pcb(tmp_path)
        _call(server, "drc_triage", pcb_path=pcb)
        _call(server, "drc_select_group", group_type="clearance", pcb_path=pcb)
        assert calls["n"] == 1                    # one DRC run reused by both
