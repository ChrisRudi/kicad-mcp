# SPDX-License-Identifier: GPL-3.0-or-later
"""pdfplumber-backed PDF table extractor for datasheet ingestion.

This module is a thin wrapper around ``pdfplumber`` so we can:

  * lazy-import the optional dependency (so the rest of kicad-mcp keeps
    working when ``pdfplumber`` is not installed),
  * surface a friendly install-hint when it is missing,
  * normalise the extracted table cells into the simple list-of-list
    format the Layer-T tools consume.

No LLM calls happen here — the extraction is purely deterministic. The
upstream tool ``extract_circuit_from_pdf`` returns these tables to the
caller, and the orchestrating LLM (Claude in the chat) does the
semantic mapping from "Pin Functions" table → ``pins[]``.
"""
from __future__ import annotations

from typing import Any


_INSTALL_HINT = (
    "pdfplumber is required for extract_pdf_tables / extract_circuit_from_pdf. "
    "Install with:  pip install 'kicad-mcp[pdf]'   (or directly: pip install pdfplumber)."
)


def _import_pdfplumber():
    try:
        import pdfplumber  # noqa: WPS433  intentional optional import
        return pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise ImportError(_INSTALL_HINT) from exc


def extract_tables(
    pdf_path: str, pages: list[int] | None = None
) -> dict[str, Any]:
    """Extract every table found on the requested pages.

    Args:
        pdf_path: Local filesystem path to the datasheet PDF.
        pages: 1-based page list. ``None`` = all pages. Pages beyond
            the document length are silently skipped.

    Returns:
        ``{success, page_count, tables: [{page, index, rows: [[cell,...]]}]}``.

    Raises:
        ImportError: if pdfplumber is not installed (caller should
            forward the friendly error to the user).
        FileNotFoundError: passed through if the file is missing.
    """
    pdfplumber = _import_pdfplumber()

    out_tables: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        wanted: list[int]
        if pages is None:
            wanted = list(range(1, page_count + 1))
        else:
            wanted = [p for p in pages if 1 <= p <= page_count]
        for p_num in wanted:
            page = pdf.pages[p_num - 1]
            try:
                tables = page.extract_tables() or []
            except Exception:  # pragma: no cover - pdfplumber exceptions vary
                tables = []
            for t_idx, raw in enumerate(tables):
                rows: list[list[str]] = []
                for row in raw or []:
                    rows.append([
                        ("" if cell is None else str(cell)).replace("\n", " ").strip()
                        for cell in row
                    ])
                if not rows:
                    continue
                out_tables.append({"page": p_num, "index": t_idx, "rows": rows})
    return {
        "success": True,
        "page_count": page_count,
        "tables": out_tables,
    }


def extract_text_blocks(
    pdf_path: str, pages: list[int] | None = None
) -> dict[str, Any]:
    """Extract per-page text in reading order — used for section detection.

    Returns ``{success, page_count, pages: [{page, text}]}``.
    """
    pdfplumber = _import_pdfplumber()
    page_texts: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        wanted: list[int] = (
            list(range(1, page_count + 1))
            if pages is None
            else [p for p in pages if 1 <= p <= page_count]
        )
        for p_num in wanted:
            try:
                txt = pdf.pages[p_num - 1].extract_text() or ""
            except Exception:  # pragma: no cover
                txt = ""
            page_texts.append({"page": p_num, "text": txt})
    return {
        "success": True,
        "page_count": page_count,
        "pages": page_texts,
    }
