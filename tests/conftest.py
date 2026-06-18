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


@pytest.fixture(autouse=True)
def _block_real_pip_install():
    """Hermeticity guard: no test may shell out to a real ``pip install``.

    A real install hits the network *and* mutates the running interpreter. The
    concrete bug this prevents: the dynamic "call every tool with {}" smoke test
    invokes ``ipc_install_kipy``, whose body runs ``pip install kicad-python``
    against ``sys.executable`` — installing kipy mid-suite and poisoning later
    tests that resolved layer enums while kipy was still absent. Non-``pip
    install`` subprocess calls (kicad-cli, git, …) pass straight through; tests
    that fake subprocess themselves override this within their own scope.

    Patches with a plain yield/finally rather than ``monkeypatch`` on purpose:
    requesting ``monkeypatch`` from an autouse fixture reorders its teardown
    relative to other autouse fixtures, which breaks tests that revert their own
    monkeypatches in a finalizer (e.g. test_path_env's cache reset).
    """
    import subprocess

    def _is_pip_install(cmd) -> bool:
        parts = [str(c) for c in cmd] if isinstance(cmd, (list, tuple)) else str(cmd).split()
        return "pip" in " ".join(parts) and "install" in parts

    def _make_guard(real):
        def _guard(cmd, *args, **kwargs):
            if _is_pip_install(cmd):
                raise RuntimeError(
                    "Blocked a real 'pip install' from a test (hermeticity guard). "
                    "Mock the installer (e.g. ipc_tools._pip_install_kipy) instead "
                    f"of shelling out: {cmd!r}"
                )
            return real(cmd, *args, **kwargs)
        return _guard

    # Only the high-level *functions* (this is what _pip_install_kipy and the
    # deps installer call). Deliberately NOT ``Popen``: it is a class used as a
    # type — e.g. ``subprocess.Popen[bytes]`` annotations in mcp — and replacing
    # it with a function breaks those at import time.
    names = ("run", "call", "check_call", "check_output")
    originals = {n: getattr(subprocess, n) for n in names}
    for _name in names:
        setattr(subprocess, _name, _make_guard(originals[_name]))
    try:
        yield
    finally:
        for _name, _real in originals.items():
            setattr(subprocess, _name, _real)
