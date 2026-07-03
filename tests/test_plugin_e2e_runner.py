# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests für den E2E-Loop durchs Produkt: Testmodus-Prompt, Verdikt-Heuristik,
Feature-Lauf mit injiziertem ask, Report-Rendering und -Schreiben."""

from __future__ import annotations

import json
from types import SimpleNamespace

from plugin import e2e_runner, superfeatures


def _plan(tmp_path=None):
    return SimpleNamespace(run_cwd=str(tmp_path or "/proj"),
                           config_arg_path="/proj/.kicad-mcp/m.json",
                           claude_cmd=["claude"])


def _feat(key="untangle"):
    return superfeatures.get(key)


class TestPromptAndJudge:
    def test_suffix_forbids_mutation(self):
        p = e2e_runner.build_test_prompt(_feat())
        assert p.startswith(_feat().prompt)
        assert "[E2E-TESTMODUS]" in p and "KEINE Board- oder Datei-Mutation" in p

    def test_judge_matrix(self):
        j = e2e_runner.judge
        assert j({"ok": False}, [], []) == ("FAIL", "bridge-fehler")
        assert j({"ok": True, "mcp_status": "failed: kicad-mcp",
                  "text": "x"}, [], []) == ("FAIL", "mcp-nicht-verbunden")
        assert j({"ok": True, "text": ""}, [], []) == ("FAIL", "leere-antwort")
        assert j({"ok": True, "text": "Plan…"}, ["t"],
                 ["Go", "Abbrechen"]) == ("PASS", "plan+go-gate")
        assert j({"ok": True, "text": "Welches IC meinst du?"}, [],
                 []) == ("PASS", "fragt-nach")
        assert j({"ok": True, "text": "Bericht."}, ["analyze_pcb_nets"],
                 []) == ("PASS", "bericht")
        assert j({"ok": True, "text": "Nur geredet."}, [],
                 []) == ("WARN", "keine-tools-benutzt")


class TestRunFeature:
    def test_happy_path_records_everything(self):
        captured = {}

        def fake_ask(prompt, **kw):
            captured.update(kw, prompt=prompt)
            kw["on_tool"]("list_pcb_footprints", {})
            return {"ok": True, "mcp_status": "connected",
                    "text": "Plan steht.\n[[CHOICES: Go|Abbrechen]]",
                    "session_id": "S"}

        res = e2e_runner.run_feature(_feat(), _plan(), ask=fake_ask)
        assert res.verdict == "PASS" and res.tag == "plan+go-gate"
        assert res.tools == ["list_pcb_footprints"]
        assert res.choices == ["Go", "Abbrechen"]
        assert "[E2E-TESTMODUS]" in captured["prompt"]
        assert captured["session_id"] is None  # frisch, kein Kontext-Bleed
        assert "--max-turns" in captured["extra_args"]

    def test_ask_exception_never_kills_loop(self):
        def boom(prompt, **kw):
            raise OSError("claude weg")

        res = e2e_runner.run_feature(_feat(), _plan(), ask=boom)
        assert res.verdict == "FAIL" and res.tag == "runner-exception"
        assert "claude weg" in res.error


class TestRunAllAndReport:
    def _fake_ask_ok(self, prompt, **kw):
        return {"ok": True, "mcp_status": "connected",
                "text": "ok?", "session_id": "S"}

    def test_run_all_defaults_to_shipped(self):
        lines = []
        results = e2e_runner.run_all(_plan(), ask=self._fake_ask_ok,
                                     on_line=lines.append)
        shipped = [f for f in superfeatures.all_features()
                   if f.status == superfeatures.SHIPPED]
        assert len(results) == len(shipped)
        assert any("→ PASS" in ln for ln in lines)

    def test_report_renders_and_writes(self, tmp_path):
        feats = [f for f in superfeatures.all_features()][:2]
        results = e2e_runner.run_all(_plan(tmp_path), features=feats,
                                     ask=self._fake_ask_ok)
        results[0].verdict, results[0].tag = "FAIL", "mcp-nicht-verbunden"
        md, js = e2e_runner.write_report(str(tmp_path), results,
                                         {"date": "2026-07-03",
                                          "board": "b.kicad_pcb",
                                          "transport": "http",
                                          "language": "de"})
        text = open(md, encoding="utf-8").read()
        assert "# E2E-Report" in text and "FAIL" in text and "PASS" in text
        # FAIL steht in den Details zuerst (zum Zurücklesen sortiert)
        assert text.index("— FAIL") < text.index("— PASS")
        data = json.load(open(js, encoding="utf-8"))
        assert data["meta"]["transport"] == "http"
        assert len(data["results"]) == 2
