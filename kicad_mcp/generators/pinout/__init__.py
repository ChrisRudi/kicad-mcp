# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinout-pipeline — symbol search → datasheet pinout validator.

Pure, headless, unit-testable logic that verifies the pinout of a KiCad
``.kicad_sym`` symbol independently against a datasheet's pinout table
(pin number, pin name, electrical type). It catches the classic failure
classes "pins swapped", "wrong package variant" and "exposed-pad / EP
number wrong" that a per-IC schematic review cannot.

This is deliberately a *separate* mechanism from
``review_ic_against_datasheet`` (review_tools): that one renders a
``.kicad_sch`` + datasheet image and asks an LLM. Here the comparison
side is the ``.kicad_sym`` file, the datasheet side is a parsed text
pinout table, and the diff is a deterministic three-field Python compare.

Public surface (the pure core functions the MCP wrappers + CLI call):

* :func:`extract_symbol_pins` (symbol_pins) — symbol → pin list.
* :func:`extract_datasheet_pins` (datasheet_pins) — PDF tables → pinout.
* :func:`map_datasheet_type` / :func:`normalize_pin_name` (type_map).
* :func:`diff_pinout` (diff) — strict three-field diff.
* :func:`search_symbol_candidates` (search) — ranked local-library search.
"""
from __future__ import annotations

from .symbol_pins import extract_symbol_pins
from .datasheet_pins import extract_datasheet_pins
from .type_map import map_datasheet_type, normalize_pin_name
from .diff import diff_pinout
from .search import search_symbol_candidates

__all__ = [
    "extract_symbol_pins",
    "extract_datasheet_pins",
    "map_datasheet_type",
    "normalize_pin_name",
    "diff_pinout",
    "search_symbol_candidates",
]
