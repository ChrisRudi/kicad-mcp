# SPDX-License-Identifier: GPL-3.0-or-later
"""
Circuit pattern recognition tools for KiCad schematics.
"""
import os
from typing import Any

from fastmcp import Context, FastMCP

from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.netlist_parser import extract_netlist
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.pattern_recognition import (
    identify_amplifiers,
    identify_digital_interfaces,
    identify_filters,
    identify_microcontrollers,
    identify_oscillators,
    identify_power_supplies,
    identify_sensor_interfaces,
)


def register_pattern_tools(mcp: FastMCP) -> None:
    """Register circuit pattern recognition tools with the MCP server.
    
    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    async def identify_circuit_patterns(schematic_path: str, ctx: Context | None = None) -> dict[str, Any]:
        """Recognize circuit-block patterns in a schematic: PSUs, opamps, filters, MCUs, oscillators, sensor-bus topologies, …

        Use this when the user asks "what kind of circuit is this", "is
        there a buck-boost in here", "find all the filter blocks". Don't
        try to cluster components yourself — pattern recognition combines
        ref-prefix heuristics, value-regex matching, and net-topology
        analysis (e.g. "regulator + cap + cap on the same net" = decoupling).

        For project-wide (multi-sheet) detection use
        ``analyze_project_circuit_patterns``.

        Args:
            schematic_path: ``.kicad_sch`` file.

        Returns:
            ``{success, schematic, identified_patterns: {power_supply_circuits,
            amplifier_circuits, filter_circuits, digital_interfaces,
            microcontroller_circuits, oscillator_circuits, sensor_interfaces},
            summary, totals}``.
        """
        schematic_path = to_local_path(schematic_path)
        if not os.path.exists(schematic_path):
            if ctx:
                ctx.info(f"Schematic file not found: {schematic_path}")
            return {"success": False, "error": f"Schematic file not found: {schematic_path}"}

        # Report progress
        if ctx:
            await ctx.report_progress(10, 100)
            ctx.info(f"Loading schematic file: {os.path.basename(schematic_path)}")

        try:
            # Extract netlist information
            if ctx:
                await ctx.report_progress(20, 100)
                ctx.info("Parsing schematic structure...")

            netlist_data = extract_netlist(schematic_path)

            if "error" in netlist_data:
                if ctx:
                    ctx.info(f"Error extracting netlist: {netlist_data['error']}")
                return {"success": False, "error": netlist_data['error']}

            # Analyze components and nets
            if ctx:
                await ctx.report_progress(30, 100)
                ctx.info("Analyzing components and connections...")

            components = netlist_data.get("components", {})
            nets = netlist_data.get("nets", {})

            # Start pattern recognition
            if ctx:
                await ctx.report_progress(50, 100)
                ctx.info("Identifying circuit patterns...")

            identified_patterns = {
                "power_supply_circuits": [],
                "amplifier_circuits": [],
                "filter_circuits": [],
                "oscillator_circuits": [],
                "digital_interface_circuits": [],
                "microcontroller_circuits": [],
                "sensor_interface_circuits": [],
                "other_patterns": []
            }

            # Identify power supply circuits
            if ctx:
                await ctx.report_progress(60, 100)
            identified_patterns["power_supply_circuits"] = identify_power_supplies(components, nets)

            # Identify amplifier circuits
            if ctx:
                await ctx.report_progress(70, 100)
            identified_patterns["amplifier_circuits"] = identify_amplifiers(components, nets)

            # Identify filter circuits
            if ctx:
                await ctx.report_progress(75, 100)
            identified_patterns["filter_circuits"] = identify_filters(components, nets)

            # Identify oscillator circuits
            if ctx:
                await ctx.report_progress(80, 100)
            identified_patterns["oscillator_circuits"] = identify_oscillators(components, nets)

            # Identify digital interface circuits
            if ctx:
                await ctx.report_progress(85, 100)
            identified_patterns["digital_interface_circuits"] = identify_digital_interfaces(components, nets)

            # Identify microcontroller circuits
            if ctx:
                await ctx.report_progress(90, 100)
            identified_patterns["microcontroller_circuits"] = identify_microcontrollers(components)

            # Identify sensor interface circuits
            if ctx:
                await ctx.report_progress(95, 100)
            identified_patterns["sensor_interface_circuits"] = identify_sensor_interfaces(components, nets)

            # Build result
            result = {
                "success": True,
                "schematic_path": schematic_path,
                "component_count": netlist_data["component_count"],
                "identified_patterns": identified_patterns
            }

            # Count total patterns
            total_patterns = sum(len(patterns) for patterns in identified_patterns.values())
            result["total_patterns_found"] = total_patterns

            # Complete progress
            if ctx:
                await ctx.report_progress(100, 100)
                ctx.info(f"Pattern recognition complete. Found {total_patterns} circuit patterns.")

            return result

        except Exception as e:
            if ctx:
                ctx.info(f"Error identifying circuit patterns: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def analyze_project_circuit_patterns(project_path: str, ctx: Context | None = None) -> dict[str, Any]:
        """Run ``identify_circuit_patterns`` against a project's main schematic, resolved from a ``.kicad_pro``.

        Use this when the user gives you a project file (rather than a
        ``.kicad_sch`` directly) and wants the pattern analysis. This tool
        resolves the schematic via ``get_project_files`` and forwards.

        Args:
            project_path: Path to ``.kicad_pro``.

        Returns:
            Same shape as ``identify_circuit_patterns``, plus
            ``project_path``.
        """
        project_path = to_local_path(project_path)
        if not os.path.exists(project_path):
            if ctx:
                ctx.info(f"Project not found: {project_path}")
            return {"success": False, "error": f"Project not found: {project_path}"}

        # Report progress
        if ctx:
            await ctx.report_progress(10, 100)

        # Get the schematic file
        try:
            files = get_project_files(project_path)

            if "schematic" not in files:
                if ctx:
                    ctx.info("Schematic file not found in project")
                return {"success": False, "error": "Schematic file not found in project"}

            schematic_path = files["schematic"]
            if ctx:
                ctx.info(f"Found schematic file: {os.path.basename(schematic_path)}")

            # Identify patterns in the schematic
            result = await identify_circuit_patterns(schematic_path, ctx)

            # Add project path to result
            if "success" in result and result["success"]:
                result["project_path"] = project_path

            return result

        except Exception as e:
            ctx.info(f"Error analyzing project circuit patterns: {str(e)}")
            return {"success": False, "error": str(e)}
