# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the visible-terminal helper — the fix for the Windows cmd /k
nested-quote bug (window flashing shut). On Windows it must write a .bat and
launch THAT, not pass the complex command inline.
"""

from __future__ import annotations

from plugin import terminal


class TestBuildBat:
    def test_has_pause_and_commands(self):
        bat = terminal.build_bat(['echo hi', 'powershell -Command "irm x | iex"'],
                                 title="T", cwd=r"C:\proj")
        assert "@echo off" in bat
        assert "title T" in bat
        # cwd rides %KICAD_MCP_CWD% (env), NOT inlined -> non-ASCII paths survive
        assert 'cd /d "%KICAD_MCP_CWD%"' in bat
        assert r"C:\proj" not in bat  # literal path must not leak into the .bat
        assert 'powershell -Command "irm x | iex"' in bat  # quotes/pipe intact
        assert bat.rstrip().endswith("pause")

    def test_no_title_no_cwd(self):
        bat = terminal.build_bat(["echo hi"])
        assert "title" not in bat and "cd /d" not in bat and "pause" in bat

    def test_crlf_line_endings(self):
        assert "\r\n" in terminal.build_bat(["echo hi"])


class TestWriteTempBat:
    def test_non_ascii_path_survives_as_utf8(self, tmp_path, monkeypatch):
        # the bug: a username like "üser" (ü) must NOT become "Sch?ler" —
        # "?" is an invalid Windows path char → pip's makedirs fails.
        cmd = r'pip install --target "C:\Users\üser\plugins\x\_deps"'
        bat = terminal.build_bat([cmd])
        path = terminal._write_temp_bat(bat)
        try:
            raw = open(path, "rb").read()
            assert "üser".encode("utf-8") in raw   # written as UTF-8
            assert b"Sch?ler" not in raw               # NOT ascii-replaced
            assert b"chcp 65001" in raw                # bat declares UTF-8
        finally:
            import os as _os
            _os.remove(path)


class TestOpenTerminal:
    def test_windows_launches_bat_not_inline(self, monkeypatch):
        monkeypatch.setattr(terminal.os, "name", "nt")
        seen = {}
        terminal.open_terminal(
            ['powershell -Command "irm x | iex"'], title="Install",
            _writer=lambda text: (seen.update(text=text), r"C:\tmp\x.bat")[1],
            _popen=lambda argv, **kw: seen.update(argv=argv) or "proc")
        # the complex command goes into the .bat, NOT the argv
        assert "irm x | iex" in seen["text"]
        assert seen["argv"] == ["cmd.exe", "/c", "start", "Install", r"C:\tmp\x.bat"]
        assert not any("irm" in a for a in seen["argv"])  # nothing inline

    def test_posix_runs_bash(self, monkeypatch):
        monkeypatch.setattr(terminal.os, "name", "posix")
        seen = {}
        terminal.open_terminal(["do-thing"], cwd="/proj",
                               _popen=lambda argv, **kw: seen.update(argv=argv))
        assert seen["argv"][0] == "bash" and seen["argv"][1] == "-lc"
        assert 'cd "/proj"' in seen["argv"][2] and "do-thing" in seen["argv"][2]

    def test_windows_passes_cwd_and_env_via_child_environment(self, monkeypatch):
        # The non-ASCII cwd/target must reach the child as env (UTF-16), never
        # inlined into the .bat text (where cmd.exe's codepage would fold ü->?).
        monkeypatch.setattr(terminal.os, "name", "nt")
        seen = {}
        terminal.open_terminal(
            ['pip install --target "%KICAD_MCP_DEPS%"'], title="Install",
            cwd=r"C:\Users\üser\proj",
            env={"KICAD_MCP_DEPS": r"C:\Users\üser\plug\_deps"},
            _writer=lambda text: (seen.update(text=text), r"C:\tmp\x.bat")[1],
            _popen=lambda argv, **kw: seen.update(argv=argv, env=kw.get("env")))
        # cwd + deps dir travel in the environment, with the real (ü) value
        assert seen["env"]["KICAD_MCP_CWD"] == r"C:\Users\üser\proj"
        assert seen["env"]["KICAD_MCP_DEPS"] == r"C:\Users\üser\plug\_deps"
        # ...and NOT as literal text in the launched .bat
        assert "üser" not in seen["text"]
        assert "%KICAD_MCP_CWD%" in seen["text"]
