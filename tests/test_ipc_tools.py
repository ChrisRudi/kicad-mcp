# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for kicad_mcp.tools.ipc_tools.

Real IPC traffic against a running KiCad GUI is not feasible in CI, so the
tests focus on the *contract* of the tools:

  * ``ipc_check_status`` correctly reports missing kipy / unreachable KiCad /
    no board open without ever actually trying to talk to a real socket.
  * ``ipc_install_kipy`` invokes pip with the right arguments.
  * ``ipc_get_pad_world_pos`` / ``ipc_route_pin_to_pin`` / etc. surface a
    clear error when prerequisites are missing — the happy paths require a
    real KiCad and are exercised manually.

We monkeypatch ``ipc_tools._kipy_available`` and ``_connect_kicad`` to drive
the branches deterministically.
"""

from __future__ import annotations

import pytest

from kicad_mcp.tools import ipc_tools


# ---------------------------------------------------------------------------
# Helpers to invoke MCP-decorated tools without spinning up a real server.
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_with_ipc_tools():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    mcp = fastmcp.FastMCP("test")
    ipc_tools.register_ipc_tools(mcp)
    return mcp


def _call_tool(mcp, name, **kwargs):
    import asyncio

    async def _do():
        result = await mcp.call_tool(name, kwargs)
        if isinstance(result, tuple):
            return result[1] if len(result) > 1 else result[0]
        return result

    return asyncio.run(_do())


# ---------------------------------------------------------------------------
# ipc_check_status
# ---------------------------------------------------------------------------


class TestCheckStatus:
    def test_kipy_missing(self, mcp_with_ipc_tools, monkeypatch):
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: False)
        out = _call_tool(mcp_with_ipc_tools, "ipc_check_status")
        assert out["kipy_installed"] is False
        assert out["ready"] is False
        assert "ipc_install_kipy" in out["hint"]

    def test_kicad_unreachable(self, mcp_with_ipc_tools, monkeypatch):
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)

        def _fail(*_a, **_kw):
            raise RuntimeError("Cannot reach KiCad IPC server: socket")

        monkeypatch.setattr(ipc_tools, "_connect_kicad", _fail)
        out = _call_tool(mcp_with_ipc_tools, "ipc_check_status")
        assert out["kipy_installed"] is True
        assert out["kicad_reachable"] is False
        assert out["ready"] is False
        assert "Plugins" in out["hint"]

    def test_no_board_open(self, mcp_with_ipc_tools, monkeypatch):
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)

        def _fail(*_a, **_kw):
            raise RuntimeError("No board accessible via IPC: not open")

        monkeypatch.setattr(ipc_tools, "_connect_kicad", _fail)
        out = _call_tool(mcp_with_ipc_tools, "ipc_check_status")
        assert out["kicad_reachable"] is True
        assert out["board_open"] is False
        assert "Open a" in out["hint"]

    def test_ready(self, mcp_with_ipc_tools, monkeypatch):
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)
        monkeypatch.setattr(
            ipc_tools, "_connect_kicad",
            lambda: (object(), object()),
        )
        monkeypatch.setattr(
            ipc_tools, "_kicad_version_string", lambda _client: "10.0.1",
        )
        out = _call_tool(mcp_with_ipc_tools, "ipc_check_status")
        assert out["ready"] is True
        assert out["kicad_version"] == "10.0.1"


# ---------------------------------------------------------------------------
# ipc_install_kipy
# ---------------------------------------------------------------------------


class TestInstallKipy:
    def test_invokes_pip(self, mcp_with_ipc_tools, monkeypatch):
        captured = {}

        def fake(target_python=None):
            captured["target"] = target_python
            return True, "Successfully installed kicad-python"

        monkeypatch.setattr(ipc_tools, "_pip_install_kipy", fake)
        out = _call_tool(mcp_with_ipc_tools, "ipc_install_kipy")
        assert out["success"] is True
        assert "kicad-python" in out["output"]
        assert "Plugins" in out["next_step"]
        assert captured["target"] is None

    def test_uses_explicit_python(self, mcp_with_ipc_tools, monkeypatch):
        captured = {}

        def fake(target_python=None):
            captured["target"] = target_python
            return True, "ok"

        monkeypatch.setattr(ipc_tools, "_pip_install_kipy", fake)
        out = _call_tool(
            mcp_with_ipc_tools, "ipc_install_kipy",
            python_executable="/opt/special/python3",
        )
        assert captured["target"] == "/opt/special/python3"
        assert out["success"] is True

    def test_failure_returned_verbatim(self, mcp_with_ipc_tools, monkeypatch):
        monkeypatch.setattr(
            ipc_tools, "_pip_install_kipy",
            lambda target_python=None: (False, "ERROR: package not found"),
        )
        out = _call_tool(mcp_with_ipc_tools, "ipc_install_kipy")
        assert out["success"] is False
        assert "ERROR" in out["output"]


# ---------------------------------------------------------------------------
# Pad lookup helpers (independent of MCP)
# ---------------------------------------------------------------------------


class _FakePad:
    def __init__(self, name, x_mm, y_mm, layer="F.Cu"):
        self.name = name
        self._pos = type("P", (), {"x": x_mm, "y": y_mm})()
        self.layer = layer

    @property
    def position(self):
        return self._pos


class _FakeFootprint:
    def __init__(self, ref, pads):
        self.reference = ref
        self._pads = pads

    def get_pads(self):
        return list(self._pads)


class _FakeBoard:
    def __init__(self, footprints):
        self._fps = footprints
        self.added = []
        self.committed = False

    def get_footprints(self):
        return list(self._fps)

    def add_item(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True


class TestPadLookup:
    def test_find_footprint_and_pad(self):
        board = _FakeBoard(
            [
                _FakeFootprint("U1", [_FakePad("1", 1.0, 2.0)]),
                _FakeFootprint("U2", [_FakePad("3", 4.0, 5.0)]),
            ]
        )
        fp = ipc_tools._find_footprint_by_ref(board, "U2")
        assert fp is not None
        pad = ipc_tools._find_pad(board, fp, "3")
        assert pad is not None
        assert ipc_tools._pad_world_xy_mm(pad) == (4.0, 5.0)

    def test_unknown_ref_returns_none(self):
        board = _FakeBoard(
            [_FakeFootprint("U1", [_FakePad("1", 0, 0)])],
        )
        assert ipc_tools._find_footprint_by_ref(board, "NEVER") is None

    def test_pad_position_in_nm_is_normalised(self):
        # Position with very large magnitude => nm => convert to mm
        big = _FakePad("1", 12_000_000, -7_500_000)
        x, y = ipc_tools._pad_world_xy_mm(big)
        assert pytest.approx(x) == 12.0
        assert pytest.approx(y) == -7.5


# ---------------------------------------------------------------------------
# ipc_route_pin_to_pin / ipc_route_power_ring error paths
# ---------------------------------------------------------------------------


class TestRoutingErrorPaths:
    def test_route_when_kipy_missing(self, mcp_with_ipc_tools, monkeypatch):
        def _fail():
            raise RuntimeError("kicad-python (kipy) is not installed.")

        monkeypatch.setattr(ipc_tools, "_connect_kicad", _fail)
        out = _call_tool(
            mcp_with_ipc_tools, "ipc_route_pin_to_pin",
            ref1="U1", pin1="1", ref2="U2", pin2="1",
        )
        assert out["success"] is False
        # The Phase-7 auto-open hook (_require_editor) probes the bus
        # before reaching _connect_kicad, so the surfaced error depends on
        # the environment: bus down -> "IPC bus is not reachable: …";
        # bus up but no project/PCB editor open -> "no kicad project
        # active …"; bus up with a project -> the monkeypatched
        # "kicad-python …". All three are legit failure modes for this tool.
        err = out["error"].lower()
        assert (
            "kicad-python" in err
            or "ipc bus is not reachable" in err
            or "no kicad project active" in err
        )

    def test_power_ring_too_few_nodes(self, mcp_with_ipc_tools):
        out = _call_tool(
            mcp_with_ipc_tools, "ipc_route_power_ring",
            net_name="VBUS", nodes=[["U1", "1"]],
        )
        assert out["success"] is False
        assert "at least 2" in out["error"]

    def test_zone_pour_too_few_vertices(self, mcp_with_ipc_tools):
        out = _call_tool(
            mcp_with_ipc_tools, "ipc_add_zone_pour",
            net_name="GND", layer="B.Cu",
            polygon_xy_mm=[[0, 0], [1, 0]],
        )
        assert out["success"] is False
        assert "at least 3" in out["error"]
