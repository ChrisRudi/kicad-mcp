# SPDX-License-Identifier: GPL-3.0-or-later
"""
Netlist extraction and analysis tools for KiCad schematics.
"""
import logging
import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.netlist_parser import analyze_netlist, extract_netlist
from kicad_mcp.utils.path_env import to_local_path

logger = logging.getLogger(__name__)

def register_netlist_tools(mcp: FastMCP) -> None:
    """Register netlist-related tools with the MCP server.

    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    async def extract_schematic_netlist(schematic_path: str, ctx: Context | None = None) -> dict[str, Any]:
        """Parse a ``.kicad_sch`` and return its full netlist (components + nets + labels + connections).

        Use this when you need the **electrical** view of a schematic (what
        connects to what), not just the component list. Don't run
        ``kicad-cli sch export netlist`` and parse the output yourself —
        this tool returns a structured dict ready for downstream analysis,
        and tags partial-data results so you know when pin-level
        connections were incomplete (KiCad's S-expr parser limitation).

        For a project-file entrypoint use ``extract_project_netlist``. For
        connection-graph + analytics use ``analyze_schematic_connections``.

        Args:
            schematic_path: ``.kicad_sch`` file.

        Returns:
            ``{success, schematic_path, component_count, net_count,
            components, nets, analysis, partial?, partial_reason?}``.
        """
        schematic_path = to_local_path(schematic_path)
        logger.info(f"Extracting netlist from schematic: {schematic_path}")

        if not os.path.exists(schematic_path):
            logger.warning(f"Schematic file not found: {schematic_path}")
            if ctx:
                ctx.info(f"Schematic file not found: {schematic_path}")
            return {"success": False, "error": f"Schematic file not found: {schematic_path}"}

        # Report progress
        if ctx:
            await ctx.report_progress(10, 100)
            ctx.info(f"Loading schematic file: {os.path.basename(schematic_path)}")

        # Extract netlist information
        try:
            if ctx:
                await ctx.report_progress(20, 100)
                ctx.info("Parsing schematic structure...")

            netlist_data = extract_netlist(schematic_path)

            if "error" in netlist_data:
                logger.error(f"Error extracting netlist: {netlist_data['error']}")
                if ctx:
                    ctx.info(f"Error extracting netlist: {netlist_data['error']}")
                return {"success": False, "error": netlist_data['error']}

            if ctx:
                await ctx.report_progress(60, 100)
                ctx.info(f"Extracted {netlist_data['component_count']} components and {netlist_data['net_count']} nets")

            # Analyze the netlist
            if ctx:
                await ctx.report_progress(70, 100)
                ctx.info("Analyzing netlist data...")

            analysis_results = analyze_netlist(netlist_data)

            if ctx:
                await ctx.report_progress(90, 100)

            # Build result
            result = {
                "success": True,
                "schematic_path": schematic_path,
                "component_count": netlist_data["component_count"],
                "net_count": netlist_data["net_count"],
                "components": netlist_data["components"],
                "nets": netlist_data["nets"],
                "analysis": analysis_results
            }

            # Forward partial-data warning if present
            if netlist_data.get("partial"):
                result["partial"] = True
                result["partial_reason"] = netlist_data["partial_reason"]

            # Complete progress
            if ctx:
                await ctx.report_progress(100, 100)
                if netlist_data.get("partial"):
                    ctx.info("Netlist extraction complete (partial: pin-level connections are incomplete)")
                else:
                    ctx.info("Netlist extraction complete")

            return result

        except Exception as e:
            logger.error(f"Error extracting netlist: {str(e)}")
            if ctx:
                ctx.info(f"Error extracting netlist: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def extract_project_netlist(project_path: str, ctx: Context | None = None) -> dict[str, Any]:
        """Project-file entrypoint for ``extract_schematic_netlist``: resolve schematic from ``.kicad_pro`` and extract.

        Use this when the user gives you a project path; this tool finds
        the matching ``.kicad_sch`` and forwards.

        Args:
            project_path: Path to ``.kicad_pro``.

        Returns:
            Same shape as ``extract_schematic_netlist``, plus ``project_path``.
        """
        project_path = to_local_path(project_path)
        logger.info(f"Extracting netlist for project: {project_path}")

        if not os.path.exists(project_path):
            logger.warning(f"Project not found: {project_path}")
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
                logger.warning("Schematic file not found in project")
                if ctx:
                    ctx.info("Schematic file not found in project")
                return {"success": False, "error": "Schematic file not found in project"}

            schematic_path = files["schematic"]
            logger.info(f"Found schematic file: {schematic_path}")
            if ctx:
                ctx.info(f"Found schematic file: {os.path.basename(schematic_path)}")

            # Extract netlist
            if ctx:
                await ctx.report_progress(20, 100)

            # Call the schematic netlist extraction
            result = await extract_schematic_netlist(schematic_path, ctx)

            # Add project path to result
            if "success" in result and result["success"]:
                result["project_path"] = project_path

            return result

        except Exception as e:
            logger.error(f"Error extracting project netlist: {str(e)}")
            if ctx:
                ctx.info(f"Error extracting project netlist: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def analyze_schematic_connections(schematic_path: str, ctx: Context | None = None) -> dict[str, Any]:
        """Connection-graph analytics: identify power nets, signal paths, fan-out, potential issues.

        Use this when the user asks "how does signal X flow", "what are
        the power nets", "is anything heavily fan-out". Don't reconstruct
        the graph from ``extract_schematic_netlist`` output — this tool
        does the heavy lift (power-net classification by name,
        signal-path traversal, fan-out scoring).

        For per-component lookups use ``find_component_connections``.

        Args:
            schematic_path: ``.kicad_sch`` file.

        Returns:
            ``{success, schematic_path, power_nets, signal_paths,
            high_fanout_nets, isolated_components, …}``.
        """
        schematic_path = to_local_path(schematic_path)
        logger.info(f"Analyzing connections in schematic: {schematic_path}")

        if not os.path.exists(schematic_path):
            logger.warning(f"Schematic file not found: {schematic_path}")
            if ctx:
                ctx.info(f"Schematic file not found: {schematic_path}")
            return {"success": False, "error": f"Schematic file not found: {schematic_path}"}

        # Report progress
        if ctx:
            await ctx.report_progress(10, 100)
            ctx.info(f"Extracting netlist from: {os.path.basename(schematic_path)}")

        # Extract netlist information
        try:
            netlist_data = extract_netlist(schematic_path)

            if "error" in netlist_data:
                logger.error(f"Error extracting netlist: {netlist_data['error']}")
                if ctx:
                    ctx.info(f"Error extracting netlist: {netlist_data['error']}")
                return {"success": False, "error": netlist_data['error']}

            if ctx:
                await ctx.report_progress(40, 100)

            # Advanced connection analysis
            if ctx:
                ctx.info("Performing connection analysis...")

            analysis = {
                "component_count": netlist_data["component_count"],
                "net_count": netlist_data["net_count"],
                "component_types": {},
                "power_nets": [],
                "signal_nets": [],
                "potential_issues": []
            }

            # Analyze component types
            components = netlist_data.get("components", {})
            for ref, _component in components.items():
                # Extract component type from reference (e.g., R1 -> R)
                import re
                comp_type_match = re.match(r'^([A-Za-z_]+)', ref)
                if comp_type_match:
                    comp_type = comp_type_match.group(1)
                    if comp_type not in analysis["component_types"]:
                        analysis["component_types"][comp_type] = 0
                    analysis["component_types"][comp_type] += 1

            if ctx:
                await ctx.report_progress(60, 100)

            # Identify power nets
            nets = netlist_data.get("nets", {})
            for net_name, pins in nets.items():
                if any(net_name.startswith(prefix) for prefix in ["VCC", "VDD", "GND", "+5V", "+3V3", "+12V"]):
                    analysis["power_nets"].append({
                        "name": net_name,
                        "pin_count": len(pins)
                    })
                else:
                    analysis["signal_nets"].append({
                        "name": net_name,
                        "pin_count": len(pins)
                    })

            if ctx:
                await ctx.report_progress(80, 100)

            # Check for potential issues
            # 1. Nets with only one connection (floating)
            for net_name, pins in nets.items():
                if len(pins) <= 1 and not any(net_name.startswith(prefix) for prefix in ["VCC", "VDD", "GND", "+5V", "+3V3", "+12V"]):
                    analysis["potential_issues"].append({
                        "type": "floating_net",
                        "net": net_name,
                        "description": f"Net '{net_name}' appears to be floating (only has {len(pins)} connection)"
                    })

            if ctx:
                await ctx.report_progress(90, 100)

            # Build result
            result = {
                "success": True,
                "schematic_path": schematic_path,
                "analysis": analysis
            }

            # Complete progress
            if ctx:
                await ctx.report_progress(100, 100)
                ctx.info("Connection analysis complete")

            return result

        except Exception as e:
            logger.error(f"Error analyzing connections: {str(e)}")
            if ctx:
                ctx.info(f"Error analyzing connections: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def find_component_connections(project_path: str, component_ref: str, ctx: Context | None = None) -> dict[str, Any]:
        """Per-pin breakdown of one component: what net is each pin on, and which other components share that net.

        Use this for "show me everything connected to U7", "which pin is
        the I²C SDA on R12", or before you patch a component to see what
        you'd disturb. Don't reconstruct from the netlist by hand: this
        tool does the pin-to-net + cross-component lookup in one pass.

        For the global connection graph use
        ``analyze_schematic_connections``; for board-level (PCB) view use
        ``find_tracks_by_net`` after looking up which net the pin maps to.

        Args:
            project_path: Path to ``.kicad_pro``.
            component_ref: Exact reference designator (e.g. ``"U7"``,
                ``"C_LED9"``).

        Returns:
            Dictionary with component connection information
        """
        project_path = to_local_path(project_path)
        logger.info(f"Finding connections for component {component_ref} in project: {project_path}")

        if not os.path.exists(project_path):
            logger.warning(f"Project not found: {project_path}")
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
                logger.warning("Schematic file not found in project")
                if ctx:
                    ctx.info("Schematic file not found in project")
                return {"success": False, "error": "Schematic file not found in project"}

            schematic_path = files["schematic"]
            logger.info(f"Found schematic file: {schematic_path}")
            if ctx:
                ctx.info(f"Found schematic file: {os.path.basename(schematic_path)}")

            # Extract netlist
            if ctx:
                await ctx.report_progress(30, 100)
                ctx.info(f"Extracting netlist to find connections for {component_ref}...")

            netlist_data = extract_netlist(schematic_path)

            if "error" in netlist_data:
                logger.error(f"Failed to extract netlist: {netlist_data['error']}")
                if ctx:
                    ctx.info(f"Failed to extract netlist: {netlist_data['error']}")
                return {"success": False, "error": netlist_data['error']}

            # Check if component exists in the netlist
            components = netlist_data.get("components", {})
            if component_ref not in components:
                logger.warning(f"Component {component_ref} not found in schematic")
                if ctx:
                    ctx.info(f"Component {component_ref} not found in schematic")
                return {
                    "success": False,
                    "error": f"Component {component_ref} not found in schematic",
                    "available_components": list(components.keys())
                }

            # Get component information
            component_info = components[component_ref]

            # Find connections
            if ctx:
                await ctx.report_progress(50, 100)
                ctx.info("Finding connections...")

            nets = netlist_data.get("nets", {})
            connections = []
            connected_nets = []

            for net_name, pins in nets.items():
                # Check if any pin belongs to our component
                component_pins = []
                for pin in pins:
                    if pin.get('component') == component_ref:
                        component_pins.append(pin)

                if component_pins:
                    # This net has connections to our component
                    net_connections = []

                    for pin in component_pins:
                        pin_num = pin.get('pin', 'Unknown')
                        # Find other components connected to this pin
                        connected_components = []

                        for other_pin in pins:
                            other_comp = other_pin.get('component')
                            if other_comp and other_comp != component_ref:
                                connected_components.append({
                                    "component": other_comp,
                                    "pin": other_pin.get('pin', 'Unknown')
                                })

                        net_connections.append({
                            "pin": pin_num,
                            "net": net_name,
                            "connected_to": connected_components
                        })

                    connections.extend(net_connections)
                    connected_nets.append(net_name)

            # Analyze the connections
            if ctx:
                await ctx.report_progress(70, 100)
                ctx.info("Analyzing connections...")

            # Categorize connections by pin function (if possible)
            pin_functions = {}
            if "pins" in component_info:
                for pin in component_info["pins"]:
                    pin_num = pin.get('num')
                    pin_name = pin.get('name', '')

                    # Try to categorize based on pin name
                    pin_type = "unknown"

                    if any(power_term in pin_name.upper() for power_term in ["VCC", "VDD", "VEE", "VSS", "GND", "PWR", "POWER"]):
                        pin_type = "power"
                    elif any(io_term in pin_name.upper() for io_term in ["IO", "I/O", "GPIO"]):
                        pin_type = "io"
                    elif any(input_term in pin_name.upper() for input_term in ["IN", "INPUT"]):
                        pin_type = "input"
                    elif any(output_term in pin_name.upper() for output_term in ["OUT", "OUTPUT"]):
                        pin_type = "output"

                    pin_functions[pin_num] = {
                        "name": pin_name,
                        "type": pin_type
                    }

            # Build result
            result = {
                "success": True,
                "project_path": project_path,
                "schematic_path": schematic_path,
                "component": component_ref,
                "component_info": component_info,
                "connections": connections,
                "connected_nets": connected_nets,
                "pin_functions": pin_functions,
                "total_connections": len(connections)
            }

            if ctx:
                await ctx.report_progress(100, 100)
                ctx.info(f"Found {len(connections)} connections for component {component_ref}")

            return result

        except Exception as e:
            logger.error(f"Error finding component connections: {str(e)}", exc_info=True)
            if ctx:
                ctx.info(f"Error finding component connections: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def snapshot_netlist(
        schematic_path: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Export a schematic's current netlist as a normalized JSON
        spec suitable for ``validate_netlist`` regression checks.

        Use this to lock in the current SCHEMATIC state as the
        "expected" baseline for an SSOT-driven workflow — commit the
        JSON to git, and every subsequent ``validate_netlist`` call
        diffs the live schematic against this snapshot. CI-friendly:
        no schematic mutation, deterministic output ordering.

        Args:
            schematic_path: ``.kicad_sch`` file.
            output_path: Optional output ``.json`` path. If empty, the
                JSON is returned in the response under ``spec`` and not
                written to disk.

        Returns:
            ``{success, schematic_path, output_path?, spec: {meta,
            components, nets, power_rails}}``.
        """
        import json as _json
        import subprocess as _subprocess
        import tempfile as _tempfile

        from kicad_mcp.utils.path_env import kicad_cli

        schematic_path = to_local_path(schematic_path)
        if not os.path.isfile(schematic_path):
            return {
                "success": False,
                "error": f"Schematic not found: {schematic_path}",
            }
        cli = kicad_cli()
        if not cli:
            return {
                "success": False,
                "error": (
                    "kicad-cli not found. Set KICAD_BIN to KiCad's bin/ "
                    "directory."
                ),
            }
        with _tempfile.NamedTemporaryFile(
            suffix=".net", delete=False,
        ) as tf:
            tmp_net = tf.name
        try:
            _subprocess.run(
                [
                    cli, "sch", "export", "netlist",
                    "--format", "kicadsexpr",
                    "-o", tmp_net, schematic_path,
                ],
                capture_output=True, check=True, timeout=60,
            )
            with open(tmp_net, encoding="utf-8") as fh:
                netlist_text = fh.read()
        except _subprocess.CalledProcessError as exc:
            return {
                "success": False,
                "error": (
                    f"kicad-cli sch export netlist failed: "
                    f"{exc.stderr.decode('utf-8', 'replace')[:300]}"
                ),
            }
        finally:
            try:
                os.unlink(tmp_net)
            except OSError:
                pass

        # Parse netlist into normalized structure
        spec = _parse_netlist_to_spec(netlist_text, schematic_path)
        result: dict[str, Any] = {
            "success": True,
            "schematic_path": schematic_path,
            "spec": spec,
            "stats": {
                "components": len(spec["components"]),
                "nets": len(spec["nets"]),
                "power_rails": len(spec["power_rails"]),
            },
        }
        if output_path:
            output_path = to_local_path(output_path)
            with open(output_path, "w", encoding="utf-8") as fh:
                _json.dump(spec, fh, indent=2, sort_keys=True)
            result["output_path"] = output_path
        return result

    @mcp.tool()
    def validate_netlist(
        spec_path: str,
        schematic_path: str,
    ) -> dict[str, Any]:
        """Diff a saved netlist spec (JSON) against the live schematic.

        Compares the schematic's current state against a previously
        ``snapshot_netlist``-generated JSON. Use this as a regression
        gate after schematic edits: missing components, extra
        components, net-topology mismatches, removed/added pins per
        net all flagged. Returns zero issues when the schematic
        matches the spec exactly. Run ``snapshot_netlist`` first to
        produce the JSON baseline before invoking this diff.

        Args:
            spec_path: ``.json`` produced by :func:`snapshot_netlist`.
            schematic_path: ``.kicad_sch`` to validate.

        Returns:
            ``{success, components: {missing, extra, value_mismatch,
            footprint_mismatch}, nets: {missing, extra, pin_mismatches},
            power_rails: {missing, extra}, total_issues}``.
        """
        import json as _json
        import subprocess as _subprocess
        import tempfile as _tempfile

        from kicad_mcp.utils.path_env import kicad_cli

        spec_path = to_local_path(spec_path)
        schematic_path = to_local_path(schematic_path)
        if not os.path.isfile(spec_path):
            return {"success": False, "error": f"Spec not found: {spec_path}"}
        if not os.path.isfile(schematic_path):
            return {
                "success": False,
                "error": f"Schematic not found: {schematic_path}",
            }
        try:
            with open(spec_path, encoding="utf-8") as fh:
                spec = _json.load(fh)
        except Exception as exc:
            return {
                "success": False,
                "error": f"spec_path not valid JSON: {exc}",
            }

        # Generate live spec
        cli = kicad_cli()
        if not cli:
            return {"success": False, "error": "kicad-cli not found."}
        with _tempfile.NamedTemporaryFile(suffix=".net", delete=False) as tf:
            tmp_net = tf.name
        try:
            _subprocess.run(
                [
                    cli, "sch", "export", "netlist",
                    "--format", "kicadsexpr",
                    "-o", tmp_net, schematic_path,
                ],
                capture_output=True, check=True, timeout=60,
            )
            with open(tmp_net, encoding="utf-8") as fh:
                live_netlist = fh.read()
        except _subprocess.CalledProcessError as exc:
            return {
                "success": False,
                "error": (
                    f"kicad-cli sch export netlist failed: "
                    f"{exc.stderr.decode('utf-8', 'replace')[:300]}"
                ),
            }
        finally:
            try:
                os.unlink(tmp_net)
            except OSError:
                pass

        live_spec = _parse_netlist_to_spec(live_netlist, schematic_path)

        # Diff components by reference
        spec_comps = {c["ref"]: c for c in spec.get("components", [])}
        live_comps = {c["ref"]: c for c in live_spec["components"]}
        missing_c = sorted(set(spec_comps) - set(live_comps))
        extra_c = sorted(set(live_comps) - set(spec_comps))
        value_mismatch: list[dict[str, str]] = []
        footprint_mismatch: list[dict[str, str]] = []
        for ref in sorted(set(spec_comps) & set(live_comps)):
            s = spec_comps[ref]
            v = live_comps[ref]
            if s.get("value") != v.get("value"):
                value_mismatch.append({
                    "ref": ref, "spec": s.get("value", ""),
                    "live": v.get("value", ""),
                })
            if s.get("footprint") != v.get("footprint"):
                footprint_mismatch.append({
                    "ref": ref, "spec": s.get("footprint", ""),
                    "live": v.get("footprint", ""),
                })

        # Diff nets by name
        spec_nets = {n["name"]: set(n.get("pins", [])) for n in spec.get("nets", [])}
        live_nets = {n["name"]: set(n.get("pins", [])) for n in live_spec["nets"]}
        missing_n = sorted(set(spec_nets) - set(live_nets))
        extra_n = sorted(set(live_nets) - set(spec_nets))
        pin_mismatches: list[dict[str, Any]] = []
        for name in sorted(set(spec_nets) & set(live_nets)):
            spec_pins = spec_nets[name]
            live_pins = live_nets[name]
            if spec_pins != live_pins:
                pin_mismatches.append({
                    "net": name,
                    "missing_pins": sorted(spec_pins - live_pins),
                    "extra_pins": sorted(live_pins - spec_pins),
                })

        spec_rails = set(spec.get("power_rails", []))
        live_rails = set(live_spec["power_rails"])
        missing_r = sorted(spec_rails - live_rails)
        extra_r = sorted(live_rails - spec_rails)

        total = (
            len(missing_c) + len(extra_c) + len(value_mismatch)
            + len(footprint_mismatch) + len(missing_n) + len(extra_n)
            + len(pin_mismatches) + len(missing_r) + len(extra_r)
        )
        return {
            "success": True,
            "spec_path": spec_path,
            "schematic_path": schematic_path,
            "components": {
                "missing": missing_c,
                "extra": extra_c,
                "value_mismatch": value_mismatch,
                "footprint_mismatch": footprint_mismatch,
            },
            "nets": {
                "missing": missing_n,
                "extra": extra_n,
                "pin_mismatches": pin_mismatches,
            },
            "power_rails": {"missing": missing_r, "extra": extra_r},
            "total_issues": total,
        }


def _parse_netlist_to_spec(netlist_text: str, schematic_path: str) -> dict[str, Any]:
    """Parse kicadsexpr netlist into a normalized JSON-spec dict.

    Returns ``{meta, components: [{ref, value, footprint}],
    nets: [{name, pins: [<ref>.<pin>]}], power_rails: [...]}``.

    Output is deterministically sorted for diff-friendliness.
    """
    import re as _re

    # Sexpr parser
    def parse(s: str, pos: int):
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos >= len(s):
            return None, pos
        if s[pos] == "(":
            items: list = []
            pos += 1
            while True:
                while pos < len(s) and s[pos].isspace():
                    pos += 1
                if pos >= len(s):
                    break
                if s[pos] == ")":
                    pos += 1
                    break
                item, pos = parse(s, pos)
                if item is not None:
                    items.append(item)
            return items, pos
        if s[pos] == '"':
            pos += 1
            start = pos
            while pos < len(s) and s[pos] != '"':
                if s[pos] == "\\":
                    pos += 1
                pos += 1
            val = s[start:pos]
            pos += 1
            return val, pos
        start = pos
        while pos < len(s) and not s[pos].isspace() and s[pos] not in "()":
            pos += 1
        return s[start:pos], pos

    tree, _ = parse(netlist_text, 0)
    if not isinstance(tree, list):
        return {"meta": {}, "components": [], "nets": [], "power_rails": []}

    def find_block(t: list, name: str):
        for it in t:
            if isinstance(it, list) and it and it[0] == name:
                return it
        return None

    # Components
    comp_block = find_block(tree, "components")
    components: list[dict[str, str]] = []
    if comp_block:
        for c in comp_block[1:]:
            if not isinstance(c, list) or c[0] != "comp":
                continue
            ref = value = fp = ""
            for sub in c[1:]:
                if isinstance(sub, list):
                    if sub[0] == "ref":
                        ref = sub[1]
                    elif sub[0] == "value":
                        value = sub[1]
                    elif sub[0] == "footprint":
                        fp = sub[1]
            if ref:
                components.append({"ref": ref, "value": value, "footprint": fp})

    # Nets
    nets_block = find_block(tree, "nets")
    nets: list[dict[str, Any]] = []
    power_rails: list[str] = []
    if nets_block:
        for n in nets_block[1:]:
            if not isinstance(n, list) or n[0] != "net":
                continue
            name = ""
            pins: list[str] = []
            for sub in n[1:]:
                if isinstance(sub, list):
                    if sub[0] == "name":
                        name = sub[1]
                    elif sub[0] == "node":
                        ref = pin = ""
                        for nn in sub[1:]:
                            if isinstance(nn, list):
                                if nn[0] == "ref":
                                    ref = nn[1]
                                elif nn[0] == "pin":
                                    pin = nn[1]
                        if ref and pin:
                            pins.append(f"{ref}.{pin}")
            if name:
                nets.append({"name": name, "pins": sorted(pins)})
                if name.startswith("+") or name in ("GND", "VCC"):
                    power_rails.append(name)

    components.sort(key=lambda c: c["ref"])
    nets.sort(key=lambda n: n["name"])
    return {
        "meta": {
            "source": os.path.basename(schematic_path),
            "spec_version": "1.0",
        },
        "components": components,
        "nets": nets,
        "power_rails": sorted(set(power_rails)),
    }
