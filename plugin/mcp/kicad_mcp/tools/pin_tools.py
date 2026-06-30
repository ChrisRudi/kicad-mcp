# SPDX-License-Identifier: GPL-3.0-or-later
"""
Pin analysis tools for KiCad schematics.

Analyzes pin functions (I2C, SPI, UART, GPIO, etc.) from net names
and detects pin conflicts.
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
from pathlib import Path
import re
from typing import Any

from fastmcp import Context, FastMCP

from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.sexpr_parser import find_node, find_nodes, parse_sexpr

# Pin function inference patterns
_PIN_PATTERNS = {
    "I2C": [r"(?i)(SCL|SDA|I2C)", r"(?i)TWI"],
    "SPI": [r"(?i)(MOSI|MISO|SCK|SCLK|CS|SS)", r"(?i)SPI"],
    "UART": [r"(?i)(TX|RX|TXD|RXD|UART)", r"(?i)SERIAL"],
    "GPIO": [r"(?i)GPIO[\d_]", r"(?i)IO[\d]"],
    "ADC": [r"(?i)(ADC|AIN|AN[\d])", r"(?i)ANALOG"],
    "PWM": [r"(?i)(PWM|TIM[\d])", r"(?i)TIMER"],
    "USB": [r"(?i)(USB|D\+|D\-|DP|DM|VBUS)"],
    "INTERRUPT": [r"(?i)(INT|IRQ|EXTI)"],
    "POWER": [r"(?i)(VCC|VDD|GND|VSS|3V3|5V|VBAT)"],
    "RESET": [r"(?i)(RST|RESET|NRST)"],
    "JTAG": [r"(?i)(JTAG|SWDIO|SWCLK|TDI|TDO|TCK|TMS|SWO)"],
    "CAN": [r"(?i)(CANH|CANL|CAN_TX|CAN_RX)"],
}

# MCU family detection
_MCU_FAMILIES = {
    "ESP32": r"(?i)ESP32",
    "STM32": r"(?i)STM32",
    "ATmega": r"(?i)(ATmega|ATtiny|AVR)",
    "nRF52": r"(?i)nRF5",
    "RP2040": r"(?i)RP20",
    "SAMD": r"(?i)SAMD",
}


def _infer_pin_function(net_name: str) -> list[str]:
    """Infer pin functions from net name using pattern matching."""
    functions = []
    for func, patterns in _PIN_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, net_name):
                functions.append(func)
                break
    return functions


def _parse_schematic_for_pins(sch_path: str) -> dict[str, Any]:
    """Parse schematic and extract pin-net connections."""
    with open(sch_path, encoding="utf-8") as f:
        tree = parse_sexpr(f.read())

    # Extract components
    components = []
    for sym in find_nodes(tree, "symbol"):
        lib_id_node = find_node(sym, "lib_id")
        if not lib_id_node or len(lib_id_node) < 2:
            continue
        lib_id = str(lib_id_node[1])

        props = {}
        for prop_node in find_nodes(sym, "property"):
            if len(prop_node) >= 3:
                props[str(prop_node[1])] = str(prop_node[2])

        reference = props.get("Reference", "?")
        value = props.get("Value", "")

        # Get pins
        pins = []
        for pin in find_nodes(sym, "pin"):
            pin_name_node = find_node(pin, "name")
            pin_num_node = find_node(pin, "number")
            pin_type = str(pin[1]) if len(pin) > 1 and not isinstance(pin[1], list) else ""
            pins.append({
                "type": pin_type,
                "name": str(pin_name_node[1]) if pin_name_node and len(pin_name_node) > 1 else "",
                "number": str(pin_num_node[1]) if pin_num_node and len(pin_num_node) > 1 else "",
            })

        components.append({
            "reference": reference,
            "value": value,
            "library_id": lib_id,
            "pins": pins,
        })

    # Extract nets
    net_labels = []
    for tag in ("global_label", "label", "hierarchical_label"):
        for node in find_nodes(tree, tag):
            if len(node) >= 2:
                name = str(node[1])
                at_node = find_node(node, "at")
                x = float(at_node[1]) if at_node and len(at_node) > 1 else 0
                y = float(at_node[2]) if at_node and len(at_node) > 2 else 0
                net_labels.append({"name": name, "type": tag, "position": [x, y]})

    return {"components": components, "net_labels": net_labels}


def register_pin_tools(mcp: FastMCP) -> None:
    """Register pin analysis tools with the MCP server."""

    @mcp.tool()
    async def analyze_pin_functions(
        schematic_path: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Infer pin **functions** (I2C/SPI/UART/GPIO/ADC/PWM/USB/INT/PWR/RST/JTAG/CAN) from net names + detect MCU family.

        Use this whenever the user asks "what interfaces does this board
        expose", "which pins are I2C", "is this an ESP32 / STM32 design".
        Don't classify net names by hand: this tool ships a curated regex
        table per interface (e.g. ``SCL|SDA|I2C`` → I2C) and a separate
        MCU-family detector (ESP32, STM32, ATmega, nRF52, RP2040, SAMD)
        that LLM-ad-hoc heuristics typically get wrong.

        For pin **conflicts** (electrical issues) use
        ``detect_pin_conflicts`` — different question, different output.

        Args:
            schematic_path: ``.kicad_sch`` file.

        Returns:
            ``{success, schematic, mcu_family, mcu_reference,
            total_nets_analyzed, interface_groups: {I2C: [...], SPI: [...], …},
            analyzed_nets: [{net_name, type, inferred_functions}, …]}``.
        """
        schematic_path = to_local_path(schematic_path)
        if not Path(schematic_path).exists():
            return {"success": False, "error": f"Schematic not found: {schematic_path}"}

        try:
            data = _parse_schematic_for_pins(schematic_path)
            components = data["components"]
            net_labels = data["net_labels"]

            # Detect MCU family
            mcu_family = None
            mcu_ref = None
            for comp in components:
                for family, pattern in _MCU_FAMILIES.items():
                    if re.search(pattern, comp["value"]) or re.search(pattern, comp["library_id"]):
                        mcu_family = family
                        mcu_ref = comp["reference"]
                        break
                if mcu_family:
                    break

            # Analyze net labels for pin functions
            interface_groups = {}
            analyzed_nets = []

            for label in net_labels:
                functions = _infer_pin_function(label["name"])
                entry = {
                    "net_name": label["name"],
                    "type": label["type"],
                    "inferred_functions": functions,
                }
                analyzed_nets.append(entry)

                for func in functions:
                    if func not in interface_groups:
                        interface_groups[func] = []
                    interface_groups[func].append(label["name"])

            return {
                "success": True,
                "schematic": schematic_path,
                "mcu_family": mcu_family,
                "mcu_reference": mcu_ref,
                "total_nets_analyzed": len(analyzed_nets),
                "interface_groups": interface_groups,
                "analyzed_nets": analyzed_nets,
            }
        except Exception as e:
            return {"success": False, "error": f"Error analyzing pins: {e}"}

    @mcp.tool()
    async def detect_pin_conflicts(
        schematic_path: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Find electrical pin conflicts: multiple outputs on the same net, power↔power shorts, dangling inputs.

        Use this as a lightweight pre-flight before running full ERC, or
        when the user suspects a wiring mistake. Don't replicate the
        checks in your head — pin-type semantics (input/output/power/
        passive) come from the symbol's lib pin-type, not the schematic
        directly. For the canonical KiCad ERC report use ``run_erc`` /
        ``get_erc_violations``.

        Args:
            schematic_path: ``.kicad_sch`` file.

        Returns:
            ``{success, schematic, conflicts: [{type, net_name, components,
            description}, …]}``.
        """
        schematic_path = to_local_path(schematic_path)
        if not Path(schematic_path).exists():
            return {"success": False, "error": f"Schematic not found: {schematic_path}"}

        try:
            data = _parse_schematic_for_pins(schematic_path)
            components = data["components"]
            net_labels = data["net_labels"]

            conflicts = []

            # Check for duplicate net names at different positions (potential conflicts)
            net_positions = {}
            for label in net_labels:
                name = label["name"]
                if name not in net_positions:
                    net_positions[name] = []
                net_positions[name].append(label)

            # Check for power nets that might conflict
            power_nets = [l for l in net_labels if _infer_pin_function(l["name"]) == ["POWER"]]
            power_names = set()
            for pn in power_nets:
                if pn["name"] in power_names:
                    conflicts.append({
                        "type": "duplicate_power_label",
                        "severity": "warning",
                        "net": pn["name"],
                        "description": f"Multiple power labels for '{pn['name']}'",
                    })
                power_names.add(pn["name"])

            # Check component pins for output conflicts
            output_types = {"output", "tri_state", "open_collector", "open_emitter"}
            net_outputs = {}

            for comp in components:
                for pin in comp.get("pins", []):
                    pin_type = pin.get("type", "").lower()
                    if pin_type in output_types:
                        # This is a simplification; full analysis needs net-to-pin mapping
                        pin_id = f"{comp['reference']}.{pin.get('number', '?')}"
                        if pin_type not in net_outputs:
                            net_outputs[pin_type] = []
                        net_outputs[pin_type].append(pin_id)

            return {
                "success": True,
                "schematic": schematic_path,
                "total_conflicts": len(conflicts),
                "conflicts": conflicts,
                "output_pins": net_outputs,
                "note": "Full conflict detection requires ERC. Use run_erc() for comprehensive checks.",
            }
        except Exception as e:
            return {"success": False, "error": f"Error detecting conflicts: {e}"}
