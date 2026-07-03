# SPDX-License-Identifier: GPL-3.0-or-later
"""
Design Rule Check (DRC) implementation using KiCad command-line interface.
"""
import asyncio
import json
import logging
import os
import subprocess
import tempfile
from typing import Any

from fastmcp import Context

from kicad_mcp.config import drc_timeout_seconds
from kicad_mcp.utils.kicad_cli import find_kicad_cli

logger = logging.getLogger(__name__)

async def run_drc_via_cli(pcb_file: str, ctx: Context | None = None) -> dict[str, Any]:
    """Run DRC using KiCad command line tools.

    Args:
        pcb_file: Path to the PCB file (.kicad_pcb)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary with DRC results
    """
    results = {
        "success": False,
        "method": "cli",
        "pcb_file": pcb_file
    }

    try:
        # Create a temporary directory for the output
        with tempfile.TemporaryDirectory() as temp_dir:
            # Output file for DRC report
            output_file = os.path.join(temp_dir, "drc_report.json")

            # Find kicad-cli executable
            kicad_cli = find_kicad_cli()
            if not kicad_cli:
                logger.warning("kicad-cli not found in PATH or common installation locations")
                results["error"] = "kicad-cli not found. Please ensure KiCad 9.0+ is installed and kicad-cli is available."
                return results

            # Report progress
            if ctx:
                await ctx.report_progress(50, 100)
                ctx.info("Running DRC using KiCad CLI...")

            # Build the DRC command
            cmd = [
                kicad_cli,
                "pcb",
                "drc",
                "--format", "json",
                "--output", output_file,
                pcb_file
            ]

            # Size-adaptive, env-overridable budget: DRC on a large board can
            # legitimately run for minutes, so we must not kill it early — but we
            # still cap it to catch a true hang (locked/corrupt board). Run the
            # blocking subprocess off the event loop so a long DRC does not freeze
            # the whole MCP server for its entire duration.
            timeout_s = drc_timeout_seconds(pcb_file)
            logger.info(
                "Running command (timeout=%s s): %s",
                "none" if timeout_s is None else f"{timeout_s:.0f}",
                " ".join(cmd),
            )
            try:
                process = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired:
                logger.error("DRC timed out after %s s", timeout_s)
                results["error"] = (
                    f"DRC timed out after {timeout_s:.0f}s. The board may be very "
                    "large or the file locked; raise the budget via the "
                    "KICAD_MCP_DRC_TIMEOUT_S env var (seconds, or 'none' to "
                    "disable) and retry."
                )
                return results

            # Check if the command was successful
            if process.returncode != 0:
                logger.error(f"DRC command failed with code {process.returncode}")
                logger.error(f"Error output: {process.stderr}")
                results["error"] = f"DRC command failed: {process.stderr}"
                return results

            # Check if the output file was created
            if not os.path.exists(output_file):
                logger.warning("DRC report file not created")
                results["error"] = "DRC report file not created"
                return results

            # Read the DRC report
            with open(output_file, encoding="utf-8") as f:
                try:
                    drc_report = json.load(f)
                except json.JSONDecodeError:
                    logger.error("Failed to parse DRC report JSON")
                    results["error"] = "Failed to parse DRC report JSON"
                    return results

            # Process the DRC report
            violations = drc_report.get("violations", [])
            violation_count = len(violations)
            logger.info(f"DRC completed with {violation_count} violations")
            if ctx:
                await ctx.report_progress(70, 100)
                ctx.info(f"DRC completed with {violation_count} violations")

            # Categorize violations by type
            error_types = {}
            for violation in violations:
                error_type = violation.get("message", "Unknown")
                if error_type not in error_types:
                    error_types[error_type] = 0
                error_types[error_type] += 1

            # Create success response
            results = {
                "success": True,
                "method": "cli",
                "pcb_file": pcb_file,
                "total_violations": violation_count,
                "violation_categories": error_types,
                "violations": violations
            }

            if ctx:
                await ctx.report_progress(90, 100)
            return results

    except Exception as e:
        logger.error(f"Error in CLI DRC: {str(e)}", exc_info=True)
        results["error"] = f"Error in CLI DRC: {str(e)}"
        return results
