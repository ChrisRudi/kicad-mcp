# SPDX-License-Identifier: GPL-3.0-or-later
"""Design-Wächter — semantic design checks that KiCad's ERC does NOT do.

Per the project rule (CLAUDE.md): don't rebuild KiCad's ERC/DRC — go *beyond*
it. These rules reason about design *intent*: a bus that is missing its pull-ups,
a rail without stützung, etc. Composes existing pieces (``bus_infer`` for the
bus grouping, ``pcb_board_parse`` for pads/nets, ``placement_eval.is_power_net``
for rail detection) rather than re-parsing anything.
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text
from kicad_mcp.utils import bus_infer
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.pcb_board_parse import parse_pcb_footprints
from kicad_mcp.utils.placement_eval import is_power_net


def _has_pullup_to_power(net: str, net_pins: dict, fp_by_ref: dict,
                         power: set) -> bool:
    """True if a resistor bridges ``net`` to a power net — the pull-up signature.
    Heuristic: a footprint whose ref starts with ``R`` has one pad on ``net`` and
    another pad on a power/rail net."""
    for ref, _pad in net_pins.get(net, []):
        if not ref.upper().startswith("R"):
            continue
        fp = fp_by_ref.get(ref)
        if not fp:
            continue
        other = {p["net"] for p in fp["pads"] if p["net"] and p["net"] != net}
        if other & power:
            return True
    return False


def register_design_rules_tools(mcp: FastMCP) -> None:
    """Register the Design-Wächter tools with the MCP server."""

    @mcp.tool()
    def audit_bus_rules(pcb_path: str) -> dict[str, Any]:
        """Semantic bus checks KiCad's ERC does NOT do — e.g. an I²C bus with no pull-ups.

        Use this to catch silent intent-level bugs that pass ERC/DRC: right now,
        every I²C bus (found via bus inference) is checked for pull-up resistors
        on SDA and SCL to a supply. Goes beyond KiCad's ERC (which only knows
        syntactic net rules); reuses the bus grouping + board parser rather than
        re-reading anything.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file (WSL or Windows path).

        Returns:
            ``{success, buses_checked, issues: [{severity, rule, bus, net,
            description}, …], summary: {warnings, total}}``. On error:
            ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            text = get_text(pcb_path)
            parsed = parse_pcb_footprints(text)
        except (OSError, ValueError) as exc:
            return {"success": False, "error": f"could not read board: {exc}"}

        fp_by_ref: dict = {}
        net_pins: dict = {}
        for fp in parsed["footprints"]:
            fp_by_ref[fp["ref"]] = fp
            for pad in fp["pads"]:
                if pad["net"]:
                    net_pins.setdefault(pad["net"], []).append(
                        (fp["ref"], pad["pad"]))

        fp_count = len(parsed["footprints"])
        power = {n for n in net_pins
                 if is_power_net(n, len(net_pins[n]), fp_count)}

        buses = bus_infer.group_buses(sorted(net_pins))
        i2c_buses = [b for b in buses if b["kind"] == "I2C"]
        issues = []
        for b in i2c_buses:
            for net in b["nets"]:
                if not _has_pullup_to_power(net, net_pins, fp_by_ref, power):
                    issues.append({
                        "severity": "warning",
                        "rule": "i2c_missing_pullup",
                        "bus": b["bus"],
                        "net": net,
                        "description": (
                            f"I²C net {net!r} (bus {b['bus']}) has no detectable "
                            "pull-up resistor to a supply — I²C is open-drain and "
                            "needs pull-ups on SDA and SCL."
                        ),
                    })

        return {
            "success": True,
            "buses_checked": [b["bus"] for b in i2c_buses],
            "issues": issues,
            "summary": {"warnings": len(issues), "total": len(issues)},
        }
