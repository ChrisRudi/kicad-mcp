# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the disk-write guard that blocks patching a .kicad_pcb while it is
open in the KiCad GUI (the file-vs-editor save-conflict prevention).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kicad_mcp.utils import board_open_guard as guard


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # clean module cache + a deterministic, override-off baseline each test
    guard._client = None
    guard._last_fail = 0.0
    monkeypatch.delenv(guard._ALLOW_ENV, raising=False)
    yield


def _fake_client(open_paths):
    docs = [SimpleNamespace(board_filename=p) for p in open_paths]
    return SimpleNamespace(get_open_documents=lambda _t: docs)


def _factory(open_paths):
    return lambda: _fake_client(open_paths)


class TestIsOpen:
    def test_open_by_basename(self, monkeypatch):
        # editor reports a different directory but the same file name
        monkeypatch.setattr(guard, "_get_client",
                            lambda factory=None: _fake_client(
                                ["/proj/sub/board.kicad_pcb"]))
        assert guard.is_pcb_open_in_gui("/other/dir/board.kicad_pcb")

    def test_not_open(self, monkeypatch):
        monkeypatch.setattr(guard, "_get_client",
                            lambda factory=None: _fake_client(
                                ["other.kicad_pcb"]))
        assert not guard.is_pcb_open_in_gui("/d/board.kicad_pcb")

    def test_no_client_is_not_open(self, monkeypatch):
        monkeypatch.setattr(guard, "_get_client", lambda factory=None: None)
        assert not guard.is_pcb_open_in_gui("/d/board.kicad_pcb")


class TestGuard:
    def test_blocks_open_pcb(self, monkeypatch):
        monkeypatch.setattr(guard, "is_pcb_open_in_gui",
                            lambda p, factory=None: True)
        with pytest.raises(guard.BoardOpenError):
            guard.guard_pcb_disk_write("/d/board.kicad_pcb")

    def test_allows_when_not_open(self, monkeypatch):
        monkeypatch.setattr(guard, "is_pcb_open_in_gui",
                            lambda p, factory=None: False)
        guard.guard_pcb_disk_write("/d/board.kicad_pcb")  # no raise

    def test_schematic_never_blocked(self, monkeypatch):
        # Eeschema has no IPC save in KiCad 10 → text-patcher is the path.
        monkeypatch.setattr(guard, "is_pcb_open_in_gui",
                            lambda p, factory=None: True)
        guard.guard_pcb_disk_write("/d/sheet.kicad_sch")  # no raise

    def test_override_env_bypasses(self, monkeypatch):
        monkeypatch.setenv(guard._ALLOW_ENV, "1")
        monkeypatch.setattr(guard, "is_pcb_open_in_gui",
                            lambda p, factory=None: True)
        guard.guard_pcb_disk_write("/d/board.kicad_pcb")  # no raise


class TestClientCache:
    def test_negative_cache_skips_retry(self, monkeypatch):
        calls = []

        def _boom():
            calls.append(1)
            raise RuntimeError("no kicad")

        # first call attempts + fails, arming the negative TTL
        assert guard._get_client(_boom) is None
        # second call within TTL must NOT retry the factory
        assert guard._get_client(_boom) is None
        assert len(calls) == 1

    def test_no_socket_env_short_circuits(self, monkeypatch):
        monkeypatch.delenv(guard._SOCKET_ENV, raising=False)
        # factory=None path: without the socket env, no connect is attempted
        assert guard._get_client() is None


class TestWriteTextGuard:
    def test_write_text_blocks_open_board(self, tmp_path, monkeypatch):
        from kicad_mcp.cache import file_cache
        monkeypatch.setattr(guard, "is_pcb_open_in_gui",
                            lambda p, factory=None: True)
        target = tmp_path / "b.kicad_pcb"
        target.write_text("(orig)")
        with pytest.raises(guard.BoardOpenError):
            file_cache.write_text(str(target), "(new)")
        assert target.read_text() == "(orig)"  # file untouched

    def test_write_text_writes_when_closed(self, tmp_path, monkeypatch):
        from kicad_mcp.cache import file_cache
        monkeypatch.setattr(guard, "is_pcb_open_in_gui",
                            lambda p, factory=None: False)
        target = tmp_path / "b.kicad_pcb"
        file_cache.write_text(str(target), "(new)")
        assert target.read_text() == "(new)"
