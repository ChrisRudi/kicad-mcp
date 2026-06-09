# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for kicad_mcp.utils.find_kicad_python."""
import pytest

from kicad_mcp.utils import find_kicad_python as mod


class TestWindowsToWsl:
    def test_c_drive(self):
        assert (
            mod._windows_to_wsl_path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
            == "/mnt/c/Program Files/KiCad/10.0/bin/python.exe"
        )

    def test_other_drive_lowercased(self):
        assert mod._windows_to_wsl_path(r"D:\KiCad\bin\python.exe") == "/mnt/d/KiCad/bin/python.exe"

    def test_non_windows_path_returns_none(self):
        assert mod._windows_to_wsl_path("/usr/bin/python3") is None
        assert mod._windows_to_wsl_path("relative/path") is None
        assert mod._windows_to_wsl_path("") is None


class TestFindKicadPython:
    def test_honors_kicad_python_path(self, monkeypatch, make_executable):
        fake = make_executable("python3")
        monkeypatch.setenv("KICAD_PYTHON_PATH", fake)

        assert mod.find_kicad_python() == fake

    def test_returns_none_when_nothing_found(self, monkeypatch):
        monkeypatch.delenv("KICAD_PYTHON_PATH", raising=False)
        monkeypatch.delenv("KICAD_INSTALL_DIR", raising=False)
        monkeypatch.setattr(mod, "_get_common_python_paths", lambda _system: [])

        assert mod.find_kicad_python() is None

    def test_derives_from_install_dir_windows(self, monkeypatch, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        py = bin_dir / "python.exe"
        py.write_text("")
        py.chmod(0o755)

        monkeypatch.setattr(mod.platform, "system", lambda: "Windows")
        monkeypatch.setenv("KICAD_INSTALL_DIR", str(tmp_path))
        monkeypatch.delenv("KICAD_PYTHON_PATH", raising=False)
        monkeypatch.setattr(mod, "_get_common_python_paths", lambda _system: [])

        assert mod.find_kicad_python() == str(py)

    def test_install_dir_candidates_windows(self):
        import os
        cands = mod._python_candidates_from_install_dir(r"C:\KiCad", "Windows")
        assert cands == [os.path.join(r"C:\KiCad", "bin", "python.exe")]

    def test_install_dir_candidates_darwin(self):
        cands = mod._python_candidates_from_install_dir("/Applications/KiCad/KiCad.app", "Darwin")
        # Normalize path separators so the assertion works on Windows CI runners
        # (os.path.join on Windows uses '\' even for Darwin install dirs).
        norm = [c.replace("\\", "/") for c in cands]
        assert any("Python.framework" in c for c in norm)
        assert any(c.endswith("MacOS/python") for c in norm)

    def test_install_dir_candidates_linux(self):
        cands = mod._python_candidates_from_install_dir("/opt/kicad", "Linux")
        norm = [c.replace("\\", "/") for c in cands]
        assert "/opt/kicad/bin/python3" in norm
        assert "/opt/kicad/bin/python.exe" in norm


class TestCommonPaths:
    @pytest.mark.parametrize(
        "system,needle",
        [
            ("Windows", r"C:\Program Files\KiCad\10.0\bin\python.exe"),
            ("Darwin", "/Applications/KiCad/KiCad.app"),
            ("Linux", "/usr/bin/python3"),
        ],
    )
    def test_platform_includes_expected_path(self, system, needle):
        paths = mod._get_common_python_paths(system)
        assert any(needle in p for p in paths), (
            f"{system!r} should include a path containing {needle!r}; got {paths}"
        )

    def test_linux_includes_wsl_paths(self):
        """Native Linux binary still searches WSL mounts so a WSL dev with
        Windows KiCad works out of the box."""
        paths = mod._get_common_python_paths("Linux")
        assert any("/mnt/c/Program Files/KiCad" in p for p in paths)
