# SPDX-License-Identifier: GPL-3.0-or-later
"""MCP wrappers for the deterministic pinout-validation pipeline.

Three read-only / non-rendering tools built on the pure core in
``kicad_mcp.generators.pinout``:

  * ``search_symbol`` — ranked symbol candidates across all local libraries.
  * ``validate_pinout`` — strict three-field diff of one symbol's pinout
    against a datasheet pinout table.
  * ``match_symbol_to_datasheet`` — the full pipeline: search candidates,
    then rank them by how cleanly each diffs against the datasheet.

These are intentionally distinct from ``review_ic_against_datasheet``
(review_tools): that one renders ``.kicad_sch`` + datasheet images and asks
an LLM to eyeball them. These tools compare a ``.kicad_sym`` file against a
parsed pinout table with a deterministic Python diff — no rendering, no LLM.
"""
from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.path_env import to_local_path


def _parse_pages(pages_json: str) -> list[int] | None:
    """Best-effort parse of a JSON page-list string; ``""`` → None (all)."""
    raw = (pages_json or "").strip()
    if not raw:
        return None
    data = json.loads(raw)
    if isinstance(data, list):
        return [int(p) for p in data if str(p).strip().lstrip("-").isdigit()]
    if isinstance(data, int):
        return [data]
    return None


def register_pinout_tools(mcp: FastMCP) -> None:
    """Register the pinout-pipeline tools (search / validate / match)."""

    @mcp.tool()
    def search_symbol(
        query: str,
        expected_pin_count: int = 0,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search all local KiCad symbol libraries and return ranked candidate symbols by name + pin count.

        Use this when you need to find which ``.kicad_sym`` symbol(s) match a
        part name and you want to *keep every package variant* rather than a
        single best guess — e.g. "find all DRV8313 symbols", "which library
        has an ESP32-S3 with 56 pins". It scans the stock KiCad symbol
        directory plus the user's global ``sym-lib-table`` libraries, enriches
        each hit with its pin count (``extends`` resolved), and ranks by name
        proximity blended with pin-count closeness.

        This is read-only and renders nothing. It ranks by name + pin count
        only and verifies nothing — to confirm a candidate is the correct
        package variant, follow up with ``validate_pinout`` or
        ``match_symbol_to_datasheet``. Unlike ``review_ic_against_datasheet``
        (which prepares LLM review material for an *already-placed* IC), this
        works purely off the symbol libraries.

        Args:
            query: Part name or partial name, e.g. ``"DRV8313"``.
            expected_pin_count: When > 0, filter candidates to this exact pin
                count and weight the ranking by pin-count closeness.
            limit: Maximum number of candidates to return (default 10).

        Returns:
            ``{success, candidates:[{lib_id, score, pin_count, source}]}`` —
            candidates sorted by descending score; empty list when nothing
            matches.
        """
        from kicad_mcp.generators.pinout.search import search_symbol_candidates

        try:
            cands = search_symbol_candidates(query, expected_pin_count, limit)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"search failed: {exc}"}
        return {"success": True, "candidates": cands}

    @mcp.tool()
    def validate_pinout(
        sym_path: str,
        symbol_name: str,
        pdf_path: str,
        pages: str = "",
        strict: bool = True,
    ) -> dict[str, Any]:
        """Strictly diff one KiCad symbol's pinout against a datasheet pinout table (number, name, electrical type).

        Use this when you have a specific symbol and its datasheet and want a
        deterministic, field-level verdict — "does Device:DRV8313 in my lib
        match the datasheet pinout?", "did pins get swapped?", "is the EP
        number right?". It extracts the symbol's pins (resolving ``extends``),
        extracts the datasheet pinout via pdfplumber tables, normalises names
        and maps types to KiCad electrical types, then joins by pin number and
        compares all three fields.

        Read-only and renders nothing. Prefer this over
        ``review_ic_against_datasheet`` when you want a precise pass/fail diff
        rather than rendered images for an LLM to eyeball, and when the
        comparison side is a ``.kicad_sym`` file (not a placed ``.kicad_sch``
        instance). Use ``match_symbol_to_datasheet`` instead when you do not
        yet know which symbol / package variant is correct.

        Args:
            sym_path: Path to the ``.kicad_sym`` library file.
            symbol_name: Bare symbol name inside the library (no ``Lib:``).
            pdf_path: Path to the datasheet PDF.
            pages: JSON list of 1-based page numbers to scan, e.g. ``"[5,6]"``;
                empty string scans the whole document.
            strict: When True (default) an unclassifiable datasheet type fails
                the affected pin; when False, a name-only match passes it.

        Returns:
            ``{success, symbol, datasheet_source, fallback_used, diff}`` where
            ``diff`` is ``{match, rows, summary}``; ``{success: False, error}``
            on a missing file or parse failure.
        """
        sym_path = to_local_path(sym_path)
        pdf_path = to_local_path(pdf_path)
        if not os.path.isfile(sym_path):
            return {"success": False, "error": f"Symbol file not found: {sym_path}"}
        if not os.path.isfile(pdf_path):
            return {"success": False, "error": f"Datasheet PDF not found: {pdf_path}"}
        try:
            page_list = _parse_pages(pages)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"Invalid JSON for pages: {exc}"}

        from kicad_mcp.generators.pinout.symbol_pins import extract_symbol_pins
        from kicad_mcp.generators.pinout.datasheet_pins import extract_datasheet_pins
        from kicad_mcp.generators.pinout.diff import diff_pinout

        sym = extract_symbol_pins(sym_path, symbol_name)
        if not sym.get("success"):
            return {"success": False, "error": sym.get("error")}
        ds = extract_datasheet_pins(
            pdf_path, page_list, expected_pin_count=sym.get("pin_count", 0)
        )
        if not ds.get("success"):
            return {"success": False, "error": ds.get("error")}
        result = diff_pinout(sym["pins"], ds["pins"], strict=strict)
        return {
            "success": True,
            "symbol": sym,
            "datasheet_source": ds.get("source"),
            "fallback_used": ds.get("fallback_used"),
            "unclassifiable_count": len(ds.get("unclassifiable", [])),
            "diff": result,
        }

    @mcp.tool()
    def match_symbol_to_datasheet(
        query: str,
        pdf_path: str,
        pages: str = "",
        expected_pin_count: int = 0,
        limit: int = 10,
        strict: bool = True,
    ) -> dict[str, Any]:
        """Find the correct symbol / package variant for a datasheet: search candidates, then rank by how cleanly each diffs against the datasheet pinout.

        Use this when you do not yet know which library symbol is the right
        one — "which DRV8313 variant matches this datasheet?", "disambiguate
        the package variant for this part". It runs ``search_symbol`` to
        gather candidates, extracts the datasheet pinout once, then diffs every
        candidate against it and ranks by fewest deviations (zero = the right
        variant).

        Read-only and renders nothing. Prefer ``validate_pinout`` when you
        already know the exact symbol; prefer ``review_ic_against_datasheet``
        when you want rendered review images for an LLM rather than a
        deterministic per-candidate diff. The datasheet PDF is parsed once and
        reused across all candidates.

        Args:
            query: Part name or partial name to search for, e.g. ``"DRV8313"``.
            pdf_path: Path to the datasheet PDF.
            pages: JSON list of 1-based page numbers to scan, e.g. ``"[5,6]"``;
                empty string scans the whole document.
            expected_pin_count: When > 0, restrict candidates to this pin count.
            limit: Maximum number of candidates to consider (default 10).
            strict: Passed through to the per-candidate diff (default True).

        Returns:
            ``{success, datasheet_source, fallback_used, candidates:[{lib_id,
            score, pin_count, source, diff_summary, match, mismatches}]}``
            sorted best-first; ``{success: False, error}`` on failure.
        """
        pdf_path = to_local_path(pdf_path)
        if not os.path.isfile(pdf_path):
            return {"success": False, "error": f"Datasheet PDF not found: {pdf_path}"}
        try:
            page_list = _parse_pages(pages)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"Invalid JSON for pages: {exc}"}

        from kicad_mcp.generators.pinout.search import search_symbol_candidates
        from kicad_mcp.generators.pinout.symbol_pins import extract_symbol_pins
        from kicad_mcp.generators.pinout.datasheet_pins import extract_datasheet_pins
        from kicad_mcp.generators.pinout.diff import diff_pinout

        cands = search_symbol_candidates(query, expected_pin_count, limit)
        if not cands:
            return {"success": True, "candidates": [], "datasheet_source": None,
                    "fallback_used": False}

        ds = extract_datasheet_pins(pdf_path, page_list, expected_pin_count=expected_pin_count)
        if not ds.get("success"):
            return {"success": False, "error": ds.get("error")}

        # Resolve each candidate's symbol pins from its library path. The
        # search result lib_id is "Lib:Sym"; locate the lib file via the same
        # library enumeration the search used.
        from kicad_mcp.generators.pinout.search import _iter_local_libs
        lib_paths = {name: path for name, path, _src in _iter_local_libs()}

        scored: list[dict[str, Any]] = []
        for c in cands:
            lib_name, _, sym_name = c["lib_id"].partition(":")
            lib_path = lib_paths.get(lib_name)
            if not lib_path:
                continue
            sym = extract_symbol_pins(lib_path, sym_name)
            if not sym.get("success"):
                continue
            result = diff_pinout(sym["pins"], ds["pins"], strict=strict)
            summ = result["summary"]
            mismatches = (
                summ["name_mismatch"] + summ["type_mismatch"]
                + summ["missing_in_symbol"] + summ["missing_in_datasheet"]
                + summ["unclassifiable"]
            )
            scored.append({
                **c,
                "diff_summary": summ,
                "match": result["match"],
                "mismatches": mismatches,
            })

        scored.sort(key=lambda c: (c["mismatches"], -c["score"], c["lib_id"]))
        return {
            "success": True,
            "datasheet_source": ds.get("source"),
            "fallback_used": ds.get("fallback_used"),
            "candidates": scored,
        }
