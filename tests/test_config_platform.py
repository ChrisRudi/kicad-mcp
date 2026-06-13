# SPDX-License-Identifier: GPL-3.0-or-later
"""Platform-default tests for kicad_mcp.config.

The module computes paths at import time based on platform.system(), so we
reload it under mocked platforms to verify each branch.
"""
import importlib
import platform
import sys

import pytest


def _reload_config(monkeypatch, system_name: str):
    monkeypatch.setattr(platform, "system", lambda: system_name)
    monkeypatch.delenv("KICAD_USER_DIR", raising=False)
    monkeypatch.delenv("KICAD_INSTALL_DIR", raising=False)
    monkeypatch.delenv("KICAD_SEARCH_PATHS", raising=False)
    sys.modules.pop("kicad_mcp.config", None)
    return importlib.import_module("kicad_mcp.config")


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    sys.modules.pop("kicad_mcp.config", None)


def test_windows_defaults(monkeypatch):
    cfg = _reload_config(monkeypatch, "Windows")
    assert cfg.KICAD_APP_PATH == r"C:\Program Files\KiCad"
    assert cfg.KICAD_USER_DIR.endswith("KiCad")


def test_linux_defaults(monkeypatch):
    cfg = _reload_config(monkeypatch, "Linux")
    assert cfg.KICAD_APP_PATH == "/usr/share/kicad"


def test_darwin_defaults(monkeypatch):
    cfg = _reload_config(monkeypatch, "Darwin")
    assert cfg.KICAD_APP_PATH == "/Applications/KiCad/KiCad.app"


def test_unknown_platform_falls_back_to_macos(monkeypatch):
    cfg = _reload_config(monkeypatch, "Plan9")
    assert cfg.KICAD_APP_PATH == "/Applications/KiCad/KiCad.app"


def test_user_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setenv("KICAD_USER_DIR", str(tmp_path))
    monkeypatch.delenv("KICAD_INSTALL_DIR", raising=False)
    monkeypatch.delenv("KICAD_SEARCH_PATHS", raising=False)
    sys.modules.pop("kicad_mcp.config", None)

    cfg = importlib.import_module("kicad_mcp.config")

    assert cfg.KICAD_USER_DIR == str(tmp_path)


def test_extensions_mapping_stable(monkeypatch):
    cfg = _reload_config(monkeypatch, "Linux")
    assert cfg.KICAD_EXTENSIONS["project"] == ".kicad_pro"
    assert cfg.KICAD_EXTENSIONS["pcb"] == ".kicad_pcb"
    assert cfg.KICAD_EXTENSIONS["schematic"] == ".kicad_sch"
