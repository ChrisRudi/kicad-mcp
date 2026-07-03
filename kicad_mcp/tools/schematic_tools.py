# SPDX-License-Identifier: GPL-3.0-or-later
"""
Schematic analysis tools for KiCad .kicad_sch files.

Parses S-expression format directly, no external dependencies.
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
from pathlib import Path
import re
from typing import Any

from fastmcp import Context, FastMCP

from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.sexpr_parser import find_node, find_nodes


from kicad_mcp.utils.schematic_parse import (
    extract_components as _extract_components,
    parse_schematic as _parse_schematic,
)


def _extract_nets(tree: list) -> list[dict[str, str]]:
    """Extract net labels (global_label, label, hierarchical_label) from schematic."""
    nets = []

    for tag in ("global_label", "label", "hierarchical_label"):
        for node in find_nodes(tree, tag):
            if len(node) >= 2:
                name = str(node[1])
                nets.append({"name": name, "type": tag})

    return nets


def _extract_sheets(tree: list) -> list[dict[str, str]]:
    """Extract hierarchical sheet references."""
    sheets = []
    for sheet in find_nodes(tree, "sheet"):
        props = {}
        for prop_node in find_nodes(sheet, "property"):
            if len(prop_node) >= 3:
                props[str(prop_node[1])] = str(prop_node[2])

        name = props.get("Sheetname", "unknown")
        filename = props.get("Sheetfile", "")
        sheets.append({"name": name, "file": filename})

    return sheets


def _extract_title_block(tree: list) -> dict[str, str]:
    """Extract title block info."""
    tb = find_node(tree, "title_block")
    if not tb:
        return {}

    result = {}
    for key in ("title", "date", "rev", "company"):
        node = find_node(tb, key)
        if node and len(node) > 1:
            result[key] = str(node[1])

    return result


def register_schematic_tools(mcp: FastMCP) -> None:
    """Register schematic analysis tools with the MCP server."""

    @mcp.tool()
    async def list_schematic_components(
        schematic_path: str,
        filter_type: str = "",
        filter_value: str = "",
        include_pins: bool = False,
        include_properties: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List every symbol instance in a ``.kicad_sch`` with reference, value, lib_id, footprint, position, unit, DNP, in_bom.

        Use this whenever the user asks for a BOM-like overview, "which
        opamps does this schematic use", "show me all caps", or you need
        per-symbol context before a patch operation. **Don't** ``Read`` the
        schematic and regex over ``(symbol …)`` blocks: KiCad-10 uses
        nested S-expressions for properties + pins, your regex will miss
        multi-line ``(at …)`` and DNP/in-bom flags.

        Sibling tools: deep dive on one ref → ``get_symbol_details``;
        regex/wildcard → ``search_symbols``; high-level summary →
        ``get_schematic_info``.

        Args:
            schematic_path: ``.kicad_sch`` file (WSL or Windows path).
            filter_type: Reference prefix (case-insensitive prefix match,
                e.g. ``"R"`` returns R1/R2/…/R99 only).
            filter_value: Substring matched against ``value`` (case-insensitive).
            include_pins: Add a lean ``pins: [{number}]`` list per symbol
                (off by default — saves tokens on large schematics).
            include_properties: Add a ``properties`` dict of the EXTRA
                (non Reference/Value/Footprint, non-empty) properties per
                symbol (off by default).

        Returns:
            ``{success, schematic, count, components: [{reference, value,
            library_id, footprint, position, unit, dnp, in_bom}, …]}``.
            ``pins`` / ``properties`` are added per symbol only when the
            respective ``include_*`` flag is set.
        """
        schematic_path = to_local_path(schematic_path)
        if not Path(schematic_path).exists():
            return {"success": False, "error": f"Schematic not found: {schematic_path}"}

        try:
            tree = _parse_schematic(schematic_path)
            components = _extract_components(
                tree, include_pins=include_pins,
                include_properties=include_properties)

            if filter_type:
                components = [c for c in components if c["reference"].startswith(filter_type.upper())]

            if filter_value:
                components = [c for c in components if filter_value.lower() in c["value"].lower()]

            return {
                "success": True,
                "schematic": schematic_path,
                "count": len(components),
                "components": components,
            }
        except Exception as e:
            return {"success": False, "error": f"Error parsing schematic: {e}"}

    @mcp.tool()
    async def get_symbol_details(
        schematic_path: str,
        reference: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Look up one symbol by exact reference and return everything KiCad knows about it.

        Use this when the user asks "what is U7", "what's the value of R12",
        or you need a target's pinout / footprint / properties before
        patching the schematic. Don't try to grep the file for the ref —
        properties on a symbol can be in arbitrary order and may include
        custom fields the user added (which a regex would skip).

        For broad "find all components like X" queries use
        ``search_symbols``; for an overview use ``list_schematic_components``.

        Args:
            schematic_path: ``.kicad_sch`` file.
            reference: Exact reference designator (case-sensitive,
                e.g. ``"U7"``, ``"C_LED9"``).

        Returns:
            ``{success, component: {reference, value, library_id, footprint,
            position, unit, dnp, in_bom, properties, pins}}`` or
            ``{success: False, error}`` if the ref is missing.
        """
        schematic_path = to_local_path(schematic_path)
        if not Path(schematic_path).exists():
            return {"success": False, "error": f"Schematic not found: {schematic_path}"}

        try:
            tree = _parse_schematic(schematic_path)
            # Deep-dive: keep the full per-symbol detail (pins + extra props).
            components = _extract_components(
                tree, include_pins=True, include_properties=True)

            for comp in components:
                if comp["reference"] == reference:
                    return {"success": True, "component": comp}

            return {"success": False, "error": f"Component '{reference}' not found"}
        except Exception as e:
            return {"success": False, "error": f"Error parsing schematic: {e}"}

    @mcp.tool()
    async def search_symbols(
        schematic_path: str,
        pattern: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Regex-search symbol instances across reference, value, and library_id.

        Use this for "find all TMC2209 drivers", "where are the LEDs",
        "show me anything matching ^C_U.*VS"-style queries. Don't ``grep``
        the schematic file: this tool searches three semantic fields
        (reference, value, library_id) and returns full structured records,
        not raw lines.

        For exact-ref lookups use ``get_symbol_details`` (faster). Result
        list is capped at 50 — narrow the pattern if you hit the cap.

        Args:
            schematic_path: ``.kicad_sch`` file.
            pattern: Python regex (case-insensitive). Examples:
                ``"TMC22\\d+"``, ``"^R[12]\\d$"``, ``"USB|FT232"``.

        Returns:
            ``{success, pattern, count, components: [...max 50]}``.
        """
        schematic_path = to_local_path(schematic_path)
        if not Path(schematic_path).exists():
            return {"success": False, "error": f"Schematic not found: {schematic_path}"}

        try:
            tree = _parse_schematic(schematic_path)
            components = _extract_components(tree)
            regex = re.compile(pattern, re.IGNORECASE)

            matches = [
                c for c in components
                if regex.search(c["reference"])
                or regex.search(c["value"])
                or regex.search(c["library_id"])
            ]

            return {
                "success": True,
                "pattern": pattern,
                "count": len(matches),
                "components": matches[:50],
            }
        except re.error as e:
            return {"success": False, "error": f"Invalid regex pattern: {e}"}
        except Exception as e:
            return {"success": False, "error": f"Error parsing schematic: {e}"}

    @mcp.tool()
    async def get_schematic_info(
        schematic_path: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """High-level snapshot of a ``.kicad_sch``: title-block, totals, sheet hierarchy, component-type histogram.

        Use this as the **first** read on an unfamiliar schematic before
        deep-diving with ``list_schematic_components`` /
        ``analyze_schematic_connections``. Tells you in one call: project
        title/rev/date, total components/nets/sheets, and the reference-prefix
        histogram (``{R: 30, C: 36, U: 8, …}``). Don't reconstruct this from
        list_schematic_components output — this is cheaper and includes the
        title-block KiCad puts in every sheet.

        Args:
            schematic_path: ``.kicad_sch`` file.

        Returns:
            ``{success, schematic, title_block: {title, date, rev, company},
            total_components, total_nets, total_sheets, component_types: {...},
            sheets: [{name, file}, …]}``.
        """
        schematic_path = to_local_path(schematic_path)
        if not Path(schematic_path).exists():
            return {"success": False, "error": f"Schematic not found: {schematic_path}"}

        try:
            tree = _parse_schematic(schematic_path)
            components = _extract_components(tree)
            nets = _extract_nets(tree)
            sheets = _extract_sheets(tree)
            title_block = _extract_title_block(tree)

            # Count by type prefix
            type_counts = {}
            for comp in components:
                prefix = "".join(c for c in comp["reference"] if c.isalpha())
                type_counts[prefix] = type_counts.get(prefix, 0) + 1

            return {
                "success": True,
                "schematic": schematic_path,
                "title_block": title_block,
                "total_components": len(components),
                "total_nets": len(nets),
                "total_sheets": len(sheets),
                "component_types": type_counts,
                "sheets": sheets,
            }
        except Exception as e:
            return {"success": False, "error": f"Error parsing schematic: {e}"}
