# SPDX-License-Identifier: GPL-3.0-or-later
"""Demo-Ablauf (Demo-Knopf): Idee → Schaltplan → Berechnung → Platine.

Der Demo führt die Systemtest-Schaltung sichtbar vor; er muss deterministisch,
LLM-frei und robust sein (ein Schritt-Fehler killt den Ablauf nicht) — sonst
blamiert er sich beim Onboarding.
"""

from __future__ import annotations

import os

from kicad_mcp import demo


class TestResistorMath:
    def test_parse_ohms_units(self):
        assert demo._parse_ohms("1k") == 1000.0
        assert demo._parse_ohms("4.7k") == 4700.0
        assert demo._parse_ohms("220") == 220.0
        assert demo._parse_ohms("1M") == 1_000_000.0
        assert demo._parse_ohms("bla") is None

    def test_led_check_from_spec_values(self):
        spec = {"parts": [{"ref": "U1", "value": "AMS1117-3.3"},
                          {"ref": "R1", "value": "1k"}], "nets": []}
        calc = demo._led_resistor_check(spec)
        # (3.3 - 2.0) / 1000 = 1.3 mA
        assert calc["ok"] is True
        assert abs(calc["current_ma"] - 1.3) < 0.05
        assert "mA" in calc["text"]

    def test_led_check_flags_out_of_range(self):
        # 100 Ω → (3.3-2)/100 = 13 mA ok; 10 Ω → 130 mA zu viel
        spec = {"parts": [{"ref": "U1", "value": "reg-3.3"},
                          {"ref": "R1", "value": "10"}], "nets": []}
        calc = demo._led_resistor_check(spec)
        assert calc["ok"] is False
        assert calc["current_ma"] > 20

    def test_led_check_without_resistor(self):
        calc = demo._led_resistor_check({"parts": [], "nets": []})
        assert calc["ok"] is False and "Vorwiderstand" in calc["text"]


class TestRunDemo:
    def test_full_flow_generates_board_and_calc(self, tmp_path):
        lines = []
        res = demo.run_demo(str(tmp_path), on_step=lines.append)
        keys = [s["key"] for s in res["steps"]]
        assert keys == ["idee", "schaltplan", "berechnung", "platine"]
        assert res["ok"] is True
        # echte Dateien
        assert os.path.isfile(res["board_path"])
        assert res["board_path"].endswith(".kicad_pcb")
        assert list(tmp_path.glob("*.kicad_sch"))
        # narriert je Schritt genau eine Zeile
        assert len(lines) == 4
        assert lines[0].startswith("①") and lines[3].startswith("④")

    def test_summary_line_reports_success(self, tmp_path):
        res = demo.run_demo(str(tmp_path))
        assert "Demo fertig" in demo.summary_line(res)
        assert "4/4" in demo.summary_line(res)

    def test_step_failure_does_not_crash_run(self, tmp_path, monkeypatch):
        # generate_project bricht → schaltplan/platine ok:False, Ablauf lebt
        class _Boom:
            def call_tool(self, *a, **k):
                raise RuntimeError("kaputt")
        res = demo.run_demo(str(tmp_path), server=_Boom())
        assert res["ok"] is False
        by = {s["key"]: s for s in res["steps"]}
        assert by["schaltplan"]["ok"] is False
        assert by["idee"]["ok"] is True          # reine Schritte laufen
        assert by["berechnung"]["ok"] is True     # Rechnung braucht keinen Server
        assert "Problemen" in demo.summary_line(res)
