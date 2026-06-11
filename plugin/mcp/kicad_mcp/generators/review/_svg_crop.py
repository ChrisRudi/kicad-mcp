# SPDX-License-Identifier: GPL-3.0-or-later
"""Crop a ``kicad-cli sch export svg`` output to a schematic-mm bbox.

Purpose
-------
``review_ic_against_datasheet`` needs a tight schematic region around one IC
plus its periphery. ``kicad-cli`` emits a full-sheet SVG; this module re-
writes the SVG's ``viewBox`` (and proportional ``width`` / ``height``) to a
sub-region, then rasterises the result via the existing cairosvg pipeline.

Inputs
------
* ``svg_path`` — path to the full-sheet SVG produced by
  ``cli_export_tools.export_svg`` (file_type=``"sch"``).
* ``bbox_mm`` — ``(xmin, ymin, xmax, ymax)`` in **schematic millimetres** —
  the unit ``get_schematic_bbox`` returns.
* ``padding_mm`` — extra millimetres added on every side; default 10 mm.

Outputs
-------
* ``render_region_to_png(...)`` writes a PNG and returns
  ``{success, output_path, bbox_used_mm, mm_per_svg_unit?}``.

Dependencies
------------
* ``xml.etree.ElementTree`` (stdlib) for SVG header parsing/rewriting.
* ``cairosvg`` via the shared loader in
  ``kicad_mcp.tools.cli_export_tools._ensure_cairosvg`` (lazy install).

The SVG-unit ↔ mm conversion is read defensively from the SVG header's
``width`` (e.g. ``297.000mm``) and the existing ``viewBox`` extents. If
parsing fails we render the whole sheet uncropped and report
``mm_per_svg_unit: None`` so the caller can warn.
"""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any

logger = logging.getLogger(__name__)

_SVG_NS = "http://www.w3.org/2000/svg"
_LEN_RE = re.compile(r"^\s*([-+]?\d*\.?\d+)\s*(mm|cm|in|px|pt)?\s*$", re.IGNORECASE)
_UNIT_TO_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "pt": 25.4 / 72.0,
    "px": 25.4 / 96.0,  # default CSS DPI
    "": 25.4 / 96.0,
}


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _parse_length_mm(text: str) -> float | None:
    if not text:
        return None
    m = _LEN_RE.match(text)
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "").lower()
    return value * _UNIT_TO_MM.get(unit, _UNIT_TO_MM[""])


def _parse_viewbox(text: str) -> tuple[float, float, float, float] | None:
    if not text:
        return None
    parts = text.replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
    except ValueError:
        return None


def _read_svg_geometry(svg_path: str) -> dict[str, Any] | None:
    """Return ``{width_mm, height_mm, viewBox, mm_per_unit_x, mm_per_unit_y}``
    or ``None`` if the SVG header can't be parsed.
    """
    try:
        ET.register_namespace("", _SVG_NS)
        tree = ET.parse(svg_path)
    except (ET.ParseError, OSError) as exc:
        logger.warning("Cannot parse SVG %s: %s", svg_path, exc)
        return None
    root = tree.getroot()
    if _strip_ns(root.tag) != "svg":
        return None
    width_mm = _parse_length_mm(root.get("width", ""))
    height_mm = _parse_length_mm(root.get("height", ""))
    viewbox = _parse_viewbox(root.get("viewBox", ""))
    if not (width_mm and height_mm and viewbox):
        return None
    vb_w = viewbox[2] or 1.0
    vb_h = viewbox[3] or 1.0
    return {
        "tree": tree,
        "root": root,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "viewBox": viewbox,
        "mm_per_unit_x": width_mm / vb_w,
        "mm_per_unit_y": height_mm / vb_h,
    }


def _padded_bbox(
    bbox_mm: tuple[float, float, float, float], padding_mm: float
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox_mm
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    return x1 - padding_mm, y1 - padding_mm, x2 + padding_mm, y2 + padding_mm


def crop_svg_to_region(
    svg_path: str,
    bbox_mm: tuple[float, float, float, float],
    padding_mm: float = 10.0,
) -> tuple[str | None, dict[str, Any]]:
    """Return ``(modified_svg_text, meta)``.

    ``modified_svg_text`` is ``None`` if the SVG header could not be parsed;
    the caller must then fall back to rendering the original file.
    """
    geom = _read_svg_geometry(svg_path)
    meta: dict[str, Any] = {"bbox_mm": bbox_mm, "padding_mm": padding_mm}
    if not geom:
        meta["fallback"] = "header_parse_failed"
        return None, meta

    pxmin, pymin, pxmax, pymax = _padded_bbox(bbox_mm, padding_mm)
    full_w_mm = geom["width_mm"]
    full_h_mm = geom["height_mm"]
    # Clamp to sheet extent so cairosvg doesn't get a viewBox outside the
    # underlying coordinate space (some renderers crop weirdly).
    pxmin = max(0.0, pxmin)
    pymin = max(0.0, pymin)
    pxmax = min(full_w_mm, pxmax)
    pymax = min(full_h_mm, pymax)
    if pxmax <= pxmin or pymax <= pymin:
        meta["fallback"] = "bbox_outside_sheet"
        return None, meta

    vx = pxmin / geom["mm_per_unit_x"]
    vy = pymin / geom["mm_per_unit_y"]
    vw = (pxmax - pxmin) / geom["mm_per_unit_x"]
    vh = (pymax - pymin) / geom["mm_per_unit_y"]

    root = geom["root"]
    root.set("viewBox", f"{vx:.3f} {vy:.3f} {vw:.3f} {vh:.3f}")
    root.set("width", f"{pxmax - pxmin:.3f}mm")
    root.set("height", f"{pymax - pymin:.3f}mm")

    out_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    meta.update(
        {
            "applied_bbox_mm": (pxmin, pymin, pxmax, pymax),
            "mm_per_unit_x": geom["mm_per_unit_x"],
            "mm_per_unit_y": geom["mm_per_unit_y"],
        }
    )
    return out_bytes.decode("utf-8"), meta


def render_region_to_png(
    svg_path: str,
    bbox_mm: tuple[float, float, float, float],
    output_png: str,
    padding_mm: float = 10.0,
    scale: float = 2.0,
) -> dict[str, Any]:
    """Crop ``svg_path`` to ``bbox_mm + padding``, write to ``output_png``.

    Returns ``{success, output_path, fallback?, applied_bbox_mm?, error?}``.
    Falls back to rendering the whole sheet if header-parse fails or the
    bbox is outside the sheet extent.
    """
    from kicad_mcp.tools.cli_export_tools import _ensure_cairosvg  # lazy

    if not os.path.isfile(svg_path):
        return {"success": False, "error": f"SVG not found: {svg_path}"}

    cairosvg = _ensure_cairosvg()
    svg_text, meta = crop_svg_to_region(svg_path, bbox_mm, padding_mm)
    os.makedirs(os.path.dirname(os.path.abspath(output_png)) or ".", exist_ok=True)
    try:
        if svg_text is None:
            cairosvg.svg2png(url=svg_path, write_to=output_png, scale=scale)
        else:
            cairosvg.svg2png(
                bytestring=svg_text.encode("utf-8"),
                write_to=output_png,
                scale=scale,
            )
    except Exception as exc:  # pragma: no cover - cairo failure modes vary
        return {"success": False, "error": f"PNG render failed: {exc}"}

    result: dict[str, Any] = {"success": True, "output_path": output_png}
    result.update(meta)
    return result
