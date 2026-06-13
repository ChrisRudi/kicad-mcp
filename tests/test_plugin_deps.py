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
    def test_installs_into_plugin_target_dir(self):
        # --target (plugin-owned dir), NOT --user: the user-site dir is shared
        # with other CPython installs and not reliably on KiCad-python's path.
        cmds = deps.pip_install_commands(r"C:\KiCad\python.exe")
        install = next(c for c in cmds if "pip install" in c)
        assert "pip install --upgrade --target" in install
        assert "--user" not in install
        assert "_deps" in install and "fastmcp" in install
        assert r'"C:\KiCad\python.exe"' in install  # quoted (path has spaces)

    def test_bootstraps_pip_when_bundle_lacks_it(self):
        cmds = deps.pip_install_commands("/k/py")
        boot = next(c for c in cmds if "ensurepip" in c)
        assert "-m pip --version ||" in boot  # only when pip is missing

    def test_verifies_imports_after_install(self):
        cmds = deps.pip_install_commands("/k/py", target="/plug/_deps")
        verify = cmds[-1]
        assert "sys.path.insert(0,r'/plug/_deps')" in verify
        for name in deps.IMPORT_NAMES:
            assert name in verify

    def test_explicit_target_wins(self):
        cmds = deps.pip_install_commands("/k/py", target="/tmp/x")
        assert any('--target "/tmp/x"' in c for c in cmds)

    def test_specs_have_no_brackets(self):
        # brackets would need cross-shell quoting; fastmcp pulls mcp anyway
        assert all("[" not in s for s in deps.PIP_SPECS)
        assert "pyyaml" in deps.PIP_SPECS  # imports as yaml


class TestDepsDir:
    def test_default_target_is_inside_plugin(self):
        d = deps.default_target_dir()
        assert d.endswith("_deps") and d.startswith(deps.PLUGIN_DIR)

    def test_active_none_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(deps, "default_target_dir",
                            lambda: str(tmp_path / "nope"))
        assert deps.active_deps_dir() is None

    def test_active_when_present(self, monkeypatch, tmp_path):
        d = tmp_path / "_deps"; d.mkdir()
        monkeypatch.setattr(deps, "default_target_dir", lambda: str(d))
        assert deps.active_deps_dir() == str(d)

    def test_check_probes_with_deps_dir_on_pythonpath(self):
        seen = {}

        def _run(cmd, **kw):
            seen.update(kw)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        deps.check_deps("/k/py", _run=_run, deps_dir="/plug/_deps")
        assert seen["env"]["PYTHONPATH"] == "/plug/_deps"
