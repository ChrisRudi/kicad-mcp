# SPDX-License-Identifier: GPL-3.0-or-later
"""Agenten-Backends (Claude Code · Codex): das Produkt ist nicht ein Modell.

Der Vertrag: Claude bleibt der erprobte Default (delegiert an die getesteten
claude_bridge-Funktionen), Codex ist ein isoliert dazusteckbares, MCP-fähiges
CLI. Getestet werden die reinen, plattformunabhängigen Teile — find/command/
config/normalize — sowie dass die Bridge das gewählte Backend wirklich nutzt.
"""

from __future__ import annotations

import json

from plugin import backends


class TestRegistry:
    def test_default_is_claude(self):
        assert backends.DEFAULT_KEY == "claude_code"
        assert backends.get("").key == "claude_code"
        assert backends.get("unbekannt").key == "claude_code"

    def test_available_order_default_first(self):
        keys = [b.key for b in backends.available()]
        assert keys[0] == "claude_code" and "codex" in keys

    def test_codex_marked_experimental(self):
        assert backends.get("codex").experimental is True
        assert backends.get("claude_code").experimental is False


class TestClaudeBackend:
    def test_config_path_unchanged(self):
        b = backends.get("claude_code")
        assert b.config_path("/x/m.json") == "/x/m.json"

    def test_build_command_delegates_to_bridge(self):
        from plugin import claude_bridge
        b = backends.get("claude_code")
        cmd = b.build_command(["claude"], "hi", "/m.json", None, None,
                              claude_bridge.BEHAVIOR_SYSTEM_PROMPT, language="")
        assert cmd[:3] == ["claude", "-p", "hi"]
        assert "--mcp-config" in cmd and "--strict-mcp-config" in cmd

    def test_normalize_maps_claude_events(self):
        from plugin import claude_bridge as cb
        init = json.dumps({"type": "system", "subtype": "init",
                           "mcp_servers": [{"name": "kicad-mcp",
                                            "status": "connected"}]})
        assert backends.get("claude_code").normalize(init)["mcp_status"] \
            == "connected"
        tool = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use",
             "name": "mcp__kicad-mcp__list_pcb_footprints"}]}})
        nev = backends.get("claude_code").normalize(tool)
        assert nev["has_kicad"] is True and nev["tools"]
        res = json.dumps({"type": "result", "subtype": "success",
                          "result": "42 Vias", "session_id": "S1"})
        nev = backends.get("claude_code").normalize(res)
        assert nev["is_result"] and nev["result_text"] == "42 Vias"
        assert nev["session_id"] == "S1"
        # Nicht-JSON-Rauschen → None (wie der Claude-Parser)
        assert cb.parse_stream_event("warn: x") is None


class TestCodexBackend:
    def test_config_path_is_toml(self):
        assert backends.get("codex").config_path("/x/m.json") \
            == "/x/m.codex.toml"

    def test_build_command_exec_json_prompt_last(self):
        b = backends.get("codex")
        cmd = b.build_command(["codex"], "prüf das Board", "/x/m.codex.toml",
                              None, None, "SYS", language="Deutsch")
        assert cmd[0] == "codex" and cmd[1] == "exec"
        assert "--json" in cmd and "--config" in cmd
        assert cmd[cmd.index("--config") + 1] == "/x/m.codex.toml"
        # System-Prompt + Sprache dem Prompt vorangestellt (kein Claude-Flag)
        assert cmd[-1].startswith("SYS") and "prüf das Board" in cmd[-1]
        assert "Deutsch" in cmd[-1]

    def test_build_command_resume_with_session(self):
        cmd = backends.get("codex").build_command(
            ["codex"], "p", "/c.toml", "sess-7", None, "", language="")
        assert "--session" in cmd and cmd[cmd.index("--session") + 1] == "sess-7"

    def test_write_mcp_config_toml(self, tmp_path, monkeypatch):
        from plugin import mcp_config
        monkeypatch.setattr(mcp_config, "find_kicad_python",
                            lambda: "/k/python")
        root = tmp_path / "mcp"; (root / "kicad_mcp").mkdir(parents=True)
        cfg = tmp_path / "m.codex.toml"
        path = backends.get("codex").write_mcp_config(
            str(cfg), str(root), deps_dir="")
        text = open(path, encoding="utf-8").read()
        assert "[mcp_servers.kicad-mcp]" in text
        assert 'command = "/k/python"' in text
        assert "KICAD_MCP_TRANSPORT = \"stdio\"" in text

    def test_normalize_codex_jsonl(self):
        b = backends.get("codex")
        msg = json.dumps({"type": "item.completed",
                          "item": {"type": "agent_message", "text": "Hallo"}})
        assert b.normalize(msg)["text"] == "Hallo"
        tool = json.dumps({"type": "item.started", "item": {
            "type": "mcp_tool_call", "tool": "kicad-mcp.list_pcb_footprints",
            "arguments": {"pcb_path": "x"}}})
        nev = b.normalize(tool)
        assert nev["has_kicad"] is True
        assert nev["tools"][0][0] == "list_pcb_footprints"
        done = json.dumps({"type": "turn.completed", "text": "fertig",
                           "session_id": "abc"})
        nev = b.normalize(done)
        assert nev["is_result"] and nev["result_text"] == "fertig"
        assert nev["session_id"] == "abc"

    def test_normalize_survives_unknown_schema(self):
        # Ein Codex-Update mit fremdem Schema darf den Zug nicht killen
        assert backends.get("codex").normalize('{"type":"weird"}') is None
        assert backends.get("codex").normalize("nicht json") is None


class TestBridgeUsesBackend:
    def test_ask_routes_through_selected_backend(self, monkeypatch):
        """ask(backend=codex) baut den Codex-Command und nutzt Codex-Parsing."""
        from plugin import claude_bridge
        from tests.test_plugin_bridge import _FakeProc, _popen_for

        codex = backends.get("codex")
        monkeypatch.setattr(codex, "find", lambda: ["codex"])
        monkeypatch.setattr(codex, "write_mcp_config",
                            lambda *a, **k: a[0] if a else "")
        seen = {}
        # ein Codex-Abschluss-Ereignis reicht für eine ok-Antwort
        done = json.dumps({"type": "turn.completed", "text": "42 Vias",
                           "session_id": "S9"})
        r = claude_bridge.ask("x", "/proj", "/m.json", backend=codex,
                              _popen=_popen_for(_FakeProc([done]), seen))
        assert r["ok"] and r["text"] == "42 Vias"
        assert r["session_id"] == "S9"
        assert seen["cmd"][0] == "codex" and seen["cmd"][1] == "exec"
