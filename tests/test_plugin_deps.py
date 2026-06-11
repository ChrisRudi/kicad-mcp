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


class TestPipInstallCommands:
    def test_command_line(self):
        cmds = deps.pip_install_commands(r"C:\KiCad\python.exe")
        assert len(cmds) == 1
        assert "pip install --user" in cmds[0] and "fastmcp" in cmds[0]
        assert r'"C:\KiCad\python.exe"' in cmds[0]  # quoted (path has spaces)

    def test_specs_have_no_brackets(self):
        # brackets would need cross-shell quoting; fastmcp pulls mcp anyway
        assert all("[" not in s for s in deps.PIP_SPECS)
        assert "pyyaml" in deps.PIP_SPECS  # imports as yaml
