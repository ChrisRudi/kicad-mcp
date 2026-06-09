# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for kicad_mcp.utils.path_env.

Covers the four code paths the rest of the codebase relies on:

  1. ``detect_environment`` — single source of truth, value pinned to the
     real runtime so we exercise the active branch in production.
  2. ``to_local_path`` / ``from_local_to_other`` — the regex fallback paths
     are deterministic per env; we patch ``detect_environment`` to drive
     each branch without a process restart.
  3. ``kicad_paths`` — env-var override and bundled-default discovery.
"""

from __future__ import annotations

import sys

import pytest

from kicad_mcp.utils import path_env


@pytest.fixture(autouse=True)
def _reset_caches():
    """Each test starts with a clean ``lru_cache`` state for the module."""
    path_env.detect_environment.cache_clear()
    path_env.kicad_paths.cache_clear()
    yield
    path_env.detect_environment.cache_clear()
    path_env.kicad_paths.cache_clear()


def _force_env(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setattr(path_env, "detect_environment", lambda: env)


# ---------------------------------------------------------------------------
# detect_environment
# ---------------------------------------------------------------------------


class TestDetectEnvironment:
    def test_returns_one_of_known_values(self):
        assert path_env.detect_environment() in {"windows", "wsl", "linux", "darwin"}

    def test_windows_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        path_env.detect_environment.cache_clear()
        assert path_env.detect_environment() == "windows"

    def test_darwin_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        path_env.detect_environment.cache_clear()
        assert path_env.detect_environment() == "darwin"

    def test_is_wsl_alias_consistent(self):
        assert path_env.is_wsl() == (path_env.detect_environment() == "wsl")


# ---------------------------------------------------------------------------
# to_local_path
# ---------------------------------------------------------------------------


class TestToLocalPath:
    def test_empty_input_returned_unchanged(self):
        assert path_env.to_local_path("") == ""
        assert path_env.to_local_path(None) is None  # type: ignore[arg-type]

    def test_on_windows_wsl_mnt_converted_to_drive(self, monkeypatch):
        _force_env(monkeypatch, "windows")
        assert path_env.to_local_path("/mnt/c/Users/foo") == r"C:\Users\foo"
        assert path_env.to_local_path("/mnt/d/A/B") == r"D:\A\B"

    def test_on_windows_drive_path_normalized(self, monkeypatch):
        _force_env(monkeypatch, "windows")
        assert path_env.to_local_path("C:/Users/foo") == r"C:\Users\foo"
        # Already-windows paths are returned unchanged.
        assert path_env.to_local_path(r"C:\Users\foo") == r"C:\Users\foo"

    def test_on_wsl_drive_path_converted_to_mnt(self, monkeypatch):
        _force_env(monkeypatch, "wsl")
        # Force the regex fallback (wslpath may not exist or may rewrite paths)
        monkeypatch.setattr(
            path_env.subprocess, "run",
            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError),
        )
        assert path_env.to_local_path(r"C:\Users\foo") == "/mnt/c/Users/foo"
        assert path_env.to_local_path(r"D:\A\B") == "/mnt/d/A/B"

    def test_on_wsl_posix_path_returned_unchanged(self, monkeypatch):
        _force_env(monkeypatch, "wsl")
        assert path_env.to_local_path("/home/user/proj") == "/home/user/proj"
        assert path_env.to_local_path("/mnt/c/Users/foo") == "/mnt/c/Users/foo"

    def test_on_linux_paths_returned_unchanged(self, monkeypatch):
        _force_env(monkeypatch, "linux")
        assert path_env.to_local_path("/home/user") == "/home/user"
        # Even Windows-shaped strings are passed through; downstream isfile
        # check will reject them with a clear "PCB not found" error.
        assert path_env.to_local_path(r"C:\Users\foo") == r"C:\Users\foo"

    def test_idempotent(self, monkeypatch):
        for env in ("windows", "wsl", "linux", "darwin"):
            _force_env(monkeypatch, env)
            sample = {
                "windows": r"C:\Users\foo",
                "wsl": "/mnt/c/Users/foo",
                "linux": "/home/u/proj",
                "darwin": "/Users/u/proj",
            }[env]
            once = path_env.to_local_path(sample)
            twice = path_env.to_local_path(once)
            assert once == twice


# ---------------------------------------------------------------------------
# from_local_to_other (round-trip)
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_wsl_to_windows_and_back(self, monkeypatch):
        _force_env(monkeypatch, "wsl")
        monkeypatch.setattr(
            path_env.subprocess, "run",
            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError),
        )
        local = path_env.to_local_path(r"C:\foo\bar")
        assert local == "/mnt/c/foo/bar"
        other = path_env.from_local_to_other(local)
        assert other == r"C:\foo\bar"

    def test_windows_round_trip(self, monkeypatch):
        _force_env(monkeypatch, "windows")
        local = path_env.to_local_path("/mnt/c/foo/bar")
        assert local == r"C:\foo\bar"
        # On Windows, "the other side" is WSL; no wslpath available there.
        other = path_env.from_local_to_other(local)
        assert other == "/mnt/c/foo/bar"


# ---------------------------------------------------------------------------
# kicad_paths discovery + override
# ---------------------------------------------------------------------------


class TestKicadPaths:
    def test_returns_all_keys(self):
        out = path_env.kicad_paths()
        assert set(out.keys()) == {"kicad_cli", "footprints", "symbols", "python"}
        # Each value is a string (possibly empty).
        for v in out.values():
            assert isinstance(v, str)

    def test_env_override_takes_precedence(self, monkeypatch, tmp_path):
        custom_lib = tmp_path / "my_kicad_libs"
        custom_lib.mkdir()
        monkeypatch.setenv("KICAD_LIB_ROOT", str(custom_lib))
        path_env.kicad_paths.cache_clear()
        assert path_env.kicad_lib_root() == str(custom_lib)

    def test_unknown_override_falls_through(self, monkeypatch, tmp_path):
        # Override that points to a nonexistent path — discovery falls
        # through to the bundled defaults.
        monkeypatch.setenv("KICAD_LIB_ROOT", str(tmp_path / "does-not-exist"))
        path_env.kicad_paths.cache_clear()
        # Whatever comes back must NOT be the bogus override.
        assert path_env.kicad_lib_root() != str(tmp_path / "does-not-exist")

    def test_kicad_cli_helper_matches_dict(self):
        assert path_env.kicad_cli() == path_env.kicad_paths()["kicad_cli"]
