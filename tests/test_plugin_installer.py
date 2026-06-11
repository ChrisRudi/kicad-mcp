# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Claude Code installer command builders and the post-install
detection at the official ~/.local/bin location.
"""

from __future__ import annotations

from plugin import installer, runtime_env


class TestInstallCommandText:
    def test_windows(self, monkeypatch):
        monkeypatch.setattr(installer.os, "name", "nt")
        assert "install.ps1" in installer.install_command_text()

    def test_posix(self, monkeypatch):
        monkeypatch.setattr(installer.os, "name", "posix")
        assert "install.sh" in installer.install_command_text()


class TestInstallTerminalCommands:
    def test_windows_powershell_oneliner(self, monkeypatch):
        monkeypatch.setattr(installer.os, "name", "nt")
        cmds = installer.install_terminal_commands()
        assert len(cmds) == 1 and "install.ps1" in cmds[0]
        assert cmds[0].startswith("powershell")  # | stays inside -Command "…"

    def test_posix_shell_oneliner(self, monkeypatch):
        monkeypatch.setattr(installer.os, "name", "posix")
        cmds = installer.install_terminal_commands()
        assert "install.sh" in cmds[0]

    def test_uses_official_claude_ai_source(self, monkeypatch):
        for nm in ("nt", "posix"):
            monkeypatch.setattr(installer.os, "name", nm)
            assert "claude.ai/install." in installer.install_command_text()


class TestPostInstallDetection:
    def test_finds_claude_in_local_bin(self, tmp_path, monkeypatch):
        # no claude on PATH, but installed at ~/.local/bin/claude
        monkeypatch.setattr(runtime_env.shutil, "which", lambda c: None)
        monkeypatch.setattr(runtime_env.os.path, "expanduser",
                            lambda p: str(tmp_path) if p == "~" else p)
        binp = tmp_path / ".local" / "bin"
        binp.mkdir(parents=True)
        (binp / "claude").write_text("")
        got = runtime_env._find_native_claude()
        assert got == [str(binp / "claude")]

    def test_none_when_absent_everywhere(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runtime_env.shutil, "which", lambda c: None)
        monkeypatch.setattr(runtime_env.os.path, "expanduser",
                            lambda p: str(tmp_path) if p == "~" else p)
        assert runtime_env._find_native_claude() is None
