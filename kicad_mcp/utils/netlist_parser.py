# SPDX-License-Identifier: GPL-3.0-or-later
"""
KiCad schematic netlist extraction utilities.
"""
# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
from collections import defaultdict
import logging
import os
import re
import subprocess
import tempfile
from typing import Any

from kicad_mcp.utils.sexpr_parser import find_node, find_nodes, parse_sexpr

logger = logging.getLogger(__name__)


def _extract_netlist_via_cli(schematic_path: str) -> dict[str, Any] | None:
    """Run ``kicad-cli sch export netlist`` and parse the resulting
    kicadsexpr file. Returns a dict matching the Label-parser format
    (components / nets / component_count / net_count) — but with full
    pin-level connectivity from KiCad's own netlist engine.

    Returns None if kicad-cli is unavailable or the export fails. Bug 2
    fix 2026-04-29 — the legacy SchematicParser is label-only and
    silently misses pin connections.
    """
    try:
        from kicad_mcp.utils.kicad_cli import KiCadCLIError, get_kicad_cli_path
        from kicad_mcp.utils.wsl_path import to_windows_path
    except ImportError:
        return None

    try:
        cli_path = get_kicad_cli_path(required=True)
    except KiCadCLIError:
        return None

    with tempfile.NamedTemporaryFile(suffix=".net", delete=False) as tmp:
        out_path = tmp.name
    try:
        cmd = [
            cli_path, "sch", "export", "netlist",
            "--format", "kicadsexpr",
            "--output", to_windows_path(out_path),
            to_windows_path(schematic_path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            logger.debug("kicad-cli netlist export failed: %s", result.stderr)
            return None
        with open(out_path, encoding="utf-8") as fh:
            netlist_text = fh.read()
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("kicad-cli netlist export errored: %s", exc)
        return None
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            # best effort: Temp-Netzliste ggf. schon entfernt
            pass

    try:
        tree = parse_sexpr(netlist_text)
    except Exception as exc:
        logger.warning("Failed to parse kicad-cli netlist: %s", exc)
        return None

    components: dict[str, dict[str, Any]] = {}
    comps_node = find_node(tree, "components")
    if comps_node:
        for comp in find_nodes(comps_node, "comp"):
            ref_node = find_node(comp, "ref")
            if not (ref_node and len(ref_node) > 1):
                continue
            ref = str(ref_node[1])
            entry: dict[str, Any] = {"reference": ref}
            for key in ("value", "footprint", "datasheet"):
                kn = find_node(comp, key)
                if kn and len(kn) > 1:
                    entry[key] = str(kn[1])
            lib_node = find_node(comp, "libsource")
            if lib_node:
                lib = find_node(lib_node, "lib")
                part = find_node(lib_node, "part")
                if lib and len(lib) > 1 and part and len(part) > 1:
                    entry["lib_id"] = f"{lib[1]}:{part[1]}"
            components[ref] = entry

    nets: dict[str, list[dict[str, str]]] = {}
    nets_node = find_node(tree, "nets")
    if nets_node:
        for net in find_nodes(nets_node, "net"):
            name_node = find_node(net, "name")
            if not (name_node and len(name_node) > 1):
                continue
            net_name = str(name_node[1])
            pin_list: list[dict[str, str]] = []
            for node in find_nodes(net, "node"):
                ref_n = find_node(node, "ref")
                pin_n = find_node(node, "pin")
                if not (ref_n and len(ref_n) > 1 and pin_n and len(pin_n) > 1):
                    continue
                pin_entry = {"ref": str(ref_n[1]), "pin": str(pin_n[1])}
                pf = find_node(node, "pinfunction")
                if pf and len(pf) > 1:
                    pin_entry["pinfunction"] = str(pf[1])
                pt = find_node(node, "pintype")
                if pt and len(pt) > 1:
                    pin_entry["pintype"] = str(pt[1])
                pin_list.append(pin_entry)
            nets[net_name] = pin_list

    return {
        "components": components,
        "nets": nets,
        "labels": [],
        "wires": [],
        "junctions": [],
        "power_symbols": [],
        "component_count": len(components),
        "net_count": len(nets),
        "partial": False,
        "source": "kicad-cli",
    }

class SchematicParser:
    """Parser for KiCad schematic files to extract netlist information."""

    def __init__(self, schematic_path: str):
        """Initialize the schematic parser.

        Args:
            schematic_path: Path to the KiCad schematic file (.kicad_sch)
        """
        self.schematic_path = schematic_path
        self.content = ""
        self.components = []
        self.labels = []
        self.wires = []
        self.junctions = []
        self.no_connects = []
        self.power_symbols = []
        self.hierarchical_labels = []
        self.global_labels = []

        # Netlist information
        self.nets = defaultdict(list)  # Net name -> connected pins
        self.component_pins = {}  # (component_ref, pin_num) -> net_name

        # Component information
        self.component_info = {}  # component_ref -> component details

        # Load the file
        self._load_schematic()

    def _load_schematic(self) -> None:
        """Load the schematic file content."""
        if not os.path.exists(self.schematic_path):
            logger.error(f"Schematic file not found: {self.schematic_path}")
            raise FileNotFoundError(f"Schematic file not found: {self.schematic_path}")

        try:
            with open(self.schematic_path, encoding="utf-8") as f:
                self.content = f.read()
                logger.info(f"Successfully loaded schematic: {self.schematic_path}")
        except Exception as e:
            logger.error(f"Error reading schematic file: {str(e)}")
            raise

    def parse(self) -> dict[str, Any]:
        """Parse the schematic to extract netlist information.

        Returns:
            Dictionary with parsed netlist information
        """
        logger.info("Starting schematic parsing")

        # Extract symbols (components)
        self._extract_components()

        # Extract wires
        self._extract_wires()

        # Extract junctions
        self._extract_junctions()

        # Extract labels
        self._extract_labels()

        # Extract power symbols
        self._extract_power_symbols()

        # Extract no-connects
        self._extract_no_connects()

        # Build netlist
        self._build_netlist()

        # Create result
        result = {
            "components": self.component_info,
            "nets": dict(self.nets),
            "labels": self.labels,
            "wires": self.wires,
            "junctions": self.junctions,
            "power_symbols": self.power_symbols,
            "component_count": len(self.component_info),
            "net_count": len(self.nets),
            "partial": True,
            "partial_reason": (
                "kicad-cli unavailable on this environment — falling back to "
                "the label-only Python parser. Nets are seeded from global "
                "labels and power symbols; pin-level wire connectivity is "
                "intentionally not traced here (the primary path delegates "
                "that to `kicad-cli sch export netlist --format kicadsexpr`). "
                "Install kicad-cli, or call it directly, to get full pin-level "
                "connectivity."
            )
        }

        logger.info(f"Schematic parsing complete: found {len(self.component_info)} components and {len(self.nets)} nets")
        return result

    def _extract_s_expressions(self, pattern: str) -> list[str]:
        """Extract all matching S-expressions from the schematic content.

        Args:
            pattern: Regex pattern to match the start of S-expressions

        Returns:
            List of matching S-expressions
        """
        matches = []
        positions = []

        # Find all starting positions of matches
        for match in re.finditer(pattern, self.content):
            positions.append(match.start())

        # Extract full S-expressions for each match
        for pos in positions:
            # Start from the matching position
            current_pos = pos
            depth = 0
            s_exp = ""

            # Extract the full S-expression by tracking parentheses
            while current_pos < len(self.content):
                char = self.content[current_pos]
                s_exp += char

                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth == 0:
                        # Found the end of the S-expression
                        break

                current_pos += 1

            matches.append(s_exp)

        return matches

    def _extract_components(self) -> None:
        """Extract component information from schematic."""
        logger.debug("Extracting components")

        # Extract all symbol expressions (components)
        symbols = self._extract_s_expressions(r'\(symbol\s+')

        for symbol in symbols:
            component = self._parse_component(symbol)
            if component:
                self.components.append(component)

                # Add to component info dictionary
                ref = component.get('reference', 'Unknown')
                self.component_info[ref] = component

        logger.debug(f"Extracted {len(self.components)} components")

    def _parse_component(self, symbol_expr: str) -> dict[str, Any]:
        """Parse a component from a symbol S-expression.

        Args:
            symbol_expr: Symbol S-expression

        Returns:
            Component information dictionary
        """
        component = {}

        # Extract library component ID
        lib_id_match = re.search(r'\(lib_id\s+"([^"]+)"\)', symbol_expr)
        if lib_id_match:
            component['lib_id'] = lib_id_match.group(1)

        # Extract reference (e.g., R1, C2)
        property_matches = re.finditer(r'\(property\s+"([^"]+)"\s+"([^"]+)"', symbol_expr)
        for match in property_matches:
            prop_name = match.group(1)
            prop_value = match.group(2)

            if prop_name == "Reference":
                component['reference'] = prop_value
            elif prop_name == "Value":
                component['value'] = prop_value
            elif prop_name == "Footprint":
                component['footprint'] = prop_value
            else:
                # Store other properties
                if 'properties' not in component:
                    component['properties'] = {}
                component['properties'][prop_name] = prop_value

        # Extract position
        pos_match = re.search(r'\(at\s+([\d\.-]+)\s+([\d\.-]+)(\s+[\d\.-]+)?\)', symbol_expr)
        if pos_match:
            component['position'] = {
                'x': float(pos_match.group(1)),
                'y': float(pos_match.group(2)),
                'angle': float(pos_match.group(3).strip() if pos_match.group(3) else 0)
            }

        # Extract pins
        pins = []
        pin_matches = re.finditer(r'\(pin\s+\(num\s+"([^"]+)"\)\s+\(name\s+"([^"]+)"\)', symbol_expr)
        for match in pin_matches:
            pin_num = match.group(1)
            pin_name = match.group(2)
            pins.append({
                'num': pin_num,
                'name': pin_name
            })

        if pins:
            component['pins'] = pins

        return component

    def _extract_wires(self) -> None:
        """Extract wire information from schematic."""
        logger.debug("Extracting wires")

        # Extract all wire expressions
        wires = self._extract_s_expressions(r'\(wire\s+')

        for wire in wires:
            # Extract the wire coordinates
            pts_match = re.search(r'\(pts\s+\(xy\s+([\d\.-]+)\s+([\d\.-]+)\)\s+\(xy\s+([\d\.-]+)\s+([\d\.-]+)\)\)', wire)
            if pts_match:
                self.wires.append({
                    'start': {
                        'x': float(pts_match.group(1)),
                        'y': float(pts_match.group(2))
                    },
                    'end': {
                        'x': float(pts_match.group(3)),
                        'y': float(pts_match.group(4))
                    }
                })

        logger.debug(f"Extracted {len(self.wires)} wires")

    def _extract_junctions(self) -> None:
        """Extract junction information from schematic."""
        logger.debug("Extracting junctions")

        # Extract all junction expressions
        junctions = self._extract_s_expressions(r'\(junction\s+')

        for junction in junctions:
            # Extract the junction coordinates
            xy_match = re.search(r'\(junction\s+\(xy\s+([\d\.-]+)\s+([\d\.-]+)\)\)', junction)
            if xy_match:
                self.junctions.append({
                    'x': float(xy_match.group(1)),
                    'y': float(xy_match.group(2))
                })

        logger.debug(f"Extracted {len(self.junctions)} junctions")

    def _extract_labels(self) -> None:
        """Extract label information from schematic."""
        logger.debug("Extracting labels")

        # Extract local labels
        local_labels = self._extract_s_expressions(r'\(label\s+')

        for label in local_labels:
            # Extract label text and position
            label_match = re.search(r'\(label\s+"([^"]+)"\s+\(at\s+([\d\.-]+)\s+([\d\.-]+)(\s+[\d\.-]+)?\)', label)
            if label_match:
                self.labels.append({
                    'type': 'local',
                    'text': label_match.group(1),
                    'position': {
                        'x': float(label_match.group(2)),
                        'y': float(label_match.group(3)),
                        'angle': float(label_match.group(4).strip() if label_match.group(4) else 0)
                    }
                })

        # Extract global labels
        global_labels = self._extract_s_expressions(r'\(global_label\s+')

        for label in global_labels:
            # Extract global label text and position
            label_match = re.search(r'\(global_label\s+"([^"]+)"\s+\(shape\s+([^\s\)]+)\)\s+\(at\s+([\d\.-]+)\s+([\d\.-]+)(\s+[\d\.-]+)?\)', label)
            if label_match:
                self.global_labels.append({
                    'type': 'global',
                    'text': label_match.group(1),
                    'shape': label_match.group(2),
                    'position': {
                        'x': float(label_match.group(3)),
                        'y': float(label_match.group(4)),
                        'angle': float(label_match.group(5).strip() if label_match.group(5) else 0)
                    }
                })

        # Extract hierarchical labels
        hierarchical_labels = self._extract_s_expressions(r'\(hierarchical_label\s+')

        for label in hierarchical_labels:
            # Extract hierarchical label text and position
            label_match = re.search(r'\(hierarchical_label\s+"([^"]+)"\s+\(shape\s+([^\s\)]+)\)\s+\(at\s+([\d\.-]+)\s+([\d\.-]+)(\s+[\d\.-]+)?\)', label)
            if label_match:
                self.hierarchical_labels.append({
                    'type': 'hierarchical',
                    'text': label_match.group(1),
                    'shape': label_match.group(2),
                    'position': {
                        'x': float(label_match.group(3)),
                        'y': float(label_match.group(4)),
                        'angle': float(label_match.group(5).strip() if label_match.group(5) else 0)
                    }
                })

        logger.debug(f"Extracted {len(self.labels)} local labels, {len(self.global_labels)} global labels, and {len(self.hierarchical_labels)} hierarchical labels")

    def _extract_power_symbols(self) -> None:
        """Extract power symbol information from schematic."""
        logger.debug("Extracting power symbols")

        # Extract all power symbol expressions
        power_symbols = self._extract_s_expressions(r'\(symbol\s+\(lib_id\s+"power:')

        for symbol in power_symbols:
            # Extract power symbol type and position
            type_match = re.search(r'\(lib_id\s+"power:([^"]+)"\)', symbol)
            pos_match = re.search(r'\(at\s+([\d\.-]+)\s+([\d\.-]+)(\s+[\d\.-]+)?\)', symbol)

            if type_match and pos_match:
                self.power_symbols.append({
                    'type': type_match.group(1),
                    'position': {
                        'x': float(pos_match.group(1)),
                        'y': float(pos_match.group(2)),
                        'angle': float(pos_match.group(3).strip() if pos_match.group(3) else 0)
                    }
                })

        logger.debug(f"Extracted {len(self.power_symbols)} power symbols")

    def _extract_no_connects(self) -> None:
        """Extract no-connect information from schematic."""
        logger.debug("Extracting no-connects")

        # Extract all no-connect expressions
        no_connects = self._extract_s_expressions(r'\(no_connect\s+')

        for no_connect in no_connects:
            # Extract the no-connect coordinates
            xy_match = re.search(r'\(no_connect\s+\(at\s+([\d\.-]+)\s+([\d\.-]+)\)', no_connect)
            if xy_match:
                self.no_connects.append({
                    'x': float(xy_match.group(1)),
                    'y': float(xy_match.group(2))
                })

        logger.debug(f"Extracted {len(self.no_connects)} no-connects")

    def _build_netlist(self) -> None:
        """Label-only net seeding for the kicad-cli-less fallback path.

        Wire-tracing is **intentionally not implemented** here.
        ``extract_netlist()`` calls ``kicad-cli sch export netlist
        --format kicadsexpr`` first (line 532) which delegates the
        full pin-level connectivity walk to KiCad's own netlist
        engine. ``SchematicParser`` is only constructed on the
        fallback branch when kicad-cli is unavailable on the
        environment — there we surface what we *can* see (labels and
        power symbols) and the ``parse()`` result is tagged
        ``partial: True`` with the explicit ``partial_reason`` so
        callers can decide what to do.

        Re-implementing wire-tracing in pure Python would duplicate
        substantial logic from KiCad's ``SCH_SCREEN::TestDanglingEnds``
        plus the connectivity-graph builder — not worth carrying
        against an upstream that is the source of truth.
        """
        logger.info(
            "Seeding fallback netlist from labels + power symbols"
            " (kicad-cli unavailable; result will be tagged partial)"
        )

        # Global labels → empty net buckets (caller sees them in `nets`
        # but with no pin connections, as documented by `partial`).
        for label in self.global_labels:
            self.nets[label["text"]] = []

        # Power symbols → empty net buckets keyed by symbol type.
        for power in self.power_symbols:
            self.nets.setdefault(power["type"], [])

        logger.info(
            "Fallback netlist: %d nets seeded from labels / power",
            len(self.nets),
        )


def extract_netlist(schematic_path: str) -> dict[str, Any]:
    """Extract netlist information from a KiCad schematic file.

    First tries ``kicad-cli sch export netlist --format kicadsexpr`` —
    that gives full pin-level connectivity (KiCad's own netlist engine
    walks wires correctly). Falls back to the label-only Python parser
    if kicad-cli is unavailable or the export fails. The fallback
    response carries ``partial: True``; the CLI path carries
    ``source: "kicad-cli"`` and ``partial: False``.

    Args:
        schematic_path: Path to the KiCad schematic file (.kicad_sch)

    Returns:
        Dictionary with netlist information
    """
    cli_result = _extract_netlist_via_cli(schematic_path)
    if cli_result is not None:
        return cli_result
    try:
        parser = SchematicParser(schematic_path)
        return parser.parse()
    except Exception as e:
        logger.error(f"Error extracting netlist: {str(e)}")
        return {
            "error": str(e),
            "components": {},
            "nets": {},
            "component_count": 0,
            "net_count": 0
        }


def analyze_netlist(netlist_data: dict[str, Any]) -> dict[str, Any]:
    """Analyze netlist data to provide insights.

    Args:
        netlist_data: Dictionary with netlist information

    Returns:
        Dictionary with analysis results
    """
    results = {
        "component_count": netlist_data.get("component_count", 0),
        "net_count": netlist_data.get("net_count", 0),
        "component_types": defaultdict(int),
        "power_nets": []
    }

    # Analyze component types
    for ref, _component in netlist_data.get("components", {}).items():
        # Extract component type from reference (e.g., R1 -> R)
        comp_type = re.match(r'^([A-Za-z_]+)', ref)
        if comp_type:
            results["component_types"][comp_type.group(1)] += 1

    # Identify power nets
    for net_name in netlist_data.get("nets", {}):
        if any(net_name.startswith(prefix) for prefix in ["VCC", "VDD", "GND", "+5V", "+3V3", "+12V"]):
            results["power_nets"].append(net_name)

    # Count pin connections
    total_pins = sum(len(pins) for pins in netlist_data.get("nets", {}).values())
    results["total_pin_connections"] = total_pins

    return results


async def load_netlist_with_progress(schematic_path, ctx):
    """Gemeinsame Tool-Präambel: Existenz-Check → Progress → extract_netlist.

    ``ctx`` ist der (optionale) FastMCP-Context, duck-typed genutzt (info/
    report_progress) — kein MCP-Import nötig. Rückgabe ``(netlist_data,
    error_result)``: genau eines von beiden ist gesetzt; bei Fehler gibt der
    Aufrufer ``error_result`` direkt als Tool-Result zurück.
    """
    if not os.path.exists(schematic_path):
        if ctx:
            ctx.info(f"Schematic file not found: {schematic_path}")
        return None, {"success": False,
                      "error": f"Schematic file not found: {schematic_path}"}
    if ctx:
        await ctx.report_progress(10, 100)
        ctx.info(f"Loading schematic file: {os.path.basename(schematic_path)}")
        await ctx.report_progress(20, 100)
        ctx.info("Parsing schematic structure...")
    netlist_data = extract_netlist(schematic_path)
    if "error" in netlist_data:
        if ctx:
            ctx.info(f"Error extracting netlist: {netlist_data['error']}")
        return None, {"success": False, "error": netlist_data["error"]}
    return netlist_data, None
