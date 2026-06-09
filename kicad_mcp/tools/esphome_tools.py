# SPDX-License-Identifier: GPL-3.0-or-later
"""
ESPHome YAML to KiCad conversion tools.

Converts ESPHome configurations into KiCad schematics and PCBs.
"""

import json
import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from kicad_mcp.generators.esphome_parser import COMPONENT_DB, esphome_to_parts_nets
from kicad_mcp.generators.pcb.builder import build_pcb
from kicad_mcp.generators.schematic.builder import build_schematic
from kicad_mcp.generators.validator import validate_all
from kicad_mcp.utils.path_env import to_local_path


def register_esphome_tools(mcp: FastMCP) -> None:
    """Register ESPHome tools with the MCP server."""

    @mcp.tool()
    async def esphome_to_kicad(
        yaml_content: str,
        output_dir: str = "",
        project_name: str = "",
        simulation: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Convert an ESPHome YAML configuration to a KiCad project.

        Parses ESPHome YAML and generates complete .kicad_sch + .kicad_pcb + .kicad_pro
        with all sensors, buses (I2C/SPI/UART), decoupling caps, and pull-up resistors.

        Args:
            yaml_content: ESPHome YAML configuration as string
            output_dir: Output directory for KiCad files (required to generate files)
            project_name: Project name (auto-detected from ESPHome 'name' field if empty)
            simulation: If True, add SPICE simulation properties
            ctx: MCP context

        Returns:
            Conversion result with parts, nets, and optionally generated file paths
        """
        if ctx:
            ctx.info("Parsing ESPHome YAML configuration")

        result = esphome_to_parts_nets(yaml_content)
        parts = result["parts"]
        nets = result["nets"]
        board = result["board"]
        warnings = result["warnings"]

        if not project_name:
            import yaml
            try:
                config = yaml.safe_load(yaml_content)
                project_name = config.get("esphome", {}).get("name", "esphome_project")
                project_name = project_name.replace("-", "_").replace(" ", "_")
            except Exception:
                project_name = "esphome_project"

        # Validate
        errors = validate_all(parts, nets, board)
        if errors:
            return {
                "success": False,
                "errors": errors,
                "warnings": warnings,
            }

        response = {
            "success": True,
            "chip": result["chip"],
            "project_name": project_name,
            "parts_count": len(parts),
            "nets_count": len(nets),
            "parts": parts,
            "nets": nets,
            "board": board,
            "warnings": warnings,
        }

        # Generate files if output_dir provided
        if output_dir:
            output_dir = to_local_path(output_dir)
            os.makedirs(output_dir, exist_ok=True)

            if ctx:
                ctx.info(f"Generating KiCad project '{project_name}' with {len(parts)} components")

            sch_content = build_schematic(parts, nets, project_name, simulation=simulation)
            pcb_content = build_pcb(parts, nets, board, project_name)

            sch_path = os.path.join(output_dir, f"{project_name}.kicad_sch")
            pcb_path = os.path.join(output_dir, f"{project_name}.kicad_pcb")
            pro_path = os.path.join(output_dir, f"{project_name}.kicad_pro")

            with open(sch_path, "w", encoding="utf-8") as f:
                f.write(sch_content)
            with open(pcb_path, "w", encoding="utf-8") as f:
                f.write(pcb_content)

            # Minimal .kicad_pro
            pro = {"meta": {"filename": f"{project_name}.kicad_pro", "version": 2}}
            with open(pro_path, "w", encoding="utf-8") as f:
                json.dump(pro, f, indent=2)

            response["files"] = {
                "schematic": sch_path,
                "pcb": pcb_path,
                "project": pro_path,
            }

            if ctx:
                ctx.info(f"KiCad project generated at {output_dir}")

        return response

    @mcp.tool()
    async def list_esphome_components(
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List every ESPHome platform/component the converter supports, with their KiCad symbol + footprint mapping.

        Use this **before** calling ``esphome_to_kicad`` if the user mentions
        an unfamiliar sensor — it's the canonical "what can it do" query.
        Don't guess from ESPHome docs: this list is what's actually in the
        ``COMPONENT_DB`` lookup-table, including I²C addresses + interface
        type. Anything not listed will show up in the converter output as a
        warning.

        Returns:
            ``{success, count, components: {platform: {kicad_name, footprint,
            interface, i2c_address?}, …}}``.
        """
        components = {}
        for platform, comp in COMPONENT_DB.items():
            components[platform] = {
                "kicad_name": comp["name"],
                "footprint": comp["footprint"],
                "interface": comp.get("interface", "unknown"),
                "pin_count": len(comp["pins"]),
            }

        return {
            "success": True,
            "supported_count": len(components),
            "components": components,
        }
