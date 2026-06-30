# SPDX-License-Identifier: GPL-3.0-or-later
"""
MCP tool: Convert LTspice .asc schematics to KiCad .kicad_sch format.

Geometry-faithful rebuilder with isotropic scaling, pin-driven
reconstruction, and guaranteed pin-wire connectivity.
"""
import os
from typing import Any

from fastmcp import Context, FastMCP

from kicad_mcp.generators.ltspice2kicad.main import convert_asc_to_kicad
from kicad_mcp.generators.ltspice2kicad.validator import format_report
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.wsl_path import to_windows_path


def register_ltspice_tools(mcp: FastMCP) -> None:
    """Register LTspice conversion tools with the MCP server."""

    @mcp.tool()
    async def convert_ltspice_to_kicad(
        input_path: str,
        output_dir: str,
        title: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Convert an LTspice .asc schematic to KiCad .kicad_sch format.

        Geometry-faithful rebuilder that preserves the original layout:
        - Isotropic scaling with auto-detected grid unit (LGU)
        - Pin-driven reconstruction with guaranteed connectivity
        - Full validation with error/warning report

        Supported components: R, C, L, Diode, MOSFET, BJT, V, I, GND, power.

        Args:
            input_path: Path to LTspice .asc file
            output_dir: Directory to write output files (.kicad_sch + .kicad_pro)
            title: Optional title for the schematic (default: filename)
            ctx: MCP context

        Returns:
            Conversion result with validation report and file paths
        """
        input_path = to_local_path(input_path)
        output_dir = to_local_path(output_dir)
        if not os.path.exists(input_path):
            return {"success": False, "error": f"Input file not found: {input_path}"}

        if not input_path.lower().endswith(".asc"):
            return {"success": False, "error": "Input must be an .asc file"}

        name = os.path.splitext(os.path.basename(input_path))[0]
        name = name.replace(" ", "_")
        output_path = os.path.join(output_dir, f"{name}.kicad_sch")

        if ctx:
            ctx.info(f"Converting {input_path} -> {output_path}")

        result = convert_asc_to_kicad(input_path, output_path, title or name)

        report = format_report(result.validation)
        if ctx:
            ctx.info(report)

        response: dict[str, Any] = {
            "success": result.success,
            "schematic_path": result.output_path,
            "scale_factor": result.scale_factor,
            "lgu": result.lgu,
            "component_count": result.component_count,
            "wire_count": result.wire_count,
            "net_count": result.net_count,
            "validation_report": report,
            "errors": result.errors,
            "warnings": result.warnings,
        }

        # Add Windows path for convenience
        if result.output_path:
            try:
                response["windows_path"] = to_windows_path(result.output_path)
            except Exception:
                pass

        return response
