# SPDX-License-Identifier: GPL-3.0-or-later
"""IPC-2221 ampacity math + the pure track-width audit behind ``check_ampacity``.

How much current a copper trace can carry is NOT in the layout — it is design
intent (which net carries how many amps) plus physics (IPC-2221 generic
standard). This module holds the physics; the semantic part (assigning currents
to nets) stays with the calling LLM/user.

IPC-2221 external/internal charts, the standard fit:

    I = k * dT^0.44 * A^0.725      (I in A, dT in Kelvin, A in mil^2)

with k = 0.048 for outer layers and k = 0.024 for inner layers (inner copper
sheds heat about half as well). 1 oz/ft^2 copper is 1.378 mil thick. Valid for
the chart range (up to ~35 A, dT 10..100 K); tiny/huge values are still
computed but flagged by the caller's judgement, not silently clamped.

Pure/stdlib only — headless unit-testable.
"""

from __future__ import annotations

import math
from typing import Any

MIL_PER_MM = 1000.0 / 25.4
OZ_TO_MIL = 1.378  # copper thickness of 1 oz/ft^2 in mil

K_EXTERNAL = 0.048
K_INTERNAL = 0.024


def _k(internal: bool) -> float:
    return K_INTERNAL if internal else K_EXTERNAL


def is_internal_layer(layer: str) -> bool:
    """``In1.Cu`` … ``In30.Cu`` are inner copper; F.Cu/B.Cu are outer."""
    return (layer or "").startswith("In")


def cross_section_mil2(width_mm: float, copper_oz: float = 1.0) -> float:
    """Trace cross-section in mil² for a width in mm and copper weight in oz."""
    return max(0.0, width_mm) * MIL_PER_MM * copper_oz * OZ_TO_MIL


def max_current_a(width_mm: float, temp_rise_c: float = 10.0,
                  copper_oz: float = 1.0, internal: bool = False) -> float:
    """IPC-2221: how many amps a trace of ``width_mm`` may carry."""
    area = cross_section_mil2(width_mm, copper_oz)
    if area <= 0 or temp_rise_c <= 0:
        return 0.0
    return _k(internal) * (temp_rise_c ** 0.44) * (area ** 0.725)


def required_width_mm(current_a: float, temp_rise_c: float = 10.0,
                      copper_oz: float = 1.0, internal: bool = False) -> float:
    """IPC-2221 inverted: minimum trace width for ``current_a`` amps."""
    if current_a <= 0:
        return 0.0
    if temp_rise_c <= 0 or copper_oz <= 0:
        return math.inf
    area = (current_a / (_k(internal) * temp_rise_c ** 0.44)) ** (1.0 / 0.725)
    return area / (MIL_PER_MM * copper_oz * OZ_TO_MIL)


def _seg_len_mm(seg: dict) -> float:
    (x1, y1), (x2, y2) = seg.get("start", [0, 0]), seg.get("end", [0, 0])
    return math.hypot(x2 - x1, y2 - y1)


def audit_tracks(
    tracks: list,
    net_names: dict,
    currents: dict,
    temp_rise_c: float = 10.0,
    copper_oz: float = 1.0,
) -> dict[str, Any]:
    """Pure ampacity audit over extracted track segments.

    Args:
        tracks: ``[{start, end, width, layer, net}, …]`` — ``net`` is the net
            NUMBER (the shape ``pcb_tools._extract_all`` produces).
        net_names: net number -> net name.
        currents: net name -> amps of design current. Nets without an entry
            are inventoried but not judged.
        temp_rise_c: allowed temperature rise (IPC-2221 chart parameter).
        copper_oz: copper weight; outer/inner distinction comes per segment
            from its layer name.

    Returns:
        ``{nets: {name: {track_count, length_mm, min_width_mm, max_width_mm,
        layers, current_a?, required_width_mm_outer?, required_width_mm_inner?,
        worst_margin_a?}}, violations: [{net, layer, width_mm,
        required_width_mm, current_a, max_current_a, start, end,
        length_mm}, …]}`` — violations sorted worst-first (largest deficit).
    """
    per_net: dict = {}
    violations: list = []
    for seg in tracks or []:
        name = net_names.get(seg.get("net"))
        if not name:
            continue
        width = float(seg.get("width") or 0.0)
        layer = str(seg.get("layer") or "")
        entry = per_net.setdefault(name, {
            "track_count": 0, "length_mm": 0.0,
            "min_width_mm": math.inf, "max_width_mm": 0.0, "layers": set(),
        })
        entry["track_count"] += 1
        entry["length_mm"] += _seg_len_mm(seg)
        entry["min_width_mm"] = min(entry["min_width_mm"], width)
        entry["max_width_mm"] = max(entry["max_width_mm"], width)
        entry["layers"].add(layer)

        amps = currents.get(name)
        if amps is None:
            continue
        internal = is_internal_layer(layer)
        need = required_width_mm(amps, temp_rise_c, copper_oz, internal)
        if width + 1e-9 < need:
            violations.append({
                "net": name,
                "layer": layer,
                "width_mm": round(width, 4),
                "required_width_mm": round(need, 4),
                "current_a": amps,
                "max_current_a": round(
                    max_current_a(width, temp_rise_c, copper_oz, internal), 3),
                "start": seg.get("start"),
                "end": seg.get("end"),
                "length_mm": round(_seg_len_mm(seg), 3),
            })

    for name, entry in per_net.items():
        entry["length_mm"] = round(entry["length_mm"], 2)
        entry["min_width_mm"] = (round(entry["min_width_mm"], 4)
                                 if entry["track_count"] else 0.0)
        entry["max_width_mm"] = round(entry["max_width_mm"], 4)
        entry["layers"] = sorted(entry["layers"])
        amps = currents.get(name)
        if amps is not None:
            entry["current_a"] = amps
            entry["required_width_mm_outer"] = round(
                required_width_mm(amps, temp_rise_c, copper_oz, False), 4)
            entry["required_width_mm_inner"] = round(
                required_width_mm(amps, temp_rise_c, copper_oz, True), 4)
            entry["worst_margin_a"] = round(min(
                (max_current_a(t, temp_rise_c, copper_oz,
                               is_internal_layer(la)) - amps)
                for t, la in [(entry["min_width_mm"], lay)
                              for lay in entry["layers"]]
            ), 3) if entry["layers"] else 0.0

    violations.sort(key=lambda v: v["required_width_mm"] - v["width_mm"],
                    reverse=True)
    return {"nets": per_net, "violations": violations}
