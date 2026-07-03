# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the one-click diagnosis report (pure module — the wx button
lives in setup_dialog and only runs inside KiCad)."""

from __future__ import annotations

from types import SimpleNamespace

from plugin import diagnose


def _runner(stdout="v1.2.3", rc=0):
    def _run(cmd, **kw):
        return SimpleNamespace(stdout=stdout, stderr="", returncode=rc)
    return _run


def _patch_env(monkeypatch, *, py="/k/py", deps_dir="/plug/_deps",
               claude=None, probe=None):
    monkeypatch.setattr(diagnose.mcp_config, "find_kicad_python", lambda: py)
    monkeypatch.setattr(diagnose.deps, "active_deps_dir", lambda: deps_dir)
    monkeypatch.setattr(diagnose.runtime_env, "find_claude",
                        lambda: claude if claude is not None else ["claude"])
    monkeypatch.setattr(
        diagnose.server_probe, "probe_server",
        lambda *a, **kw: probe if probe is not None else {"ok": True})


class TestCollect:
    def test_contains_all_decisive_paths(self, monkeypatch, tmp_path):
        _patch_env(monkeypatch)
        (tmp_path / "kicad_mcp").mkdir()
        report = diagnose.collect(str(tmp_path), "/proj", _run=_runner())
        for needle in (str(tmp_path), "/proj", "/k/py", "/plug/_deps",
                       "PYTHONPATH", "kicad_mcp/ vorhanden: JA"):
            assert needle in report

    def test_failure_includes_full_stderr_and_repro(self, monkeypatch,
                                                    tmp_path):
        _patch_env(monkeypatch, probe={
            "ok": False, "error": "boom [PYTHONPATH=x]",
            "stderr": "Traceback ...\nValueError: kaputt"})
        report = diagnose.collect(str(tmp_path), "/proj", _run=_runner())
        assert "FEHLER" in report
        assert "ValueError: kaputt" in report          # FULL stderr
        # manual repro recipe uses the -c sys.path bootstrap (KiCad's
        # Python ignores PYTHONPATH)
        assert "kicad_mcp.server" in report and "sys.path[:0]" in report

    def test_missing_pieces_named_not_crashing(self, monkeypatch, tmp_path):
        _patch_env(monkeypatch, py=None, deps_dir=None, claude=[],
                   probe={"ok": False, "error": "kein python"})
        report = diagnose.collect(str(tmp_path), "/proj", _run=_runner())
        assert "NICHT GEFUNDEN" in report
        assert "kicad_mcp/ vorhanden: NEIN" in report

    def test_env_overrides_shown(self, monkeypatch, tmp_path):
        _patch_env(monkeypatch)
        monkeypatch.setenv("KICAD_MCP_ROOT", r"C:\x\y")
        monkeypatch.delenv("KICAD_PYTHON_PATH", raising=False)
        report = diagnose.collect(str(tmp_path), "/proj", _run=_runner())
        assert r"KICAD_MCP_ROOT    = C:\x\y" in report
        assert "KICAD_PYTHON_PATH = (nicht gesetzt)" in report


class TestTransportSection:
    def test_stdio_mode_named_in_report(self, monkeypatch, tmp_path):
        _patch_env(monkeypatch)
        monkeypatch.delenv("KICAD_MCP_TRANSPORT", raising=False)
        report = diagnose.collect(str(tmp_path), "/proj", _run=_runner())
        assert "Transport (KICAD_MCP_TRANSPORT): stdio" in report
        assert "pro Nachricht" in report

    def test_http_mode_not_running(self, monkeypatch, tmp_path):
        _patch_env(monkeypatch)
        monkeypatch.setenv("KICAD_MCP_TRANSPORT", "http")
        monkeypatch.setattr(diagnose.server_manager, "status",
                            lambda: {"running": False, "pid": 0, "port": 0,
                                     "url": "", "uptime_s": 0,
                                     "transport": ""})
        report = diagnose.collect(str(tmp_path), "/proj", _run=_runner())
        assert "Transport (KICAD_MCP_TRANSPORT): http" in report
        assert "LÄUFT NICHT" in report

    def test_http_mode_running_shows_pid_port_uptime_ping(self, monkeypatch,
                                                          tmp_path):
        _patch_env(monkeypatch)
        monkeypatch.setenv("KICAD_MCP_TRANSPORT", "http")
        monkeypatch.setattr(
            diagnose.server_manager, "status",
            lambda: {"running": True, "pid": 42, "port": 8331,
                     "url": "http://127.0.0.1:8331/mcp/", "uptime_s": 77,
                     "transport": "streamable-http"})
        monkeypatch.setattr(diagnose.server_manager, "read_state",
                            lambda: {"token": "tok"})
        pings = []
        monkeypatch.setattr(
            diagnose.server_probe, "probe_http",
            lambda url, token, **kw: (pings.append((url, token))
                                      or {"ok": True, "seconds": 0.02}))
        report = diagnose.collect(str(tmp_path), "/proj", _run=_runner())
        assert "PID 42" in report and "Port 8331" in report
        assert "Uptime 77s" in report
        assert "MCP-Ping: OK" in report
        assert pings == [("http://127.0.0.1:8331/mcp/", "tok")]
