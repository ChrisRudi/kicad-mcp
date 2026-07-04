# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests für die GUI-0.8.0-Fundamente: Auto-Sprache (i18n), persistente
Einstellungen (settings → Env) und die Antwort-Chips-Parser (Entscheidungs-
Marker + Codeblöcke) in claude_bridge."""

from __future__ import annotations

import json

from plugin import claude_bridge, i18n, settings


class TestDetectLang:
    def test_explicit_setting_wins(self, tmp_path):
        common = tmp_path / "kicad_common.json"
        common.write_text(json.dumps({"system": {"language": "de"}}))
        assert i18n.detect_lang("en", str(common)) == "en"

    def test_kicad_language_used(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
        common = tmp_path / "kicad_common.json"
        common.write_text(json.dumps({"system": {"language": "Deutsch"}}))
        assert i18n.detect_lang("auto", str(common)) == "de"

    def test_kicad_default_falls_to_locale(self, tmp_path, monkeypatch):
        common = tmp_path / "kicad_common.json"
        common.write_text(json.dumps({"system": {"language": "Default"}}))
        monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
        assert i18n.detect_lang("auto", str(common)) == "de"
        monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
        assert i18n.detect_lang("auto", str(common)) == "en"

    def test_broken_common_is_safe(self, tmp_path, monkeypatch):
        common = tmp_path / "kicad_common.json"
        common.write_text("{kaputt")
        monkeypatch.setenv("LC_ALL", "en_GB.UTF-8")
        assert i18n.detect_lang("auto", str(common)) == "en"


class TestTr:
    def test_german_passthrough(self):
        i18n.set_lang("de")
        assert i18n.tr("Senden") == "Senden"

    def test_english_catalog(self):
        i18n.set_lang("en")
        try:
            assert i18n.tr("Senden") == "Send"
            assert i18n.tr("🧶 Entwirren") == "🧶 Untangle"
            # fehlender Eintrag → deutscher Text bleibt (kein Platzhalter)
            assert i18n.tr("nie übersetzt xyz") == "nie übersetzt xyz"
            assert i18n.reply_language_name() == "English"
        finally:
            i18n.set_lang("de")

    def test_all_feature_labels_translated(self):
        from plugin import superfeatures as sf
        missing = [f.label for f in sf.all_features() if f.label not in i18n._EN]
        assert not missing, f"Labels ohne EN-Übersetzung: {missing}"

    def test_all_category_labels_translated(self):
        from plugin import superfeatures as sf
        missing = [lb for _k, lb in sf.CATEGORIES if lb not in i18n._EN]
        assert not missing


class TestSettings:
    def test_roundtrip_and_defaults(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KICAD_MCP_STATE_DIR", str(tmp_path))
        assert settings.load()["language"] == "auto"
        assert settings.load()["backend"] == "claude_code"  # Default
        settings.save({"language": "en", "transport": "http",
                       "backend": "codex", "unbekannt": "wird ignoriert"})
        loaded = settings.load()
        assert loaded["language"] == "en" and loaded["transport"] == "http"
        assert loaded["backend"] == "codex"
        assert "unbekannt" not in loaded

    def test_apply_env_sets_only_configured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KICAD_MCP_STATE_DIR", str(tmp_path))
        settings.save({"transport": "http", "max_turns": 120})
        env: dict = {}
        applied = settings.apply_env(env)
        assert env["KICAD_MCP_TRANSPORT"] == "http"
        assert env["KICAD_MCP_MAX_TURNS"] == "120"
        assert "KICAD_MCP_NGSPICE" not in env  # nicht konfiguriert
        assert set(applied) == {"KICAD_MCP_TRANSPORT", "KICAD_MCP_MAX_TURNS"}

    def test_hand_set_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KICAD_MCP_STATE_DIR", str(tmp_path))
        settings.save({"transport": "http"})
        env = {"KICAD_MCP_TRANSPORT": "stdio"}  # Power-User hat Env gesetzt
        settings.apply_env(env)
        assert env["KICAD_MCP_TRANSPORT"] == "stdio"


class TestParseChoices:
    def test_marker_stripped_and_options_returned(self):
        text, opts = claude_bridge.parse_choices(
            "Plan steht.\n\n[[CHOICES: Go|Verwerfen]]")
        assert text == "Plan steht." and opts == ["Go", "Verwerfen"]

    def test_no_marker(self):
        text, opts = claude_bridge.parse_choices("Nur Text.")
        assert text == "Nur Text." and opts == []

    def test_marker_only_at_end(self):
        raw = "[[CHOICES: A|B]] mitten im Text bleibt stehen."
        text, opts = claude_bridge.parse_choices(raw)
        assert text == raw and opts == []

    def test_single_option_is_not_a_decision(self):
        text, opts = claude_bridge.parse_choices("Hm.\n[[CHOICES: Go]]")
        assert opts == [] and "[[CHOICES" in text

    def test_capped_at_four(self):
        _t, opts = claude_bridge.parse_choices(
            "x\n[[CHOICES: a|b|c|d|e|f]]")
        assert opts == ["a", "b", "c", "d"]


class TestExtractCodeBlocks:
    def test_fenced_blocks_found(self):
        text = ("Deck:\n```spice\nv1 in 0 dc 5\n.end\n```\n"
                "und Header:\n```c\n#define LED_PIN 5\n```\n")
        blocks = claude_bridge.extract_code_blocks(text)
        assert len(blocks) == 2
        assert blocks[0].startswith("v1 in 0") and "#define" in blocks[1]

    def test_empty_and_no_blocks(self):
        assert claude_bridge.extract_code_blocks("kein code") == []
        assert claude_bridge.extract_code_blocks("```\n\n```") == []


class TestLanguageInCommand:
    def test_language_appended_to_system_prompt(self):
        cmd = claude_bridge.build_command(
            ["claude"], "hi", "/m.json", None, language="English")
        idx = cmd.index("--append-system-prompt") + 1
        assert "Antworte IMMER in dieser Sprache: English." in cmd[idx]

    def test_no_language_no_suffix(self):
        cmd = claude_bridge.build_command(["claude"], "hi", "/m.json", None)
        idx = cmd.index("--append-system-prompt") + 1
        assert "Antworte IMMER" not in cmd[idx]
