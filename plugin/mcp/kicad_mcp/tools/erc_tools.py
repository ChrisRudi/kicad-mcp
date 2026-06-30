# SPDX-License-Identifier: GPL-3.0-or-later
"""
Electrical Rules Check (ERC) tools for KiCad schematic files.

Uses kicad-cli for headless ERC execution with JSON output (KiCad 10).
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from fastmcp import Context, FastMCP

from kicad_mcp.utils.kicad_cli import KiCadCLIError, get_kicad_cli_path
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.wsl_path import to_windows_path


def _find_root_schematic(sch_path: Path) -> Path | None:
    """If sch_path is a sub-sheet, return the root schematic.

    KiCad convention: root schematic has the same stem as the .kicad_pro file.
    """
    pro_files = list(sch_path.parent.glob("*.kicad_pro"))
    if not pro_files:
        return None
    root_sch = pro_files[0].with_suffix(".kicad_sch")
    if root_sch.exists() and root_sch.resolve() != sch_path.resolve():
        return root_sch
    return None


def _run_erc_cli(sch_path: str, output_path: str) -> dict[str, Any]:
    """Run ERC via kicad-cli and return parsed JSON results.

    Args:
        sch_path: Path to schematic file
        output_path: Path for JSON output report

    Returns:
        Dict with 'success', 'violations', 'error' keys
    """
    try:
        cli_path = get_kicad_cli_path(required=True)
    except KiCadCLIError as e:
        return {"success": False, "violations": [], "error": str(e)}

    win_sch = to_windows_path(sch_path)
    win_out = to_windows_path(output_path)

    cmd = [cli_path, "sch", "erc", "--format", "json", "--output", win_out, win_sch]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False,
        )
        # kicad-cli returns 0 even with violations, writes JSON report
        if os.path.exists(output_path):
            with open(output_path, encoding="utf-8") as f:
                report = json.load(f)
            return {"success": True, "report": report}

        # Report file not created
        return {
            "success": False,
            "report": None,
            "error": f"ERC report not created. stderr: {result.stderr}",
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "report": None, "error": "ERC timed out after 60s"}
    except Exception as e:
        return {"success": False, "report": None, "error": str(e)}


def register_erc_tools(mcp: FastMCP) -> None:
    """Register ERC tools with the MCP server."""

    @mcp.tool()
    async def run_erc(schematic_path: str, ctx: Context | None = None) -> dict[str, Any]:
        """Headless ERC via ``kicad-cli`` — checks unconnected pins, power conflicts, output collisions.

        This is the **default ERC path** in 10.0.x. Use it after every
        schematic patch (``add_schematic_symbols`` / ``connect_pins``)
        before declaring a design complete. Don't shell out to
        ``kicad-cli sch erc`` yourself — this tool also (a) auto-detects
        sub-sheets and runs ERC on the root, (b) aggregates the
        ``sheets[N].violations`` JSON structure (KiCad-10 splits per
        sheet), and (c) normalises Windows / WSL paths.

        For filtered output (errors only / warnings only) call
        ``get_erc_violations`` instead — it propagates the same sub-sheet
        redirect.

        Args:
            schematic_path: Path to ``.kicad_sch`` file.
            ctx: MCP context for progress reporting.

        Returns:
            ``{success, schematic, total_violations, total_unconnected,
            errors, warnings, violations[:50], unconnected_items[:20],
            note?}``. ``note`` carries a sub-sheet→root redirect message
            when applicable.
        """
        schematic_path = to_local_path(schematic_path)
        sch_path = Path(schematic_path)
        if not sch_path.exists():
            return {"success": False, "error": f"Schematic not found: {schematic_path}"}

        # Detect sub-sheet and redirect to root
        subsheet_note = None
        root_sch = _find_root_schematic(sch_path)
        if root_sch:
            subsheet_note = (
                f"{sch_path.name} is a sub-sheet. "
                f"Using root schematic {root_sch.name} for complete ERC."
            )
            sch_path = root_sch

        if ctx:
            await ctx.report_progress(10, 100)
            ctx.info(f"Running ERC on {sch_path.name}")

        # Run ERC
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            output_path = tmp.name

        try:
            result = _run_erc_cli(str(sch_path), output_path)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

        if ctx:
            await ctx.report_progress(80, 100)

        if not result["success"]:
            return result

        report = result["report"]
        # KiCad-10 ERC JSON puts violations under sheets[N].violations (one per sheet),
        # NOT at top-level. Bug #1 fix 2026-04-27: aggregate across sheets, plus keep
        # backwards-compat with any future top-level field.
        violations = list(report.get("violations", []))
        unconnected = list(report.get("unconnected_items", []))
        for sheet in report.get("sheets", []):
            violations.extend(sheet.get("violations", []))
            unconnected.extend(sheet.get("unconnected_items", []))

        error_count = sum(1 for v in violations if v.get("severity") == "error")
        warning_count = sum(1 for v in violations if v.get("severity") == "warning")

        response = {
            "success": True,
            "schematic": str(sch_path),
            "total_violations": len(violations),
            "total_unconnected": len(unconnected),
            "errors": error_count,
            "warnings": warning_count,
            "violations": violations[:50],
            "unconnected_items": unconnected[:20],
        }

        if subsheet_note:
            response["note"] = subsheet_note

        if ctx:
            await ctx.report_progress(100, 100)
            if len(violations) == 0 and len(unconnected) == 0:
                ctx.info("ERC passed with no violations.")
            else:
                ctx.info(
                    f"ERC found {error_count} errors, {warning_count} warnings, "
                    f"{len(unconnected)} unconnected items."
                )

        return response

    @mcp.tool()
    async def get_erc_violations(
        schematic_path: str,
        severity_filter: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Run ERC and return only the violations matching ``severity_filter``.

        Use this when the user only cares about errors (or only warnings).
        Don't call ``run_erc`` and post-filter yourself — that's exactly
        what this tool does, and it propagates ``run_erc``'s sub-sheet
        redirection (root vs. sub) so you don't accidentally ERC a
        sub-sheet in isolation.

        Args:
            schematic_path: ``.kicad_sch`` file.
            severity_filter: ``"error"`` / ``"warning"`` / ``""`` (all).

        Returns:
            ``{success, schematic, filter, count, violations: [...]}``.
        """
        # Run ERC first
        result = await run_erc(schematic_path, ctx)

        if not result.get("success"):
            return result

        violations = result.get("violations", [])

        if severity_filter:
            violations = [v for v in violations if v.get("severity") == severity_filter]

        return {
            "success": True,
            "schematic": result["schematic"],
            "filter": severity_filter or "all",
            "count": len(violations),
            "violations": violations,
        }
