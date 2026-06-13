# SPDX-License-Identifier: GPL-3.0-or-later
"""Rasterise one page of a datasheet PDF to PNG.

Purpose
-------
``review_ic_against_datasheet`` needs the **reference image** of a chip's
application schematic at a specific datasheet page. ``pdfplumber`` already
ships with the project as the optional ``[pdf]`` extra (also used by
``circuit_block`` for table extraction); its ``page.to_image()`` produces a
PIL image we can write straight to disk.

Inputs
------
* ``pdf_path`` — datasheet PDF.
* ``page_1based`` — 1-based page index, matching the spec's ``datasheet_page``.
* ``output_png`` — destination PNG path (parent dir is created).
* ``dpi`` — render resolution; default 300 (spec).

Outputs
-------
``{success, output_path, page, dpi}`` or ``{success: False, error}``.

Dependencies
------------
``pdfplumber`` (lazy-imported; reuses the install-hint pattern from
``generators/circuit_block/_pdf_extract.py``). Pillow rides along with
pdfplumber so no extra dep is needed.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_INSTALL_HINT = (
    "pdfplumber is required for review_ic_against_datasheet. "
    "Install with:  pip install 'kicad-mcp[pdf]'   (or: pip install pdfplumber)."
)


def _import_pdfplumber():
    try:
        import pdfplumber  # noqa: WPS433  intentional optional import
        return pdfplumber
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc


def rasterize_pdf_page(
    pdf_path: str,
    page_1based: int,
    output_png: str,
    dpi: int = 300,
) -> dict[str, Any]:
    """Render one PDF page to a PNG file.

    Page numbers outside the document are returned as
    ``{"success": False, "error": ...}`` rather than raising, so the caller
    can decide whether the whole review run should fail or continue with a
    warning.
    """
    if not os.path.isfile(pdf_path):
        return {"success": False, "error": f"PDF not found: {pdf_path}"}

    try:
        pdfplumber = _import_pdfplumber()
    except ImportError as exc:
        return {"success": False, "error": str(exc)}

    os.makedirs(os.path.dirname(os.path.abspath(output_png)) or ".", exist_ok=True)

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            if page_1based < 1 or page_1based > total:
                return {
                    "success": False,
                    "error": (
                        f"Page {page_1based} out of range (PDF has {total} pages)"
                    ),
                    "page_count": total,
                }
            page = pdf.pages[page_1based - 1]
            img = page.to_image(resolution=dpi)
            img.save(output_png, format="PNG")
    except Exception as exc:  # pragma: no cover - pdfplumber failure modes
        return {"success": False, "error": f"PDF rasterise failed: {exc}"}

    return {
        "success": True,
        "output_path": output_png,
        "page": page_1based,
        "dpi": dpi,
    }
