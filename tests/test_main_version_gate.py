# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ``_ensure_kicad_version_at_least`` in main.py.

Pre-KiCad-10 installs lack the IPC API the server depends on. The gate
runs as the very first thing in ``__main__`` and aborts with a clear
``sys.exit(1)`` if it detects a too-old install.
"""

from __future__ import annotations

import importlib.util
import os

import pytest


# ---------------------------------------------------------------------------
# Module loader — main.py is a script, not a package; load it on demand.
# ---------------------------------------------------------------------------


@pytest.fixture
def main_module():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_path = os.path.join(here, "main.py")
    spec = importlib.util.spec_from_file_location("kicad_mcp_main", main_path)
    assert spec and spec.loader, "could not locate main.py"
    module = importlib.util.module_from_spec(spec)
    # Avoid running __main__ block — exec_module respects __name__ != __main__.
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------


class TestVersionGate:
    def _patch_cli(self, monkeypatch, fake_cli: str) -> None:
        from kicad_mcp.utils import path_env

        monkeypatch.setattr(path_env, "kicad_cli", lambda: fake_cli)

    def _patch_subprocess(self, monkeypatch, stdout: str, returncode: int = 0) -> None:
        import subprocess

        class _Result:
            def __init__(self):
                self.stdout = stdout
                self.stderr = ""
                self.returncode = returncode

        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: _Result())

    def test_no_cli_returns_silently(self, main_module, monkeypatch) -> None:
        self._patch_cli(monkeypatch, "")
        # also clear PATH-based fallback
        monkeypatch.setattr("shutil.which", lambda *_a, **_kw: None)
        # Should NOT raise SystemExit; simply return.
        main_module._ensure_kicad_version_at_least(10)

    def test_kicad_10_passes(self, main_module, monkeypatch) -> None:
        self._patch_cli(monkeypatch, "/fake/kicad-cli")
        self._patch_subprocess(monkeypatch, "10.0.1\n")
        main_module._ensure_kicad_version_at_least(10)  # no exit expected

    def test_kicad_11_passes(self, main_module, monkeypatch) -> None:
        self._patch_cli(monkeypatch, "/fake/kicad-cli")
        self._patch_subprocess(monkeypatch, "11.0.0\n")
        main_module._ensure_kicad_version_at_least(10)

    def test_kicad_9_aborts(self, main_module, monkeypatch) -> None:
        self._patch_cli(monkeypatch, "/fake/kicad-cli")
        self._patch_subprocess(monkeypatch, "KiCad 9.0.3 (Build 12345)\n")
        with pytest.raises(SystemExit) as exc:
            main_module._ensure_kicad_version_at_least(10)
        assert exc.value.code == 1

    def test_kicad_8_aborts(self, main_module, monkeypatch) -> None:
        self._patch_cli(monkeypatch, "/fake/kicad-cli")
        self._patch_subprocess(monkeypatch, "8.0.7\n")
        with pytest.raises(SystemExit):
            main_module._ensure_kicad_version_at_least(10)

    def test_unparseable_output_does_not_abort(
        self, main_module, monkeypatch
    ) -> None:
        self._patch_cli(monkeypatch, "/fake/kicad-cli")
        # Output without anything matching the version regex.
        self._patch_subprocess(monkeypatch, "no version here\n")
        # Don't abort — just warn (logs a message). Caller continues.
        main_module._ensure_kicad_version_at_least(10)

    def test_subprocess_failure_does_not_abort(
        self, main_module, monkeypatch
    ) -> None:
        self._patch_cli(monkeypatch, "/fake/kicad-cli")
        import subprocess

        def _raise(*_a, **_kw):
            raise FileNotFoundError("kicad-cli not on disk")

        monkeypatch.setattr(subprocess, "run", _raise)
        # Should warn-and-return, not abort.
        main_module._ensure_kicad_version_at_least(10)

    def test_minimum_argument_respected(self, main_module, monkeypatch) -> None:
        # Asking for KiCad 99 against a 10.0.1 install must abort.
        self._patch_cli(monkeypatch, "/fake/kicad-cli")
        self._patch_subprocess(monkeypatch, "10.0.1\n")
        with pytest.raises(SystemExit):
            main_module._ensure_kicad_version_at_least(99)

    def test_default_minimum_is_10(self, main_module) -> None:
        # The constant is the contract: pre-10 is unsupported.
        assert main_module._MIN_KICAD_MAJOR == 10
