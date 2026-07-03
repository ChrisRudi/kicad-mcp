# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests für die IEC-60664-1-Lookups (Schutzklassen-Feature): die Fixpunkte
sind gegen die publizierten Normtabellen verifiziert (F.1/F.2/F.4; Quercheck
BS EN 60335-1 Tab. 15/16/18) — genau die Werte, die jeder Zulassungsprüfer
nachschlagen würde."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from kicad_mcp.utils import safety_spacing as sp


class TestImpulseVoltage:
    def test_230v_mains_ovc2_is_2k5(self):
        # 230 V L-N liegt in der "≤ 300 V"-Reihe: OVC II → 2,5 kV
        assert sp.impulse_voltage_kv(230, "II") == 2.5

    def test_230v_ovc3_fixed_installation_is_4k(self):
        assert sp.impulse_voltage_kv(230, "III") == 4.0

    def test_120v_ovc2_is_1k5(self):
        assert sp.impulse_voltage_kv(120, "II") == 1.5

    def test_selv_48v_ovc2_is_0k5(self):
        assert sp.impulse_voltage_kv(48, "II") == 0.5

    def test_beyond_table_or_bad_ovc_is_none(self):
        assert sp.impulse_voltage_kv(1500, "II") is None
        assert sp.impulse_voltage_kv(230, "V") is None


class TestClearance:
    def test_2k5_impulse_is_1_5mm(self):
        assert sp.clearance_mm(2.5) == 1.5

    def test_4k_impulse_is_3mm(self):
        assert sp.clearance_mm(4.0) == 3.0

    def test_pd2_minimum_floors_small_values(self):
        # 0,5 kV → 0,04 mm laut F.2, aber PD2-Minimum ist 0,2 mm
        assert sp.clearance_mm(0.5, pollution_degree=2) == 0.2

    def test_pd3_minimum_is_0_8(self):
        assert sp.clearance_mm(0.5, pollution_degree=3) == 0.8

    def test_reinforced_steps_impulse_up(self):
        # verstärkt: 2,5 kV → nächste Stufe 3,0 kV → 2,0 mm
        assert sp.clearance_mm(2.5, reinforced=True) == 2.0


class TestCreepage:
    def test_250v_pd2_groups(self):
        # Tabelle F.4, Zeile 250 V, PD2: MG I 1,25 / II 1,80 / III 2,50
        assert sp.creepage_mm(230, 2, "I") == 1.25
        assert sp.creepage_mm(230, 2, "II") == 1.8
        assert sp.creepage_mm(230, 2, "IIIa") == 2.5

    def test_250v_pd3_group_iii_is_4mm(self):
        assert sp.creepage_mm(230, 3, "III") == 4.0

    def test_pd1_column(self):
        assert sp.creepage_mm(230, 1, "I") == 0.56

    def test_reinforced_doubles(self):
        assert sp.creepage_mm(230, 2, "IIIa", reinforced=True) == 5.0

    def test_selv_row(self):
        # 12 V → Zeile 12,5 V: PD2 alle Gruppen 0,42
        assert sp.creepage_mm(12, 2, "II") == 0.42

    def test_beyond_table_is_none(self):
        assert sp.creepage_mm(1200, 2, "I") is None


class TestSpacingRequirements:
    def test_mains_230v_basic_pd2_fr4(self):
        res = sp.spacing_requirements(230, nominal_mains_v=230,
                                      material_group="IIIa")
        assert res["success"]
        assert res["impulse_voltage_kv"] == 2.5
        assert res["clearance_mm"] == 1.5
        assert res["creepage_mm"] == 2.5
        assert "keine" in res["disclaimer"].lower() or "KEINE" in res["disclaimer"]

    def test_class2_reinforced_230v(self):
        res = sp.spacing_requirements(230, nominal_mains_v=230,
                                      insulation="reinforced")
        assert res["success"]
        assert res["clearance_mm"] == 2.0   # 2,5 kV → Stufe 3,0 kV → 2,0 mm
        assert res["creepage_mm"] == 5.0    # 2,5 mm × 2
        assert "II" in res["protection_classes"]

    def test_creepage_never_below_clearance(self):
        # SELV 48 V, OVC IV (1,5 kV → 0,5 mm Luft), PD1-Kriechweg wäre 0,18 mm
        res = sp.spacing_requirements(48, pollution_degree=1,
                                      overvoltage_category="IV")
        assert res["success"]
        assert res["creepage_mm"] >= res["clearance_mm"]

    def test_bad_insulation_rejected(self):
        res = sp.spacing_requirements(230, insulation="magisch")
        assert not res["success"] and "insulation" in res["error"]

    def test_out_of_table_reports(self):
        res = sp.spacing_requirements(230, nominal_mains_v=1500)
        assert not res["success"]


# --- the MCP tool -----------------------------------------------------------------

@pytest.fixture(scope="module")
def server() -> FastMCP:
    from kicad_mcp.tools.safety_tools import register_safety_tools
    mcp = FastMCP("safety-test")
    register_safety_tools(mcp)
    return mcp


def _call(server, **args):
    result = asyncio.run(server.call_tool("get_safety_spacing", args))
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return result.structured_content


class TestTool:
    def test_happy_path_230v(self, server):
        out = _call(server, working_voltage_v=230, nominal_mains_v=230)
        assert out["success"]
        assert out["creepage_mm"] == 2.5 and out["clearance_mm"] == 1.5
        assert out["snapshot_date"] and out["source"]

    def test_reinforced_for_class2(self, server):
        out = _call(server, working_voltage_v=230, nominal_mains_v=230,
                    insulation="reinforced")
        assert out["success"] and out["creepage_mm"] == 5.0

    def test_error_path_structured(self, server):
        out = _call(server, working_voltage_v=230, overvoltage_category="X")
        assert out["success"] is False and "OVC" in out["error"]
