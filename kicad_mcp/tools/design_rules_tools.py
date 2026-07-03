# SPDX-License-Identifier: GPL-3.0-or-later
"""Design-Wächter tool — runs the semantic rule registry over a board.

Per the project rule (CLAUDE.md): don't rebuild KiCad's ERC/DRC — go *beyond*
it. The rules themselves live in ``utils/design_rules`` (the persistent registry
+ shared board context); this module is only the MCP entry point.
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text
from kicad_mcp.utils import design_rules
from kicad_mcp.utils.path_env import to_local_path


def register_design_rules_tools(mcp: FastMCP) -> None:
    """Register the Design-Wächter tools with the MCP server."""

    @mcp.tool()
    def audit_design(pcb_path: str, rules: str = "") -> dict[str, Any]:
        """Run semantic design checks KiCad's ERC does NOT do — one call, all rules.

        The Design-Wächter: reasons about design *intent*, not net syntax. The
        board is parsed once and every registered rule runs against it. Current
        rules: I²C bus without pull-ups; crystal terminal without a load cap.
        Use this to catch silent bugs that pass ERC/DRC. New rules are added in
        ``utils/design_rules.RULES`` and appear here automatically.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file (WSL or Windows path).
            rules: Optional comma-separated rule keys to run a subset (see the
                ``available_rules`` in the result); empty = all.

        Returns:
            ``{success, issues: [{rule, severity, description, …}, …],
            available_rules, summary: {errors, warnings, infos, total}}``. On
            error: ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            text = get_text(pcb_path)
            ctx = design_rules.build_context(text)
        except (OSError, ValueError) as exc:
            return {"success": False, "error": f"could not read board: {exc}"}

        only = {r.strip() for r in rules.split(",") if r.strip()} or None
        issues = design_rules.run_rules(ctx, only=only)

        by_sev = {"error": 0, "warning": 0, "info": 0}
        for it in issues:
            by_sev[it.get("severity", "info")] = by_sev.get(
                it.get("severity", "info"), 0) + 1
        return {
            "success": True,
            "issues": issues,
            "available_rules": design_rules.rule_catalog(),
            "summary": {"errors": by_sev["error"], "warnings": by_sev["warning"],
                        "infos": by_sev["info"], "total": len(issues)},
        }
