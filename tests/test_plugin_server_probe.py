# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the MCP-server handshake probe — the check behind "Claude
antwortet, hat aber keine Board-Tools" (claude -p drops a dead MCP server
silently, so the plugin must detect a non-starting server itself). The probe
is a full dress rehearsal: server started like Claude starts it, and it must
answer the MCP ``initialize`` request over stdio.
"""

from __future__ import annotations

import json
import os
import subprocess

from plugin import server_probe

_OK_REPLY = ('{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":'
             '"2024-11-05","serverInfo":{"name":"KiCad","version":"x"}}}')


class _FakeProc:
    def __init__(self, stdout="", stderr="", rc=0, hang=False):
        self._stdout, self._stderr = stdout, stderr
        self.returncode = rc
        self._hang = hang
        self.killed = False
        self.sent = None

    def communicate(self, request=None, timeout=None):
        # (positional arg mirrors Popen.communicate(input=...))
        if self._hang and not self.killed:
            raise subprocess.TimeoutExpired("cmd", timeout)
        self.sent = request if request is not None else self.sent
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


def _popen_for(proc, capture=None):
    def _popen(cmd, **kw):
        if capture is not None:
            capture.update(kw, cmd=cmd)
        return proc
    return _popen


class TestProbeServer:
    def test_handshake_reply_is_ok(self):
        proc = _FakeProc(stdout=_OK_REPLY)
        res = server_probe.probe_server("/k/py", "/repo",
                                        _popen=_popen_for(proc))
        assert res["ok"] is True and res["error"] == ""
        req = json.loads(proc.sent)
        assert req["method"] == "initialize"  # real MCP handshake sent

    def test_launches_like_claude_does(self):
        seen = {}
        server_probe.probe_server(
            "/k/py", "/repo", _popen=_popen_for(_FakeProc(stdout=_OK_REPLY),
                                                capture=seen),
            deps_dir="/plug/_deps")
        assert seen["cmd"] == ["/k/py", "-m", "kicad_mcp.server"]
        assert seen["env"]["PYTHONPATH"] == "/repo" + os.pathsep + "/plug/_deps"

    def test_missing_dep_flagged_with_traceback_tail(self):
        stderr = ("Traceback (most recent call last):\n"
                  '  File "<string>", line 1, in <module>\n'
                  "ModuleNotFoundError: No module named 'fastmcp'\n")
        proc = _FakeProc(stderr=stderr, rc=1)
        res = server_probe.probe_server("/k/py", "/repo",
                                        _popen=_popen_for(proc))
        assert res["ok"] is False and res["missing_dep"] is True
        assert "fastmcp" in res["error"]

    def test_runtime_crash_is_not_missing_dep(self):
        proc = _FakeProc(stderr="ValueError: kaputt", rc=1)
        res = server_probe.probe_server("/k/py", "/repo",
                                        _popen=_popen_for(proc))
        assert res["ok"] is False and res["missing_dep"] is False
        assert "kaputt" in res["error"]

    def test_silent_exit_without_reply_is_error(self):
        proc = _FakeProc(stdout="", stderr="", rc=0)
        res = server_probe.probe_server("/k/py", "/repo",
                                        _popen=_popen_for(proc))
        assert res["ok"] is False and "Handshake" in res["error"]

    def test_hanging_server_killed_and_reported(self):
        proc = _FakeProc(hang=True)
        res = server_probe.probe_server("/k/py", "/repo", timeout=5,
                                        _popen=_popen_for(proc))
        assert res["ok"] is False and "5" in res["error"]
        assert proc.killed is True

    def test_no_python(self):
        res = server_probe.probe_server(None, "/repo",
                                        _popen=_popen_for(_FakeProc()))
        assert res["ok"] is False and "Python" in res["error"]


class TestHelpers:
    def test_error_tail_keeps_only_last_lines(self):
        assert server_probe.error_tail("a\nb\nc\nd\ne", lines=2) == "d | e"

    def test_error_tail_empty(self):
        assert server_probe.error_tail("") == ""

    def test_is_handshake_reply(self):
        assert server_probe.is_handshake_reply(_OK_REPLY)
        assert not server_probe.is_handshake_reply("")
        assert not server_probe.is_handshake_reply('{"error": "boom"}')

    def test_popen_kwargs_has_no_stdin_override(self):
        assert "stdin" not in server_probe._popen_kwargs()
