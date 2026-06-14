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

import pytest

from plugin import server_probe

# Full handshake reply: initialize (serverInfo) AND tools/list (tools array).
_OK_REPLY = (
    '{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",'
    '"serverInfo":{"name":"KiCad","version":"x"}}}\n'
    '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"x"}]}}')


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


@pytest.fixture
def root(tmp_path):
    """A valid mcp_root: contains the kicad_mcp package directory."""
    (tmp_path / "kicad_mcp").mkdir()
    return str(tmp_path)


class TestProbeServer:
    def test_handshake_reply_is_ok(self, root):
        proc = _FakeProc(stdout=_OK_REPLY)
        res = server_probe.probe_server("/k/py", root,
                                        _popen=_popen_for(proc))
        assert res["ok"] is True and res["error"] == ""
        assert "seconds" in res  # elapsed time reported
        # the full handshake is sent: initialize, then tools/list
        sent_methods = [json.loads(line)["method"] for line in
                        proc.sent.splitlines() if line.strip()]
        assert sent_methods[0] == "initialize" and "tools/list" in sent_methods

    def test_initialize_ok_but_tools_list_slow_is_error(self, root):
        # initialize answered, tools/list did NOT — claude would time out here
        init_only = ('{"jsonrpc":"2.0","id":1,"result":{"serverInfo":'
                     '{"name":"KiCad"}}}')
        res = server_probe.probe_server(
            "/k/py", root, _popen=_popen_for(_FakeProc(stdout=init_only)))
        assert res["ok"] is False and "tools/list" in res["error"]

    def test_launches_like_claude_does(self, root):
        seen = {}
        server_probe.probe_server(
            "/k/py", root, _popen=_popen_for(_FakeProc(stdout=_OK_REPLY),
                                             capture=seen),
            deps_dir="/plug/_deps")
        # -c bootstrap with in-process sys.path — KiCad's Python ignores
        # PYTHONPATH (proven in the field), so -m would not find the package.
        assert seen["cmd"][0] == "/k/py" and seen["cmd"][1] == "-c"
        assert root in seen["cmd"][2] and "/plug/_deps" in seen["cmd"][2]
        assert "kicad_mcp.server" in seen["cmd"][2]
        assert seen["env"]["PYTHONPATH"] == root + os.pathsep + "/plug/_deps"

    def test_missing_package_named_precisely(self, tmp_path):
        # "Error while finding module specification" == kicad_mcp missing
        # under mcp_root: the probe must say that BEFORE starting anything.
        res = server_probe.probe_server(
            "/k/py", str(tmp_path / "leer"), _popen=_popen_for(_FakeProc()))
        assert res["ok"] is False and res["missing_root"] is True
        assert "kicad_mcp-Paket fehlt" in res["error"]
        assert str(tmp_path / "leer") in res["error"]

    def test_missing_dep_flagged_with_traceback_tail(self, root):
        stderr = ("Traceback (most recent call last):\n"
                  '  File "<string>", line 1, in <module>\n'
                  "ModuleNotFoundError: No module named 'fastmcp'\n")
        proc = _FakeProc(stderr=stderr, rc=1)
        res = server_probe.probe_server("/k/py", root,
                                        _popen=_popen_for(proc))
        assert res["ok"] is False and res["missing_dep"] is True
        assert "fastmcp" in res["error"]

    def test_failure_shows_used_pythonpath(self, root):
        proc = _FakeProc(stderr="ValueError: kaputt", rc=1)
        res = server_probe.probe_server("/k/py", root,
                                        _popen=_popen_for(proc))
        assert "PYTHONPATH=" in res["error"] and root in res["error"]

    def test_runtime_crash_is_not_missing_dep(self, root):
        proc = _FakeProc(stderr="ValueError: kaputt", rc=1)
        res = server_probe.probe_server("/k/py", root,
                                        _popen=_popen_for(proc))
        assert res["ok"] is False and res["missing_dep"] is False
        assert "kaputt" in res["error"]

    def test_silent_exit_without_reply_is_error(self, root):
        proc = _FakeProc(stdout="", stderr="", rc=0)
        res = server_probe.probe_server("/k/py", root,
                                        _popen=_popen_for(proc))
        assert res["ok"] is False and "Handshake" in res["error"]

    def test_hanging_server_killed_and_reported(self, root):
        proc = _FakeProc(hang=True)
        res = server_probe.probe_server("/k/py", root, timeout=5,
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
