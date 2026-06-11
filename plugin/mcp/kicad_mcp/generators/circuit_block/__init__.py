# SPDX-License-Identifier: GPL-3.0-or-later
"""Layer T helpers — Spec-driven circuit-block composition.

This package backs the ``circuit_block_tools`` MCP module. It contains:

* ``schema_v1_1.json`` — JSON-Schema for circuit-block specs.
* ``_block_to_patch.py`` — translates a validated spec into a sequence of
  Layer-S tool calls (``add_schematic_symbols``, ``connect_pins``,
  ``add_power_symbols``).
* ``_power_convention.py`` — thin wrapper around ``add_power_symbols`` so
  the power-symbol convention lives in exactly one place.
* ``_pdf_extract.py`` — pdfplumber-based table/section extractor (lazy
  import; raises a friendly hint if the optional dependency is missing).

No Layer-S logic is duplicated here — the modules orchestrate, they do
not re-implement.
"""
from __future__ import annotations

import json
import os

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema_v1_1.json")


def schema_v1_1() -> dict:
    """Return the parsed JSON-Schema as a dict (lazy file read)."""
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def schema_path() -> str:
    """Return the absolute path to the bundled JSON-Schema file."""
    return _SCHEMA_PATH
