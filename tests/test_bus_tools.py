# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end test for the Bus-Radar tool: a real .kicad_pcb → buses + pins.
Headless (text parse + name inference), no KiCad runtime."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kicad_mcp.tools.bus_tools import register_bus_tools


_PCB = '''(kicad_pcb (version 20240108)
\t(footprint "U" (layer "F.Cu")
\t\t(uuid "u1")
\t\t(at 10 10 0)
\t\t(property "Reference" "U1" (at 0 0 0))
\t\t(property "Value" "MCU" (at 0 0 0))
\t\t(pad "1" smd rect (at 0 0) (layers "F.Cu") (net 1 "SDA"))
\t\t(pad "2" smd rect (at 1 0) (layers "F.Cu") (net 2 "SCL"))
\t\t(pad "3" smd rect (at 2 0) (layers "F.Cu") (net 3 "VCC"))
\t)
\t(footprint "U" (layer "F.Cu")
\t\t(uuid "u2")
\t\t(at 30 10 0)
\t\t(property "Reference" "U2" (at 0 0 0))
\t\t(property "Value" "SENSOR" (at 0 0 0))
\t\t(pad "1" smd rect (at 0 0) (layers "F.Cu") (net 1 "SDA"))
\t\t(pad "2" smd rect (at 1 0) (layers "F.Cu") (net 2 "SCL"))
\t)
)
'''


def _register(monkeypatch, pcb_text):
    captured = {}
    mcp = FastMCP("test")
    orig = mcp.tool

    def _capture(*a, **k):
        deco = orig(*a, **k)

        def wrap(fn):
            captured[fn.__name__] = fn
            return deco(fn)
        return wrap

    monkeypatch.setattr(mcp, "tool", _capture)
    register_bus_tools(mcp)
    # feed our board text through the file cache read the tool uses
    import kicad_mcp.tools.bus_tools as bt
    monkeypatch.setattr(bt, "get_text", lambda _p: pcb_text)
    monkeypatch.setattr(bt.os.path, "isfile", lambda _p: True)
    return captured["list_bus_members"]


def test_finds_i2c_bus_with_pins(monkeypatch, tmp_path):
    tool = _register(monkeypatch, _PCB)
    out = tool(str(tmp_path / "b.kicad_pcb"))
    assert out["success"]
    i2c = next(b for b in out["buses"] if b["bus"] == "I2C")
    assert set(i2c["nets"]) == {"SDA", "SCL"}
    # pins come from the real board (both ICs on both nets)
    assert set(i2c["pins"]) == {"U1.1", "U1.2", "U2.1", "U2.2"}
    assert i2c["pin_count"] == 4


def test_filter_by_member_net(monkeypatch, tmp_path):
    tool = _register(monkeypatch, _PCB)
    out = tool(str(tmp_path / "b.kicad_pcb"), bus="SDA")
    assert out["success"]
    assert len(out["matches"]) == 1 and out["matches"][0]["bus"] == "I2C"


def test_missing_file():
    mcp = FastMCP("t")
    captured = {}
    orig = mcp.tool

    def _cap(*a, **k):
        deco = orig(*a, **k)
        def wrap(fn):
            captured[fn.__name__] = fn
            return deco(fn)
        return wrap
    mcp.tool = _cap  # type: ignore
    register_bus_tools(mcp)
    out = captured["list_bus_members"]("/no/such/board.kicad_pcb")
    assert out["success"] is False and "not found" in out["error"].lower()
