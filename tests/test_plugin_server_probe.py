# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the MCP-server start probe — the check behind "Claude
antwortet, hat aber keine Board-Tools" (claude -p drops a dead MCP server
silently, so the plugin must detect a non-starting server itself).
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from plugin import server_probe


def _runner(stdout="", stderr="", rc=0, capture=None):
    def _run(cmd, **kw):
        if capture is not None:
            capture.update(kw, cmd=cmd)
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)
    return _run


class TestBuildProbeCmd:
    def test_imports_the_server_module(self):
        cmd = server_probe.build_probe_cmd("/k/python.exe")
        assert cmd[0] == "/k/python.exe" and cmd[1] == "-c"
        assert "kicad_mcp.server" in cmd[2]


class TestProbeServer:
    def test_clean_import_is_ok(self):
        res = server_probe.probe_server("/k/py", "/repo", _run=_runner(rc=0))
        assert res["ok"] is True and res["error"] == ""

    def test_missing_dep_flagged_with_traceback_tail(self):
        stderr = ("Traceback (most recent call last):\n"
                  '  File "<string>", line 1, in <module>\n'
                  "ModuleNotFoundError: No module named 'fastmcp'\n")
        res = server_probe.probe_server(
            "/k/py", "/repo", _run=_runner(stderr=stderr, rc=1))
        assert res["ok"] is False and res["missing_dep"] is True
        assert "fastmcp" in res["error"]

    def test_other_import_error_is_not_missing_dep(self):
        res = server_probe.probe_server(
            "/k/py", "/repo",
            _run=_runner(stderr="ValueError: kaputt", rc=1))
        assert res["ok"] is False and res["missing_dep"] is False
        assert "kaputt" in res["error"]

    def test_no_python(self):
        res = server_probe.probe_server(None, "/repo", _run=_runner())
        assert res["ok"] is False and "Python" in res["error"]

    def test_timeout_reported(self):
        def _run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 90)
        res = server_probe.probe_server("/k/py", "/repo", _run=_run)
        assert res["ok"] is False and "90" in res["error"]

    def test_pythonpath_matches_mcp_config(self):
        seen = {}
        server_probe.probe_server(
            "/k/py", "/repo", _run=_runner(rc=0, capture=seen))
        assert seen["env"]["PYTHONPATH"] == "/repo"


class TestErrorTail:
    def test_keeps_only_last_lines(self):
        text = "a\nb\nc\nd\ne"
        assert server_probe.error_tail(text, lines=2) == "d | e"

    def test_empty_is_empty(self):
        assert server_probe.error_tail("") == ""
