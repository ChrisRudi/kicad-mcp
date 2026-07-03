# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the KiCad plugin's pure-logic layer (no KiCad/wx needed):
the Claude Code streaming bridge + the MCP config generator.
"""

from __future__ import annotations

import json

from plugin import claude_bridge, mcp_config


def _ev(**kw) -> str:
    return json.dumps(kw)


_INIT_OK = _ev(type="system", subtype="init", session_id="S1",
               mcp_servers=[{"name": "kicad-mcp", "status": "connected"}])
_INIT_BAD = _ev(type="system", subtype="init", session_id="S1",
                mcp_servers=[{"name": "kicad-mcp", "status": "failed"}])
_TOOL = _ev(type="assistant", message={"content": [
    {"type": "tool_use", "name": "mcp__kicad-mcp__list_pcb_footprints"}]})
_TEXT = _ev(type="assistant", message={"content": [
    {"type": "text", "text": "42 Vias"}]})
_RESULT = _ev(type="result", subtype="success", result="42 Vias",
              session_id="S1")


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def __iter__(self):
        return iter(self._lines)


class _FakeProc:
    def __init__(self, lines, rc=0, stderr="", pid=4321):
        self.stdout = _FakeStdout(lines)
        self.stderr = type("E", (), {"read": staticmethod(lambda: stderr)})()
        self.returncode = rc
        self.pid = pid
        self.killed = False
        self._exited = False

    def wait(self, timeout=None):
        self._exited = True
        return self.returncode

    def poll(self):
        return self.returncode if self._exited else None

    def kill(self):
        self.killed = True
        self._exited = True


def _popen_for(proc, capture=None):
    def _popen(cmd, **kw):
        if capture is not None:
            capture.update(kw, cmd=cmd)
        return proc
    return _popen


def _popen_seq(procs, calls=None):
    """A _popen returning ``procs`` in order, one per call — exercises the
    cold-start retry in ``ask`` (attempt 1, attempt 2, …). ``calls['n']``
    counts spawns."""
    it = iter(procs)

    def _popen(cmd, **kw):
        if calls is not None:
            calls["n"] = calls.get("n", 0) + 1
        return next(it)
    return _popen


# --- build_command ------------------------------------------------------------

class TestBuildCommand:
    def test_core_flags_present(self):
        cmd = claude_bridge.build_command(
            ["claude"], "hallo", "/tmp/m.json", session_id=None)
        assert cmd[:3] == ["claude", "-p", "hallo"]
        assert "--mcp-config" in cmd and "/tmp/m.json" in cmd
        assert "--strict-mcp-config" in cmd          # only the bundled MCP
        assert "--dangerously-skip-permissions" in cmd  # headless tool use
        # stream-json for live progress; claude demands --verbose with it
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in cmd
        assert "--resume" not in cmd                 # first turn, no session

    def test_extra_args_appended(self):
        # raw Claude switches (e.g. --model) are appended to the turn command
        cmd = claude_bridge.build_command(
            ["claude"], "x", "/m.json", session_id=None,
            extra_args=["--model", "sonnet"])
        assert cmd[-2:] == ["--model", "sonnet"]

    def test_no_extra_args_by_default(self):
        cmd = claude_bridge.build_command(["claude"], "x", "/m.json", None)
        assert "--model" not in cmd

    def test_file_mutation_tools_forbidden(self):
        # Each tool must be its OWN argv value after --disallowedTools — a
        # single comma-joined string matches nothing. Windows shell=PowerShell.
        cmd = claude_bridge.build_command(
            ["claude"], "x", "/m.json", session_id=None)
        i = cmd.index("--disallowedTools")
        for tool in ("Bash", "PowerShell", "Edit", "Write", "MultiEdit",
                     "NotebookEdit"):
            assert tool in cmd, f"{tool} not denied"
        assert cmd[i + 1] == "Bash" and "," not in cmd[i + 1]

    def test_behavior_system_prompt_injected(self):
        # CLAUDE.md isn't loaded (cwd = board folder) → rules injected per turn
        cmd = claude_bridge.build_command(["claude"], "x", "/m.json", None)
        sp = cmd[cmd.index("--append-system-prompt") + 1]
        assert "check_connectivity" in sp and "pcb_render" in sp
        assert "mcp__" in sp  # the "no MCP tools → say so, don't flail" rule
        # Dok 3 Hebel 1: canonical-naming rule so replies stay click-linkable
        assert "kanonisch" in sp and "<ref>.<pin>" in sp
        # Senior-PCB role framing + open-board steering to the Live (IPC) tools
        assert "Senior" in sp
        assert "ipc_" in sp and "BoardOpenError" in sp

    def test_max_turns_default_override_and_off(self, monkeypatch):
        monkeypatch.delenv("KICAD_MCP_MAX_TURNS", raising=False)
        cmd = claude_bridge.build_command(["claude"], "x", "/m.json", None)
        assert cmd[cmd.index("--max-turns") + 1] == str(
            claude_bridge.DEFAULT_MAX_TURNS)
        monkeypatch.setenv("KICAD_MCP_MAX_TURNS", "10")
        cmd = claude_bridge.build_command(["claude"], "x", "/m.json", None)
        assert cmd[cmd.index("--max-turns") + 1] == "10"
        monkeypatch.setenv("KICAD_MCP_MAX_TURNS", "0")
        assert "--max-turns" not in claude_bridge.build_command(
            ["claude"], "x", "/m.json", None)

    def test_resume_added_when_session(self):
        cmd = claude_bridge.build_command(
            ["wsl.exe", "claude"], "weiter", "/tmp/m.json", session_id="abc-123")
        assert cmd[:2] == ["wsl.exe", "claude"]
        assert cmd[cmd.index("--resume") + 1] == "abc-123"


# --- stream parsing -----------------------------------------------------------

class TestStreamParsing:
    def test_non_json_noise_ignored(self):
        assert claude_bridge.parse_stream_event("") is None
        assert claude_bridge.parse_stream_event("warn: blah") is None
        assert claude_bridge.parse_stream_event('"nur-string"') is None

    def test_mcp_status_connected_and_failed(self):
        ok = claude_bridge.parse_stream_event(_INIT_OK)
        bad = claude_bridge.parse_stream_event(_INIT_BAD)
        assert claude_bridge.mcp_status_from_init(ok) == "connected"
        assert claude_bridge.mcp_status_from_init(bad) == "failed: kicad-mcp"

    def test_describe_tool_use_shows_board_language(self):
        # status bar narrates in board language, not the raw tool slug
        ev = claude_bridge.parse_stream_event(_TOOL)
        assert claude_bridge.describe_event(ev) == "liest die Bauteile …"

    def test_extract_text(self):
        ev = claude_bridge.parse_stream_event(_TEXT)
        assert claude_bridge.extract_text(ev) == "42 Vias"

    def test_tool_names_short(self):
        ev = claude_bridge.parse_stream_event(_TOOL)
        assert claude_bridge.tool_names(ev) == ["list_pcb_footprints"]
        assert claude_bridge.tool_names(
            claude_bridge.parse_stream_event(_TEXT)) == []

    def test_tool_calls_carry_input(self):
        ev = claude_bridge.parse_stream_event(_ev(
            type="assistant", message={"content": [
                {"type": "tool_use", "name": "mcp__kicad-mcp__add_vias_to_pcb",
                 "input": {"vias": [{"x_mm": 1}, {"x_mm": 2}]}}]}))
        calls = claude_bridge.tool_calls(ev)
        assert calls == [("add_vias_to_pcb", {"vias": [{"x_mm": 1}, {"x_mm": 2}]})]


class TestDescribeTool:
    def test_mutation_with_count(self):
        assert claude_bridge.describe_tool(
            "add_vias_to_pcb", {"vias": [1, 2, 3, 4, 5, 6]}) == "6× Via gesetzt"

    def test_count_from_json_string_arg(self):
        # MCP tools take JSON-string args → the list may arrive as a string
        assert claude_bridge.describe_tool(
            "add_vias_to_pcb", {"vias": "[{}, {}]"}) == "2× Via gesetzt"

    def test_mutation_without_count_defaults_to_phrase(self):
        assert claude_bridge.describe_tool("ipc_route_pin_to_pin", {}) == \
            "Leiterbahn verlegt"

    def test_read_tool_is_calm(self):
        assert claude_bridge.describe_tool("check_connectivity", {}) == \
            "prüft die Konnektivität"

    def test_unknown_tool_humanised_not_slug(self):
        assert claude_bridge.describe_tool("some_new_tool", {}) == "some new tool"


class TestChangedTargets:
    def test_via_batch_yields_coords_and_nets(self):
        got = claude_bridge.changed_targets(
            "add_vias_to_pcb",
            {"vias": [{"x_mm": 10.0, "y_mm": 5.0, "net_name": "GND"},
                      {"x_mm": 12.0, "y_mm": 5.0, "net_name": "GND"}]})
        assert ("net", "GND") in got
        assert ("coord", (10.0, 5.0)) in got and ("coord", (12.0, 5.0)) in got

    def test_move_yields_refs(self):
        got = claude_bridge.changed_targets(
            "move_components", {"moves": [{"ref": "R12"}, {"reference": "U3"}]})
        assert ("ref", "R12") in got and ("ref", "U3") in got

    def test_pin_route_yields_pins(self):
        got = claude_bridge.changed_targets(
            "ipc_route_pin_to_pin",
            {"from_ref": "U1", "from_pin": 3, "to_ref": "R5", "to_pin": "2"})
        assert ("pin", ("U1", "3")) in got and ("pin", ("R5", "2")) in got

    def test_read_tool_contributes_nothing(self):
        assert claude_bridge.changed_targets("check_connectivity", {}) == []


# --- ask (streaming turn) -------------------------------------------------

class TestAsk:
    def test_happy_path_returns_text_session_and_status(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        statuses = []
        proc = _FakeProc([_INIT_OK, _TOOL, _TEXT, _RESULT])
        r = claude_bridge.ask("x", "/proj", "/m.json",
                              on_status=statuses.append,
                              _popen=_popen_for(proc))
        assert r["ok"] and r["text"] == "42 Vias" and r["session_id"] == "S1"
        assert r["mcp_status"] == "connected"
        assert any("liest die Bauteile" in s for s in statuses)

    def test_on_tool_and_on_proc_callbacks(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        tools, procs = [], []
        proc = _FakeProc([_INIT_OK, _TOOL, _TEXT, _RESULT])
        claude_bridge.ask("x", "/proj", "/m.json",
                          on_tool=lambda n, i: tools.append(n),
                          on_proc=procs.append, _popen=_popen_for(proc))
        assert tools == ["list_pcb_footprints"]  # streamed tool call surfaced
        assert procs == [proc]                    # live process handed back

    def test_extra_args_forwarded_to_command(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        seen = {}
        claude_bridge.ask("x", "/proj", "/m.json",
                          extra_args=["--model", "sonnet"],
                          _popen=_popen_for(_FakeProc([_RESULT]), seen))
        assert seen["cmd"][-2:] == ["--model", "sonnet"]

    def test_failed_mcp_retried_then_reported(self, monkeypatch):
        # default = 1 retry; both cold starts fail → still reported failed,
        # and the spawn happened twice (self-heal attempt was made).
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        monkeypatch.delenv("KICAD_MCP_CONNECT_RETRIES", raising=False)
        calls = {}
        procs = [_FakeProc([_INIT_BAD, _RESULT]),
                 _FakeProc([_INIT_BAD, _RESULT])]
        r = claude_bridge.ask("x", "/proj", "/m.json",
                              _popen=_popen_seq(procs, calls))
        assert r["mcp_status"].startswith("failed")
        assert calls["n"] == 2  # cold-start was retried once

    def test_failed_mcp_retry_recovers(self, monkeypatch):
        # attempt 1 fails to connect (no board tools), attempt 2 (warm) works.
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        monkeypatch.delenv("KICAD_MCP_CONNECT_RETRIES", raising=False)
        calls = {}
        procs = [_FakeProc([_INIT_BAD]),
                 _FakeProc([_INIT_OK, _TEXT, _RESULT])]
        r = claude_bridge.ask("x", "/proj", "/m.json",
                              _popen=_popen_seq(procs, calls))
        assert r["ok"] and r["mcp_status"] == "connected"
        assert r["text"] == "42 Vias" and calls["n"] == 2

    def test_connected_first_try_does_not_respawn(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        calls = {}
        procs = [_FakeProc([_INIT_OK, _TEXT, _RESULT])]
        r = claude_bridge.ask("x", "/proj", "/m.json",
                              _popen=_popen_seq(procs, calls))
        assert r["ok"] and calls["n"] == 1  # no needless second cold start

    def test_retry_disabled_via_env(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        monkeypatch.setenv("KICAD_MCP_CONNECT_RETRIES", "0")
        calls = {}
        procs = [_FakeProc([_INIT_BAD, _RESULT])]
        r = claude_bridge.ask("x", "/proj", "/m.json",
                              _popen=_popen_seq(procs, calls))
        assert r["mcp_status"].startswith("failed") and calls["n"] == 1

    def test_max_turns_hit_gives_friendly_note(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        limit_ev = _ev(type="result", subtype="error_max_turns",
                       session_id="S1")
        proc = _FakeProc([_INIT_OK, _TOOL, limit_ev])
        r = claude_bridge.ask("x", "/proj", "/m.json",
                              _popen=_popen_for(proc))
        # surfaced as ok with a clear note, NOT a cryptic error
        assert r["ok"] is True and "Limit" in r["text"]

    def test_claude_missing(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: None)
        r = claude_bridge.ask("x", "/proj", "/m.json",
                              _popen=_popen_for(_FakeProc([])))
        assert r["ok"] is False and "claude" in r["error"].lower()

    def test_stream_without_result_uses_text(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        proc = _FakeProc([_INIT_OK, _TEXT])
        r = claude_bridge.ask("x", "/proj", "/m.json",
                              _popen=_popen_for(proc))
        assert r["ok"] and r["text"] == "42 Vias"

    def test_empty_stream_is_error_with_stderr(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        proc = _FakeProc([], stderr="boom: not logged in")
        r = claude_bridge.ask("x", "/proj", "/m.json",
                              _popen=_popen_for(proc))
        assert r["ok"] is False and "boom" in r["error"]

    def test_session_preserved_when_no_new_id(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        proc = _FakeProc([_TEXT])
        r = claude_bridge.ask("x", "/proj", "/m.json", session_id="OLD",
                              _popen=_popen_for(proc))
        assert r["ok"] and r["session_id"] == "OLD"

    def test_idle_timeout_kills_and_explains(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])

        class _Blocking:
            def __iter__(self):
                import time as _t
                _t.sleep(5)  # longer than the test's idle_timeout
                return iter([])

        proc = _FakeProc([])
        proc.stdout = _Blocking()
        r = claude_bridge.ask("x", "/proj", "/m.json", idle_timeout=0.1,
                              _popen=_popen_for(proc))
        assert r["ok"] is False and proc.killed is True
        assert "Lebenszeichen" in r["error"]

    def test_ask_gives_mcp_startup_headroom(self, monkeypatch):
        # claude drops a too-slow MCP server silently → generous MCP_TIMEOUT.
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        monkeypatch.delenv("MCP_TIMEOUT", raising=False)
        seen = {}
        claude_bridge.ask("x", "/proj", "/m.json",
                          _popen=_popen_for(_FakeProc([_RESULT]), seen))
        assert seen["env"]["MCP_TIMEOUT"] == "300000"

    def test_ask_respects_user_mcp_timeout(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        monkeypatch.setenv("MCP_TIMEOUT", "5000")
        seen = {}
        claude_bridge.ask("x", "/proj", "/m.json",
                          _popen=_popen_for(_FakeProc([_RESULT]), seen))
        assert seen["env"]["MCP_TIMEOUT"] == "5000"

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

    def test_posix_needs_nothing(self):
        assert claude_bridge.hidden_console_kwargs("posix") == {}


class TestChildLifecycle:
    """claude + its MCP child must never outlive a closed chat / closed KiCad."""

    def test_turn_unregisters_on_completion(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        claude_bridge.terminate_all()  # clean slate
        claude_bridge.ask("x", "/proj", "/m.json",
                          _popen=_popen_for(_FakeProc([_RESULT])))
        # a finished turn leaves nothing tracked
        assert claude_bridge.terminate_all() == 0

    def test_inflight_proc_is_tracked_and_killed(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        claude_bridge.terminate_all()
        killed = {}
        monkeypatch.setattr(claude_bridge, "_kill_tree",
                            lambda p: killed.setdefault("p", p))
        # a proc that never streams EOF would block ask; simulate by
        # registering directly (unit-test terminate_all in isolation)
        proc = _FakeProc([])
        claude_bridge._register(proc)
        assert claude_bridge.terminate_all() == 1
        assert killed["p"] is proc
        # registry cleared afterwards
        assert claude_bridge.terminate_all() == 0

    def test_stop_kills_one_and_untracks(self, monkeypatch):
        # the Stopp button path: kill THIS turn's tree + drop it from tracking
        claude_bridge.terminate_all()
        killed = {}
        monkeypatch.setattr(claude_bridge, "_kill_tree",
                            lambda p: killed.setdefault("p", p))
        proc = _FakeProc([])
        claude_bridge._register(proc)
        claude_bridge.stop(proc)
        assert killed["p"] is proc
        assert claude_bridge.terminate_all() == 0  # already untracked

    def test_stop_none_is_safe(self):
        claude_bridge.stop(None)  # no raise

    def test_kill_tree_skips_exited(self, monkeypatch):
        called = {"run": False}
        monkeypatch.setattr(claude_bridge.subprocess, "run",
                            lambda *a, **k: called.update(run=True))
        proc = _FakeProc([])
        proc.kill()  # mark exited
        claude_bridge._kill_tree(proc)
        assert called["run"] is False  # no taskkill on an already-dead proc

    def test_start_new_session_requested(self, monkeypatch):
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        seen = {}
        claude_bridge.ask("x", "/proj", "/m.json",
                          _popen=_popen_for(_FakeProc([_RESULT]), seen))
        assert seen.get("start_new_session") is True


# --- mcp_config --------------------------------------------------------------

class TestMcpConfig:
    def test_build_shape(self):
        cfg = mcp_config.build_mcp_config("/repo", "/kipy/python.exe",
                                          deps_dir="")
        srv = cfg["mcpServers"]["kicad-mcp"]
        assert srv["type"] == "stdio"
        assert srv["command"] == "/kipy/python.exe"
        # -c bootstrap, NOT -m + PYTHONPATH: KiCad's bundled Python proved to
        # ignore the env var (._pth isolation) — sys.path must be set
        # in-process.
        assert srv["args"][0] == "-c"
        assert "sys.path[:0] = ['/repo']" in srv["args"][1]
        assert "kicad_mcp.server" in srv["args"][1]
        assert srv["env"]["PYTHONPATH"] == "/repo"  # belt-and-suspenders
        # Chat läuft in der KiCad-GUI: der Server darf nie einen zweiten
        # Editor auto-spawnen (Geister-Instanz = alle Links tot).
        assert srv["env"]["KICAD_MCP_NO_AUTO_OPEN"] == "1"
        # stdio-Config pinnt den Transport: der Fallback-Server darf das
        # http-Env des Plugins nicht erben (sonst bindet er Port 8331 statt
        # stdio zu sprechen — Feld-Befund 0.8.2).
        assert srv["env"]["KICAD_MCP_TRANSPORT"] == "stdio"

    def test_bootstrap_includes_deps_dir_and_escapes_windows_paths(self):
        import ast

        from plugin import deps
        code = mcp_config.server_bootstrap_code(r"C:\plug\mcp",
                                                r"C:\plug\_deps")
        # the generated path list must be valid Python despite backslashes, and
        # carry the deps dir plus pywin32's .pth dirs (win32, win32/lib) — those
        # are required so mcp's eager ``import pywintypes`` resolves under a
        # ``pip install --target`` _deps (see deps.pywin32_path_entries).
        list_src = code.split("= ", 1)[1].split("];")[0] + "]"
        assert ast.literal_eval(list_src) == (
            [r"C:\plug\mcp", r"C:\plug\_deps"]
            + deps.pywin32_path_entries(r"C:\plug\_deps")
        )

    def test_write_creates_valid_json(self, tmp_path):
        root = tmp_path / "repo"; root.mkdir()
        py = tmp_path / "python.exe"; py.write_text("")
        out = tmp_path / ".kicad-mcp" / "claude_mcp.json"
        mcp_config.write_mcp_config(str(out), str(root), str(py))
        data = json.loads(out.read_text())
        assert data["mcpServers"]["kicad-mcp"]["command"] == str(py)

    def test_write_errors_on_missing_root(self, tmp_path):
        py = tmp_path / "python.exe"; py.write_text("")
        import pytest
        with pytest.raises(RuntimeError):
            mcp_config.write_mcp_config(
                str(tmp_path / "x.json"), str(tmp_path / "nope"), str(py))

    def test_write_errors_without_python(self, tmp_path, monkeypatch):
        root = tmp_path / "repo"; root.mkdir()
        monkeypatch.setattr(mcp_config, "find_kicad_python", lambda: None)
        monkeypatch.delenv("KICAD_PYTHON_PATH", raising=False)
        import pytest
        with pytest.raises(RuntimeError):
            mcp_config.write_mcp_config(str(tmp_path / "x.json"), str(root))

    def test_find_kicad_python_env_override(self, tmp_path, monkeypatch):
        py = tmp_path / "python.exe"; py.write_text("")
        monkeypatch.setenv("KICAD_PYTHON_PATH", str(py))
        assert mcp_config.find_kicad_python() == str(py)


# --- warm-server (http) wiring --------------------------------------------------

class TestHttpMcpConfig:
    def test_build_http_shape_with_token(self):
        cfg = mcp_config.build_http_mcp_config(
            "http://127.0.0.1:8331/mcp/", "tok123")
        srv = cfg["mcpServers"]["kicad-mcp"]
        assert srv["type"] == "http"
        assert srv["url"] == "http://127.0.0.1:8331/mcp/"
        assert srv["headers"] == {"Authorization": "Bearer tok123"}
        # http mode: claude spawns NOTHING — no command/args in the config
        assert "command" not in srv and "args" not in srv

    def test_build_http_without_token_has_no_headers(self):
        srv = mcp_config.build_http_mcp_config(
            "http://127.0.0.1:1/mcp/")["mcpServers"]["kicad-mcp"]
        assert "headers" not in srv

    def test_write_http_creates_valid_json(self, tmp_path):
        out = tmp_path / ".kicad-mcp" / "claude_mcp.json"
        mcp_config.write_http_mcp_config(str(out), "http://x/mcp/", "t")
        data = json.loads(out.read_text())
        assert data["mcpServers"]["kicad-mcp"]["url"] == "http://x/mcp/"


class TestAskWarmServer:
    """ask() must warm the server BEFORE claude spawns — and only in http mode."""

    def _ok_info(self):
        return {"ok": True, "url": "http://127.0.0.1:8331/mcp/",
                "token": "tok", "port": 8331, "pid": 1, "reused": False}

    def test_http_mode_ensures_once_before_spawn(self, monkeypatch, tmp_path):
        from plugin import server_manager
        monkeypatch.setenv("KICAD_MCP_TRANSPORT", "http")
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        events = []
        monkeypatch.setattr(
            server_manager, "ensure_running",
            lambda **kw: (events.append("ensure"), self._ok_info())[1])
        cfg = tmp_path / "m.json"

        def spy_popen(cmd, **kw):
            events.append("spawn")
            return _FakeProc([_INIT_OK, _RESULT])

        r = claude_bridge.ask("x", "/proj", str(cfg), _popen=spy_popen)
        assert r["ok"]
        assert events == ["ensure", "spawn"]  # exactly once, and before spawn
        srv = json.loads(cfg.read_text())["mcpServers"]["kicad-mcp"]
        assert srv["type"] == "http"
        assert srv["url"] == "http://127.0.0.1:8331/mcp/"
        assert srv["headers"] == {"Authorization": "Bearer tok"}

    def test_stdio_mode_never_touches_server_manager(self, monkeypatch,
                                                     tmp_path):
        from plugin import server_manager
        monkeypatch.delenv("KICAD_MCP_TRANSPORT", raising=False)
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])

        def boom(**kw):
            raise AssertionError("ensure_running must not run in stdio mode")

        monkeypatch.setattr(server_manager, "ensure_running", boom)
        cfg = tmp_path / "m.json"
        cfg.write_text("{}")
        r = claude_bridge.ask("x", "/proj", str(cfg),
                              _popen=_popen_for(_FakeProc([_RESULT])))
        assert r["ok"]
        assert cfg.read_text() == "{}"  # stdio mode: config untouched

    def test_http_failure_falls_back_to_stdio_config(self, monkeypatch,
                                                     tmp_path):
        from plugin import server_manager
        monkeypatch.setenv("KICAD_MCP_TRANSPORT", "http")
        monkeypatch.setattr(claude_bridge, "find_claude", lambda: ["claude"])
        monkeypatch.setattr(server_manager, "ensure_running",
                            lambda **kw: {"ok": False, "error": "kaputt"})
        root = tmp_path / "mcp"; (root / "kicad_mcp").mkdir(parents=True)
        py = tmp_path / "python"; py.write_text("")
        monkeypatch.setattr(server_manager, "default_mcp_root",
                            lambda: str(root))
        monkeypatch.setattr(mcp_config, "find_kicad_python", lambda: str(py))
        # simulate an earlier http turn: the file still points into the void
        cfg = tmp_path / "m.json"
        mcp_config.write_http_mcp_config(str(cfg), "http://tot/mcp/", "alt")
        statuses = []
        r = claude_bridge.ask("x", "/proj", str(cfg),
                              on_status=statuses.append,
                              _popen=_popen_for(_FakeProc([_INIT_OK, _RESULT])))
        assert r["ok"]  # the turn still ran — via stdio
        srv = json.loads(cfg.read_text())["mcpServers"]["kicad-mcp"]
        assert srv["type"] == "stdio"  # config restored, not left broken
        assert srv["command"] == str(py)
        assert any("stdio-Fallback" in s for s in statuses)
