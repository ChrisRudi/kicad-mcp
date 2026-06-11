# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the bundled-MCP runtime-dependency check + pip-install command."""

from __future__ import annotations

from types import SimpleNamespace

from plugin import deps


def _runner(stdout="", rc=0):
    def _run(cmd, **kw):
        return SimpleNamespace(stdout=stdout, stderr="", returncode=rc)
    return _run


class TestCheckDeps:
    def test_all_present(self):
        res = deps.check_deps("/k/py", _run=_runner(stdout="", rc=0))
        assert res["ok"] is True and res["missing"] == []

    def test_some_missing(self):
        res = deps.check_deps("/k/py", _run=_runner(stdout="fastmcp,pandas", rc=1))
        assert res["ok"] is False and res["missing"] == ["fastmcp", "pandas"]

    def test_no_python(self):
        res = deps.check_deps(None, _run=_runner())
        assert res["ok"] is False and res["error"]

    def test_runner_raises_is_soft(self):
        def boom(cmd, **kw):
            raise OSError("spawn failed")
        res = deps.check_deps("/k/py", _run=boom)
        assert res["ok"] is False and "spawn failed" in res["error"]

    def test_check_cmd_probes_find_spec(self):
        cmd = deps.build_check_cmd("/k/py")
        assert cmd[0] == "/k/py" and cmd[1] == "-c"
        assert "find_spec" in cmd[2] and "fastmcp" in cmd[2]


class TestPipInstallCmd:
    def test_windows(self, monkeypatch):
        monkeypatch.setattr(deps.os, "name", "nt")
        cmd = deps.build_pip_install_terminal_cmd(r"C:\KiCad\python.exe")
        assert cmd[0] == "cmd.exe"
        inner = cmd[-1]
        assert "pip install --user" in inner and "fastmcp" in inner
        assert "pause" in inner

    def test_posix(self, monkeypatch):
        monkeypatch.setattr(deps.os, "name", "posix")
        cmd = deps.build_pip_install_terminal_cmd("/k/py")
        assert cmd[0] == "bash" and "pip install --user" in cmd[-1]

    def test_specs_have_no_brackets(self):
        # brackets would need cross-shell quoting; fastmcp pulls mcp anyway
        assert all("[" not in s for s in deps.PIP_SPECS)
        assert "pyyaml" in deps.PIP_SPECS  # imports as yaml
