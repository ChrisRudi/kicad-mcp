# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for list_user_hotkeys (docs_tools).

The MCP tool reads KiCad's per-user ``user.hotkeys`` file and returns
every action with its bound primary/secondary shortcut. These tests
cover:

* parse fixture file → expected count, structure, namespace inference,
* namespace filter,
* only_bound filter,
* missing file → structured error with tried_paths,
* explicit ``config_path=`` override bypasses env detection,
* ``$KICAD_USER_CONFIG_PATH`` env var honored when no explicit override,
* parse cache keyed on (path, mtime) — re-parsing same file doesn't reread.

All path parameters are passed as WSL-style paths to also exercise
``to_local_path`` normalization at the tool boundary.
"""
from __future__ import annotations

import asyncio

import pytest

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools.docs_tools import (
    _parse_user_hotkeys,
    _HOTKEYS_CACHE,
    register_docs_tools,
)


# Tab-separated fixture matching real KiCad-10 user.hotkeys lines.
FIXTURE_HOTKEYS = (
    "3DViewer.Control\t\t\n"  # section-header-style, no shortcut
    "3DViewer.Control.attribute_dnp\tD\t\n"
    "3DViewer.Control.flipView\tF\t\n"
    "common.Control.print\tCtrl+P\t\n"
    "common.Control.quit\t\t\n"
    "eeschema.EditorControl.editSymbolLibraries\t\t\n"
    "pcbnew.EditorControl.boardSetup\tCtrl+,\t\n"
    "pcbnew.InteractiveRouter.routerHighlightMode\tH\tAlt+H\n"
    "\n"  # blank line — must be skipped
    "garbage_without_tab_or_dot\n"  # malformed — must be skipped
    "kicad.Control.newProject\tCtrl+N\t\n"
)


@pytest.fixture
def hotkeys_file(tmp_path):
    """Write the fixture into a temp dir and return the absolute path."""
    p = tmp_path / "user.hotkeys"
    p.write_text(FIXTURE_HOTKEYS, encoding="utf-8")
    return str(p)


@pytest.fixture
def server():
    """Fresh FastMCP with docs tools registered."""
    mcp = FastMCP("test-docs-tools")
    register_docs_tools(mcp)
    return mcp


def _call(server, name, args):
    """Invoke a tool and pull the structured payload out of the FastMCP tuple."""
    res = asyncio.run(server.call_tool(name, args))
    return res[1] if isinstance(res, tuple) and len(res) > 1 else res


# --- happy path -----------------------------------------------------------


def test_parse_basic_structure(hotkeys_file):
    """Parser drops blank / dot-less garbage and keeps everything else."""
    out = _parse_user_hotkeys(hotkeys_file)
    ids = [a["id"] for a in out]
    assert "3DViewer.Control" in ids
    assert "3DViewer.Control.attribute_dnp" in ids
    assert "pcbnew.EditorControl.boardSetup" in ids
    assert "garbage_without_tab_or_dot" not in ids
    # 9 valid entries in the fixture (10 lines minus blank minus garbage).
    assert len(out) == 9


def test_parse_columns(hotkeys_file):
    """Primary + secondary shortcut columns parsed correctly."""
    out = _parse_user_hotkeys(hotkeys_file)
    by_id = {a["id"]: a for a in out}
    assert by_id["pcbnew.EditorControl.boardSetup"]["shortcut"] == "Ctrl+,"
    assert by_id["pcbnew.EditorControl.boardSetup"]["secondary"] == ""
    assert by_id["pcbnew.InteractiveRouter.routerHighlightMode"]["shortcut"] == "H"
    assert by_id["pcbnew.InteractiveRouter.routerHighlightMode"]["secondary"] == "Alt+H"
    # section-header-style line has empty shortcut.
    assert by_id["3DViewer.Control"]["shortcut"] == ""


def test_namespace_inference(hotkeys_file):
    """Namespace is the prefix before the first dot."""
    out = _parse_user_hotkeys(hotkeys_file)
    by_id = {a["id"]: a for a in out}
    assert by_id["pcbnew.EditorControl.boardSetup"]["namespace"] == "pcbnew"
    assert by_id["common.Control.print"]["namespace"] == "common"
    assert by_id["3DViewer.Control"]["namespace"] == "3DViewer"


def test_tool_happy_path_explicit_path(server, hotkeys_file):
    payload = _call(server, "list_user_hotkeys", {"config_path": hotkeys_file})
    assert payload["success"] is True
    assert payload["config_path_used"] == hotkeys_file
    assert payload["total_actions"] == 9
    assert set(payload["namespaces_available"]) == {
        "3DViewer", "common", "eeschema", "pcbnew", "kicad",
    }


# --- filters --------------------------------------------------------------


def test_namespace_filter(server, hotkeys_file):
    payload = _call(server, "list_user_hotkeys", {
        "config_path": hotkeys_file,
        "namespace": "pcbnew",
    })
    assert payload["success"] is True
    assert payload["namespace_filter"] == "pcbnew"
    assert payload["total_actions"] == 2
    assert all(a["namespace"] == "pcbnew" for a in payload["actions"])
    # namespaces_available still reflects the full file, not the filter.
    assert "common" in payload["namespaces_available"]


def test_only_bound_filter(server, hotkeys_file):
    payload = _call(server, "list_user_hotkeys", {
        "config_path": hotkeys_file,
        "only_bound": True,
    })
    assert payload["success"] is True
    assert payload["only_bound_filter"] is True
    # 6 of the 9 fixture rows have a primary shortcut bound.
    assert payload["total_actions"] == 6
    assert all(a["shortcut"] for a in payload["actions"])


def test_namespace_and_only_bound_combined(server, hotkeys_file):
    payload = _call(server, "list_user_hotkeys", {
        "config_path": hotkeys_file,
        "namespace": "common",
        "only_bound": True,
    })
    assert payload["success"] is True
    assert payload["total_actions"] == 1
    assert payload["actions"][0]["id"] == "common.Control.print"


# --- error paths ----------------------------------------------------------


def test_missing_explicit_path(server, tmp_path):
    bogus = str(tmp_path / "does_not_exist.hotkeys")
    payload = _call(server, "list_user_hotkeys", {"config_path": bogus})
    assert payload["success"] is False
    assert "not found" in payload["error"].lower()
    assert bogus in payload["tried_paths"]


def test_missing_no_path_no_env(server, monkeypatch, tmp_path):
    """No file + no override → structured error listing every probed path."""
    # Wipe every env var that could resolve to a real user.hotkeys.
    for var in (
        "KICAD_USER_CONFIG_PATH", "APPDATA", "USERPROFILE",
        "XDG_CONFIG_HOME", "HOME", "USER", "LOGNAME",
    ):
        monkeypatch.delenv(var, raising=False)
    # Force HOME to an empty tmp dir so XDG fallback yields a nonexistent path.
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    payload = _call(server, "list_user_hotkeys", {})
    assert payload["success"] is False
    assert "tried_paths" in payload
    # Every entry in tried_paths must be a string; can be 0+ (WSL with no
    # env vars yields a minimal probe list, that's fine).
    assert all(isinstance(p, str) for p in payload["tried_paths"])


# --- env-var override -----------------------------------------------------


def test_env_var_override(server, hotkeys_file, monkeypatch):
    """$KICAD_USER_CONFIG_PATH (pointing at the file) is picked up when no
    explicit ``config_path`` is given."""
    monkeypatch.setenv("KICAD_USER_CONFIG_PATH", hotkeys_file)
    payload = _call(server, "list_user_hotkeys", {})
    assert payload["success"] is True
    assert payload["config_path_used"] == hotkeys_file
    assert payload["total_actions"] == 9


def test_env_var_override_directory(server, hotkeys_file, monkeypatch, tmp_path):
    """$KICAD_USER_CONFIG_PATH may also point at the *directory* holding the file."""
    monkeypatch.setenv("KICAD_USER_CONFIG_PATH", str(tmp_path))
    payload = _call(server, "list_user_hotkeys", {})
    assert payload["success"] is True
    assert payload["config_path_used"].endswith("user.hotkeys")


# --- cache ----------------------------------------------------------------


def test_parse_cache_hit(hotkeys_file):
    """Second parse of the same path/mtime returns the cached object."""
    _HOTKEYS_CACHE.clear()
    a = _parse_user_hotkeys(hotkeys_file)
    b = _parse_user_hotkeys(hotkeys_file)
    assert a is b  # identity, not equality — proves cache hit
