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
        assert keys == ["idee", "pruefen", "schaltplan", "berechnung",
                        "platine", "bilder"]
        assert res["ok"] is True
        # echte Dateien
        assert os.path.isfile(res["board_path"])
        assert res["board_path"].endswith(".kicad_pcb")
        assert list(tmp_path.glob("*.kicad_sch"))
        # Sichtbare Tool-Kette: die drei echten MCP-Tools stehen als ⚙-Zeilen
        # im Transkript (Feld-Wunsch „die Entstehung verfolgen").
        tool_lines = [ln for ln in lines if ln.startswith("⚙")]
        assert any("validate_design" in ln for ln in tool_lines)
        assert any("generate_schematic" in ln for ln in tool_lines)
        assert any("generate_pcb" in ln for ln in tool_lines)
        # nummerierte Narration je Schritt — erste Person („Ich …"), der Nutzer
        # schaut zu; Steckbrief zuerst, dann die gebauten Schritte.
        assert lines[0].startswith("①")
        assert any(ln.startswith("① Das baue ich jetzt") for ln in lines)
        assert any(ln.startswith("⑤ Ich route die Platine") for ln in lines)

    def test_summary_line_reports_success(self, tmp_path):
        res = demo.run_demo(str(tmp_path))
        assert "Demo fertig" in demo.summary_line(res)
        # 6 Schritte inkl. Bild-Render (best-effort: ok auch ohne kicad-cli).
        assert "6/6" in demo.summary_line(res)

    def test_step_failure_does_not_crash_run(self, tmp_path, monkeypatch):
        # Tool-Kette bricht → prüfen/schaltplan/platine ok:False, Ablauf lebt
        class _Boom:
            def call_tool(self, *a, **k):
                raise RuntimeError("kaputt")
        res = demo.run_demo(str(tmp_path), server=_Boom())
        assert res["ok"] is False
        by = {s["key"]: s for s in res["steps"]}
        assert by["schaltplan"]["ok"] is False
        assert by["pruefen"]["ok"] is False       # validate_design brach
        assert by["idee"]["ok"] is True           # reine Schritte laufen
        assert by["berechnung"]["ok"] is True     # Rechnung braucht keinen Server
        assert "Problemen" in demo.summary_line(res)

    def test_build_is_byte_identical_to_generate_project(self, tmp_path):
        # Sicherheitsnetz: die zerlegte Tool-Kette (generate_schematic +
        # generate_pcb) muss dieselben Bytes liefern wie das gebündelte
        # generate_project — sonst driften die DRC-/Determinismus-Gates.
        import json as _json
        from kicad_mcp.selftest import SPEC_PATH, load_spec
        from kicad_mcp.generators.schematic.builder import build_schematic
        from kicad_mcp.generators.pcb.builder import build_pcb
        spec = load_spec(SPEC_PATH)
        name = spec.get("project_name", "kicad_mcp_demo")
        res = demo.run_demo(str(tmp_path))
        sch_demo = open(res["board_path"][:-10] + ".kicad_sch",
                        encoding="utf-8").read()
        pcb_demo = open(res["board_path"], encoding="utf-8").read()
        sch_ref = build_schematic(_json.loads(_json.dumps(spec["parts"])),
                                  _json.loads(_json.dumps(spec["nets"])),
                                  name, optimize=True)
        pcb_ref = build_pcb(_json.loads(_json.dumps(spec["parts"])),
                            _json.loads(_json.dumps(spec["nets"])),
                            spec.get("board") or {}, name)
        assert sch_demo == sch_ref
        assert pcb_demo == pcb_ref
