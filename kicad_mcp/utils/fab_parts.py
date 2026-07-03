# SPDX-License-Identifier: GPL-3.0-or-later
"""Fab preferred-parts — is there an in-house/no-load-fee part for this value?

Big assemblers keep a curated in-stock parts library and charge a **per-type
feeder load fee** for anything outside it (JLCPCB Basic vs Extended, Seeed OPL,
Aisler Push-Parts, …). Pinning each jelly-bean R/C to the fab's preferred part
removes that fee and de-risks stock. KiCad has zero distributor/fab knowledge —
this is external manufacturing data on top of the netlist.

Provider-agnostic by design: each fab is one dated JSON snapshot under
``resources/data/fab_parts_<provider>.json`` and one entry in ``PROVIDERS`` — the
same Single-Source registry pattern as ``design_rules.RULES``. Adding a fab =
drop a JSON + a registry line, no tool change. Snapshots are curated seed
coverage (not the live catalog) and carry a date + disclaimer; the tool surfaces
both so nobody treats them as ground truth.

Pure/stdlib, headless-testable. Reuses ``bom_consolidate`` for canonical SI value
parsing (so ``4k7`` and ``4.7k`` match the same snapshot row).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from kicad_mcp.utils import bom_consolidate

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "resources", "data")

# provider key -> snapshot filename. One line per fab (the registry).
PROVIDERS: dict[str, str] = {
    "jlcpcb": "fab_parts_jlcpcb.json",
}

_SNAPSHOT_CACHE: dict[str, dict[str, Any]] = {}

# SMD size codes we can read out of a KiCad footprint id (imperial).
_SMD_SIZE_RE = re.compile(
    r"[_:\-](01005|0201|0402|0603|0805|1206|1210|1806|2010|2512)(?:[_\-]|$)")


def provider_keys() -> list[str]:
    """Registered fab providers (sorted)."""
    return sorted(PROVIDERS)


def load_snapshot(provider: str) -> dict[str, Any]:
    """Load (and cache) a provider's parts snapshot. Raises ``KeyError`` for an
    unknown provider, ``OSError``/``ValueError`` if the JSON is missing/bad.

    The snapshot's ``parts`` are indexed by ``(cls, canonical_si, package)`` so
    lookups are O(1) and value-notation agnostic.
    """
    if provider in _SNAPSHOT_CACHE:
        return _SNAPSHOT_CACHE[provider]
    if provider not in PROVIDERS:
        raise KeyError(provider)
    path = os.path.join(_DATA_DIR, PROVIDERS[provider])
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    index: dict[tuple, dict[str, Any]] = {}
    for part in data.get("parts", []):
        cls = part.get("cls", "")
        si = bom_consolidate.normalize_value(part.get("value", ""), cls)
        if si is None:
            continue
        index[(cls, round_si(si), part.get("package", ""))] = part
    data["_index"] = index
    _SNAPSHOT_CACHE[provider] = data
    return data


def round_si(si: float) -> float:
    """Quantise an SI value to 4 significant figures so snapshot and board
    values key identically despite float noise."""
    if si <= 0:
        return 0.0
    from math import floor, log10
    d = 3 - floor(log10(si))
    return round(si, d)


def extract_package(fpid: str) -> str:
    """Pull the SMD size code (``"0402"``) out of a footprint id, else ``""``.

    ``"Resistor_SMD:R_0402_1005Metric"`` → ``"0402"``.
    """
    m = _SMD_SIZE_RE.search(fpid or "")
    return m.group(1) if m else ""


def lookup(provider_data: dict[str, Any], cls: str, si: float,
           package: str) -> dict[str, Any] | None:
    """A preferred part for this exact (class, value, package), or ``None``."""
    return provider_data["_index"].get((cls, round_si(si), package))


def suggest(items: list[dict[str, Any]], provider: str) -> dict[str, Any]:
    """Report which distinct R/C types have a preferred (no-load-fee) part.

    Args:
        items: ``[{"ref", "cls", "si", "package"}, …]`` (already parsed).
        provider: a key from :func:`provider_keys`.

    Returns per distinct ``(cls, value, package)`` type: whether the fab has a
    preferred part (+ its part number), and the refs folding into it. Plus a
    savings estimate = ``load_fee × types_with_preferred`` (an *upper bound* —
    it only materialises for types you'd otherwise have ordered as Extended).
    """
    data = load_snapshot(provider)
    fee = float(data.get("load_fee_usd", 0.0))

    types: dict[tuple, dict[str, Any]] = {}
    for it in items:
        key = (it["cls"], round_si(it["si"]), it["package"])
        t = types.get(key)
        if t is None:
            hit = lookup(data, it["cls"], it["si"], it["package"])
            t = types[key] = {
                "cls": it["cls"],
                "value": bom_consolidate.format_value(it["si"], it["cls"]),
                "package": it["package"] or "?",
                "has_preferred": hit is not None,
                "part": (hit or {}).get("lcsc", ""),
                "refs": [],
            }
        t["refs"].append(it["ref"])

    rows = sorted(types.values(),
                  key=lambda r: (not r["has_preferred"], r["cls"], r["value"]))
    for r in rows:
        r["refs"] = sorted(r["refs"])
        r["count"] = len(r["refs"])
    with_pref = [r for r in rows if r["has_preferred"]]
    without = [r for r in rows if not r["has_preferred"]]
    return {
        "provider": provider,
        "display_name": data.get("display_name", provider),
        "tier_name": data.get("tier_name", "preferred"),
        "snapshot_date": data.get("snapshot_date", "unknown"),
        "disclaimer": data.get("disclaimer", ""),
        "distinct_types": len(rows),
        "types_with_preferred": len(with_pref),
        "types_without_preferred": len(without),
        "load_fee_usd": fee,
        "potential_saving_usd": round(fee * len(with_pref), 2),
        "saving_is_upper_bound": True,
        "types": rows,
    }
