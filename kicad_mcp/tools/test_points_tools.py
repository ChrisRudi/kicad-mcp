# SPDX-License-Identifier: GPL-3.0-or-later
"""Test-Punkt-Wächter tool — probe-access coverage of a board's critical nets.

KiCad has no notion of which nets *deserve* a test point. This ranks nets by
bring-up/production-test importance (power, reset, clock, bus) and reports which
critical nets have no probe access — a testability gap ERC/DRC can't see. The
ranking + coverage math live in ``utils/test_points`` on top of the shared
``design_rules.BoardContext``; this module is only the MCP entry point.
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text
from kicad_mcp.utils import design_rules, test_points
from kicad_mcp.utils.path_env import to_local_path


def register_test_points_tools(mcp: FastMCP) -> None:
    """Register the Test-Punkt-Wächter tool with the MCP server."""

    @mcp.tool()
    def audit_test_points(pcb_path: str, include_signals: bool = False,
                          refs: str = "") -> dict[str, Any]:
        """Score whether a board's critical nets can be probed — testability KiCad can't see.

        Ranks nets by bring-up / production-test importance (power rails, reset,
        clock, bus) and checks each for a probe-able access point (a ``TP*`` test
        point, a ``TestPoint`` footprint, or a connector). Reports coverage % and
        the *blind* critical nets — the ones you can't reach on a flying-probe /
        bed-of-nails or during bring-up. Use this before release to catch missing
        test access. Not a KiCad feature — net *importance* is external
        bring-up/manufacturing knowledge, not geometry.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file (WSL or Windows path).
            include_signals: also list ordinary signal nets (they never count
                toward the critical coverage %); default False.
            refs: Optional comma-separated reference list (``"U1,J2"``) to scope
                to the current selection — only nets touching those parts are
                audited; empty = whole board.

        Returns:
            ``{success, report: {coverage_pct, critical_total, critical_covered,
            critical_blind, blind_nets, nets}}``. On error:
            ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            text = get_text(pcb_path)
            ctx = design_rules.build_context(text)
        except (OSError, ValueError) as exc:
            return {"success": False, "error": f"could not read board: {exc}"}

        want = {r.strip() for r in refs.split(",") if r.strip()} or None
        nets = None
        if want is not None:
            nets = {net for net, pins in ctx.net_pins.items()
                    if any(ref in want for ref, _pad in pins)}

        report = test_points.evaluate_test_points(
            ctx, include_signals=include_signals, nets=nets)
        return {"success": True, "report": report}
