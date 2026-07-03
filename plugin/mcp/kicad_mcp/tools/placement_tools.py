# SPDX-License-Identifier: GPL-3.0-or-later
"""Placement tools — scoring a hypothetical layout for the "Entwirren" flow.

``evaluate_layout`` is the non-mutating notepad behind de-crossing: the agent
reads the board once, proposes footprint positions in its head, and scores each
candidate here WITHOUT touching KiCad. Pure data in, numbers out — the board is
only mutated once a final layout is chosen (a separate batch move).
"""

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text
from kicad_mcp.utils import placement_eval
from kicad_mcp.utils.path_env import to_local_path


def register_placement_tools(mcp: FastMCP) -> None:
    """Register placement-scoring tools with the MCP server."""

    @mcp.tool()
    def get_board_layout(pcb_path: str) -> dict[str, Any]:
        """Read a board into the ``evaluate_layout`` input shape — the read side of de-crossing.

        Use this ONCE at the start of an "Entwirren" pass: it returns every
        footprint's pose + pad-local offsets + courtyard size and the net→pad
        map. You then reason about better positions in your head, edit the
        ``x``/``y``/``rot`` of the returned footprints, and score each candidate
        with ``evaluate_layout`` — no board access in the loop. Apply the chosen
        layout once (a batch ``move_components``) at the end.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file (WSL or Windows path).

        Returns:
            ``{success, footprints, nets, footprint_count, current}`` where
            ``current`` is the ``evaluate_layout`` score of the board as-is (a
            baseline to beat). On error: ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            text = get_text(pcb_path)
            footprints, nets = placement_eval.board_to_layout(text)
            current = placement_eval.evaluate_layout(footprints, nets)
        except (OSError, ValueError, KeyError) as exc:
            return {"success": False, "error": f"could not read layout: {exc}"}
        return {
            "success": True,
            "footprints": footprints,
            "nets": nets,
            "footprint_count": len(footprints),
            "current": current,
        }

    @mcp.tool()
    def evaluate_layout(footprints: str, nets: str, power_nets: str = "") -> dict[str, Any]:
        """Score a HYPOTHETICAL footprint placement — the non-mutating notepad for de-crossing.

        Does NOT touch the board. Use this when planning an initial placement
        ("Entwirren"): read the board once, then call this repeatedly with
        candidate positions to compare them by signal-net ratsnest crossings,
        footprint overlaps and wirelength — reason to a good layout, then apply
        it once with a batch move. GND/VCC nets are auto-excluded (they become
        copper pours, not routed airwires) unless you pass ``power_nets``
        explicitly.

        Args:
            footprints: JSON list of ``{"ref", "x", "y", "rot"?, "flipped"?,
                "bbox": [w, h], "pads": [{"name", "lx", "ly"}]}``. Positions in
                mm; pad offsets are footprint-local (rotation is applied
                KiCad-CW / B.Cu-flip-aware).
            nets: JSON object ``{"NET_NAME": [["REF", "PAD"], …]}`` — pad
                membership per net.
            power_nets: optional JSON list of net names to exclude from scoring.
                Empty = auto-detect (name pattern + high fan-out).

        Returns:
            ``{success, signal_crossings, overlaps, wirelength_mm, airwires,
            signal_nets, excluded_power_nets}``. On bad input:
            ``{success: False, error}``.
        """
        try:
            fps = json.loads(footprints) if isinstance(footprints, str) else footprints
            net_map = json.loads(nets) if isinstance(nets, str) else nets
            power = json.loads(power_nets) if power_nets else None
        except (ValueError, TypeError) as exc:
            return {"success": False, "error": f"invalid JSON input: {exc}"}
        if not isinstance(fps, list) or not isinstance(net_map, dict):
            return {"success": False,
                    "error": "footprints must be a list and nets an object"}
        try:
            result = placement_eval.evaluate_layout(fps, net_map, power_nets=power)
        except (KeyError, TypeError, ValueError) as exc:
            return {"success": False, "error": f"layout scoring failed: {exc}"}
        return {"success": True, **result}
