# SPDX-License-Identifier: GPL-3.0-or-later
"""Lightweight ``.kicad_pcb`` footprint/pad/net parser — shared, not per-tool.

Extracted from ``tools/audit_tools.py`` (was ``_parse_pcb_for_audit``) so the
audits **and** the placement scorer share one parser instead of each rolling
their own. It walks top-level ``(footprint …)`` blocks and returns, per
footprint: reference, value, pose (anchor + rotation), layer/flip, and each pad
with its **local** offset ``(lx, ly)``, **world** position ``(x, y)`` (via the
footgun-safe :func:`pcb_local_to_world`) and net name. Not a full S-expression
loader — tailored to what these consumers need, forgiving on odd input.
"""

from __future__ import annotations

import re
from typing import Any

from kicad_mcp.utils.pcb_geometry import compute_fp_bbox, pcb_local_to_world


def parse_pcb_footprints(pcb_text: str, with_bbox: bool = False) -> dict[str, Any]:
    """Parse footprints + pads (local & world coords) + per-pad net from PCB text.

    Args:
        pcb_text: the ``.kicad_pcb`` text.
        with_bbox: also attach ``"bbox": (w_mm, h_mm)`` per footprint (local-frame
            courtyard/fab extent via :func:`compute_fp_bbox`). Off by default so
            the audit callers pay nothing for it.

    Returns:
        ``{"footprints": [{ref, value, fpid, anchor:(x,y), rot, layer, flipped,
        bbox?, pads:[{pad, lx, ly, x, y, net}]}, …]}``. ``fpid`` is the footprint
        library id (e.g. ``"Resistor_SMD:R_0402_1005Metric"``) or ``""``.
    """
    fps: list[dict[str, Any]] = []
    i = 0
    while True:
        idx = pcb_text.find("\t(footprint", i)
        if idx == -1:
            break
        # Find the matching close paren of this top-level footprint block.
        depth = 0
        j = idx
        while j < len(pcb_text):
            if pcb_text[j] == "(":
                depth += 1
            elif pcb_text[j] == ")":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        block = pcb_text[idx:j]
        fpid_m = re.match(r'\s*\(footprint\s+"([^"]*)"', block)
        fpid = fpid_m.group(1) if fpid_m else ""
        ref_m = re.search(r'\(property "Reference" "([^"]+)"', block)
        val_m = re.search(r'\(property "Value" "([^"]*)"', block)
        if not ref_m:
            i = j
            continue
        ref = ref_m.group(1)
        at_m = re.search(
            r'\)\s+\(uuid\s+"[^"]+"\)\s+\(at\s+([\d.\-]+)\s+([\d.\-]+)'
            r'(?:\s+([\d.\-]+))?\)',
            block,
        )
        if not at_m:
            i = j
            continue
        fx = float(at_m.group(1)); fy = float(at_m.group(2))
        frot = float(at_m.group(3)) if at_m.group(3) else 0.0
        layer_m = re.search(r'\(layer "([^"]+)"\)', block)
        layer = layer_m.group(1) if layer_m else "F.Cu"
        flipped = layer.startswith("B.")
        pads: list[dict[str, Any]] = []
        for pm in re.finditer(r'\(pad "([^"]+)"', block):
            ps = pm.start()
            pad_snippet = block[ps:ps + 1200]
            at_p = re.search(r'\(at\s+([\d.\-]+)\s+([\d.\-]+)', pad_snippet)
            if not at_p:
                continue
            lx, ly = float(at_p.group(1)), float(at_p.group(2))
            wx, wy = pcb_local_to_world((fx, fy), frot, lx, ly, flipped=flipped)
            # Pad net spelling varies: ``(net 5 "+5V")`` (number + name) and
            # ``(net "+5V")`` (name only, net-patch tools) — number is optional.
            net_m = re.search(r'\(net\s+(?:\d+\s+)?"([^"]*)"\)', pad_snippet)
            pads.append({
                "pad": pm.group(1),
                "lx": lx, "ly": ly,
                "x": wx, "y": wy,
                "net": net_m.group(1) if net_m else "",
            })
        fp: dict[str, Any] = {
            "ref": ref,
            "value": val_m.group(1) if val_m else "",
            "fpid": fpid,
            "anchor": (fx, fy),
            "rot": frot,
            "layer": layer,
            "flipped": flipped,
            "pads": pads,
        }
        if with_bbox:
            try:
                minx, miny, maxx, maxy = compute_fp_bbox(block)
                fp["bbox"] = (maxx - minx, maxy - miny)
            except Exception:
                fp["bbox"] = (0.0, 0.0)
        fps.append(fp)
        i = j
    return {"footprints": fps}
