# SPDX-License-Identifier: GPL-3.0-or-later
"""T10: generate_project's ERC gate must (a) actually run ERC and count real
violations (old argv used a non-existent ``sch drc`` + parsed top-level
``violations`` so it never fired) and (b) be NON-destructive — emit the files
and report ``erc_clean`` instead of deleting output.
"""
import asyncio
import json
import os

import pytest

from fastmcp import FastMCP
from kicad_mcp.tools.generation_tools import register_generation_tools
from kicad_mcp.utils.kicad_cli import get_kicad_cli_path

pytestmark = pytest.mark.skipif(
    not get_kicad_cli_path(), reason="kicad-cli not available for ERC")


def _call(server, name, **kw):
    r = asyncio.run(server.call_tool(name, kw))
    if isinstance(r, tuple) and len(r) > 1:
        r = r[1]
    sc = getattr(r, "structured_content", None)
    if isinstance(sc, dict):
        return sc
    data = getattr(r, "data", None)
    if isinstance(data, dict):
        return data
    content = getattr(r, "content", r)
    txt = "".join(getattr(c, "text", "") or "" for c in content)
    return json.loads(txt)


# A connector-fed +5V rail → ERC `power_pin_not_driven` (no PWR_FLAG): a real
# violation the gate must surface without nuking the generated files.
_PARTS = json.dumps([
    {"ref": "J1", "name": "Connector_Generic:Conn_01x02", "value": "+5V",
     "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
     "pins": [{"num": "1", "name": "+5V", "type": "passive"},
              {"num": "2", "name": "GND", "type": "passive"}]},
    {"ref": "R1", "name": "Device:R", "value": "330",
     "footprint": "Resistor_SMD:R_0603_1608Metric",
     "pins": [{"num": "1", "name": "~", "type": "passive"},
              {"num": "2", "name": "~", "type": "passive"}]},
])
_NETS = json.dumps([
    {"name": "+5V", "type": "power", "connections": ["J1:1", "R1:1"]},
    {"name": "GND", "type": "power", "connections": ["J1:2", "R1:2"]},
])


@pytest.fixture
def server():
    m = FastMCP("test-gen")
    register_generation_tools(m)
    return m


def test_gate_counts_erc_and_is_non_destructive(server, tmp_path):
    out = _call(server, "generate_project", output_dir=str(tmp_path),
                project_name="t", parts=_PARTS, nets=_NETS, board="{}")
    # Non-destructive: success + files still on disk.
    assert out["success"] is True
    assert os.path.isfile(str(tmp_path / "t.kicad_sch"))
    assert os.path.isfile(str(tmp_path / "t.kicad_pcb"))
    # ERC actually ran and counted the real violation(s).
    assert "drc" in out, "ERC report missing — gate did not run"
    assert out["drc"]["error_count"] >= 1
    assert out["erc_clean"] is False
