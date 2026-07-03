# SPDX-License-Identifier: GPL-3.0-or-later
"""BOM-Konsolidierung tool — E-series standardisation to cut feeders/cost.

KiCad has no notion of E-series, feeders or purchase lots. This tool reads the
board's R/C values, snaps near-duplicates to standard E-series values and reports
which lines can be merged (fewer reels at assembly, cheaper build) without moving
any part beyond a safe tolerance. The value math lives in
``utils/bom_consolidate``; this module is only the MCP entry point. It reuses the
shared ``pcb_board_parse`` footprint reader (no re-parse).
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text
from kicad_mcp.utils import bom_consolidate
from kicad_mcp.utils.pcb_board_parse import parse_pcb_footprints
from kicad_mcp.utils.path_env import to_local_path


def register_bom_consolidate_tools(mcp: FastMCP) -> None:
    """Register the BOM-consolidation tool with the MCP server."""

    @mcp.tool()
    def consolidate_bom(pcb_path: str, series: str = "E24",
                        max_shift_pct: float = 5.0,
                        refs: str = "") -> dict[str, Any]:
        """Cut distinct R/C values by snapping near-duplicates to E-series — saves feeders/cost.

        Every distinct resistor/capacitor value is its own BOM line, reel and
        assembly feeder. Boards drift into near-duplicate values (10k, 10.2k,
        9.1k) that do the same job. This proposes merging them onto standard
        E-series values so fewer parts are stocked/placed — without moving any
        part more than ``max_shift_pct``. Reads values only; proposes, does not
        edit. Use this before ordering/assembly to trim cost. Not a KiCad
        feature — E-series/feeder knowledge is external to the netlist.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file (WSL or Windows path).
            series: E-series target: ``E6`` | ``E12`` | ``E24`` (default) |
                ``E48`` | ``E96``. Coarser series consolidate harder.
            max_shift_pct: Never move a part more than this percent (default 5).
                Parts whose nearest standard value is further are reported as
                ``unmergeable`` instead of merged.
            refs: Optional comma-separated reference list (e.g. ``"R1,R2,C7"``)
                to scope to the current selection; empty = whole board.

        Returns:
            ``{success, report: {series, distinct_before, distinct_after,
            feeders_saved, classes: {R: {…merges…}, C: {…}}}, skipped}``. On
            error: ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        try:
            text = get_text(pcb_path)
            parsed = parse_pcb_footprints(text)
        except (OSError, ValueError) as exc:
            return {"success": False, "error": f"could not read board: {exc}"}

        want = {r.strip() for r in refs.split(",") if r.strip()} or None
        items: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for fp in parsed["footprints"]:
            ref = fp["ref"]
            if want is not None and ref not in want:
                continue
            cls = bom_consolidate.ref_class(ref)
            if cls is None:
                continue
            si = bom_consolidate.normalize_value(fp.get("value", ""), cls)
            if si is None:
                skipped.append({"ref": ref, "value": fp.get("value", "")})
                continue
            items.append({"ref": ref, "cls": cls, "si": si})

        report = bom_consolidate.consolidate(items, series=series,
                                             max_shift_pct=max_shift_pct)
        return {"success": True, "report": report, "skipped": skipped}
