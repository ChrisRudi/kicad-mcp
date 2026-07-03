# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the size-adaptive DRC timeout (config.drc_timeout_seconds) and
the timeout handling in the CLI DRC runner.

DRC on a large board can legitimately run for minutes (KiCad #17434) and a
cold cloud-synced read alone is ~80 s, so the budget must be generous and
scale with board size — never a fixed short value that kills real work — while
still capping a true hang. These tests pin that policy.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from kicad_mcp.config import TIMEOUT_CONSTANTS, drc_timeout_seconds
import kicad_mcp.tools.drc_impl.cli_drc as cli_drc


# --- drc_timeout_seconds: env override --------------------------------------

def test_env_override_positive_number_wins(monkeypatch):
    monkeypatch.setenv("KICAD_MCP_DRC_TIMEOUT_S", "123")
    # even with a huge board on disk, the explicit override is used verbatim
    assert drc_timeout_seconds("/nonexistent/huge.kicad_pcb") == 123.0


@pytest.mark.parametrize("val", ["0", "none", "off", "None", "OFF"])
def test_env_override_disables_timeout(monkeypatch, val):
    monkeypatch.setenv("KICAD_MCP_DRC_TIMEOUT_S", val)
    assert drc_timeout_seconds("/whatever.kicad_pcb") is None


def test_env_override_malformed_falls_back_to_adaptive(monkeypatch):
    monkeypatch.setenv("KICAD_MCP_DRC_TIMEOUT_S", "not-a-number")
    # falls through to the size-adaptive budget → base for a missing path
    assert drc_timeout_seconds(None) == TIMEOUT_CONSTANTS["drc_base"]


# --- drc_timeout_seconds: size-adaptive budget ------------------------------

def test_missing_path_returns_base(monkeypatch):
    monkeypatch.delenv("KICAD_MCP_DRC_TIMEOUT_S", raising=False)
    assert drc_timeout_seconds(None) == TIMEOUT_CONSTANTS["drc_base"]
    assert drc_timeout_seconds("/does/not/exist.kicad_pcb") == TIMEOUT_CONSTANTS["drc_base"]


def test_budget_scales_with_size(monkeypatch, tmp_path):
    monkeypatch.delenv("KICAD_MCP_DRC_TIMEOUT_S", raising=False)
    board = tmp_path / "board.kicad_pcb"
    board.write_bytes(b"x" * (4 * 1024 * 1024))  # 4 MB
    expected = TIMEOUT_CONSTANTS["drc_base"] + 4 * TIMEOUT_CONSTANTS["drc_per_mb"]
    assert drc_timeout_seconds(str(board)) == pytest.approx(expected, rel=0.02)


def test_budget_clamped_to_max(monkeypatch, tmp_path):
    monkeypatch.delenv("KICAD_MCP_DRC_TIMEOUT_S", raising=False)
    board = tmp_path / "giant.kicad_pcb"
    board.write_bytes(b"")
    # 1 GB sparse file → adaptive budget would blow past the ceiling
    os.truncate(str(board), 1024 * 1024 * 1024)
    assert drc_timeout_seconds(str(board)) == TIMEOUT_CONSTANTS["drc_max"]


# --- cli_drc: TimeoutExpired is surfaced as a clean error, never a hang ------

def test_drc_timeout_returns_clean_error(monkeypatch, tmp_path):
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)")

    monkeypatch.setattr(cli_drc, "find_kicad_cli", lambda: "/fake/kicad-cli")

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="kicad-cli", timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(cli_drc.subprocess, "run", _raise_timeout)

    result = asyncio.run(cli_drc.run_drc_via_cli(str(board)))
    assert result["success"] is False
    assert "timed out" in result["error"].lower()
    assert "KICAD_MCP_DRC_TIMEOUT_S" in result["error"]


def test_drc_runs_off_event_loop(monkeypatch, tmp_path):
    """The blocking subprocess must be dispatched via asyncio.to_thread so a
    long DRC does not freeze the event loop."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)")
    monkeypatch.setattr(cli_drc, "find_kicad_cli", lambda: "/fake/kicad-cli")

    seen = {}

    def _fake_run(cmd, **kwargs):
        # a report file is expected by the caller; write an empty violations set
        out_idx = cmd.index("--output") + 1
        with open(cmd[out_idx], "w", encoding="utf-8") as fh:
            fh.write('{"violations": []}')
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    real_to_thread = asyncio.to_thread
    called = {"to_thread": False}

    async def _tracking_to_thread(func, *a, **k):
        called["to_thread"] = True
        return await real_to_thread(func, *a, **k)

    monkeypatch.setattr(cli_drc.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli_drc.asyncio, "to_thread", _tracking_to_thread)

    result = asyncio.run(cli_drc.run_drc_via_cli(str(board)))
    assert called["to_thread"] is True
    assert result["success"] is True
    # a finite, generous budget was passed down for a tiny board (~base)
    assert seen["timeout"] == pytest.approx(TIMEOUT_CONSTANTS["drc_base"], abs=1.0)
