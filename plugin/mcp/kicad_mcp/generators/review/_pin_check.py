# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-check schematic-symbol pins against footprint pads.

Purpose
-------
Plausibility gate before a per-IC datasheet review: if the schematic
symbol's pin list and the PCB footprint's pad list disagree on numbers,
the review can't trust either side. Returns a list of warnings rather than
hard errors — a divergent list is informational for the reviewing LLM.

Inputs
------
* ``symbol_pins`` — list of ``{number, name, type, ...}`` dicts as returned
  by ``get_symbol_details(...).component.pins``.
* ``pcb_path`` — optional path to ``.kicad_pcb``; if missing or
  unavailable, the check is skipped (and a single warning is emitted).
* ``reference`` — the IC's reference designator.

Outputs
-------
``{checked: bool, warnings: [str], symbol_pin_numbers: [...],
   footprint_pad_numbers: [...] | None}``

Dependencies
------------
Stdlib only. PCB-pad extraction reuses the existing helper
``_parse_pcb_pads_per_ref`` from ``tools.pcb_patch_tools``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def _pin_number_set(pins: Iterable[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for p in pins or []:
        num = p.get("number") or p.get("num")
        if num is None:
            continue
        out.add(str(num).strip())
    out.discard("")
    return out


def compare_symbol_pins_to_footprint(
    symbol_pins: list[dict[str, Any]],
    pcb_path: str,
    reference: str,
) -> dict[str, Any]:
    """Compare the symbol's pin numbers against the footprint's pad numbers."""
    sym_nums = _pin_number_set(symbol_pins)

    result: dict[str, Any] = {
        "checked": False,
        "warnings": [],
        "symbol_pin_numbers": sorted(sym_nums),
        "footprint_pad_numbers": None,
    }

    if not symbol_pins:
        result["warnings"].append(
            f"Symbol '{reference}' has no pin list — datasheet review will rely on "
            "datasheet pinout only."
        )
        return result

    if not pcb_path or not os.path.isfile(pcb_path):
        result["warnings"].append(
            "No PCB file available — symbol-pin vs footprint-pad consistency not checked."
        )
        return result

    try:
        from kicad_mcp.tools.pcb_patch_tools import _parse_pcb_pads_per_ref  # type: ignore
    except ImportError as exc:  # pragma: no cover - import-time issues
        result["warnings"].append(f"PCB pad parser unavailable: {exc}")
        return result

    try:
        with open(pcb_path, encoding="utf-8") as fh:
            pcb_text = fh.read()
        pcb_map = _parse_pcb_pads_per_ref(pcb_text)
    except Exception as exc:
        result["warnings"].append(f"PCB parse failed: {exc}")
        return result

    if reference not in pcb_map:
        result["warnings"].append(
            f"Reference '{reference}' not placed in PCB yet — pad-consistency skipped."
        )
        return result

    _lib, pad_nums = pcb_map[reference]
    pad_nums_str = {str(p).strip() for p in pad_nums if str(p).strip()}
    result["footprint_pad_numbers"] = sorted(pad_nums_str)
    result["checked"] = True

    missing_in_pcb = sorted(sym_nums - pad_nums_str)
    extra_in_pcb = sorted(pad_nums_str - sym_nums)
    if missing_in_pcb:
        result["warnings"].append(
            f"Symbol pins not present as pads in PCB: {missing_in_pcb}"
        )
    if extra_in_pcb:
        result["warnings"].append(
            f"Footprint pads not present as symbol pins (e.g. EP, mech): {extra_in_pcb}"
        )
    return result
