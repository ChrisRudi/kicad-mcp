# SPDX-License-Identifier: GPL-3.0-or-later
"""Fab preferred-parts tool — flag R/C that could use a no-load-fee fab part.

Assemblers charge a per-type feeder load fee for parts outside their in-house
library (JLCPCB Basic vs Extended, Seeed OPL, …). This tool maps each board R/C
value+package to the fab's preferred part and estimates the fee you'd save by
pinning them. Provider-agnostic: the ``provider`` arg selects a dated snapshot
from the registry in ``utils/fab_parts``. Reads only; proposes, does not edit.
Reuses the shared ``pcb_board_parse`` reader (footprint id → package).
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text
from kicad_mcp.utils import bom_consolidate, fab_parts
from kicad_mcp.utils.pcb_board_parse import parse_pcb_footprints
from kicad_mcp.utils.path_env import to_local_path


def register_fab_parts_tools(mcp: FastMCP) -> None:
    """Register the fab preferred-parts tool with the MCP server."""

    @mcp.tool()
    def suggest_preferred_parts(pcb_path: str, provider: str = "jlcpcb",
                                refs: str = "") -> dict[str, Any]:
        """Flag R/C that could use a fab's no-load-fee preferred part — saves assembly cost.

        Assemblers keep an in-house parts library and add a one-time feeder load
        fee per distinct type outside it (JLCPCB Basic vs Extended, etc.). This
        maps each board resistor/capacitor (value + SMD package) to the fab's
        preferred part and estimates the fee saved by pinning them. Pairs with
        ``consolidate_bom`` — consolidate values first, then map to preferred
        parts. Reads only. Use before ordering assembly. Not a KiCad feature —
        fab catalogs/fees are external manufacturing knowledge.

        The snapshot is curated seed coverage with a date + disclaimer (returned
        in the result), not the live catalog — verify stock before ordering.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file (WSL or Windows path).
            provider: Fab key (default ``jlcpcb``). See the ``available_providers``
                field in the result for registered fabs.
            refs: Optional comma-separated reference list (``"R1,C7"``) to scope to
                the current selection; empty = whole board.

        Returns:
            ``{success, report: {provider, tier_name, snapshot_date, disclaimer,
            distinct_types, types_with_preferred, potential_saving_usd, types:
            [{cls, value, package, has_preferred, part, refs, count}]},
            available_providers, skipped}``. On error: ``{success: False, error}``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if provider not in fab_parts.PROVIDERS:
            return {"success": False,
                    "error": f"unknown provider {provider!r}; available: "
                             f"{fab_parts.provider_keys()}"}
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
            package = fab_parts.extract_package(fp.get("fpid", ""))
            if si is None or not package:
                skipped.append({"ref": ref, "value": fp.get("value", ""),
                                "reason": "unparsable value" if si is None
                                          else "no SMD package in footprint id"})
                continue
            items.append({"ref": ref, "cls": cls, "si": si, "package": package})

        try:
            report = fab_parts.suggest(items, provider)
        except (OSError, ValueError, KeyError) as exc:
            return {"success": False,
                    "error": f"could not load provider snapshot: {exc}"}
        return {"success": True, "report": report,
                "available_providers": fab_parts.provider_keys(),
                "skipped": skipped}
