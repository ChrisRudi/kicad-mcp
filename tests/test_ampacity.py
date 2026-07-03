# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests für die Stromtragfähigkeit: IPC-2221-Mathe (utils/ampacity) und das
``check_ampacity``-Tool (Breiten-Inventar ohne Ströme, Verstöße mit Strömen,
Innenlagen strenger, Fehlerpfade)."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import FastMCP

from kicad_mcp.utils import ampacity


# --- IPC-2221 math --------------------------------------------------------- #

class TestMath:
    def test_one_amp_external_needs_about_0_3mm(self):
        # Textbook value: 1 A at 10 K rise on 1 oz outer copper ≈ 0.3 mm.
        w = ampacity.required_width_mm(1.0, temp_rise_c=10, copper_oz=1.0)
        assert 0.25 <= w <= 0.35

    def test_internal_needs_about_2_6x_the_width(self):
        ext = ampacity.required_width_mm(2.0, internal=False)
        internal = ampacity.required_width_mm(2.0, internal=True)
        # k halves → area factor 2^(1/0.725) ≈ 2.60
        assert internal / ext == pytest.approx(2.0 ** (1 / 0.725), rel=1e-6)

    def test_roundtrip_width_current(self):
        w = ampacity.required_width_mm(2.0, temp_rise_c=20, copper_oz=2.0)
        back = ampacity.max_current_a(w, temp_rise_c=20, copper_oz=2.0)
        assert back == pytest.approx(2.0, rel=1e-6)

    def test_heavier_copper_needs_less_width(self):
        assert (ampacity.required_width_mm(3.0, copper_oz=2.0)
                < ampacity.required_width_mm(3.0, copper_oz=1.0))

    def test_edge_cases(self):
        assert ampacity.required_width_mm(0.0) == 0.0
        assert ampacity.max_current_a(0.0) == 0.0
        assert ampacity.required_width_mm(1.0, temp_rise_c=0) == float("inf")

    def test_layer_classification(self):
        assert ampacity.is_internal_layer("In1.Cu")
        assert ampacity.is_internal_layer("In12.Cu")
        assert not ampacity.is_internal_layer("F.Cu")
        assert not ampacity.is_internal_layer("B.Cu")


# --- pure audit -------------------------------------------------------------- #

def _seg(net, width, layer="F.Cu", x2=10.0):
    return {"start": [0.0, 0.0], "end": [x2, 0.0], "width": width,
            "layer": layer, "net": net}


class TestAudit:
    NAMES = {1: "VBUS", 2: "SIG"}

    def test_inventory_without_currents(self):
        res = ampacity.audit_tracks(
            [_seg(1, 0.3), _seg(1, 1.2), _seg(2, 0.2, "In1.Cu")],
            self.NAMES, {})
        assert res["violations"] == []
        vbus = res["nets"]["VBUS"]
        assert vbus["track_count"] == 2
        assert vbus["min_width_mm"] == 0.3 and vbus["max_width_mm"] == 1.2
        assert vbus["length_mm"] == pytest.approx(20.0)
        assert res["nets"]["SIG"]["layers"] == ["In1.Cu"]

    def test_violation_detected_and_sorted_worst_first(self):
        res = ampacity.audit_tracks(
            [_seg(1, 0.3), _seg(1, 1.2)], self.NAMES, {"VBUS": 2.0})
        # 2 A external needs ~0.78 mm: the 0.3 mm segment fails, 1.2 mm passes
        assert len(res["violations"]) == 1
        v = res["violations"][0]
        assert v["net"] == "VBUS" and v["width_mm"] == 0.3
        assert v["required_width_mm"] > 0.7
        assert v["max_current_a"] < 2.0
        assert res["nets"]["VBUS"]["current_a"] == 2.0

    def test_inner_layer_judged_stricter(self):
        # 0.3 mm carries 0.9 A outside, but NOT inside (needs ~0.68 mm inner)
        tracks = [_seg(1, 0.3, "F.Cu"), _seg(2, 0.3, "In1.Cu")]
        res = ampacity.audit_tracks(tracks, self.NAMES,
                                    {"VBUS": 0.9, "SIG": 0.9})
        nets_in_violation = {v["net"] for v in res["violations"]}
        assert nets_in_violation == {"SIG"}

    def test_unknown_net_numbers_ignored(self):
        res = ampacity.audit_tracks([_seg(99, 0.1)], self.NAMES, {"VBUS": 1})
        assert res["nets"] == {} and res["violations"] == []


# --- the MCP tool -------------------------------------------------------------- #

_PCB = """(kicad_pcb (version 20240101) (generator test)
  (net 0 "")
  (net 1 "VBUS")
  (net 2 "SIG")
  (segment (start 0 0) (end 10 0) (width 0.3) (layer "F.Cu") (net 1))
  (segment (start 10 0) (end 20 0) (width 1.2) (layer "F.Cu") (net 1))
  (segment (start 0 5) (end 5 5) (width 0.2) (layer "In1.Cu") (net 2))
)
"""


@pytest.fixture()
def board(tmp_path):
    p = tmp_path / "amp.kicad_pcb"
    p.write_text(_PCB)
    return str(p)


@pytest.fixture(scope="module")
def server() -> FastMCP:
    from kicad_mcp.tools.pcb_tools import register_pcb_tools
    mcp = FastMCP("ampacity-test")
    register_pcb_tools(mcp)
    return mcp


def _call(server, **args):
    result = asyncio.run(server.call_tool("check_ampacity", args))
    if isinstance(result, tuple) and len(result) > 1:  # ältere fastmcp-Form
        return result[1]
    return result.structured_content  # fastmcp 3.x: ToolResult


class TestCheckAmpacityTool:
    def test_inventory_mode(self, server, board):
        out = _call(server, pcb_path=board)
        assert out["success"] and out["violation_count"] == 0
        assert out["nets"]["VBUS"]["min_width_mm"] == 0.3
        assert out["nets"]["SIG"]["layers"] == ["In1.Cu"]

    def test_currents_find_violations(self, server, board):
        out = _call(server, pcb_path=board,
                    currents=json.dumps({"VBUS": 2.0, "SIG": 0.5}))
        assert out["success"]
        nets = {v["net"] for v in out["violations"]}
        # VBUS: 0.3 mm < ~0.78 mm für 2 A; SIG innen: 0.2 mm < ~0.30 mm für 0.5 A
        assert nets == {"VBUS", "SIG"} and out["violation_count"] == 2

    def test_net_filter_scopes_report(self, server, board):
        out = _call(server, pcb_path=board,
                    currents=json.dumps({"VBUS": 2.0, "SIG": 0.5}),
                    nets=json.dumps(["SIG"]))
        assert out["success"]
        assert set(out["nets"]) == {"SIG"}
        assert {v["net"] for v in out["violations"]} == {"SIG"}

    def test_unknown_current_net_reported(self, server, board):
        out = _call(server, pcb_path=board,
                    currents=json.dumps({"NIX": 1.0}))
        assert out["success"] and out["unknown_current_nets"] == ["NIX"]

    def test_bad_json_is_structured_error(self, server, board):
        out = _call(server, pcb_path=board, currents="{kaputt")
        assert out["success"] is False and "JSON" in out["error"]

    def test_non_numeric_current_rejected(self, server, board):
        out = _call(server, pcb_path=board,
                    currents=json.dumps({"VBUS": "viel"}))
        assert out["success"] is False and "numbers" in out["error"]

    def test_missing_file(self, server, tmp_path):
        out = _call(server, pcb_path=str(tmp_path / "fehlt.kicad_pcb"))
        assert out["success"] is False and "not found" in out["error"]
