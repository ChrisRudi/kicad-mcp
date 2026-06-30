# SPDX-License-Identifier: GPL-3.0-or-later
"""
Design Rule Check (DRC) tools for KiCad PCB files.
"""
import logging
import os
from typing import Any

from fastmcp import Context, FastMCP

# Import implementations
from kicad_mcp.tools.drc_impl.cli_drc import run_drc_via_cli
from kicad_mcp.utils.drc_history import compare_with_previous, get_drc_history, save_drc_result
from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.path_env import to_local_path

logger = logging.getLogger(__name__)

def register_drc_tools(mcp: FastMCP) -> None:
    """Register DRC tools with the MCP server.

    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    def get_drc_history_tool(project_path: str) -> dict[str, Any]:
        """Past DRC runs for a project, with trend (improving / degrading / stable).

        Use this when the user asks "did DRC get better since last week"
        or you need to know whether a recent edit introduced new
        violations. The history is auto-saved by ``run_drc_check`` to
        ``<project_dir>/.kicad-mcp/drc_history.json`` — no extra tracking
        needed. Don't try to find old DRC reports elsewhere; this is the
        canonical store.

        Args:
            project_path: Path to ``.kicad_pro``.

        Returns:
            ``{success, project_path, history_entries: [{timestamp,
            total_violations, errors, warnings}, …], entry_count, trend}``.
        """
        project_path = to_local_path(project_path)
        logger.info(f"Getting DRC history for project: {project_path}")

        if not os.path.exists(project_path):
            logger.warning(f"Project not found: {project_path}")
            return {"success": False, "error": f"Project not found: {project_path}"}

        # Get history entries
        history_entries = get_drc_history(project_path)

        # Calculate trend information
        trend = None
        if len(history_entries) >= 2:
            first = history_entries[-1]  # Oldest entry
            last = history_entries[0]    # Newest entry

            first_violations = first.get("total_violations", 0)
            last_violations = last.get("total_violations", 0)

            if first_violations > last_violations:
                trend = "improving"
            elif first_violations < last_violations:
                trend = "degrading"
            else:
                trend = "stable"

        return {
            "success": True,
            "project_path": project_path,
            "history_entries": history_entries,
            "entry_count": len(history_entries),
            "trend": trend
        }

    @mcp.tool()
    async def run_drc_check(project_path: str, ctx: Context | None = None) -> dict[str, Any]:
        """Run KiCad's Design Rule Check headless via ``kicad-cli`` and persist the result.

        Use this for any "is the PCB DRC-clean" question, CI gate, or
        before-after comparison. **Don't** call ``kicad-cli pcb drc``
        yourself: this tool resolves the right CLI path on Windows / WSL,
        handles output-file plumbing, parses the JSON report into a
        structured dict, *and* appends the run to ``drc_history.json``
        (which ``get_drc_history_tool`` reads).

        For DRC against the **live** in-editor PCB (without saving to disk
        first) use ``ipc_run_drc`` — that opens the DRC dialog inside the
        running KiCad GUI.

        Args:
            project_path: Path to ``.kicad_pro``. The matching ``.kicad_pcb``
                is resolved automatically via ``get_project_files``.

        Returns:
            ``{success, total_violations, violations: [...], comparison:
            {previous_violations, delta}, severity_counts}``.
        """
        project_path = to_local_path(project_path)
        logger.info(f"Running DRC check for project: {project_path}")

        if not os.path.exists(project_path):
            logger.warning(f"Project not found: {project_path}")
            return {"success": False, "error": f"Project not found: {project_path}"}

        # Get PCB file from project
        files = get_project_files(project_path)
        if "pcb" not in files:
            logger.warning("PCB file not found in project")
            return {"success": False, "error": "PCB file not found in project"}

        pcb_file = files["pcb"]
        logger.info(f"Found PCB file: {pcb_file}")

        # Report progress to user
        if ctx:
            await ctx.report_progress(10, 100)
            ctx.info(f"Starting DRC check on {os.path.basename(pcb_file)}")

        # Run DRC using the appropriate approach
        drc_results = None

        logger.info("Using kicad-cli for DRC")
        if ctx:
            ctx.info("Using KiCad CLI for DRC check...")
        drc_results = await run_drc_via_cli(pcb_file, ctx)

        # Process and save results if successful
        if drc_results and drc_results.get("success", False):
            # Save results to history
            save_drc_result(project_path, drc_results)

            # Add comparison with previous run
            comparison = compare_with_previous(project_path, drc_results)
            if comparison:
                drc_results["comparison"] = comparison

                if ctx:
                    if comparison["change"] < 0:
                        ctx.info(f"Great progress! You've fixed {abs(comparison['change'])} DRC violations since the last check.")
                    elif comparison["change"] > 0:
                        ctx.info(f"Found {comparison['change']} new DRC violations since the last check.")
                    else:
                        ctx.info("No change in the number of DRC violations since the last check.")

        # Complete progress
        if ctx:
            await ctx.report_progress(100, 100)

        return drc_results or {
            "success": False,
            "error": "DRC check failed with an unknown error"
        }
