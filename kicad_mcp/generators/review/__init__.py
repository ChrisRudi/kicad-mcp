# SPDX-License-Identifier: GPL-3.0-or-later
"""Datasheet-vs-implementation review helpers (Layer R).

Submodules:

* ``_svg_crop`` — re-render a portion of a ``kicad-cli sch export svg``
  output as a PNG cropped to a schematic-mm bounding box.
* ``_pdf_raster`` — rasterise one page of a datasheet PDF to PNG.
* ``_pin_check`` — cross-check symbol-pin numbers against footprint pads.
* ``_brief`` — render the human-readable Markdown review brief from the
  structured payload dict that ``review_ic_against_datasheet`` returns.

The orchestrating MCP tools live in ``kicad_mcp/tools/review_tools.py``.
"""
