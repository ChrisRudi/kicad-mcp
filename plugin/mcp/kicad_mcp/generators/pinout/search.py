# SPDX-License-Identifier: GPL-3.0-or-later
"""Ranked symbol-candidate search across all local KiCad libraries.

This is the search half of the pinout pipeline. Unlike
``kicad_library_index.find_symbol`` (which collapses package-suffix variants
and returns a *single* best lib_id), this keeps *every* matching symbol so a
downstream datasheet diff can disambiguate which package variant is the right
one. Sources scanned:

  * the stock KiCad symbol directory (``share/kicad/symbols/*.kicad_sym``),
  * the user's global ``sym-lib-table`` libraries (custom / third-party),

reusing :mod:`generators.symbol_cache` for library discovery. Each candidate
is enriched with its pin count (via :func:`extract_symbol_pins`, so
``extends`` is resolved) and scored on name proximity (the
``footprint_search_tools._score_name_match`` algorithm, ported to symbol
names) blended with a pin-count closeness factor.

Hard limit: search ranks by name + pin count only — it verifies nothing.
Field-level verification is the diff's job.
"""
from __future__ import annotations

import difflib
import glob
import os
import re

from ..symbol_cache import _find_kicad_sym_dir, _load_user_sym_libs
from .symbol_pins import extract_symbol_pins

# Top-level symbol names; skip the ``Name_unit_style`` body sub-units.
_SYM_DEF_RE = re.compile(r'^\s{1,4}\(symbol "([^"]+)"', re.MULTILINE)


def _is_subunit(name: str) -> bool:
    parts = name.rsplit("_", 2)
    return len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit()


def _iter_local_libs() -> list[tuple[str, str, str]]:
    """Yield ``(lib_name, lib_path, source)`` for every local library.

    ``source`` is ``"stock"`` or ``"user"``. User libs that duplicate a stock
    lib_name are still listed (different path) — the caller keys candidates by
    full lib_id so duplicates surface rather than silently shadow.
    """
    libs: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    sym_dir = _find_kicad_sym_dir()
    if sym_dir:
        for path in sorted(glob.glob(os.path.join(sym_dir, "*.kicad_sym"))):
            lib_name = os.path.splitext(os.path.basename(path))[0]
            key = (lib_name, path)
            if key in seen:
                continue
            seen.add(key)
            libs.append((lib_name, path, "stock"))

    for lib_name, path in _load_user_sym_libs().items():
        key = (lib_name, path)
        if key in seen:
            continue
        seen.add(key)
        libs.append((lib_name, path, "user"))
    return libs


def _name_score(query: str, sym_name: str, lib_id: str) -> float:
    """Port of footprint_search_tools._score_name_match for symbol names."""
    q_lower = query.lower()
    full = lib_id.lower()
    name = sym_name.lower()
    if q_lower == full or q_lower == name:
        return 1.0
    score = 0.0
    if q_lower in full:
        score = max(score, 0.85)
    if q_lower in name:
        score = max(score, 0.9)
    sm = difflib.SequenceMatcher(None, q_lower, name).ratio()
    score = max(score, sm)
    return score


def _pin_factor(pin_count: int, expected: int) -> float:
    """Closeness of pin_count to expected, in [0,1]. Neutral (1.0) when no
    expectation given or the candidate's pin count is unknown."""
    if not expected or pin_count <= 0:
        return 1.0
    if pin_count == expected:
        return 1.0
    delta = abs(pin_count - expected)
    return max(0.0, 1.0 - delta / max(expected, 1))


def search_symbol_candidates(
    query: str,
    expected_pin_count: int = 0,
    limit: int = 10,
) -> list[dict]:
    """Return ranked symbol candidates from all local libraries.

    Args:
        query: Part name / partial name to search for (e.g. ``"DRV8313"``).
        expected_pin_count: When > 0, candidates are filtered to this exact
            pin count and the count also feeds the ranking factor.
        limit: Maximum number of candidates to return.

    Returns:
        A list of ``{lib_id, score, pin_count, source, footprint_hint?}``
        dicts sorted by descending score. Empty list when nothing matches
        (or the library index is empty).
    """
    q = (query or "").strip()
    if not q:
        return []
    q_upper = q.upper()

    candidates: list[dict] = []
    for lib_name, lib_path, source in _iter_local_libs():
        try:
            with open(lib_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue
        for m in _SYM_DEF_RE.finditer(content):
            sym_name = m.group(1)
            if _is_subunit(sym_name):
                continue
            up = sym_name.upper()
            if q_upper not in up and up not in q_upper:
                # Allow fuzzy only when reasonably similar to avoid flooding.
                if difflib.SequenceMatcher(None, q.lower(), sym_name.lower()).ratio() < 0.6:
                    continue
            lib_id = f"{lib_name}:{sym_name}"
            info = extract_symbol_pins(lib_path, sym_name)
            pin_count = info.get("pin_count", 0) if info.get("success") else 0
            if expected_pin_count and pin_count != expected_pin_count:
                continue
            score = _name_score(q, sym_name, lib_id) * _pin_factor(
                pin_count, expected_pin_count
            )
            candidates.append({
                "lib_id": lib_id,
                "score": round(score, 4),
                "pin_count": pin_count,
                "source": source,
            })

    candidates.sort(key=lambda c: (-c["score"], c["lib_id"]))
    return candidates[:limit]
