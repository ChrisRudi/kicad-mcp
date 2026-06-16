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

    def test_check_cmd_injects_deps_dir_into_syspath(self):
        # KiCad's Python ignores PYTHONPATH -> the probe must put _deps on
        # sys.path IN-PROCESS, else it reports installed deps as missing.
        cmd = deps.build_check_cmd("/k/py", deps_dir="/plug/_deps")
        assert "sys.path[:0]=[" in cmd[2] and "'/plug/_deps'" in cmd[2]
        assert cmd[2].index("sys.path[:0]") < cmd[2].index("find_spec")

    def test_check_cmd_no_injection_without_deps_dir(self):
        # legacy pip --user installs: no _deps dir -> rely on default sys.path
        assert "sys.path[:0]" not in deps.build_check_cmd("/k/py")[2]

    def test_check_passes_deps_dir_to_probe_code(self):
        seen = {}

        def _run(cmd, **kw):
            seen["code"] = cmd[2]
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        deps.check_deps("/k/py", _run=_run, deps_dir="/plug/_deps")
        assert "sys.path[:0]=[" in seen["code"] and "'/plug/_deps'" in seen["code"]


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
        assert "sys.path[:0]=" in verify and "r'/plug/_deps'" in verify
        # pywin32's .pth is never executed under --target, so the verify must
        # replicate it (win32 dirs + add_dll_directory) or mcp's eager
        # `import pywintypes` fails.
        assert "win32" in verify and "add_dll_directory" in verify
        for name in deps.IMPORT_NAMES:
            assert name in verify

    def test_explicit_target_wins(self):
        cmds = deps.pip_install_commands("/k/py", target="/tmp/x")
        assert any('--target "/tmp/x"' in c for c in cmds)

    def test_specs_have_no_brackets(self):
        # brackets would need cross-shell quoting; fastmcp pulls mcp anyway
        assert all("[" not in s for s in deps.PIP_SPECS)
        assert "pyyaml" in deps.PIP_SPECS  # imports as yaml

    def test_windows_references_target_via_env_var_not_literal(self, monkeypatch):
        # On Windows the non-ASCII target (C:\Users\üser\...\_deps) must ride
        # %KICAD_MCP_DEPS% (env, UTF-16) — inlining it lets cmd.exe fold ü->? and
        # pip's makedirs dies with WinError 123. So the literal must NOT appear.
        monkeypatch.setattr(deps.os, "name", "nt")
        target = r"C:\Users\üser\plug\_deps"
        cmds = deps.pip_install_commands(r"C:\KiCad\python.exe", target=target)
        install = next(c for c in cmds if "pip install" in c)
        assert '--target "%KICAD_MCP_DEPS%"' in install
        assert all("üser" not in c for c in cmds)  # literal path never inlined
        verify = cmds[-1]
        assert "sys.path[:0]=" in verify and "r'%KICAD_MCP_DEPS%'" in verify


class TestPipInstallEnv:
    def test_carries_target_with_real_value(self):
        env = deps.pip_install_env(r"C:\Users\üser\plug\_deps")
        assert env[deps.DEPS_ENV_VAR] == r"C:\Users\üser\plug\_deps"

    def test_defaults_to_plugin_target_dir(self):
        assert deps.pip_install_env()[deps.DEPS_ENV_VAR] == deps.default_target_dir()


class TestArgvBuilders:
    # The Umlaut fix: these run via subprocess DIRECTLY (argv list, no shell),
    # so a non-ASCII --target (C:\Users\üser\…) reaches CreateProcessW as
    # proper unicode instead of being code-page-mangled to "?" (WinError 123).
    def test_pip_install_argv_is_a_list_no_shell_quoting(self):
        umlaut = r"C:\Users\üser\plugins\x\_deps"
        argv = deps.pip_install_argv(r"C:\KiCad\python.exe", target=umlaut)
        assert isinstance(argv, list)
        assert argv[0] == r"C:\KiCad\python.exe"
        assert argv[1:5] == ["-m", "pip", "install", "--upgrade"]
        # the target is its OWN element, raw (no quotes, no "?" replacement)
        assert "--target" in argv
        assert argv[argv.index("--target") + 1] == umlaut
        assert "ü" in argv[argv.index("--target") + 1]
        for spec in deps.PIP_SPECS:
            assert spec in argv

    def test_pip_install_argv_defaults_to_plugin_dir(self):
        argv = deps.pip_install_argv("/k/py")
        assert argv[argv.index("--target") + 1] == deps.default_target_dir()

    def test_verify_import_argv_embeds_path_via_repr(self):
        umlaut = r"C:\Users\üser\_deps"
        argv = deps.verify_import_argv("/k/py", target=umlaut)
        assert argv[0] == "/k/py" and argv[1] == "-c"
        code = argv[2]
        # repr produces a valid Python string literal -> unicode-safe, and the
        # backslashes are escaped so the path can't break the literal.
        assert repr(umlaut) in code
        for name in deps.IMPORT_NAMES:
            assert name in code


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
