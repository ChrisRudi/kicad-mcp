# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for KiCad-Python discovery — robust across install path / drive /
32-vs-64-bit / version, because the plugin runs inside KiCad's own interpreter.
"""

from __future__ import annotations

import os

from plugin import mcp_config


class TestFindOrder:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        py = tmp_path / "custom_python"; py.write_text("")
        monkeypatch.setenv("KICAD_PYTHON_PATH", str(py))
        monkeypatch.setattr(mcp_config, "_sys_python", lambda: "/never")
        assert mcp_config.find_kicad_python() == str(py)

    def test_env_ignored_if_missing_file(self, monkeypatch):
        monkeypatch.setenv("KICAD_PYTHON_PATH", "/does/not/exist")
        monkeypatch.setattr(mcp_config, "_sys_python", lambda: "/k/py")
        assert mcp_config.find_kicad_python() == "/k/py"

    def test_falls_to_sys(self, monkeypatch):
        monkeypatch.delenv("KICAD_PYTHON_PATH", raising=False)
        monkeypatch.setattr(mcp_config, "_sys_python", lambda: "/kicad/py")
        assert mcp_config.find_kicad_python() == "/kicad/py"

    def test_falls_to_scan(self, monkeypatch):
        monkeypatch.delenv("KICAD_PYTHON_PATH", raising=False)
        monkeypatch.setattr(mcp_config, "_sys_python", lambda: None)
        monkeypatch.setattr(mcp_config, "_scan_python", lambda: "/scanned/py")
        assert mcp_config.find_kicad_python() == "/scanned/py"


class TestSysPython:
    def test_uses_executable_when_python(self, tmp_path, monkeypatch):
        name = "python.exe" if os.name == "nt" else "python3"
        py = tmp_path / name; py.write_text("")
        monkeypatch.setattr(mcp_config.sys, "executable", str(py))
        assert mcp_config._sys_python() == str(py)

    def test_derives_from_base_prefix(self, tmp_path, monkeypatch):
        name = "python.exe" if os.name == "nt" else "python3"
        py = tmp_path / name; py.write_text("")
        # executable not python-like (e.g. embedded host exe like kicad.exe)
        monkeypatch.setattr(mcp_config.sys, "executable", "/x/kicad.exe")
        monkeypatch.setattr(mcp_config.sys, "base_prefix", str(tmp_path))
        monkeypatch.setattr(mcp_config.sys, "prefix", str(tmp_path))
        assert mcp_config._sys_python() == str(py)

    def test_none_when_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mcp_config.sys, "executable", "/x/kicad.exe")
        monkeypatch.setattr(mcp_config.sys, "base_prefix", str(tmp_path))
        monkeypatch.setattr(mcp_config.sys, "prefix", str(tmp_path))
        assert mcp_config._sys_python() is None


class TestVersionKey:
    def test_10_beats_9(self):
        k10 = mcp_config._version_key(r"C:\Program Files\KiCad\10.0\bin\python.exe")
        k9 = mcp_config._version_key(r"C:\Program Files (x86)\KiCad\9.0\bin\python.exe")
        assert k10 > k9

    def test_posix_path(self):
        assert mcp_config._version_key(
            "/mnt/d/Program Files/KiCad/10.0/bin/python.exe") == [10, 0]


class TestScanPython:
    def test_finds_x86_install_newest_version(self, tmp_path, monkeypatch):
        if os.name != "nt":
            import pytest
            pytest.skip("nt-only scan patterns")
        name = "python.exe"
        pf86 = tmp_path / "PFx86"
        for ver in ("9.0", "10.0"):
            d = pf86 / "KiCad" / ver / "bin"; d.mkdir(parents=True)
            (d / name).write_text("")
        # redirect ALL bases to our tmp so the real C:\ install can't interfere
        for v in ("ProgramW6432", "ProgramFiles", "LOCALAPPDATA"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("ProgramFiles(x86)", str(pf86))
        real_glob = mcp_config.glob.glob  # capture before patching
        monkeypatch.setattr(mcp_config.glob, "glob",
                            lambda pat: real_glob(pat) if str(pf86) in pat else [])
        got = mcp_config._scan_python()
        assert got and "10.0" in got and "9.0" not in got
