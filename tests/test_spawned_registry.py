# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the spawned-editor registry that prevents orphaned, board-less
``pcbnew`` ghosts from squatting the IPC socket (the "kein eindeutiges Board"
root cause). Headless: injected killer/aliveness, tmp registry file."""

from __future__ import annotations

import json

from kicad_mcp.utils import spawned_registry as sr


def _path(tmp_path):
    return str(tmp_path / "spawned.json")


class TestRecordForget:
    def test_record_and_read(self, tmp_path):
        p = _path(tmp_path)
        sr.record(1234, p)
        sr.record(5678, p)
        assert sorted(sr.pids(p)) == [1234, 5678]

    def test_record_is_idempotent(self, tmp_path):
        p = _path(tmp_path)
        sr.record(1234, p)
        sr.record(1234, p)
        assert sr.pids(p) == [1234]

    def test_record_zero_is_noop(self, tmp_path):
        p = _path(tmp_path)
        sr.record(0, p)
        assert sr.pids(p) == []

    def test_forget(self, tmp_path):
        p = _path(tmp_path)
        sr.record(1, p)
        sr.record(2, p)
        sr.forget(1, p)
        assert sr.pids(p) == [2]

    def test_missing_file_reads_empty(self, tmp_path):
        assert sr.pids(_path(tmp_path)) == []

    def test_corrupt_file_reads_empty(self, tmp_path):
        p = _path(tmp_path)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        assert sr.pids(p) == []


class TestReap:
    def test_kills_alive_clears_registry(self, tmp_path):
        p = _path(tmp_path)
        sr.record(11, p)
        sr.record(22, p)
        killed = []
        reaped = sr.reap(p, killer=killed.append, alive=lambda pid: True)
        assert sorted(reaped) == [11, 22]
        assert sorted(killed) == [11, 22]
        assert sr.pids(p) == []          # registry cleared

    def test_skips_dead_pids_but_still_clears(self, tmp_path):
        p = _path(tmp_path)
        sr.record(11, p)
        sr.record(22, p)
        killed = []
        # 11 already gone, 22 alive -> only 22 gets killed, file cleared
        reaped = sr.reap(p, killer=killed.append,
                         alive=lambda pid: pid == 22)
        assert reaped == [22] and killed == [22]
        assert sr.pids(p) == []

    def test_reap_empty_is_safe(self, tmp_path):
        assert sr.reap(_path(tmp_path), killer=lambda pid: None,
                       alive=lambda pid: True) == []

    def test_reap_is_repeatable(self, tmp_path):
        p = _path(tmp_path)
        sr.record(99, p)
        sr.reap(p, killer=lambda pid: None, alive=lambda pid: True)
        # second call: nothing left, no error
        assert sr.reap(p, killer=lambda pid: None, alive=lambda pid: True) == []


class TestRegistryPath:
    def test_path_is_in_tempdir(self):
        import tempfile
        assert sr.registry_path().startswith(tempfile.gettempdir())
        assert sr.registry_path().endswith(sr.REGISTRY_FILENAME)


class TestPluginContract:
    """The plugin reaper cannot import this package (kicad_mcp/__init__ pulls the
    whole server), so it mirrors the filename by literal. This guards that the
    two never silently diverge — a drifted name = ghosts never reaped."""

    def test_plugin_filename_matches_canonical(self):
        from plugin import claude_bridge
        assert (claude_bridge._SPAWNED_REGISTRY_FILENAME
                == sr.REGISTRY_FILENAME)

    def test_plugin_reaper_reads_same_file_and_kills(self, tmp_path,
                                                     monkeypatch):
        # Point the plugin reaper's tempdir at tmp_path and verify it reads the
        # registry the server format writes, kills the PIDs, and deletes it.
        from plugin import claude_bridge
        reg = tmp_path / claude_bridge._SPAWNED_REGISTRY_FILENAME
        reg.write_text(json.dumps([4242, 4243]), encoding="utf-8")

        import tempfile as _tf
        monkeypatch.setattr(_tf, "gettempdir", lambda: str(tmp_path))
        calls = []
        monkeypatch.setattr(claude_bridge.subprocess, "run",
                            lambda *a, **k: calls.append(a[0]))
        monkeypatch.setattr(claude_bridge.os, "name", "nt")

        n = claude_bridge._reap_spawned_editors()
        assert n == 2
        # both PIDs taskkilled
        flat = " ".join(" ".join(map(str, c)) for c in calls)
        assert "4242" in flat and "4243" in flat and "taskkill" in flat
        assert not reg.exists()          # registry file removed
