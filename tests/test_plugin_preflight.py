# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the plugin onboarding preflight (pure logic, no KiCad/wx):
the green/red detectors, the hard_ok gate, and the login-terminal command.
"""

from __future__ import annotations

import json

from plugin import preflight


# --- individual detectors ----------------------------------------------------

class TestCheckClaude:
    def test_found_native(self, monkeypatch):
        monkeypatch.setattr(preflight.runtime_env, "find_claude",
                            lambda: ["/usr/bin/claude"])
        c = preflight.check_claude()
        assert c.status == preflight.OK and c.fix is None
        assert "nativ" in c.detail

    def test_missing(self, monkeypatch):
        monkeypatch.setattr(preflight.runtime_env, "find_claude", lambda: None)
        c = preflight.check_claude()
        assert c.status == preflight.FAIL and c.fix == "install_claude"
        assert "WSL" in c.detail  # message reassures: no WSL needed


class TestCheckKicadPython:
    def test_found(self, monkeypatch):
        monkeypatch.setattr(preflight.mcp_config, "find_kicad_python",
                            lambda: "/k/python.exe")
        c = preflight.check_kicad_python()
        assert c.status == preflight.OK and "/k/python.exe" in c.detail

    def test_missing(self, monkeypatch):
        monkeypatch.setattr(preflight.mcp_config, "find_kicad_python",
                            lambda: None)
        c = preflight.check_kicad_python()
        assert c.status == preflight.FAIL and c.fix == "env_help"


class TestCheckMcpRoot:
    def test_ok_when_package_present(self, tmp_path):
        (tmp_path / "kicad_mcp").mkdir()
        c = preflight.check_mcp_root(str(tmp_path))
        assert c.status == preflight.OK

    def test_fail_when_missing(self, tmp_path):
        c = preflight.check_mcp_root(str(tmp_path / "nope"))
        assert c.status == preflight.FAIL and c.fix == "env_help"

    def test_fail_on_empty(self):
        assert preflight.check_mcp_root("").status == preflight.FAIL


class TestCheckBoard:
    def test_open(self):
        c = preflight.check_board(True, "iFloat.kicad_pcb")
        assert c.status == preflight.OK and "iFloat" in c.detail

    def test_closed(self):
        assert preflight.check_board(False).status == preflight.FAIL


class TestCheckLogin:
    def test_never_fails_without_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preflight, "_claude_config_paths",
                            lambda: [str(tmp_path / "absent.json")])
        c = preflight.check_login(str(tmp_path))
        assert c.status == preflight.WARN and c.fix == "login"

    def test_trusted_dir_is_ok(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"; proj.mkdir()
        cfg = tmp_path / ".claude.json"
        cfg.write_text(json.dumps({"projects": {str(proj): {}}}))
        monkeypatch.setattr(preflight, "_claude_config_paths",
                            lambda: [str(cfg)])
        c = preflight.check_login(str(proj))
        assert c.status == preflight.OK

    def test_untrusted_dir_warns(self, tmp_path, monkeypatch):
        cfg = tmp_path / ".claude.json"
        cfg.write_text(json.dumps({"projects": {"/some/other": {}}}))
        monkeypatch.setattr(preflight, "_claude_config_paths",
                            lambda: [str(cfg)])
        c = preflight.check_login(str(tmp_path / "proj"))
        assert c.status == preflight.WARN and c.fix == "login"


class TestIsTrusted:
    def test_match_normalized(self, tmp_path):
        cfg = tmp_path / "c.json"
        cfg.write_text(json.dumps({"projects": {"/a/b/": {}}}))
        assert preflight._is_trusted(str(cfg), "/a/b") is True

    def test_no_match(self, tmp_path):
        cfg = tmp_path / "c.json"
        cfg.write_text(json.dumps({"projects": {"/x": {}}}))
        assert preflight._is_trusted(str(cfg), "/a/b") is False

    def test_bad_json_is_false(self, tmp_path):
        cfg = tmp_path / "c.json"
        cfg.write_text("not json")
        assert preflight._is_trusted(str(cfg), "/a/b") is False


# --- aggregate ---------------------------------------------------------------

class TestRunPreflight:
    def test_order_and_keys(self, monkeypatch, tmp_path):
        monkeypatch.setattr(preflight.runtime_env, "find_claude",
                            lambda: ["claude"])
        monkeypatch.setattr(preflight.mcp_config, "find_kicad_python",
                            lambda: "/k/python.exe")
        (tmp_path / "kicad_mcp").mkdir()
        monkeypatch.setattr(preflight, "_claude_config_paths", lambda: [])
        monkeypatch.setattr(preflight.deps, "check_deps",
                            lambda py, **kw: {"ok": True, "missing": [], "error": ""})
        checks = preflight.run_preflight(
            str(tmp_path), str(tmp_path), board_open=True, board_name="b")
        assert [c.key for c in checks] == [
            "claude", "python", "mcp", "deps", "login", "ipc", "board"]


class TestCheckDeps:
    def test_present(self, monkeypatch):
        monkeypatch.setattr(preflight.mcp_config, "find_kicad_python",
                            lambda: "/k/py")
        monkeypatch.setattr(preflight.deps, "check_deps",
                            lambda py, **kw: {"ok": True, "missing": []})
        c = preflight.check_deps()
        assert c.status == preflight.OK and c.fix is None

    def test_missing_warns_with_fix(self, monkeypatch):
        monkeypatch.setattr(preflight.mcp_config, "find_kicad_python",
                            lambda: "/k/py")
        monkeypatch.setattr(preflight.deps, "check_deps",
                            lambda py, **kw: {"ok": False, "missing": ["fastmcp"]})
        c = preflight.check_deps()
        assert c.status == preflight.WARN and c.fix == "install_deps"
        assert "fastmcp" in c.detail

    def test_no_python_is_soft_warn(self, monkeypatch):
        monkeypatch.setattr(preflight.mcp_config, "find_kicad_python",
                            lambda: None)
        monkeypatch.setattr(preflight.deps, "check_deps",
                            lambda py, **kw: {"ok": False, "missing": [],
                                              "error": "x"})
        c = preflight.check_deps()
        assert c.status == preflight.WARN and c.fix is None


class TestCheckIpc:
    def test_enabled(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text('{"api": {"enable_server": true}}')
        c = preflight.check_ipc(str(f))
        assert c.status == preflight.OK and c.fix is None

    def test_enabled_with_restart_hint(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text('{"api": {"enable_server": true}}')
        c = preflight.check_ipc(str(f), restart_hint=True)
        assert c.status == preflight.OK and "neu starten" in c.detail

    def test_disabled_warns_with_fix(self, tmp_path):
        f = tmp_path / "kicad_common.json"
        f.write_text('{"api": {"enable_server": false}}')
        c = preflight.check_ipc(str(f))
        assert c.status == preflight.WARN and c.fix == "enable_ipc"

    def test_missing_file_warns(self):
        c = preflight.check_ipc(None)
        assert c.status == preflight.WARN and c.fix == "enable_ipc"


class TestHardOk:
    def test_true_with_only_warn(self):
        checks = [preflight.Check("a", "", preflight.OK),
                  preflight.Check("b", "", preflight.WARN)]
        assert preflight.hard_ok(checks) is True

    def test_false_with_a_fail(self):
        checks = [preflight.Check("a", "", preflight.OK),
                  preflight.Check("b", "", preflight.FAIL)]
        assert preflight.hard_ok(checks) is False


class TestLoginCommands:
    def test_native_claude(self):
        cmds = preflight.login_commands(["claude"])
        assert cmds == ["claude login"]

    def test_wsl_claude(self):
        cmds = preflight.login_commands(["wsl.exe", "claude"])
        assert len(cmds) == 1 and cmds[0].endswith("login")
        assert "wsl.exe" in cmds[0] and "claude" in cmds[0]
