# SPDX-License-Identifier: GPL-3.0-or-later
"""Warm-server Phase 2: lifecycle manager pure logic — port picking, pidfile
round-trip, ensure-once (second call reuses), restart decision on a dead/hung
leftover, URL/token building, shutdown. Process spawn is injected (``_popen``)
exactly like the claude_bridge tests, so nothing real ever starts.
"""

from __future__ import annotations

import os
import socket

from plugin import server_manager


class _FakeProc:
    def __init__(self, pid=4321, alive=True):
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 1


def _popen_recording(calls, proc=None):
    def _popen(cmd, **kw):
        calls.append((cmd, kw))
        return proc or _FakeProc()
    return _popen


def _use_tmp_state(monkeypatch, tmp_path):
    monkeypatch.setenv(server_manager.STATE_DIR_ENV, str(tmp_path / "state"))


def _probe_ok(url, token, timeout=0.0):  # pylint: disable=unused-argument
    return {"ok": True, "error": "", "status": 200, "seconds": 0.0}


def _fake_env(monkeypatch, tmp_path):
    """A resolvable python + mcp_root so ensure_running reaches the spawn."""
    _use_tmp_state(monkeypatch, tmp_path)
    py = tmp_path / "python"
    py.write_text("")
    root = tmp_path / "mcp"
    (root / "kicad_mcp").mkdir(parents=True)
    return str(py), str(root)


class TestPurePieces:
    def test_pick_free_port_is_usable(self):
        port = server_manager.pick_free_port()
        assert 0 < port < 65536
        # the freed port must be bindable again (that's the whole point)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))

    def test_server_url(self):
        assert server_manager.server_url(8331) == "http://127.0.0.1:8331/mcp"

    def test_new_token_random_and_urlsafe(self):
        a, b = server_manager.new_token(), server_manager.new_token()
        assert a != b and len(a) >= 24
        assert all(c.isalnum() or c in "-_" for c in a)

    def test_pid_alive_self_and_garbage(self):
        assert server_manager.pid_alive(os.getpid())
        assert not server_manager.pid_alive(None)
        assert not server_manager.pid_alive("x")
        assert not server_manager.pid_alive(-5)

    def test_pid_alive_survives_systemerror(self, monkeypatch):
        # Feld-Crash (WinError 6): os.kill(pid, 0) warf unter KiCads
        # eingebettetem Windows-Python einen SystemError (KEIN OSError-
        # Subtyp) — pid_alive muss ihn schlucken statt den Diagnose-Dialog
        # zu töten. Windows selbst geht seit dem Fix gar nicht mehr über
        # os.kill (OpenProcess), der Guard ist das letzte Netz.
        def _boom(_pid, _sig):
            raise SystemError("<class 'OSError'> returned a result "
                              "with an exception set")
        monkeypatch.setattr(server_manager.os, "kill", _boom)
        monkeypatch.setattr(server_manager.os, "name", "posix")
        assert server_manager.pid_alive(os.getpid()) is False

    def test_pid_alive_windows_uses_openprocess_not_kill(self, monkeypatch):
        # Unter nt darf os.kill NIE gefragt werden (dafür war der Crash) —
        # der Dispatch muss auf den OpenProcess-Pfad gehen.
        monkeypatch.setattr(server_manager.os, "name", "nt")
        monkeypatch.setattr(server_manager, "_pid_alive_nt",
                            lambda pid: pid == 4242)
        monkeypatch.setattr(
            server_manager.os, "kill",
            lambda *_: (_ for _ in ()).throw(AssertionError("os.kill benutzt")))
        assert server_manager.pid_alive(4242) is True
        assert server_manager.pid_alive(4243) is False

    def test_state_roundtrip_and_clear(self, monkeypatch, tmp_path):
        _use_tmp_state(monkeypatch, tmp_path)
        assert server_manager.read_state() == {}
        server_manager.write_state({"pid": 7, "port": 8331, "token": "t"})
        assert server_manager.read_state()["port"] == 8331
        server_manager.clear_state()
        assert server_manager.read_state() == {}

    def test_read_state_survives_corrupt_file(self, monkeypatch, tmp_path):
        _use_tmp_state(monkeypatch, tmp_path)
        path = server_manager.state_path()
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{broken json")
        assert server_manager.read_state() == {}

    def test_build_server_cmd_bootstrap_plus_http_args(self):
        cmd = server_manager.build_server_cmd(
            "/k/python", "/opt/mcp", "/opt/deps", 8331)
        assert cmd[0] == "/k/python" and cmd[1] == "-c"
        assert "/opt/mcp" in cmd[2] and "/opt/deps" in cmd[2]
        assert cmd[3:] == ["--transport", "streamable-http",
                           "--host", "127.0.0.1", "--port", "8331"]


class TestEnsureRunning:
    def test_starts_once_then_reuses(self, monkeypatch, tmp_path):
        py, root = _fake_env(monkeypatch, tmp_path)
        calls = []
        kwargs = dict(mcp_root=root, python_exe=py, deps_dir="",
                      _popen=_popen_recording(calls, _FakeProc(pid=os.getpid())),
                      _port_open=lambda *a, **k: True, _sleep=lambda s: None,
                      _probe=_probe_ok)
        first = server_manager.ensure_running(**kwargs)
        assert first["ok"] and not first["reused"]
        assert first["url"] == server_manager.server_url(first["port"])
        assert first["token"] and first["pid"] == os.getpid()
        assert len(calls) == 1
        # the spawn carried the token + unbuffered env
        env = calls[0][1]["env"]
        assert env["KICAD_MCP_HTTP_TOKEN"] == first["token"]
        assert env["PYTHONUNBUFFERED"] == "1"
        assert env["KICAD_MCP_NO_AUTO_OPEN"] == "1"  # GUI-Modus: nie spawnen

        second = server_manager.ensure_running(**kwargs)
        assert second["ok"] and second["reused"]
        assert second["port"] == first["port"]
        assert second["token"] == first["token"]
        assert len(calls) == 1  # no second spawn

    def test_dead_pid_triggers_restart(self, monkeypatch, tmp_path):
        py, root = _fake_env(monkeypatch, tmp_path)
        server_manager.write_state(
            {"pid": 2 ** 22 + 12345, "port": 1, "token": "old"})
        calls = []
        res = server_manager.ensure_running(
            mcp_root=root, python_exe=py, deps_dir="",
            _popen=_popen_recording(calls, _FakeProc(pid=os.getpid())),
            _port_open=lambda *a, **k: True, _sleep=lambda s: None,
            _probe=_probe_ok)
        assert res["ok"] and not res["reused"]
        assert len(calls) == 1
        assert res["token"] != "old"

    def test_hung_server_is_killed_and_replaced(self, monkeypatch, tmp_path):
        py, root = _fake_env(monkeypatch, tmp_path)
        # alive pid (our own) but the port answers nothing → hung
        server_manager.write_state(
            {"pid": os.getpid(), "port": 1, "token": "hung"})
        killed = []
        monkeypatch.setattr(server_manager, "_kill_pid_tree", killed.append)
        opens = iter([False, True])  # health-check fails, new server answers

        res = server_manager.ensure_running(
            mcp_root=root, python_exe=py, deps_dir="",
            _popen=_popen_recording([], _FakeProc(pid=999)),
            _port_open=lambda *a, **k: next(opens), _sleep=lambda s: None,
            _probe=_probe_ok)
        assert res["ok"] and killed == [os.getpid()]
        assert server_manager.read_state()["pid"] == 999

    def test_mute_server_is_killed_and_replaced(self, monkeypatch, tmp_path):
        """DER Feld-Fall (E2E 34/34 FAIL): pid lebt, Port offen — aber kein
        MCP dahinter (wedged/fremder Port-Nachnutzer). pid+port allein sagte
        'gesund', der Server wurde nie ersetzt. Jetzt entscheidet der Ping."""
        py, root = _fake_env(monkeypatch, tmp_path)
        server_manager.write_state(
            {"pid": os.getpid(), "port": 1, "token": "mute"})
        killed = []
        monkeypatch.setattr(server_manager, "_kill_pid_tree", killed.append)
        pings = iter([{"ok": False, "error": "kein serverInfo"},
                      {"ok": True}])

        res = server_manager.ensure_running(
            mcp_root=root, python_exe=py, deps_dir="",
            _popen=_popen_recording([], _FakeProc(pid=999)),
            _port_open=lambda *a, **k: True, _sleep=lambda s: None,
            _probe=lambda *a, **k: next(pings))
        assert res["ok"] and not res["reused"]
        assert killed == [os.getpid()]
        assert server_manager.read_state()["pid"] == 999
        assert res["token"] != "mute"

    def test_reuse_pings_with_recorded_url_and_token(self, monkeypatch,
                                                     tmp_path):
        _use_tmp_state(monkeypatch, tmp_path)
        server_manager.write_state(
            {"pid": os.getpid(), "port": 4711, "token": "sesam"})
        seen = []

        def probe(url, token, timeout=0.0):
            seen.append((url, token, timeout))
            return {"ok": True}

        res = server_manager.ensure_running(
            _port_open=lambda *a, **k: True, _probe=probe)
        assert res["ok"] and res["reused"]
        assert seen == [("http://127.0.0.1:4711/mcp", "sesam",
                         server_manager.PING_TIMEOUT_S)]

    def test_missing_python_reports_not_spawns(self, monkeypatch, tmp_path):
        _use_tmp_state(monkeypatch, tmp_path)
        monkeypatch.setattr(server_manager.mcp_config, "find_kicad_python",
                            lambda: None)
        calls = []
        res = server_manager.ensure_running(
            mcp_root=str(tmp_path), _popen=_popen_recording(calls))
        assert not res["ok"] and "KiCad-Python" in res["error"]
        assert calls == []

    def test_missing_package_reports(self, monkeypatch, tmp_path):
        py, _ = _fake_env(monkeypatch, tmp_path)
        res = server_manager.ensure_running(
            mcp_root=str(tmp_path / "leer"), python_exe=py,
            _popen=_popen_recording([]))
        assert not res["ok"] and "kicad_mcp-Paket fehlt" in res["error"]

    def test_immediate_exit_reports_error(self, monkeypatch, tmp_path):
        py, root = _fake_env(monkeypatch, tmp_path)
        res = server_manager.ensure_running(
            mcp_root=root, python_exe=py, deps_dir="",
            _popen=_popen_recording([], _FakeProc(alive=False)),
            _port_open=lambda *a, **k: False, _sleep=lambda s: None)
        assert not res["ok"] and "beendete sich sofort" in res["error"]
        assert server_manager.read_state() == {}  # no pidfile for a corpse

    def test_start_timeout_kills_and_reports(self, monkeypatch, tmp_path):
        py, root = _fake_env(monkeypatch, tmp_path)
        killed = []
        monkeypatch.setattr(server_manager, "_kill_pid_tree", killed.append)
        clock = iter(range(0, 10_000, 100))  # jumps past any timeout fast
        res = server_manager.ensure_running(
            mcp_root=root, python_exe=py, deps_dir="", timeout=50,
            _popen=_popen_recording([], _FakeProc(pid=777)),
            _port_open=lambda *a, **k: False, _sleep=lambda s: None,
            _monotonic=lambda: next(clock))
        assert not res["ok"] and "nicht innerhalb" in res["error"]
        assert killed == [777]

    def test_spawned_server_must_answer_mcp_not_just_bind(self, monkeypatch,
                                                          tmp_path):
        """Port offen, aber der neue Server beantwortet nie ein initialize →
        kein ok (sonst zeigt claude auf einen stummen Server)."""
        py, root = _fake_env(monkeypatch, tmp_path)
        killed = []
        monkeypatch.setattr(server_manager, "_kill_pid_tree", killed.append)
        clock = iter(range(0, 10_000, 10))  # klein genug für ein paar Pings
        res = server_manager.ensure_running(
            mcp_root=root, python_exe=py, deps_dir="", timeout=50,
            _popen=_popen_recording([], _FakeProc(pid=778)),
            _port_open=lambda *a, **k: True, _sleep=lambda s: None,
            _monotonic=lambda: next(clock),
            _probe=lambda *a, **k: {"ok": False, "error": "401"})
        assert not res["ok"]
        assert "beantwortet kein MCP-initialize" in res["error"]
        assert killed == [778]
        assert server_manager.read_state() == {}


class TestShutdownAndStatus:
    def test_shutdown_kills_recorded_pid(self, monkeypatch, tmp_path):
        _use_tmp_state(monkeypatch, tmp_path)
        server_manager.write_state({"pid": os.getpid(), "port": 5, "token": "t"})
        killed = []
        monkeypatch.setattr(server_manager, "_kill_pid_tree", killed.append)
        assert server_manager.shutdown() is True
        assert killed == [os.getpid()]
        assert server_manager.read_state() == {}

    def test_shutdown_noop_without_state(self, monkeypatch, tmp_path):
        _use_tmp_state(monkeypatch, tmp_path)
        assert server_manager.shutdown() is False

    def test_status_running_and_not(self, monkeypatch, tmp_path):
        _use_tmp_state(monkeypatch, tmp_path)
        st = server_manager.status()
        assert st["running"] is False and st["pid"] == 0

        server_manager.write_state(
            {"pid": os.getpid(), "port": 8331, "token": "t",
             "started": 1.0, "transport": "streamable-http"})
        monkeypatch.setattr(server_manager, "port_open",
                            lambda *a, **k: True)
        st = server_manager.status()
        assert st["running"] is True
        assert st["pid"] == os.getpid() and st["port"] == 8331
        assert st["url"].endswith(":8331/mcp")
        assert st["uptime_s"] > 0


class TestStateDirPlacement:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(server_manager.STATE_DIR_ENV, str(tmp_path))
        assert server_manager.state_dir() == str(tmp_path)
        assert server_manager.state_path() == str(
            tmp_path / server_manager.STATE_FILENAME)

    def test_default_is_per_user(self, monkeypatch):
        monkeypatch.delenv(server_manager.STATE_DIR_ENV, raising=False)
        d = server_manager.state_dir()
        assert "kicad-claude" in d
