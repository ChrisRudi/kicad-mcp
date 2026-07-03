# SPDX-License-Identifier: GPL-3.0-or-later
"""BOM-Konsolidierung — E-series value standardisation to cut distinct parts.

Every distinct R/C value on a board is its own BOM line → its own reel/feeder at
assembly (setup cost) and its own (smaller) purchase lot (worse unit price).
Boards accrete near-duplicate values (10k next to 10.2k next to 9.1k) that do the
same job. This module snaps each value to the nearest **E-series** preferred
value and reports which values can be *merged* — fewer feeders, cheaper build —
without shifting any part more than a safe tolerance.

KiCad has no concept of E-series, feeders, or purchase lots — this is external
manufacturing knowledge on top of its netlist. Pure/stdlib, headless-testable.

The value parser here yields **canonical SI** (ohms / farads) and handles the
infix notation (``4k7`` = 4.7 kΩ, ``4n7`` = 4.7 nF) that the tuple-returning
``component_utils`` parsers mis-read — E-series work needs the exact magnitude.
"""

from __future__ import annotations

import math
import re
from typing import Any

# E-series preferred-value mantissas (one decade). E24 is the usual
# consolidation target for jelly-bean R/C; E6/E12 collapse harder, E96 barely.
_E_SERIES: dict[str, tuple[float, ...]] = {
    "E6": (10, 15, 22, 33, 47, 68),
    "E12": (10, 12, 15, 18, 22, 27, 33, 39, 47, 56, 68, 82),
    "E24": (10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30, 33, 36, 39,
            43, 47, 51, 56, 62, 68, 75, 82, 91),
    "E48": (100, 105, 110, 115, 121, 127, 133, 140, 147, 154, 162, 169, 178,
            187, 196, 205, 215, 226, 237, 249, 261, 274, 287, 301, 316, 332,
            348, 365, 383, 402, 422, 442, 464, 487, 511, 536, 562, 590, 619,
            649, 681, 715, 750, 787, 825, 866, 909, 953),
    "E96": (100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130, 133,
            137, 140, 143, 147, 150, 154, 158, 162, 165, 169, 174, 178, 182,
            187, 191, 196, 200, 205, 210, 215, 221, 226, 232, 237, 243, 249,
            255, 261, 267, 274, 280, 287, 294, 301, 309, 316, 324, 332, 340,
            348, 357, 365, 374, 383, 392, 402, 412, 422, 432, 442, 453, 464,
            475, 487, 499, 511, 523, 536, 549, 562, 576, 590, 604, 619, 634,
            649, 665, 681, 698, 715, 732, 750, 768, 787, 806, 825, 845, 866,
            887, 909, 931, 953, 976),
}

DEFAULT_SERIES = "E24"
DEFAULT_MAX_SHIFT_PCT = 5.0   # never move a part more than this to consolidate

# ref-prefix → component class. Only the two feeder-dominating passive classes.
_CLASS_BY_PREFIX = {"R": "R", "C": "C"}

_DNP_RE = re.compile(r"\b(DNP|DNI|NOFIT|DO_?NOT_?(FIT|POPULATE)|NC|N/?A)\b",
                     re.IGNORECASE)


def ref_class(ref: str) -> str | None:
    """``"R12" -> "R"``, ``"C3" -> "C"``, else ``None`` (only R/C consolidate)."""
    m = re.match(r"([A-Za-z]+)", ref or "")
    if not m:
        return None
    return _CLASS_BY_PREFIX.get(m.group(1).upper())


def normalize_value(value: str, cls: str) -> float | None:
    """Parse a resistor/cap value string to canonical SI (ohms / farads).

    Handles plain (``100``, ``4.7k``, ``22pF``), suffix (``100R``, ``1M``) and
    the infix decimal notation (``4k7`` → 4.7 kΩ, ``4n7`` → 4.7 nF). Returns
    ``None`` for empty/DNP/unparseable values.
    """
    if not value or _DNP_RE.search(value):
        return None
    v = value.strip().replace("Ω", "").replace("µ", "u").replace("μ", "u")
    if cls == "R":
        mult = {"R": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}
        letters = "RKMG"
    elif cls == "C":
        # Bare cap numbers are ambiguous; require a unit letter for caps.
        mult = {"P": 1e-12, "N": 1e-9, "U": 1e-6, "F": 1.0}
        letters = "PNUF"
    else:
        return None

    # Infix: digit(s) <letter> digit(s)  →  "4k7" = 4.7 * mult[k]
    m = re.fullmatch(rf"(\d+)([{letters}{letters.lower()}])(\d+)", v)
    if m:
        num = float(f"{m.group(1)}.{m.group(3)}")
        return num * mult[m.group(2).upper()]

    # Suffix / plain:  digits[.digits] optional-letter
    m = re.fullmatch(rf"(\d+\.?\d*)\s*([{letters}{letters.lower()}]?)F?", v)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2).upper()
    if cls == "C" and not unit:
        return None            # a cap with no unit is not trustworthy
    return num * mult.get(unit, 1.0)


def format_value(si: float, cls: str) -> str:
    """Canonical human label for an SI value, e.g. ``4.7k``, ``100R``, ``22pF``."""
    if cls == "R":
        for thresh, suf in ((1e6, "M"), (1e3, "k"), (1.0, "R")):
            if si >= thresh:
                return f"{_trim(si / thresh)}{suf}"
        return f"{_trim(si)}R"
    # capacitance
    for thresh, suf in ((1e-6, "uF"), (1e-9, "nF"), (1e-12, "pF")):
        if si >= thresh:
            return f"{_trim(si / thresh)}{suf}"
    return f"{_trim(si / 1e-12)}pF"


def _trim(x: float) -> str:
    """Compact number: ``4.7``, ``100``, ``1`` (no trailing ``.0``)."""
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return s or "0"


def nearest_eseries(si: float, series: str) -> tuple[float, float]:
    """Snap an SI value to the nearest E-series preferred value.

    Returns ``(snapped_si, shift_pct)`` where ``shift_pct`` is the relative
    magnitude change (always ≥ 0). ``si <= 0`` returns ``(si, 0.0)``.
    """
    mant = _E_SERIES.get(series.upper())
    if not mant or si <= 0:
        return si, 0.0
    base = mant[0]                       # 10 for E6/12/24, 100 for E48/96
    decade = math.floor(math.log10(si / base))
    best = si
    best_err = math.inf
    # check the mantissas in the decade below/at/above to catch wrap-around
    for d in (decade - 1, decade, decade + 1):
        scale = (10.0 ** d)
        for m in mant:
            cand = m * scale
            err = abs(cand - si) / si
            if err < best_err:
                best_err, best = err, cand
    return best, best_err * 100.0


def consolidate(items: list[dict[str, Any]], series: str = DEFAULT_SERIES,
                max_shift_pct: float = DEFAULT_MAX_SHIFT_PCT) -> dict[str, Any]:
    """Group R/C parts by class, snap to E-series, propose feeder-cutting merges.

    Args:
        items: ``[{"ref": str, "cls": "R"|"C", "si": float}, …]`` (already
            parsed; unparseable parts filtered out by the caller).
        series: E-series to standardise to (``E6``…``E96``).
        max_shift_pct: never move a part more than this many percent; parts whose
            nearest preferred value is further are reported as *unmergeable*.

    Returns a per-class report: distinct value count before/after, feeders saved,
    and the concrete merges (target value + the source values/refs folding in).
    """
    by_cls: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_cls.setdefault(it["cls"], []).append(it)

    classes: dict[str, Any] = {}
    total_before = total_after = 0
    for cls, parts in sorted(by_cls.items()):
        # distinct source values → refs
        src: dict[float, list[str]] = {}
        for p in parts:
            src.setdefault(p["si"], []).append(p["ref"])

        # map each source value to a consolidation target (snapped) or keep it
        target_of: dict[float, float] = {}
        unmergeable: list[dict[str, Any]] = []
        for si in src:
            snapped, shift = nearest_eseries(si, series)
            if shift <= max_shift_pct:
                target_of[si] = snapped
            else:
                target_of[si] = si          # keep as-is
                unmergeable.append({
                    "value": format_value(si, cls),
                    "nearest": format_value(snapped, cls),
                    "shift_pct": round(shift, 2),
                    "refs": sorted(src[si]),
                })

        # group source values by their target
        groups: dict[float, list[float]] = {}
        for si, tgt in target_of.items():
            groups.setdefault(tgt, []).append(si)

        merges = []
        for tgt, sources in sorted(groups.items()):
            # a merge only matters if >1 source value collapses into the target
            distinct_sources = sorted(set(sources))
            if len(distinct_sources) < 2:
                continue
            refs = sorted(r for si in distinct_sources for r in src[si])
            merges.append({
                "to": format_value(tgt, cls),
                "to_si": tgt,
                "from": [format_value(si, cls) for si in distinct_sources],
                "refs": refs,
                "count": len(refs),
            })

        before = len(src)
        after = len(groups)
        total_before += before
        total_after += after
        classes[cls] = {
            "distinct_before": before,
            "distinct_after": after,
            "feeders_saved": before - after,
            "merges": merges,
            "unmergeable": unmergeable,
        }

    return {
        "series": series.upper(),
        "max_shift_pct": max_shift_pct,
        "classes": classes,
        "distinct_before": total_before,
        "distinct_after": total_after,
        "feeders_saved": total_before - total_after,
    }
