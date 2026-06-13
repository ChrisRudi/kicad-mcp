# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the cross-OS RunPlan resolver: native Windows, native Linux, and
the opt-in WSL bridge — all paths must stay in ONE consistent style per plan.
"""

from __future__ import annotations

from plugin import runtime_env


class TestWinToWsl:
    def test_backslash(self):
        assert runtime_env.win_to_wsl(r"C:\Users\x\p.json") == "/mnt/c/Users/x/p.json"

    def test_forward_slash(self):
        assert runtime_env.win_to_wsl("D:/a/b") == "/mnt/d/a/b"

    def test_already_posix_untouched(self):
        assert runtime_env.win_to_wsl("/mnt/c/a") == "/mnt/c/a"


class TestNativePlanLinux:
    def test_all_paths_local(self, monkeypatch):
        monkeypatch.setattr(runtime_env, "kicad_os", lambda: "posix")
        plan = runtime_env.resolve(
            "/home/u/proj", "/opt/kicad-mcp", "/home/u/proj/.kicad-mcp/m.json",
            python_exe="/usr/bin/python3", claude_native=["/usr/bin/claude"],
            wsl=None)
        assert plan.mode == runtime_env.NATIVE
        assert plan.claude_cmd == ["/usr/bin/claude"]
        assert plan.config_command == "/usr/bin/python3"
        assert plan.config_pythonpath == "/opt/kicad-mcp"
        assert plan.config_arg_path == "/home/u/proj/.kicad-mcp/m.json"
        assert plan.run_cwd == "/home/u/proj"
        assert plan.trust_dir == "/home/u/proj"


class TestNativePlanWindows:
    def test_all_paths_windows(self, monkeypatch):
        monkeypatch.setattr(runtime_env, "kicad_os", lambda: "windows")
        plan = runtime_env.resolve(
            r"C:\proj", r"C:\repo", r"C:\proj\.kicad-mcp\m.json",
            python_exe=r"C:\KiCad\python.exe",
            claude_native=[r"C:\claude.cmd"], wsl="wsl.exe")
        assert plan.mode == runtime_env.NATIVE
        # native wins even when wsl exists -> no bridge, all Windows paths
        assert plan.config_command == r"C:\KiCad\python.exe"
        assert plan.config_arg_path == r"C:\proj\.kicad-mcp\m.json"
        assert plan.trust_dir == r"C:\proj"


class TestBridgePlan:
    def _resolve(self, monkeypatch, enabled):
        monkeypatch.setattr(runtime_env, "kicad_os", lambda: "windows")
        monkeypatch.setattr(runtime_env, "wsl_bridge_enabled", lambda: enabled)
        monkeypatch.setattr(runtime_env, "_find_native_claude", lambda: None)
        return runtime_env.resolve(
            r"C:\Users\user\proj", r"C:\Users\user\repo",
            r"C:\Users\user\proj\.kicad-mcp\m.json",
            python_exe=r"C:\Program Files\KiCad\10.0\bin\python.exe",
            wsl="wsl.exe")

    def test_disabled_by_default_returns_none(self, monkeypatch):
        # no native claude + bridge off -> no plan (preflight tells user to
        # install native Claude; nobody is asked to install WSL)
        assert self._resolve(monkeypatch, enabled=False) is None

    def test_enabled_translates_paths(self, monkeypatch):
        plan = self._resolve(monkeypatch, enabled=True)
        assert plan.mode == runtime_env.BRIDGE
        assert plan.claude_cmd == ["wsl.exe", "claude"]
        # command is the /mnt/c path (WSL execs the Windows python via interop)
        assert plan.config_command == (
            "/mnt/c/Program Files/KiCad/10.0/bin/python.exe")
        # but PYTHONPATH stays Windows-style: the *Windows* python reads it
        assert plan.config_pythonpath == r"C:\Users\user\repo"
        # the plugin still WRITES the json to a Windows path...
        assert plan.config_write_path == r"C:\Users\user\proj\.kicad-mcp\m.json"
        # ...but passes the /mnt/c form to --mcp-config (Claude reads it in WSL)
        assert plan.config_arg_path == (
            "/mnt/c/Users/user/proj/.kicad-mcp/m.json")
        assert plan.trust_dir == "/mnt/c/Users/user/proj"


class TestNoPython:
    def test_returns_none_without_python(self, monkeypatch):
        monkeypatch.setattr(runtime_env.mcp_config, "find_kicad_python",
                            lambda: None)
        plan = runtime_env.resolve(
            "/p", "/r", "/p/m.json", python_exe=None,
            claude_native=["/usr/bin/claude"])
        assert plan is None


class TestWslBridgeEnabled:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("KICAD_CLAUDE_ALLOW_WSL", raising=False)
        assert runtime_env.wsl_bridge_enabled() is False

    def test_on_values(self, monkeypatch):
        for v in ("1", "true", "YES", "on"):
            monkeypatch.setenv("KICAD_CLAUDE_ALLOW_WSL", v)
            assert runtime_env.wsl_bridge_enabled() is True


class TestFindClaude:
    def test_native_preferred(self, monkeypatch):
        monkeypatch.setattr(runtime_env, "_find_native_claude",
                            lambda: ["/usr/bin/claude"])
        assert runtime_env.find_claude() == ["/usr/bin/claude"]

    def test_no_wsl_when_disabled(self, monkeypatch):
        monkeypatch.setattr(runtime_env, "_find_native_claude", lambda: None)
        monkeypatch.setattr(runtime_env, "kicad_os", lambda: "windows")
        monkeypatch.setattr(runtime_env, "wsl_bridge_enabled", lambda: False)
        monkeypatch.setattr(runtime_env, "_find_wsl", lambda: "wsl.exe")
        assert runtime_env.find_claude() is None

    def test_wsl_when_enabled(self, monkeypatch):
        monkeypatch.setattr(runtime_env, "_find_native_claude", lambda: None)
        monkeypatch.setattr(runtime_env, "kicad_os", lambda: "windows")
        monkeypatch.setattr(runtime_env, "wsl_bridge_enabled", lambda: True)
        monkeypatch.setattr(runtime_env, "_find_wsl", lambda: "wsl.exe")
        assert runtime_env.find_claude() == ["wsl.exe", "claude"]
