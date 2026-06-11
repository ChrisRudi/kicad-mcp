# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the KiCad plugin's pure-logic layer (no KiCad/wx needed):
the Claude Code subprocess bridge + the MCP config generator.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from plugin import claude_bridge, mcp_config


# --- claude_bridge -----------------------------------------------------------

class TestBuildCommand:
    def test_core_flags_present(self):
        cmd = claude_bridge.build_command(
            ["claude"], "hallo", "/tmp/m.json", session_id=None)
        assert cmd[:3] == ["claude", "-p", "hallo"]
        assert "--mcp-config" in cmd and "/tmp/m.json" in cmd
        assert "--strict-mcp-config" in cmd          # only the bundled MCP
        assert "--dangerously-skip-permissions" in cmd  # headless tool use
        assert cmd[cmd.index("--output-format") + 1] == "json"
        assert "--resume" not in cmd                 # first turn, no session

    def test_resume_added_when_session(self):
        cmd = claude_bridge.build_command(
            ["wsl.exe", "claude"], "weiter", "/tmp/m.json", session_id="abc-123")
        assert cmd[:2] == ["wsl.exe", "claude"]
        assert cmd[cmd.index("--resume") + 1] == "abc-123"


class TestParseReply:
    def test_result_schema(self):
        out = json.dumps({"result": "42 Vias", "session_id": "s1"})
        text, sid = claude_bridge._parse_json_reply(out)
        assert text == "42 Vias" and sid == "s1"

    def test_assistant_content_schema(self):
        out = json.dumps({
            "assistant_content": [{"type": "text", "text": "Hallo "},
                                  {"type": "text", "text": "Welt"}],
            "session_id": "s2",
        })
        text, sid = claude_bridge._parse_json_reply(out)
        assert text == "Hallo Welt" and sid == "s2"

    def test_plain_text_fallback(self):
        text, sid = claude_bridge._parse_json_reply("nicht json")
        assert text == "nicht json" and sid is None

    def test_empty(self):
        assert claude_bridge._parse_json_reply("") == ("", None)


class TestAsk:
    def _runner(self, stdout="", stderr="", rc=0):
        def _run(cmd, **kw):
            return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)
        return _run

    def test_happy_path_returns_text_and_session(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        r = claude_bridge.ask(
            "x", "/proj", "/m.json",
            _runner=self._runner(json.dumps({"result": "ok", "session_id": "S"})),
        )
        assert r["ok"] and r["text"] == "ok" and r["session_id"] == "S"

    def test_claude_missing(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: None)
        r = claude_bridge.ask("x", "/proj", "/m.json", _runner=self._runner())
        assert r["ok"] is False and "claude" in r["error"].lower()

    def test_nonzero_with_no_text_is_error(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        r = claude_bridge.ask(
            "x", "/proj", "/m.json",
            _runner=self._runner(stdout="", stderr="boom", rc=1),
        )
        assert r["ok"] is False and "boom" in r["error"]

    def test_session_preserved_when_no_new_id(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        r = claude_bridge.ask(
            "x", "/proj", "/m.json", session_id="OLD",
            _runner=self._runner("plain reply"),
        )
        assert r["ok"] and r["text"] == "plain reply" and r["session_id"] == "OLD"

    def test_find_claude_native(self, monkeypatch):
        monkeypatch.setattr(claude_bridge.shutil, "which",
                            lambda c: "/usr/bin/claude" if c == "claude" else None)
        assert claude_bridge.find_claude() == ["/usr/bin/claude"]

    def test_find_claude_wsl_fallback(self, monkeypatch):
        monkeypatch.setattr(claude_bridge.shutil, "which",
                            lambda c: "/usr/bin/wsl" if c in ("wsl", "wsl.exe") else None)
        assert claude_bridge.find_claude() == ["/usr/bin/wsl", "claude"]


class TestHiddenConsole:
    """The claude child must not flash a black console window on Windows."""

    def test_windows_suppresses_console(self):
        kw = claude_bridge.hidden_console_kwargs("nt")
        assert kw["creationflags"] == 0x08000000  # CREATE_NO_WINDOW
        assert "stdin" in kw

    def test_posix_needs_nothing(self):
        assert claude_bridge.hidden_console_kwargs("posix") == {}

    def test_ask_passes_flags_to_runner(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        monkeypatch.setattr(claude_bridge, "hidden_console_kwargs",
                            lambda: {"creationflags": 0x08000000})
        seen = {}

        def _run(cmd, **kw):
            seen.update(kw)
            return SimpleNamespace(stdout="ok", stderr="", returncode=0)

        r = claude_bridge.ask("x", "/proj", "/m.json", _runner=_run)
        assert r["ok"] and seen["creationflags"] == 0x08000000


# --- mcp_config --------------------------------------------------------------

class TestMcpConfig:
    def test_build_shape(self):
        cfg = mcp_config.build_mcp_config("/repo", "/kipy/python.exe")
        srv = cfg["mcpServers"]["kicad-mcp"]
        assert srv["type"] == "stdio"
        assert srv["command"] == "/kipy/python.exe"
        assert srv["args"] == ["-m", "kicad_mcp.server"]
        assert srv["env"]["PYTHONPATH"] == "/repo"

    def test_write_creates_valid_json(self, tmp_path):
        root = tmp_path / "repo"; root.mkdir()
        py = tmp_path / "python.exe"; py.write_text("")
        out = tmp_path / ".kicad-mcp" / "claude_mcp.json"
        mcp_config.write_mcp_config(str(out), str(root), str(py))
        data = json.loads(out.read_text())
        assert data["mcpServers"]["kicad-mcp"]["command"] == str(py)

    def test_write_errors_on_missing_root(self, tmp_path):
        py = tmp_path / "python.exe"; py.write_text("")
        with pytest.raises(RuntimeError):
            mcp_config.write_mcp_config(
                str(tmp_path / "x.json"), str(tmp_path / "nope"), str(py))

    def test_write_errors_without_python(self, tmp_path, monkeypatch):
        root = tmp_path / "repo"; root.mkdir()
        monkeypatch.setattr(mcp_config, "find_kicad_python", lambda: None)
        monkeypatch.delenv("KICAD_PYTHON_PATH", raising=False)
        with pytest.raises(RuntimeError):
            mcp_config.write_mcp_config(str(tmp_path / "x.json"), str(root))

    def test_find_kicad_python_env_override(self, tmp_path, monkeypatch):
        py = tmp_path / "python.exe"; py.write_text("")
        monkeypatch.setenv("KICAD_PYTHON_PATH", str(py))
        assert mcp_config.find_kicad_python() == str(py)
