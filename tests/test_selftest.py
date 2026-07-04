# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone-Systemtest (``python -m kicad_mcp.selftest``, 0.9.0).

Der Selftest ist das Orchestrierungs-Werkzeug für Feldtests auf fremden
Rechnern — er muss selbst wasserdicht sein: Spec valide, Demo-Pipeline
(spec→sch→pcb→Tools) grün, Fehler-Isolation (ein roter Schritt killt nie
den Lauf), Report maschinenlesbar, Exit-Code ehrlich.
"""

from __future__ import annotations

import json
import os

from kicad_mcp import selftest
from kicad_mcp.generators.validator import validate_all


class TestSpec:
    def test_bundled_spec_is_valid(self):
        spec = selftest.load_spec()
        errors = validate_all(spec["parts"], spec["nets"],
                              spec.get("board") or {})
        assert errors == []

    def test_spec_covers_power_and_signal_nets(self):
        # Der Demo-Umfang, auf den die Schritte sich verlassen
        spec = selftest.load_spec()
        types = {n.get("type", "signal") for n in spec["nets"]}
        assert "power" in types and "signal" in types
        assert len(spec["parts"]) >= 5


class TestRunAll:
    def test_full_pipeline_green_without_optional_deps(self, tmp_path):
        # Container-Realität: kein pcbnew, kein kicad-cli → SKIP, nie FAIL.
        report = selftest.run_all(str(tmp_path), include_handshake=False)
        fails = [s for s in report["steps"]
                 if s["status"] == selftest.FAIL]
        assert fails == [], f"rote Schritte: {fails}"
        assert report["summary"][selftest.PASS] >= 6
        # Demo-Projekt wurde wirklich erzeugt
        proj = tmp_path / "demo_projekt"
        assert list(proj.glob("*.kicad_sch"))
        assert list(proj.glob("*.kicad_pcb"))

    def test_reports_written_and_machine_readable(self, tmp_path):
        report = selftest.run_all(str(tmp_path), include_handshake=False)
        assert os.path.isfile(report["report_json"])
        assert os.path.isfile(report["report_md"])
        with open(report["report_json"], encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["summary"] == {
            k: v for k, v in report["summary"].items()}
        assert "PASS" in open(report["report_md"], encoding="utf-8").read()

    def test_failing_step_never_kills_the_run(self, tmp_path):
        def boom(_ctx):
            raise RuntimeError("kaputt")

        def fine(_ctx):
            return {"ok": 1}

        report = selftest.run_all(
            str(tmp_path), include_handshake=False,
            steps=[("a", boom), ("b", fine)])
        assert [s["status"] for s in report["steps"]] == [
            selftest.FAIL, selftest.PASS]
        assert "kaputt" in report["steps"][0]["error"]

    def test_skip_step_counts_as_skip(self, tmp_path):
        def skip(_ctx):
            raise selftest.SkipStep("fehlt halt")

        report = selftest.run_all(str(tmp_path), include_handshake=False,
                                  steps=[("s", skip)])
        assert report["steps"][0]["status"] == selftest.SKIP
        assert report["summary"][selftest.FAIL] == 0

    def test_on_line_streams_and_marks_errors(self, tmp_path):
        def boom(_ctx):
            raise RuntimeError("rot")

        lines: list = []
        selftest.run_all(str(tmp_path), include_handshake=False,
                         steps=[("x", boom)], on_line=lines.append)
        assert lines and lines[0].startswith("[FEHLER] x")


class TestMainCli:
    def test_exit_codes(self, tmp_path, monkeypatch, capsys):
        # grün → 0, still bis auf die eine OK-Zeile
        monkeypatch.setattr(selftest, "STEPS",
                            [("gut", lambda ctx: {"ok": True})])
        rc = selftest.main(["--out", str(tmp_path / "ok"), "--no-handshake"])
        out = capsys.readouterr().out
        assert rc == 0 and "SELFTEST OK" in out
        assert "[ok]" not in out  # Interaktion nur bei Fehlern

        # rot → 1, FAIL-Zeilen sichtbar
        def boom(_ctx):
            raise RuntimeError("explodiert")

        monkeypatch.setattr(selftest, "STEPS", [("schlecht", boom)])
        rc = selftest.main(["--out", str(tmp_path / "bad"), "--no-handshake"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "SELFTEST FEHLGESCHLAGEN" in out and "[FEHLER]" in out

    def test_stdio_handshake_step_real(self, tmp_path):
        # Der Ernstfall einmal echt: Server-Spawn + initialize + tools/list.
        ctx: dict = {}
        detail = selftest._step_stdio_handshake(ctx)
        assert detail == {"handshake": "ok"}


class TestRamMeasurement:
    def test_peak_ram_is_measured_and_reported(self, tmp_path):
        # Feld-Frage 0.9.0 ("braucht viel RAM?") → Messung im Report
        val = selftest.peak_ram_mb()
        assert val is None or val > 1.0
        report = selftest.run_all(str(tmp_path), include_handshake=False,
                                  steps=[("noop", lambda ctx: {})])
        assert "peak_ram_mb" in report["meta"]
        if report["meta"]["peak_ram_mb"]:
            assert "Peak-RAM" in selftest.render_report(report)