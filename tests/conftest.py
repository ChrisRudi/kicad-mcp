# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared pytest fixtures."""
import sys

import pytest


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """Run the test in an empty temp cwd with no .env above it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fresh_config(monkeypatch):
    """Force kicad_mcp.config to be re-imported so platform mocks take effect.

    Use this as the first line of a test that sets platform.system() via
    monkeypatch and then imports kicad_mcp.config.
    """
    for mod in list(sys.modules):
        if mod == "kicad_mcp.config" or mod.startswith("kicad_mcp.config."):
            del sys.modules[mod]
    yield
    for mod in list(sys.modules):
        if mod == "kicad_mcp.config" or mod.startswith("kicad_mcp.config."):
            del sys.modules[mod]


@pytest.fixture
def make_executable(tmp_path):
    """Create a tiny fake executable and return its path (as str)."""
    def _make(name: str = "fake-python") -> str:
        path = tmp_path / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
        return str(path)
    return _make
