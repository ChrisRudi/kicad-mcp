# SPDX-License-Identifier: GPL-3.0-or-later
"""Drift guard for the bundled MCP server.

``plugin/mcp/kicad_mcp/`` is what the INSTALLED plugin runs (``claude_action``
loads it bundled-first), so if it drifts from canonical ``kicad_mcp/`` users
silently run stale server code — exactly how the v0.4.0 pinout feature and
several IPC-hardening fixes never reached the live system. This test fails the
moment the two diverge; the fix is one command:

    python scripts/sync_bundle.py
"""

from __future__ import annotations

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sync_bundle():
    spec = importlib.util.spec_from_file_location(
        "sync_bundle", os.path.join(ROOT, "scripts", "sync_bundle.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bundle_mirrors_canonical():
    missing, extra, differing = _load_sync_bundle().drift()
    problems = []
    if missing:
        problems.append(f"im Bundle FEHLEND: {sorted(missing)}")
    if extra:
        problems.append(f"im Bundle ÜBERZÄHLIG: {sorted(extra)}")
    if differing:
        problems.append(f"ABWEICHEND: {sorted(differing)}")
    assert not problems, (
        "plugin/mcp/kicad_mcp/ ist nicht synchron mit kicad_mcp/ — der "
        "installierte Plugin-Server läuft sonst auf altem Code.\n"
        "Beheben: python scripts/sync_bundle.py\n" + "\n".join(problems))


def test_sync_bundle_check_mode_is_clean():
    # The script's own --check path must agree the tree is in sync (no drift),
    # i.e. drift() returns empty sets — guards the script and the bundle at once.
    missing, extra, differing = _load_sync_bundle().drift()
    assert (missing, extra, differing) == (set(), set(), set())
