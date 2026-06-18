# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict three-field pinout diff (number, name, electrical type).

Joins the symbol-side pin list against the datasheet-side pin list by pin
*number* (string compare — BGA ``A1`` / EP ``"29"``/``"EP"`` are never
coerced to int). Each joined pin is compared on the normalised name and the
mapped electrical type. The result mirrors the row-status vocabulary used by
``review/_pin_check`` but is a pure, both-sides-structured compare.

Status vocabulary per row:
    match | name_mismatch | type_mismatch | unclassifiable
    | missing_in_symbol | missing_in_datasheet

``match`` (top-level) is True only when *every* joined pin is ``match`` and
neither side has a leftover pin.
"""
from __future__ import annotations

from typing import Any

from .type_map import normalize_pin_name, map_datasheet_type, EP_TOKENS


def _index_by_num(pins: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in pins or []:
        num = str(p.get("num", "")).strip()
        if num and num not in out:
            out[num] = p
    return out


def _ep_aliases(num: str, name: str) -> set[str]:
    """Numbers that should be considered equivalent to this pin when it is the
    exposed pad. Lets a symbol that labels its EP ``"29"`` match a datasheet
    that labels it ``"EP"`` (and vice-versa)."""
    aliases = {num}
    if name and normalize_pin_name(name) in {normalize_pin_name(t) for t in EP_TOKENS}:
        aliases.update(EP_TOKENS)
    if num.upper() in EP_TOKENS:
        aliases.update(EP_TOKENS)
    return aliases


def diff_pinout(
    symbol_pins: list[dict[str, Any]],
    datasheet_pins: list[dict[str, Any]],
    strict: bool = True,
) -> dict[str, Any]:
    """Diff a symbol pin list against a datasheet pin list on all three fields.

    Args:
        symbol_pins: ``[{num, name, type}]`` from :func:`extract_symbol_pins`
            (``type`` is the raw KiCad electrical type, already valid).
        datasheet_pins: ``[{num, name, type, type_raw}]`` from
            :func:`extract_datasheet_pins` (``type`` is the mapped KiCad type
            or ``None`` when unclassifiable).
        strict: When True (default) a row whose datasheet type is
            unclassifiable counts as a failure (status ``unclassifiable``).
            When False such a row is treated as a name-only match if the name
            agrees.

    Returns:
        ``{match, rows:[{num, status, sym:{name,type}, ds:{name,type}}],
        summary:{matched, name_mismatch, type_mismatch, missing_in_symbol,
        missing_in_datasheet, unclassifiable}}``.
    """
    sym_idx = _index_by_num(symbol_pins)
    ds_idx = _index_by_num(datasheet_pins)

    # Build EP alias bridging: if one side has an EP pin under a number the
    # other lacks, try to pair them through the EP token aliases.
    rows: list[dict[str, Any]] = []
    summary = {
        "matched": 0, "name_mismatch": 0, "type_mismatch": 0,
        "missing_in_symbol": 0, "missing_in_datasheet": 0, "unclassifiable": 0,
    }

    matched_ds: set[str] = set()

    def _resolve_ds(num: str, sym_pin: dict[str, Any]) -> tuple[str, dict] | None:
        if num in ds_idx:
            return num, ds_idx[num]
        for alias in _ep_aliases(num, str(sym_pin.get("name", ""))):
            if alias in ds_idx:
                return alias, ds_idx[alias]
        return None

    for num in sym_idx:
        sym_pin = sym_idx[num]
        sym_name_n = normalize_pin_name(str(sym_pin.get("name", "")))
        sym_type = str(sym_pin.get("type", "") or "")

        resolved = _resolve_ds(num, sym_pin)
        if resolved is None:
            rows.append({
                "num": num, "status": "missing_in_datasheet",
                "sym": {"name": sym_pin.get("name", ""), "type": sym_type},
                "ds": None,
            })
            summary["missing_in_datasheet"] += 1
            continue

        ds_num, ds_pin = resolved
        matched_ds.add(ds_num)
        ds_name_n = normalize_pin_name(str(ds_pin.get("name", "")))
        ds_type = ds_pin.get("type", None)

        row = {
            "num": num,
            "sym": {"name": sym_pin.get("name", ""), "type": sym_type},
            "ds": {"name": ds_pin.get("name", ""), "type": ds_type},
        }

        if ds_type is None:
            # Type could not be classified.
            if not strict and sym_name_n == ds_name_n:
                row["status"] = "match"
                summary["matched"] += 1
            else:
                row["status"] = "unclassifiable"
                summary["unclassifiable"] += 1
        elif sym_name_n != ds_name_n:
            row["status"] = "name_mismatch"
            summary["name_mismatch"] += 1
        elif sym_type != ds_type:
            row["status"] = "type_mismatch"
            summary["type_mismatch"] += 1
        else:
            row["status"] = "match"
            summary["matched"] += 1
        rows.append(row)

    # Datasheet pins with no symbol counterpart.
    for num in ds_idx:
        if num in matched_ds:
            continue
        ds_pin = ds_idx[num]
        rows.append({
            "num": num, "status": "missing_in_symbol",
            "sym": None,
            "ds": {"name": ds_pin.get("name", ""), "type": ds_pin.get("type")},
        })
        summary["missing_in_symbol"] += 1

    match = (
        summary["matched"] == len(rows)
        and len(rows) > 0
    )
    return {"match": match, "rows": rows, "summary": summary}


# map_datasheet_type re-exported for callers that pre-map datasheet rows.
__all__ = ["diff_pinout", "map_datasheet_type"]
