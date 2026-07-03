# SPDX-License-Identifier: GPL-3.0-or-later
"""Bus-Radar — group a board's nets into semantic buses (I²C, SPI, data bus …).

KiCad knows single nets, not *buses*. This exposes the pure inference in
``utils/bus_infer`` over a real board (nets + pins parsed via the shared
``utils/pcb_board_parse``), so the agent can answer "list all members of bus X"
and act on the whole group — the foundation for group placement / routing.
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text
from kicad_mcp.utils import bus_infer
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.pcb_board_parse import parse_pcb_footprints


def register_bus_tools(mcp: FastMCP) -> None:
    """Register the Bus-Radar tools with the MCP server."""

    @mcp.tool()
    def list_bus_members(pcb_path: str, bus: str = "") -> dict[str, Any]:
        """Identify buses on a board and list each bus's nets + pins — the semantic grouping KiCad lacks.

        Use this when the user talks about a *bus* rather than single nets
        ("place the I²C bus", "route the data bus", "what's on SPI1"). Buses are
        inferred from net names: protocol vocabularies (I²C = SDA+SCL, SPI =
        MOSI/MISO/SCK, UART, USB, CAN, SWD/JTAG), numbered buses (``D0..D7``) and
        differential pairs (``X_P``/``X_N``). Pins (``REF.PAD``) per bus come
        from the real board.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file (WSL or Windows path).
            bus: Optional filter — a bus label (e.g. ``"SPI1:SPI"``, ``"I2C"``)
                or any member net (e.g. ``"SDA"``). Empty = list every bus.

        Returns:
            Without ``bus``: ``{success, bus_count, buses: [{bus, kind, nets,
            pins, pin_count}, …]}``. With ``bus``: ``{success, bus, matches:
            [...]}``. On error: ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            text = get_text(pcb_path)
            parsed = parse_pcb_footprints(text)
        except (OSError, ValueError) as exc:
            return {"success": False, "error": f"could not read board: {exc}"}

        net_pins: dict[str, list] = {}
        for fp in parsed["footprints"]:
            for pad in fp["pads"]:
                if pad["net"]:
                    net_pins.setdefault(pad["net"], []).append(
                        f"{fp['ref']}.{pad['pad']}")

        buses = bus_infer.group_buses(sorted(net_pins))
        for b in buses:
            pins = sorted({p for n in b["nets"] for p in net_pins.get(n, [])})
            b["pins"] = pins
            b["pin_count"] = len(pins)

        if bus:
            key = bus.strip().upper()
            matches = [b for b in buses
                       if b["bus"].upper() == key
                       or key in {n.upper() for n in b["nets"]}]
            return {"success": True, "bus": bus, "matches": matches}
        return {"success": True, "bus_count": len(buses), "buses": buses}
