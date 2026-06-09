# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for kicad_mcp.utils.kicad_cli.KiCadCLIManager.

Focused on detection logic, not on actually running kicad-cli.
"""
import pytest

from kicad_mcp.utils.kicad_cli import KiCadCLIManager


class TestPathConversion:
    def test_windows_to_wsl(self):
        mgr = KiCadCLIManager()
        assert (
            mgr._windows_to_wsl_path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")
            == "/mnt/c/Program Files/KiCad/10.0/bin/kicad-cli.exe"
        )

    def test_non_windows_path_returns_none(self):
        mgr = KiCadCLIManager()
        assert mgr._windows_to_wsl_path("/usr/bin/kicad-cli") is None
        assert mgr._windows_to_wsl_path("") is None


class TestExecutableName:
    @pytest.mark.parametrize(
        "system,expected",
        [
            ("Windows", "kicad-cli.exe"),
            ("Linux", "kicad-cli"),
            ("Darwin", "kicad-cli"),
        ],
    )
    def test_name_per_platform(self, system, expected):
        mgr = KiCadCLIManager()
        mgr._system = system
        assert mgr._get_cli_executable_name() == expected


class TestCommonInstallationPaths:
    def test_windows_paths_include_program_files(self):
        mgr = KiCadCLIManager()
        mgr._system = "Windows"
        paths = mgr._get_common_installation_paths()
        assert any(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe" in p for p in paths)

    def test_linux_paths_include_native_and_wsl(self):
        mgr = KiCadCLIManager()
        mgr._system = "Linux"
        paths = mgr._get_common_installation_paths()
        assert "/usr/bin/kicad-cli" in paths
        assert any("/mnt/c/Program Files/KiCad" in p for p in paths)

    def test_macos_paths_include_app_bundle_and_homebrew(self):
        mgr = KiCadCLIManager()
        mgr._system = "Darwin"
        paths = mgr._get_common_installation_paths()
        assert any("/Applications/KiCad/KiCad.app" in p for p in paths)
        assert any("homebrew" in p for p in paths)


class TestDetectCliPath:
    def test_env_var_takes_precedence(self, monkeypatch, make_executable):
        fake = make_executable("kicad-cli")
        monkeypatch.setenv("KICAD_CLI_PATH", fake)

        mgr = KiCadCLIManager()
        assert mgr._detect_cli_path() == fake

    def test_env_var_with_windows_path_on_wsl_normalized(
        self, monkeypatch, tmp_path, make_executable
    ):
        """Regression: a Windows-style KICAD_CLI_PATH injected on a
        Linux/WSL runtime must be translated to /mnt/c/..."""
        # Build a fake filesystem mimicking /mnt/c/fake/kicad-cli.exe at tmp_path
        fake_exe = make_executable("kicad-cli.exe")

        mgr = KiCadCLIManager()
        mgr._system = "Linux"

        # Point _windows_to_wsl_path at a real file so _normalize_cli_path finds it.
        monkeypatch.setattr(
            mgr, "_windows_to_wsl_path", lambda p: fake_exe if p.endswith(".exe") else None
        )
        monkeypatch.setenv("KICAD_CLI_PATH", r"C:\does-not-exist\kicad-cli.exe")

        assert mgr._detect_cli_path() == fake_exe

    def test_env_var_missing_file_falls_through(self, monkeypatch):
        monkeypatch.setenv("KICAD_CLI_PATH", "/definitely/does/not/exist/kicad-cli")
        mgr = KiCadCLIManager()
        mgr._system = "Linux"
        monkeypatch.setattr(mgr, "_get_common_installation_paths", lambda: [])
        monkeypatch.setattr("shutil.which", lambda _name: None)

        assert mgr._detect_cli_path() is None


class TestCaching:
    def test_find_kicad_cli_caches_validated_result(self, monkeypatch, make_executable):
        fake = make_executable("kicad-cli")
        mgr = KiCadCLIManager()

        monkeypatch.setattr(mgr, "_detect_cli_path", lambda: fake)
        monkeypatch.setattr(mgr, "_validate_cli_path", lambda _p: True)

        assert mgr.find_kicad_cli() == fake
        # Second call: _detect_cli_path should not be consulted again.
        monkeypatch.setattr(
            mgr, "_detect_cli_path", lambda: pytest.fail("cache miss")
        )
        assert mgr.find_kicad_cli() == fake

    def test_force_refresh_bypasses_cache(self, monkeypatch, make_executable):
        fake1 = make_executable("kicad-cli")
        fake2 = make_executable("kicad-cli-v2")
        mgr = KiCadCLIManager()
        monkeypatch.setattr(mgr, "_validate_cli_path", lambda _p: True)

        monkeypatch.setattr(mgr, "_detect_cli_path", lambda: fake1)
        assert mgr.find_kicad_cli() == fake1

        monkeypatch.setattr(mgr, "_detect_cli_path", lambda: fake2)
        assert mgr.find_kicad_cli(force_refresh=True) == fake2
