# SPDX-License-Identifier: GPL-3.0-or-later
"""
MCP tool wrappers for KiCad project generation.

Provides tools to generate complete KiCad projects, schematics, and PCBs
from JSON specifications, plus benchmarking and quality analysis.
"""

import json
import os
import re
import subprocess
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from kicad_mcp.generators.pcb.builder import build_pcb
from kicad_mcp.generators.schematic.builder import build_schematic
from kicad_mcp.generators.schematic_scorer import score_schematic
from kicad_mcp.generators.validator import validate_all
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.wsl_path import to_windows_path


def _run_drc(sch_path: str, ctx=None) -> dict | None:
    """6.2: Run KiCad DRC on a schematic file if kicad-cli is available.

    Returns dict with errors/warnings, or None if kicad-cli not found.
    """
    try:
        from kicad_mcp.utils.kicad_cli import get_kicad_cli_path
        cli = get_kicad_cli_path()
        if not cli:
            return None

        drc_output = sch_path.replace(".kicad_sch", "_drc.json")
        _result = subprocess.run(
            [cli, "sch", "drc",
             "--output", to_windows_path(drc_output),
             "--format", "json",
             to_windows_path(sch_path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if os.path.exists(drc_output):
            with open(drc_output, encoding="utf-8") as f:
                drc_data = json.load(f)

            errors = [v for v in drc_data.get("violations", [])
                      if v.get("severity") == "error"]
            warnings = [v for v in drc_data.get("violations", [])
                        if v.get("severity") == "warning"]

            os.remove(drc_output)  # cleanup

            if errors and ctx:
                ctx.info(f"DRC: {len(errors)} errors, {len(warnings)} warnings")

            return {
                "errors": errors,
                "warnings": warnings,
                "error_count": len(errors),
                "warning_count": len(warnings),
            }

        return None
    except Exception:
        return None  # graceful skip


def _write_kicad_pro(output_path: str, project_name: str) -> None:
    """Write a minimal .kicad_pro project file."""
    pro = {
        "board": {"design_settings": {"defaults": {}}},
        "meta": {
            "filename": f"{project_name}.kicad_pro",
            "version": 2,
        },
        "schematic": {"meta": {"version": 1}},
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(pro, f, indent=2)


def register_generation_tools(mcp: FastMCP) -> None:
    """Register generation tools with the MCP server."""

    @mcp.tool()
    async def generate_project(
        output_dir: str,
        parts: str,
        nets: str,
        board: str = "{}",
        project_name: str = "project",
        simulation: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Generate a complete KiCad project (.kicad_pro + .kicad_sch + .kicad_pcb).

        Args:
            output_dir: Directory to write output files
            parts: JSON string - list of components with ref, name, footprint, value, pins
            nets: JSON string - list of nets with name, type, connections
            board: JSON string - board config with shape, width, depth, layers, thickness
            project_name: Project name (used for filenames)
            simulation: If True, add SPICE simulation properties for simulation-ready schematic
            ctx: MCP context

        Returns:
            Generation result with file paths
        """
        try:
            parts_data = json.loads(parts)
            nets_data = json.loads(nets)
            board_data = json.loads(board)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON input: {e}"}

        # Validate
        errors = validate_all(parts_data, nets_data, board_data)
        if errors:
            return {"success": False, "errors": errors}

        if ctx:
            ctx.info(f"Generating KiCad project '{project_name}'")

        output_dir = to_local_path(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        # Generate files
        # 6.1: Check if multi-sheet is needed
        sch_files = {}
        try:
            from kicad_mcp.generators.schematic.multisheet import (
                build_root_sheet,
                find_intersheet_nets,
                partition_by_group,
                should_use_multisheet,
            )
            from kicad_mcp.generators.schematic.place import place_schematic

            if should_use_multisheet(parts_data):
                # Classify parts first (needed for group assignment)
                place_schematic(parts_data, nets_data)
                groups = partition_by_group(parts_data, nets_data)
                intersheet = find_intersheet_nets(parts_data, nets_data)

                # Build root sheet (with parts for proper pin filtering)
                root_sch = build_root_sheet(
                    list(groups.keys()), intersheet, project_name,
                    parts=parts_data,
                )
                sch_files["root"] = root_sch

                # Build sub-sheets with hierarchical labels for inter-sheet nets
                for gname, (gparts, gnets) in groups.items():
                    # Reset placement for sub-sheet
                    for p in gparts:
                        p.pop("_place_x", None)
                        p.pop("_place_y", None)
                    # Pass `intersheet` so signal nets that cross sheet
                    # boundaries get hierarchical_label entries — the
                    # matching pin on the root sheet-symbol resolves them
                    # back at parse time. Power nets continue to use
                    # global labels / real power symbols and are excluded
                    # from `intersheet` by `find_intersheet_nets`.
                    sub_sch = build_schematic(
                        gparts, gnets, f"{project_name}_{gname}",
                        simulation=simulation,
                        intersheet_nets=intersheet,
                    )
                    sch_files[gname] = sub_sch

                if ctx:
                    ctx.info(f"Multi-sheet: {len(groups)} sub-sheets generated")
        except Exception:
            pass  # Fall back to single-sheet

        if not sch_files:
            sch_content = build_schematic(parts_data, nets_data, project_name,
                                          simulation=simulation)
            sch_files["main"] = sch_content

        pcb_content = build_pcb(parts_data, nets_data, board_data, project_name)

        # Write schematic files
        sch_path = os.path.join(output_dir, f"{project_name}.kicad_sch")
        pcb_path = os.path.join(output_dir, f"{project_name}.kicad_pcb")
        pro_path = os.path.join(output_dir, f"{project_name}.kicad_pro")

        if "root" in sch_files:
            # Multi-sheet: write root + sub-sheets
            with open(sch_path, "w", encoding="utf-8") as f:
                f.write(sch_files["root"])
            for gname, content in sch_files.items():
                if gname == "root":
                    continue
                sub_path = os.path.join(output_dir, f"{project_name}_{gname}.kicad_sch")
                with open(sub_path, "w", encoding="utf-8") as f:
                    f.write(content)
        else:
            with open(sch_path, "w", encoding="utf-8") as f:
                f.write(sch_files["main"])

        with open(pcb_path, "w", encoding="utf-8") as f:
            f.write(pcb_content)
        _write_kicad_pro(pro_path, project_name)

        if ctx:
            ctx.info(f"Project generated: {sch_path}, {pcb_path}, {pro_path}")

        # 6.2: DRC integration — errors cause abort + output removal
        drc_result = _run_drc(sch_path, ctx)
        if drc_result and drc_result["error_count"] > 0:
            # Remove generated files on DRC errors
            for fpath in [sch_path, pcb_path, pro_path]:
                if os.path.exists(fpath):
                    os.remove(fpath)
            # Also remove multi-sheet sub-files
            if "root" in sch_files:
                for gname in sch_files:
                    if gname == "root":
                        continue
                    sub_path = os.path.join(output_dir, f"{project_name}_{gname}.kicad_sch")
                    if os.path.exists(sub_path):
                        os.remove(sub_path)
            return {
                "success": False,
                "error": f"DRC failed with {drc_result['error_count']} error(s)",
                "drc": drc_result,
            }

        result = {
            "success": True,
            "project_name": project_name,
            "files": {
                "schematic": sch_path,
                "pcb": pcb_path,
                "project": pro_path,
            },
        }
        if drc_result:
            result["drc"] = drc_result
        return result

    @mcp.tool()
    async def generate_schematic(
        output_path: str,
        parts: str,
        nets: str,
        project_name: str = "project",
        simulation: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Generate a KiCad schematic (``.kicad_sch``) from parts + nets specs.

        Use this when you have a fully-specified design (every part has
        ``pins`` listed, every net has ``connections``) and want a
        single schematic file. For incremental edits to an existing
        schematic prefer the Phase-S patch tools (``add_schematic_symbols``
        + ``connect_pins``) instead — this generator overwrites the
        whole file. For compact "ref + value only" specs use
        ``generate_from_netlist`` (auto-resolves pins from libraries).
        Don't run this twice into the same path expecting an additive
        merge — second run replaces the file.

        Args:
            output_path: Output ``.kicad_sch`` file path.
            parts: JSON string — list of components with ``ref``,
                ``name``, ``footprint``, ``value``, ``pins``.
            nets: JSON string — list of nets with ``name``, ``type``,
                ``connections`` (``"REF:PIN"`` strings).
            project_name: Project name for UUID generation (must match
                the matching ``.kicad_pcb`` for cross-file references).
            simulation: If True, embed SPICE properties (``Sim.Device``,
                ``Sim.Params``, ``Sim.Pins``) for simulation-ready output.
            ctx: MCP context.

        Returns:
            Generation result with file path
        """
        try:
            parts_data = json.loads(parts)
            nets_data = json.loads(nets)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON input: {e}"}

        errors = validate_all(parts_data, nets_data)
        if errors:
            return {"success": False, "errors": errors}

        if ctx:
            ctx.info(f"Generating schematic with {len(parts_data)} components")

        content = build_schematic(parts_data, nets_data, project_name, simulation=simulation)

        output_path = to_local_path(output_path)
        out_dir = os.path.dirname(output_path) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Auto-create .kicad_pro if missing (links schematic ↔ PCB via shared UUIDs)
        pro_path = os.path.join(out_dir, f"{project_name}.kicad_pro")
        if not os.path.exists(pro_path):
            _write_kicad_pro(pro_path, project_name)

        # 6.2: DRC integration — errors cause abort + output removal
        drc_result = _run_drc(output_path, ctx)
        if drc_result and drc_result["error_count"] > 0:
            if os.path.exists(output_path):
                os.remove(output_path)
            return {
                "success": False,
                "error": f"DRC failed with {drc_result['error_count']} error(s)",
                "drc": drc_result,
            }

        result = {"success": True, "output_path": output_path, "project_file": pro_path}
        if drc_result:
            result["drc"] = drc_result
        return result

    @mcp.tool()
    async def generate_from_netlist(
        output_path: str,
        parts: str,
        nets: str,
        project_name: str = "project",
        simulation: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Generate a KiCad schematic from a compact netlist.

        Unlike generate_schematic, this tool auto-resolves:
        - Pin definitions from KiCad symbol libraries
        - Library IDs from component names/prefixes
        - Default footprints
        - Placement from template matching

        Compact part format (only ref required, rest is auto-resolved):
            [{"ref": "R1", "value": "1k"},
             {"ref": "Q1", "name": "NPN", "value": "BC547"},
             {"ref": "U1", "name": "NE555", "value": "NE555"}]

        Compact net format (same as generate_schematic):
            [{"name": "VCC", "connections": ["R1:1", "Q1:C"], "type": "power"},
             {"name": "N1", "connections": ["R1:2", "Q1:B"]}]

        Args:
            output_path: Output .kicad_sch file path
            parts: JSON string - compact component list
            nets: JSON string - net connections
            project_name: Project name
            simulation: If True, add SPICE properties
            ctx: MCP context

        Returns:
            Generation result with file path
        """
        try:
            parts_data = json.loads(parts)
            nets_data = json.loads(nets)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON: {e}"}

        if not parts_data:
            return {"success": False, "error": "Parts list is empty"}
        if not nets_data:
            return {"success": False, "error": "Nets list is empty"}

        # Expand compact netlist into full parts+nets
        from kicad_mcp.generators.netlist_expander import expand_netlist
        try:
            expanded_parts, expanded_nets = expand_netlist(parts_data, nets_data)
        except Exception as e:
            return {"success": False, "error": f"Netlist expansion failed: {e}"}

        if ctx:
            resolved = sum(1 for p in expanded_parts if p.get("pins"))
            ctx.info(f"Expanded {len(parts_data)} parts ({resolved} with auto-resolved pins)")

        # Build schematic using the existing pipeline
        content = build_schematic(expanded_parts, expanded_nets, project_name, simulation=simulation)

        output_path = to_local_path(output_path)
        out_dir = os.path.dirname(output_path) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        pro_path = os.path.join(out_dir, f"{project_name}.kicad_pro")
        if not os.path.exists(pro_path):
            _write_kicad_pro(pro_path, project_name)

        # DRC check
        drc_result = _run_drc(output_path, ctx)
        if drc_result and drc_result["error_count"] > 0:
            if os.path.exists(output_path):
                os.remove(output_path)
            return {
                "success": False,
                "error": f"DRC failed with {drc_result['error_count']} error(s)",
                "drc": drc_result,
            }

        result = {
            "success": True,
            "output_path": output_path,
            "project_file": pro_path,
            "parts_expanded": len(expanded_parts),
            "pins_resolved": sum(len(p.get("pins", [])) for p in expanded_parts),
        }
        if drc_result:
            result["drc"] = drc_result
        return result

    @mcp.tool()
    async def generate_pcb(
        output_path: str,
        parts: str,
        nets: str,
        board: str = "{}",
        project_name: str = "project",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Generate a KiCad PCB (``.kicad_pcb``) from parts + nets + board spec.

        Use this for the *initial* PCB skeleton: footprint placement,
        layer stackup, board outline (rectangle / circle / Eurocard),
        GND copper pour, mounting holes, JLCPCB design rules. For
        adding tracks / vias / zones afterwards use the Phase-A / E
        tools (``add_track_to_pcb`` / ``add_zone_pour_to_pcb`` /
        ``ipc_route_*``). Don't hand-craft the ``.kicad_pcb`` S-expr —
        the layer setup and net-class blocks have non-trivial structure
        and this tool gets them right.

        Args:
            output_path: Output ``.kicad_pcb`` file path.
            parts: JSON string — list of components with ``footprint``
                + pin info (same format as ``generate_schematic``).
            nets: JSON string — list of nets driving pad-to-net
                assignment.
            board: JSON string — ``{shape, width, depth, layers,
                thickness}`` (shape: ``rectangle`` / ``circle`` /
                ``euro_divider`` with ``euro_type``).
            project_name: Project name for UUID generation (must match
                the matching ``.kicad_sch`` for cross-file references).
            ctx: MCP context.

        Returns:
            Generation result with file path
        """
        try:
            parts_data = json.loads(parts)
            nets_data = json.loads(nets)
            board_data = json.loads(board)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON input: {e}"}

        errors = validate_all(parts_data, nets_data, board_data)
        if errors:
            return {"success": False, "errors": errors}

        if ctx:
            ctx.info(f"Generating PCB with {len(parts_data)} footprints")

        content = build_pcb(parts_data, nets_data, board_data, project_name)

        output_path = to_local_path(output_path)
        out_dir = os.path.dirname(output_path) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Auto-create .kicad_pro if missing (links schematic ↔ PCB via shared UUIDs)
        pro_path = os.path.join(out_dir, f"{project_name}.kicad_pro")
        if not os.path.exists(pro_path):
            _write_kicad_pro(pro_path, project_name)

        return {"success": True, "output_path": output_path, "project_file": pro_path}

    @mcp.tool()
    async def validate_design(
        parts: str,
        nets: str,
        board: str = "{}",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Pre-flight validation of a parts + nets + board spec — no files written.

        Use this **before** every ``generate_project`` / ``generate_schematic`` /
        ``generate_pcb`` call when the spec was synthesised by an LLM
        or assembled from multiple sources. Catches the most common
        failure modes (duplicate refs, mismatched pin types on the same
        net, references to undefined parts in connections, board too
        small for the footprints) without leaving half-generated files
        on disk. Don't run a generate-and-rollback loop: this tool is
        the cheap pre-flight.

        Args:
            parts: JSON string — list of components.
            nets: JSON string — list of nets.
            board: JSON string — board configuration (optional).
            ctx: MCP context.

        Returns:
            Validation result with any errors found
        """
        try:
            parts_data = json.loads(parts)
            nets_data = json.loads(nets)
            board_data = json.loads(board)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON input: {e}"}

        errors = validate_all(parts_data, nets_data, board_data)

        if errors:
            return {
                "success": False,
                "valid": False,
                "error_count": len(errors),
                "errors": errors,
            }

        return {
            "success": True,
            "valid": True,
            "parts_count": len(parts_data),
            "nets_count": len(nets_data),
            "message": "Design specification is valid",
        }

    @mcp.tool()
    async def benchmark_schematic(
        parts: str,
        nets: str,
        project_name: str = "benchmark",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Generate schematic and measure quality metrics (wire:label ratio, score).

        Hidden benchmarking tool for iterative improvement loops.
        Generates a schematic from the given parts/nets, then analyzes
        the output for wire count, label count, wire:label ratio,
        and the 15-rule schematic quality score.

        Args:
            parts: JSON string - list of components
            nets: JSON string - list of nets
            project_name: Name for UUID generation
            ctx: MCP context

        Returns:
            Detailed quality metrics including wire:label ratio and score
        """
        try:
            parts_data = json.loads(parts)
            nets_data = json.loads(nets)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON input: {e}"}

        errors = validate_all(parts_data, nets_data)
        if errors:
            return {"success": False, "errors": errors}

        if ctx:
            ctx.info(f"Benchmarking schematic with {len(parts_data)} components")

        sch = build_schematic(parts_data, nets_data, project_name)

        # Count wire segments and labels
        wire_count = len(re.findall(r'\(wire\s', sch))
        label_count = len(re.findall(r'\(label\s', sch))
        global_label_count = len(re.findall(r'\(global_label\s', sch))
        no_connect_count = len(re.findall(r'\(no_connect\s', sch))
        total_labels = label_count + global_label_count
        wire_label_ratio = wire_count / max(total_labels, 1)
        signal_ratio = wire_count / max(label_count, 1)

        # Score the schematic layout
        score, violations = score_schematic(parts_data, nets_data)

        # Power vs signal net breakdown
        power_nets = [n for n in nets_data if n.get("type") == "power"]
        signal_nets = [n for n in nets_data if n.get("type") != "power"]
        power_connections = sum(len(n.get("connections", [])) for n in power_nets)
        signal_connections = sum(len(n.get("connections", [])) for n in signal_nets)

        return {
            "success": True,
            "metrics": {
                "parts": len(parts_data),
                "nets": len(nets_data),
                "power_nets": len(power_nets),
                "signal_nets": len(signal_nets),
                "power_connections": power_connections,
                "signal_connections": signal_connections,
                "wires": wire_count,
                "labels": label_count,
                "global_labels": global_label_count,
                "total_labels": total_labels,
                "no_connects": no_connect_count,
                "wire_label_ratio": round(wire_label_ratio, 2),
                "signal_wire_label_ratio": round(signal_ratio, 2),
                "score": score,
                "violation_count": len(violations),
            },
            "violations": violations[:10],
            "schematic_size_bytes": len(sch),
        }

    @mcp.tool()
    async def benchmark_loop(
        output_dir: str,
        parts: str,
        nets: str,
        board: str = "{}",
        project_name: str = "benchmark",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Generate project, export SVG, score, and report — full benchmark loop.

        Complete quality analysis loop:
        1. Generate .kicad_sch + .kicad_pcb
        2. Export schematic SVG via kicad-cli (for visual comparison)
        3. Measure wire:label ratio and quality score
        4. Return comprehensive report with file paths

        Args:
            output_dir: Directory for output files
            parts: JSON string - list of components
            nets: JSON string - list of nets
            board: JSON string - board configuration
            project_name: Project name for filenames
            ctx: MCP context

        Returns:
            Full benchmark report with metrics, file paths, and SVG for review
        """
        try:
            parts_data = json.loads(parts)
            nets_data = json.loads(nets)
            board_data = json.loads(board)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON input: {e}"}

        errors = validate_all(parts_data, nets_data, board_data)
        if errors:
            return {"success": False, "errors": errors}

        output_dir = to_local_path(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        # 1. Generate schematic + PCB
        if ctx:
            ctx.info(f"Generating project '{project_name}'...")
        sch = build_schematic(parts_data, nets_data, project_name)
        pcb = build_pcb(parts_data, nets_data, board_data, project_name)

        sch_path = os.path.join(output_dir, f"{project_name}.kicad_sch")
        pcb_path = os.path.join(output_dir, f"{project_name}.kicad_pcb")
        pro_path = os.path.join(output_dir, f"{project_name}.kicad_pro")

        with open(sch_path, "w", encoding="utf-8") as f:
            f.write(sch)
        with open(pcb_path, "w", encoding="utf-8") as f:
            f.write(pcb)
        _write_kicad_pro(pro_path, project_name)

        # 2. Export SVG via kicad-cli
        svg_path = os.path.join(output_dir, f"{project_name}.svg")
        svg_ok = False
        try:
            from kicad_mcp.utils.kicad_cli import get_kicad_cli_path
            cli = get_kicad_cli_path()
            if cli:
                if ctx:
                    ctx.info("Exporting SVG...")
                result = subprocess.run(
                    [cli, "sch", "export", "svg",
                     "--output", to_windows_path(svg_path),
                     to_windows_path(sch_path)],
                    capture_output=True, text=True, timeout=30, check=False,
                )
                svg_ok = result.returncode == 0
        except Exception:
            pass  # kicad-cli not available — skip SVG export

        # 3. Measure metrics
        wire_count = len(re.findall(r'\(wire\s', sch))
        label_count = len(re.findall(r'\(label\s', sch))
        global_label_count = len(re.findall(r'\(global_label\s', sch))
        total_labels = label_count + global_label_count
        wire_label_ratio = wire_count / max(total_labels, 1)
        signal_ratio = wire_count / max(label_count, 1)

        score, violations = score_schematic(parts_data, nets_data)

        _power_nets = [n for n in nets_data if n.get("type") == "power"]
        _signal_nets = [n for n in nets_data if n.get("type") != "power"]

        return {
            "success": True,
            "files": {
                "schematic": sch_path,
                "pcb": pcb_path,
                "project": pro_path,
                "svg": svg_path if svg_ok else None,
            },
            "metrics": {
                "parts": len(parts_data),
                "nets": len(nets_data),
                "wires": wire_count,
                "labels": label_count,
                "global_labels": global_label_count,
                "total_labels": total_labels,
                "wire_label_ratio": round(wire_label_ratio, 2),
                "signal_wire_label_ratio": round(signal_ratio, 2),
                "score": score,
                "violation_count": len(violations),
            },
            "violations": violations[:10],
            "visual_review": (
                f"SVG exported to {svg_path} — open in browser for visual check"
                if svg_ok else
                "SVG export skipped (kicad-cli not available)"
            ),
        }
