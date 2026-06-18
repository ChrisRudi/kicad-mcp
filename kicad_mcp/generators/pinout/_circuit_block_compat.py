# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe adapter over :func:`generators.circuit_block._pdf_extract.extract_tables`.

``extract_tables`` raises (``ImportError`` if pdfplumber is missing,
``FileNotFoundError`` if the PDF is gone). The pinout pipeline contract is
"return a dict, never raise", so this thin wrapper turns those into the
structured ``{success: False, error}`` shape. It is also the single seam the
datasheet tests monkeypatch, which keeps :mod:`datasheet_pins` free of any
pdfplumber import.
"""
from __future__ import annotations

from typing import Any


def extract_tables_safe(
    pdf_path: str, pages: list[int] | None = None
) -> dict[str, Any]:
    """Call ``extract_tables`` and convert any exception to a result dict.

    Returns the upstream ``{success, page_count, tables}`` on success, or
    ``{success: False, error}`` when pdfplumber is absent or the file is
    missing.
    """
    try:
        from ..circuit_block._pdf_extract import extract_tables
        return extract_tables(pdf_path, pages)
    except Exception as exc:  # noqa: BLE001 — surface friendly error, never raise
        return {"success": False, "error": str(exc)}
