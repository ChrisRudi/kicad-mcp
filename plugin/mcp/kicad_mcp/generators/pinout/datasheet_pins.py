# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse a datasheet's pinout table into a normalised pin list.

Hybrid extraction, deterministic-first:

  1. ``pdfplumber`` tables (via :mod:`generators.circuit_block._pdf_extract`)
     are scanned for a pin-function table (a header row that names a pin
     *number* column, a pin *name* column, and optionally a *type* column).
  2. The deterministic result is only handed to an optional ``llm_extract``
     hook when it looks unreliable: the pin count disagrees with the
     expected count, or the pin numbers are not a clean ``1..N`` run /
     contain duplicates. The hook is ``extract(pdf_path, pages) -> dict``
     and defaults to ``None`` (best-effort, flagged, never a hard abort).

No LLM call lives here — ``llm_extract`` is an injected, swappable hook so
the core stays pure and unit-testable.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ._circuit_block_compat import extract_tables_safe
from .type_map import map_datasheet_type

# Header keywords that identify each column role.
_NUM_HEADERS = ("pin", "pin#", "pin no", "pin number", "no", "no.", "number", "pad")
_NAME_HEADERS = ("name", "symbol", "signal", "pin name", "mnemonic")
_TYPE_HEADERS = ("type", "i/o", "io", "dir", "direction", "function type")


def _norm_header(cell: str) -> str:
    return re.sub(r"\s+", " ", (cell or "").strip().lower())


def _classify_columns(header: list[str]) -> dict[str, int] | None:
    """Map header roles → column index. Requires at least num + name."""
    roles: dict[str, int] = {}
    for idx, raw in enumerate(header):
        h = _norm_header(raw)
        if not h:
            continue
        if "num" not in roles and h in _NUM_HEADERS:
            roles["num"] = idx
        elif "name" not in roles and any(h == k or h.endswith(k) for k in _NAME_HEADERS):
            roles["name"] = idx
        elif "type" not in roles and h in _TYPE_HEADERS:
            roles["type"] = idx
    if "num" in roles and "name" in roles:
        return roles
    return None


def _rows_to_pins(rows: list[list[str]], roles: dict[str, int]) -> list[dict[str, str]]:
    """Turn data rows into pin dicts using the classified column roles."""
    pins: list[dict[str, str]] = []
    ncol = max(roles.values()) + 1
    for row in rows:
        if len(row) < ncol:
            continue
        num = (row[roles["num"]] or "").strip()
        name = (row[roles["name"]] or "").strip()
        if not num and not name:
            continue
        type_raw = (
            (row[roles["type"]] or "").strip() if "type" in roles else ""
        )
        pins.append({"num": num, "name": name, "type_raw": type_raw})
    return pins


def _find_pinout_table(tables: list[dict[str, Any]]) -> list[dict[str, str]] | None:
    """Scan extracted tables; return pins from the first that classifies."""
    for tbl in tables:
        rows = tbl.get("rows") or []
        if len(rows) < 2:
            continue
        roles = _classify_columns(rows[0])
        if roles is None:
            continue
        pins = _rows_to_pins(rows[1:], roles)
        if pins:
            return pins
    return None


def _classify_pins(pins: list[dict[str, str]]) -> tuple[
    list[dict[str, str]], list[dict[str, str]]
]:
    """Split pins into classified (type mapped) and unclassifiable lists.

    A missing or unrecognised type cell yields ``type=None`` and the pin is
    additionally surfaced in ``unclassifiable`` (strict default — no silent
    pass).
    """
    classified: list[dict[str, str]] = []
    unclassifiable: list[dict[str, str]] = []
    for p in pins:
        mapped = map_datasheet_type(p.get("type_raw", ""))
        rec = {
            "num": p["num"],
            "name": p["name"],
            "type": mapped,
            "type_raw": p.get("type_raw", ""),
        }
        classified.append(rec)
        if mapped is None:
            unclassifiable.append({
                "num": p["num"], "name": p["name"], "type_raw": p.get("type_raw", ""),
            })
    return classified, unclassifiable


def _numbers_are_clean(pins: list[dict[str, str]], expected: int) -> bool:
    """True when pin numbers form a non-duplicated, gapless ``1..N`` run that
    matches ``expected`` (when expected > 0). BGA / alphanumeric pads (``A1``,
    ``EP``) are treated as clean — they are not expected to be ``1..N``."""
    nums = [p["num"].strip() for p in pins if p["num"].strip()]
    if len(nums) != len(set(nums)):
        return False  # duplicates
    if expected and len(nums) != expected:
        return False
    int_nums = []
    for n in nums:
        if n.isdigit():
            int_nums.append(int(n))
    # If everything is numeric, require a gapless 1..N run.
    if int_nums and len(int_nums) == len(nums):
        if sorted(int_nums) != list(range(1, len(int_nums) + 1)):
            return False
    return True


def extract_datasheet_pins(
    pdf_path: str,
    pages: list[int] | None = None,
    llm_extract: Callable[[str, list[int]], dict] | None = None,
    expected_pin_count: int = 0,
) -> dict[str, Any]:
    """Extract a normalised pinout from a datasheet PDF.

    Args:
        pdf_path: Local filesystem path to the datasheet PDF.
        pages: 1-based page list to scan, or ``None`` for the whole document.
        llm_extract: Optional fallback hook ``extract(pdf_path, pages) ->
            dict`` invoked only when the deterministic result looks
            unreliable. Must return ``{"pins": [{num, name, type|type_raw}]}``.
            ``None`` (default) disables the fallback.
        expected_pin_count: When > 0, used by the fallback trigger to detect a
            pin-count mismatch.

    Returns:
        ``{success, source:"pdfplumber"|"llm", pins:[{num,name,type,type_raw}],
        unclassifiable:[...], fallback_used:bool}`` on success, else
        ``{success: False, error}``.
    """
    res = extract_tables_safe(pdf_path, pages)
    if not res.get("success"):
        return {"success": False, "error": res.get("error", "PDF extraction failed")}

    det_pins = _find_pinout_table(res.get("tables") or [])
    fallback_used = False
    source = "pdfplumber"

    # Fallback trigger: no table found, OR numbers not clean / count mismatch.
    need_fallback = (
        det_pins is None
        or not _numbers_are_clean(det_pins, expected_pin_count)
    )
    if need_fallback and llm_extract is not None:
        try:
            llm_res = llm_extract(pdf_path, pages or [])
        except Exception as exc:  # noqa: BLE001 — hook may be anything
            return {
                "success": False,
                "error": f"llm_extract hook failed: {exc}",
            }
        llm_pins_raw = (llm_res or {}).get("pins") or []
        det_pins = [
            {
                "num": str(p.get("num", "")).strip(),
                "name": str(p.get("name", "")).strip(),
                "type_raw": str(p.get("type_raw", p.get("type", ""))).strip(),
            }
            for p in llm_pins_raw
        ]
        fallback_used = True
        source = "llm"
    elif det_pins is None:
        # No table and no usable fallback — best-effort empty, flagged.
        det_pins = []

    classified, unclassifiable = _classify_pins(det_pins)
    return {
        "success": True,
        "source": source,
        "pins": classified,
        "unclassifiable": unclassifiable,
        "fallback_used": fallback_used,
    }
