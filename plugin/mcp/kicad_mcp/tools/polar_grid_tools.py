# SPDX-License-Identifier: GPL-3.0-or-later
"""
Polar-coordinate grid helper for circular PCBs.

Codifies the polar-coordinate workflow common in motor-drive boards, coil
PCBs, and other circular layouts: a concentric set of N rings between
``r_inner`` and ``r_outer``, plus M radial spokes. Components sit at
``(ring, spoke)`` intersections; arcs run on one inner layer
("arc_layer"), radial stubs on the neighbouring inner layer
("radial_layer"). Via stitching at grid nodes reaches any point.

Single MCP tool ``polar_grid`` with an ``op`` dispatcher exposes 13
operations (including ``route``, the pin-to-pin polar router).  Defaults
match the reference mainboard (centre 148.5, 105, rings 13.5..30 step 0.55,
18 spokes every 20°).

Design rationale: every routing/placement Python snippet we wrote
ad-hoc during reference V14→V15 sessions reduces to one of these ops —
``polar_to_xy``, ``align_rotation``, ``place_on_ring``,
``add_polar_arc``, ``add_radial_segment``, ``align_outer_components``.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, fields
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text, put_text
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.pcb_geometry import align_radial_rotation


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class PolarConfig:
    """Polar grid parameters with reference defaults."""

    center_x_mm: float = 148.5
    center_y_mm: float = 105.0
    r_outer_mm: float = 30.0
    r_inner_mm: float = 13.5
    ring_step_mm: float = 0.55
    ring_count_from: str = "outer"  # "outer" → ring 1 at r_outer, or "inner"
    spoke_count: int = 18
    spoke_offset_deg: float = 0.0
    arc_layer: str = "In1.Cu"
    radial_layer: str = "In2.Cu"
    snap_to_spoke: bool = True
    snap_to_ring: bool = True

    @property
    def ring_count(self) -> int:
        return int(round((self.r_outer_mm - self.r_inner_mm) / self.ring_step_mm)) + 1

    @property
    def spoke_step_deg(self) -> float:
        return 360.0 / self.spoke_count

    def ring_to_r(self, ring: int) -> float:
        if not (1 <= ring <= self.ring_count):
            raise ValueError(
                f"ring {ring} out of range [1, {self.ring_count}]"
            )
        if self.ring_count_from == "outer":
            return self.r_outer_mm - (ring - 1) * self.ring_step_mm
        return self.r_inner_mm + (ring - 1) * self.ring_step_mm

    def r_to_ring(self, r_mm: float) -> int:
        if self.ring_count_from == "outer":
            ring = round((self.r_outer_mm - r_mm) / self.ring_step_mm) + 1
        else:
            ring = round((r_mm - self.r_inner_mm) / self.ring_step_mm) + 1
        return max(1, min(self.ring_count, ring))

    def snap_spoke(self, theta_deg: float) -> tuple[int, float]:
        """Return (spoke_index, spoke_angle_deg) closest to ``theta_deg``."""
        normalised = ((theta_deg - self.spoke_offset_deg) % 360.0)
        step = self.spoke_step_deg
        idx = int(round(normalised / step)) % self.spoke_count
        return idx, _normalise_deg(self.spoke_offset_deg + idx * step)


def _config_from_kwargs(kwargs: dict[str, Any]) -> PolarConfig:
    """Build a PolarConfig from caller-supplied overrides."""
    cfg = PolarConfig()
    for f in fields(PolarConfig):
        if f.name in kwargs and kwargs[f.name] is not None:
            setattr(cfg, f.name, kwargs[f.name])
    return cfg


def _normalise_deg(angle_deg: float) -> float:
    """Fold an angle into ``(-180, 180]``."""
    a = ((angle_deg + 180.0) % 360.0) - 180.0
    if a <= -180.0:
        a += 360.0
    return a


def _persist(pcb_path: str, text: str) -> None:
    """Write ``text`` to disk and sync the cache.

    ``put_text`` alone ONLY updates the in-memory cache — it does not
    write the file (its contract assumes the caller already wrote it). An
    edit op must therefore write the file itself and then sync the cache,
    or the change is invisible to KiCad / the next process (the edit is
    masked in-process by a cache hit, then silently lost on disk)."""
    with open(pcb_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    put_text(pcb_path, text)


# ---------------------------------------------------------------------------
# Footprint long-axis auto-detect
# ---------------------------------------------------------------------------

_LONG_AXIS_Y_PATTERNS = [
    r"SOIC[-_]\d",
    r"TSSOP[-_]\d",
    r"HTSSOP[-_]\d",
    r"SOT-23-[56]",
    r"TDSON-",
    r"TO-252-",
    r"L_Chilisin",
]
_LONG_AXIS_Y_RE = re.compile("|".join(_LONG_AXIS_Y_PATTERNS))


def _long_axis_of(footprint_id: str) -> str:
    return "y" if _LONG_AXIS_Y_RE.search(footprint_id or "") else "x"


# ---------------------------------------------------------------------------
# PCB footprint inspection (lightweight: no full parser, just regex)
# ---------------------------------------------------------------------------


@dataclass
class _FPInfo:
    ref: str
    x_mm: float
    y_mm: float
    angle_deg: float
    layer: str
    footprint_id: str
    block_start: int
    ref_pos: int  # position of (property "Reference" ...) inside block


def _index_footprints(pcb_text: str) -> dict[str, _FPInfo]:
    """Return {ref: _FPInfo} for all top-level footprints."""
    out: dict[str, _FPInfo] = {}
    pat = re.compile(r'^\t\(footprint "([^"]+)"', re.MULTILINE)
    for m in pat.finditer(pcb_text):
        fp_id = m.group(1)
        block_start = m.start() + 1
        depth, j = 1, block_start + 1
        while depth and j < len(pcb_text):
            if pcb_text[j] == "(":
                depth += 1
            elif pcb_text[j] == ")":
                depth -= 1
            j += 1
        block = pcb_text[block_start:j]
        head = block[:1500]
        ref_m = re.search(r'\(property "Reference" "([^"]+)"', block)
        at_m = re.search(
            r'^\s+\(at ([\d.-]+) ([\d.-]+)(?: ([\d.-]+))?\)',
            head, re.MULTILINE,
        )
        layer_m = re.search(r'^\s+\(layer "([^"]+)"\)', head, re.MULTILINE)
        if not (ref_m and at_m):
            continue
        out[ref_m.group(1)] = _FPInfo(
            ref=ref_m.group(1),
            x_mm=float(at_m.group(1)),
            y_mm=float(at_m.group(2)),
            angle_deg=float(at_m.group(3)) if at_m.group(3) else 0.0,
            layer=layer_m.group(1) if layer_m else "?",
            footprint_id=fp_id,
            block_start=block_start,
            ref_pos=block_start + ref_m.start(),
        )
    return out


def _rewrite_footprint_pose(
    pcb_text: str,
    fp: _FPInfo,
    new_x: float | None = None,
    new_y: float | None = None,
    new_angle: float | None = None,
) -> str:
    """Patch the main ``(at x y a)`` line of a footprint."""
    head = pcb_text[fp.block_start: fp.ref_pos]
    at_m = re.search(
        r'^(\s+)\(at ([\d.-]+) ([\d.-]+)(?: ([\d.-]+))?\)',
        head, re.MULTILINE,
    )
    if not at_m:
        return pcb_text
    indent = at_m.group(1)
    x = new_x if new_x is not None else float(at_m.group(2))
    y = new_y if new_y is not None else float(at_m.group(3))
    cur_a = float(at_m.group(4)) if at_m.group(4) else 0.0
    a = new_angle if new_angle is not None else cur_a
    new_line = f"{indent}(at {round(x, 4)} {round(y, 4)} {round(a, 4)})"
    new_head = head[: at_m.start()] + new_line + head[at_m.end():]
    return pcb_text[: fp.block_start] + new_head + pcb_text[fp.ref_pos:]


# ---------------------------------------------------------------------------
# Operation implementations
# ---------------------------------------------------------------------------


def _op_polar_to_xy(cfg: PolarConfig, ring: int | None, r_mm: float | None,
                    theta_deg: float) -> dict[str, Any]:
    if ring is not None:
        r = cfg.ring_to_r(ring)
    elif r_mm is not None:
        r = float(r_mm)
        ring = cfg.r_to_ring(r) if cfg.snap_to_ring else None
    else:
        return {"success": False, "error": "Provide ring or r_mm."}
    rad = math.radians(theta_deg)
    x = cfg.center_x_mm + r * math.cos(rad)
    y = cfg.center_y_mm + r * math.sin(rad)
    return {
        "success": True,
        "op": "polar_to_xy",
        "ring": ring,
        "r_mm": round(r, 4),
        "theta_deg": _normalise_deg(theta_deg),
        "x_mm": round(x, 4),
        "y_mm": round(y, 4),
    }


def _op_xy_to_polar(cfg: PolarConfig, x_mm: float, y_mm: float) -> dict[str, Any]:
    dx = x_mm - cfg.center_x_mm
    dy = y_mm - cfg.center_y_mm
    r = math.hypot(dx, dy)
    theta = math.degrees(math.atan2(dy, dx))
    ring = cfg.r_to_ring(r) if cfg.r_inner_mm <= r <= cfg.r_outer_mm else None
    spoke_idx, spoke_deg = cfg.snap_spoke(theta)
    return {
        "success": True,
        "op": "xy_to_polar",
        "x_mm": x_mm, "y_mm": y_mm,
        "r_mm": round(r, 4),
        "theta_deg": round(_normalise_deg(theta), 4),
        "ring": ring,
        "ring_deviation_mm": round(abs(r - cfg.ring_to_r(ring)), 4) if ring else None,
        "closest_spoke_idx": spoke_idx,
        "closest_spoke_deg": round(spoke_deg, 4),
        "spoke_deviation_deg": round(abs(_normalise_deg(theta - spoke_deg)), 4),
    }


def _op_ring_radius(cfg: PolarConfig, ring: int) -> dict[str, Any]:
    return {
        "success": True,
        "op": "ring_radius",
        "ring": ring,
        "r_mm": round(cfg.ring_to_r(ring), 4),
        "ring_count": cfg.ring_count,
        "ring_count_from": cfg.ring_count_from,
    }


def _op_align_rotation(cfg: PolarConfig, target_x_mm: float, target_y_mm: float,
                       mode: str, long_axis: str) -> dict[str, Any]:
    try:
        rot = align_radial_rotation(
            (target_x_mm, target_y_mm),
            (cfg.center_x_mm, cfg.center_y_mm),
            mode=mode,
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    if long_axis == "y":
        rot += 90.0
    rot = _normalise_deg(rot)
    return {
        "success": True,
        "op": "align_rotation",
        "rotation_deg": round(rot, 4),
        "mode": mode,
        "long_axis": long_axis,
    }


def _op_place_on_ring(pcb_path: str, cfg: PolarConfig, ref: str,
                      ring: int | None, r_mm: float | None,
                      theta_deg: float, mode: str,
                      long_axis: str) -> dict[str, Any]:
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    text = get_text(pcb_path)
    fps = _index_footprints(text)
    if ref not in fps:
        return {"success": False, "error": f"Footprint {ref} not found"}
    pos = _op_polar_to_xy(cfg, ring, r_mm, theta_deg)
    if not pos["success"]:
        return pos
    actual_long_axis = (
        _long_axis_of(fps[ref].footprint_id) if long_axis == "auto" else long_axis
    )
    rot_dict = _op_align_rotation(
        cfg, pos["x_mm"], pos["y_mm"], mode, actual_long_axis,
    )
    if not rot_dict["success"]:
        return rot_dict
    new_text = _rewrite_footprint_pose(
        text, fps[ref], pos["x_mm"], pos["y_mm"], rot_dict["rotation_deg"],
    )
    _persist(pcb_path, new_text)
    return {
        "success": True,
        "op": "place_on_ring",
        "ref": ref,
        "ring": pos["ring"],
        "ring_radius_mm": pos["r_mm"],
        "theta_deg": pos["theta_deg"],
        "applied_pose": [pos["x_mm"], pos["y_mm"], rot_dict["rotation_deg"]],
        "long_axis": actual_long_axis,
        "mode": mode,
    }


def _op_place_on_spoke(pcb_path: str, cfg: PolarConfig, ref: str,
                       spoke_idx: int | None, spoke_deg: float | None,
                       ring: int | None, r_mm: float | None,
                       mode: str, long_axis: str) -> dict[str, Any]:
    if spoke_idx is not None:
        theta_deg = _normalise_deg(
            cfg.spoke_offset_deg + spoke_idx * cfg.spoke_step_deg
        )
    elif spoke_deg is not None:
        _, theta_deg = cfg.snap_spoke(spoke_deg)
    else:
        return {"success": False, "error": "Provide spoke_idx or spoke_deg"}
    result = _op_place_on_ring(pcb_path, cfg, ref, ring, r_mm, theta_deg,
                               mode, long_axis)
    if result.get("success"):
        result["op"] = "place_on_spoke"
        result["spoke_deg"] = theta_deg
    return result


def _op_align_outer_components(pcb_path: str, cfg: PolarConfig,
                               r_min_mm: float, r_max_mm: float,
                               mode: str, exempt_refs: list[str],
                               dry_run: bool) -> dict[str, Any]:
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    text = get_text(pcb_path)
    fps = _index_footprints(text)
    plan: list[dict[str, Any]] = []
    exempt = set(exempt_refs or [])
    for ref, fp in fps.items():
        if ref in exempt:
            continue
        r = math.hypot(fp.x_mm - cfg.center_x_mm, fp.y_mm - cfg.center_y_mm)
        if not (r_min_mm <= r <= r_max_mm):
            continue
        long_axis = _long_axis_of(fp.footprint_id)
        rot_dict = _op_align_rotation(cfg, fp.x_mm, fp.y_mm, mode, long_axis)
        if not rot_dict["success"]:
            continue
        plan.append({
            "ref": ref,
            "r_mm": round(r, 3),
            "from_angle": round(fp.angle_deg, 2),
            "to_angle": rot_dict["rotation_deg"],
            "long_axis": long_axis,
        })
    if not dry_run:
        # Reapply rotations in reverse position order so block_start indices stay valid.
        ordered = sorted(plan, key=lambda d: -fps[d["ref"]].block_start)
        new_text = text
        for item in ordered:
            fp = _index_footprints(new_text).get(item["ref"])
            if fp is None:
                continue
            new_text = _rewrite_footprint_pose(
                new_text, fp, None, None, item["to_angle"],
            )
        _persist(pcb_path, new_text)
    return {
        "success": True,
        "op": "align_outer_components",
        "applied": 0 if dry_run else len(plan),
        "planned": len(plan),
        "plan": plan,
        "dry_run": dry_run,
    }


def _op_add_polar_arc(pcb_path: str, cfg: PolarConfig, net_name: str,
                      layer: str | None, ring: int | None, r_mm: float | None,
                      theta_start_deg: float, theta_end_deg: float,
                      width_mm: float) -> dict[str, Any]:
    from kicad_mcp.tools.pcb_geometry_tools import add_arc_to_pcb_text

    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if layer is None:
        layer = cfg.arc_layer
    r = cfg.ring_to_r(ring) if ring is not None else float(r_mm)
    text = get_text(pcb_path)
    start = _op_polar_to_xy(cfg, ring, r_mm, theta_start_deg)
    end = _op_polar_to_xy(cfg, ring, r_mm, theta_end_deg)
    new_text, result = add_arc_to_pcb_text(
        text,
        start_x_mm=start["x_mm"], start_y_mm=start["y_mm"],
        end_x_mm=end["x_mm"], end_y_mm=end["y_mm"],
        layer=layer, net_name=net_name, width_mm=width_mm,
        center_x_mm=cfg.center_x_mm, center_y_mm=cfg.center_y_mm,
    )
    if result.get("success"):
        _persist(pcb_path, new_text)
        result["op"] = "add_polar_arc"
        result["ring"] = ring
        result["ring_radius_mm"] = round(r, 4)
        result["theta_start_deg"] = start["theta_deg"]
        result["theta_end_deg"] = end["theta_deg"]
    return result


def _op_add_radial_segment(pcb_path: str, cfg: PolarConfig, net_name: str,
                           theta_deg: float, ring_from: int | None,
                           ring_to: int | None, r_from_mm: float | None,
                           r_to_mm: float | None, layer: str | None,
                           width_mm: float) -> dict[str, Any]:
    from kicad_mcp.tools.pcb_patch_tools import add_segment_text

    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if layer is None:
        layer = cfg.radial_layer
    r1 = cfg.ring_to_r(ring_from) if ring_from is not None else float(r_from_mm)
    r2 = cfg.ring_to_r(ring_to) if ring_to is not None else float(r_to_mm)
    if cfg.snap_to_spoke:
        _, theta_deg = cfg.snap_spoke(theta_deg)
    rad = math.radians(theta_deg)
    sx = cfg.center_x_mm + r1 * math.cos(rad)
    sy = cfg.center_y_mm + r1 * math.sin(rad)
    ex = cfg.center_x_mm + r2 * math.cos(rad)
    ey = cfg.center_y_mm + r2 * math.sin(rad)
    text = get_text(pcb_path)
    new_text, result = add_segment_text(
        text, sx, sy, ex, ey, layer, net_name, width_mm,
    )
    if result.get("success"):
        _persist(pcb_path, new_text)
        result["op"] = "add_radial_segment"
        result["theta_deg"] = round(_normalise_deg(theta_deg), 4)
        result["r_from_mm"] = round(r1, 4)
        result["r_to_mm"] = round(r2, 4)
    return result


def _op_add_polar_via(pcb_path: str, cfg: PolarConfig, net_name: str,
                      ring: int | None, r_mm: float | None,
                      spoke_idx: int | None, spoke_deg: float | None,
                      layer_pair: list[str] | None,
                      size_mm: float, drill_mm: float) -> dict[str, Any]:
    from kicad_mcp.tools.pcb_geometry_tools import add_via_to_pcb_text

    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if layer_pair is None:
        layer_pair = [cfg.arc_layer, cfg.radial_layer]
    if spoke_idx is not None:
        theta_deg = _normalise_deg(
            cfg.spoke_offset_deg + spoke_idx * cfg.spoke_step_deg
        )
    elif spoke_deg is not None:
        _, theta_deg = cfg.snap_spoke(spoke_deg)
    else:
        return {"success": False, "error": "Provide spoke_idx or spoke_deg"}
    pos = _op_polar_to_xy(cfg, ring, r_mm, theta_deg)
    if not pos["success"]:
        return pos
    text = get_text(pcb_path)
    new_text, result = add_via_to_pcb_text(
        text, pos["x_mm"], pos["y_mm"], net_name, layer_pair, size_mm, drill_mm,
    )
    if result.get("success"):
        _persist(pcb_path, new_text)
        result["op"] = "add_polar_via"
        result["ring"] = ring
        result["spoke_deg"] = round(theta_deg, 4)
    return result


def _op_list_ring_occupants(pcb_path: str, cfg: PolarConfig,
                            ring: int | None, r_mm: float | None,
                            tolerance_mm: float | None) -> dict[str, Any]:
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    target_r = cfg.ring_to_r(ring) if ring is not None else float(r_mm)
    tol = tolerance_mm if tolerance_mm is not None else cfg.ring_step_mm / 2
    text = get_text(pcb_path)
    fps = _index_footprints(text)
    hits = []
    for ref, fp in fps.items():
        r = math.hypot(fp.x_mm - cfg.center_x_mm, fp.y_mm - cfg.center_y_mm)
        if abs(r - target_r) <= tol:
            theta = math.degrees(
                math.atan2(fp.y_mm - cfg.center_y_mm, fp.x_mm - cfg.center_x_mm)
            )
            hits.append({
                "ref": ref,
                "layer": fp.layer,
                "r_mm": round(r, 4),
                "deviation_mm": round(r - target_r, 4),
                "theta_deg": round(_normalise_deg(theta), 4),
                "footprint_id": fp.footprint_id,
            })
    hits.sort(key=lambda h: h["theta_deg"])
    return {
        "success": True,
        "op": "list_ring_occupants",
        "ring": ring,
        "target_r_mm": round(target_r, 4),
        "tolerance_mm": round(tol, 4),
        "count": len(hits),
        "occupants": hits,
    }


def _op_check_grid_config(pcb_path: str, cfg: PolarConfig) -> dict[str, Any]:
    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    text = get_text(pcb_path)
    # Find board outline (gr_circle on Edge.Cuts)
    m = re.search(
        r'\(gr_circle\s*\(center ([\d.-]+) ([\d.-]+)\)\s*\(end ([\d.-]+) ([\d.-]+)\)[\s\S]{0,300}\(layer "Edge\.Cuts"\)',
        text,
    )
    warnings = []
    detected = {}
    if m:
        cx, cy = float(m.group(1)), float(m.group(2))
        ex, ey = float(m.group(3)), float(m.group(4))
        detected["center_x_mm"] = cx
        detected["center_y_mm"] = cy
        detected["board_radius_mm"] = round(math.hypot(ex - cx, ey - cy), 4)
        if abs(cx - cfg.center_x_mm) > 0.01 or abs(cy - cfg.center_y_mm) > 0.01:
            warnings.append(
                f"Configured center ({cfg.center_x_mm}, {cfg.center_y_mm}) "
                f"≠ detected center ({cx}, {cy})"
            )
    else:
        warnings.append("No (gr_circle) on Edge.Cuts found — board not circular?")
    return {
        "success": True,
        "op": "check_grid_config",
        "configured": {
            "center": [cfg.center_x_mm, cfg.center_y_mm],
            "r_outer_mm": cfg.r_outer_mm,
            "r_inner_mm": cfg.r_inner_mm,
            "ring_count": cfg.ring_count,
            "spoke_count": cfg.spoke_count,
            "arc_layer": cfg.arc_layer,
            "radial_layer": cfg.radial_layer,
        },
        "detected": detected,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# route — pin-to-pin polar connection (arc on arc_layer, radial stubs on
# radial_layer, vias only where a pad does not already reach the routing
# layer). Reads + writes the file ONCE for a whole connection list.
# ---------------------------------------------------------------------------


def _resolve_pad(fps: dict, addr: str):
    """Resolve a ``"REF.PAD"`` address to a ``PadInfo`` (or None)."""
    ref, _, pad = str(addr).rpartition(".")
    if not ref or not pad:
        return None
    fp = fps.get(ref)
    if fp is None:
        return None
    return fp.pads.get(pad)


def _pad_reaches(pad, layer: str) -> bool:
    """True if the pad's copper already spans ``layer`` — i.e. a via is
    NOT needed to drop onto it. THT pads carry ``*.Cu`` (all layers)."""
    return layer in pad.layers or any(L.startswith("*.") for L in pad.layers)


def _route_one_text(text: str, fps: dict, cfg: PolarConfig,
                    conn: dict, defaults: dict) -> tuple[str, dict]:
    """Route one pin-to-pin connection against the in-memory text.

    Topology (clean 2-inner-layer discipline):
        pad → [via padLayer↔radial] → radial stub (In2) → [buried via
        radial↔arc] → arc (In1) → … mirrored on the far end.
    A via is emitted ONLY where the pad does not already reach the layer
    (THT ``*.Cu`` pads need none). When the pad already sits on the ring
    (within ``ring_snap_tol_mm``) the radial stub collapses and a single
    via drops the pad straight onto the arc layer.
    """
    from kicad_mcp.tools.pcb_geometry_tools import (
        add_arc_to_pcb_text, add_via_to_pcb_text,
    )
    from kicad_mcp.tools.pcb_patch_tools import add_segment_text

    frm = conn.get("from") or conn.get("from_ref_pad")
    to = conn.get("to") or conn.get("to_ref_pad")
    if not frm or not to:
        return text, {"success": False,
                      "error": "connection needs 'from' and 'to'"}

    ring = conn.get("ring", defaults.get("ring"))
    r_mm = conn.get("r_mm", defaults.get("r_mm"))
    if ring is not None:
        try:
            R = cfg.ring_to_r(int(ring))
        except ValueError as exc:
            return text, {"success": False, "error": str(exc),
                          "from": frm, "to": to}
    elif r_mm is not None:
        R = float(r_mm)
    else:
        return text, {"success": False, "from": frm, "to": to,
                      "error": "connection needs 'ring' or 'r_mm'"}

    pa = _resolve_pad(fps, frm)
    pb = _resolve_pad(fps, to)
    if pa is None:
        return text, {"success": False, "from": frm, "to": to,
                      "error": f"pad not found: {frm}"}
    if pb is None:
        return text, {"success": False, "from": frm, "to": to,
                      "error": f"pad not found: {to}"}
    na = (pa.net_name or "").strip()
    nb = (pb.net_name or "").strip()
    if not na:
        return text, {"success": False, "from": frm, "to": to,
                      "error": f"{frm} has no net assigned"}
    if na != nb:
        return text, {"success": False, "from": frm, "to": to,
                      "error": (f"pins are on different nets — {frm}={na!r} "
                                f"vs {to}={nb!r}; cannot route")}
    net = pa.net_name

    arc_w = conn.get("arc_width_mm", defaults["arc_width_mm"])
    stub_w = conn.get("stub_width_mm", defaults["stub_width_mm"])
    direction = conn.get("direction", defaults["direction"])
    via_size = defaults["via_size_mm"]
    via_drill = defaults["via_drill_mm"]
    tol = defaults["ring_snap_tol_mm"]
    cx, cy = cfg.center_x_mm, cfg.center_y_mm

    nseg = nvia = 0
    arc_pts: dict[str, tuple[float, float]] = {}
    for key, P in (("A", pa), ("B", pb)):
        phi = math.atan2(P.y_mm - cy, P.x_mm - cx)
        rP = math.hypot(P.x_mm - cx, P.y_mm - cy)
        ring_pt = (cx + R * math.cos(phi), cy + R * math.sin(phi))
        if abs(rP - R) <= tol:
            # Pad already on the ring → arc starts at the pad; one via
            # only if the pad doesn't already reach the arc layer.
            arc_pts[key] = (P.x_mm, P.y_mm)
            if not _pad_reaches(P, cfg.arc_layer):
                text, r = add_via_to_pcb_text(
                    text, P.x_mm, P.y_mm, net,
                    [P.primary_layer, cfg.arc_layer], via_size, via_drill)
                if not r.get("success"):
                    return text, {"success": False, "from": frm, "to": to,
                                  "error": f"via@{key}: {r.get('error')}"}
                nvia += 1
        else:
            # Radial stub on the radial layer from pad out/in to the ring.
            text, r = add_segment_text(
                text, P.x_mm, P.y_mm, ring_pt[0], ring_pt[1],
                cfg.radial_layer, net, stub_w)
            if not r.get("success"):
                return text, {"success": False, "from": frm, "to": to,
                              "error": f"stub@{key}: {r.get('error')}"}
            nseg += 1
            if not _pad_reaches(P, cfg.radial_layer):
                text, r = add_via_to_pcb_text(
                    text, P.x_mm, P.y_mm, net,
                    [P.primary_layer, cfg.radial_layer], via_size, via_drill)
                if not r.get("success"):
                    return text, {"success": False, "from": frm, "to": to,
                                  "error": f"padvia@{key}: {r.get('error')}"}
                nvia += 1
            # Buried via joining the radial stub to the arc layer.
            text, r = add_via_to_pcb_text(
                text, ring_pt[0], ring_pt[1], net,
                [cfg.radial_layer, cfg.arc_layer], via_size, via_drill)
            if not r.get("success"):
                return text, {"success": False, "from": frm, "to": to,
                              "error": f"ringvia@{key}: {r.get('error')}"}
            nvia += 1
            arc_pts[key] = ring_pt

    sA, sB = arc_pts["A"], arc_pts["B"]
    pa_deg = math.degrees(math.atan2(sA[1] - cy, sA[0] - cx))
    pb_deg = math.degrees(math.atan2(sB[1] - cy, sB[0] - cx))
    short_delta = _normalise_deg(pb_deg - pa_deg)
    if direction == "long":
        mid_deg = pa_deg + short_delta / 2.0 + 180.0
        mr = math.radians(mid_deg)
        midpt = (cx + R * math.cos(mr), cy + R * math.sin(mr))
        text, r = add_arc_to_pcb_text(
            text, start_x_mm=sA[0], start_y_mm=sA[1],
            end_x_mm=sB[0], end_y_mm=sB[1], layer=cfg.arc_layer,
            net_name=net, width_mm=arc_w,
            mid_x_mm=midpt[0], mid_y_mm=midpt[1])
        sweep = 360.0 - abs(short_delta)
        mid_ang = _normalise_deg(mid_deg)
    else:
        text, r = add_arc_to_pcb_text(
            text, start_x_mm=sA[0], start_y_mm=sA[1],
            end_x_mm=sB[0], end_y_mm=sB[1], layer=cfg.arc_layer,
            net_name=net, width_mm=arc_w,
            center_x_mm=cx, center_y_mm=cy)
        sweep = abs(short_delta)
        mid_ang = _normalise_deg(pa_deg + short_delta / 2.0)
    if not r.get("success"):
        return text, {"success": False, "from": frm, "to": to,
                      "error": f"arc: {r.get('error')}"}

    return text, {
        "success": True, "from": frm, "to": to, "net": net,
        "ring": ring, "ring_radius_mm": round(R, 4),
        "arc_layer": cfg.arc_layer, "stub_layer": cfg.radial_layer,
        "direction": direction, "sweep_deg": round(sweep, 2),
        "segments": nseg, "vias": nvia,
        # internal — popped by _op_route before returning to caller
        "_R": round(R, 3), "_mid_ang": mid_ang, "_half": sweep / 2.0,
    }


def _op_route(pcb_path: str, cfg: PolarConfig, connections: list,
              defaults: dict, dry_run: bool,
              halt_on_error: bool) -> dict[str, Any]:
    from kicad_mcp.tools.pcb_geometry_tools import (
        _index_footprints as _index_pads,
    )

    pcb_path = to_local_path(pcb_path)
    if not os.path.isfile(pcb_path):
        return {"success": False, "error": f"PCB not found: {pcb_path}"}
    if not isinstance(connections, list) or not connections:
        return {"success": False, "error": "connections must be a non-empty list"}

    text = get_text(pcb_path)
    fps = _index_pads(text)
    results: list[dict] = []
    warnings: list[str] = []
    placed: list[tuple] = []   # (R, mid_ang, half, label) for collision check
    all_ok = True
    tot_arc = tot_seg = tot_via = 0

    for conn in connections:
        new_text, res = _route_one_text(text, fps, cfg, conn, defaults)
        if res.get("success"):
            text = new_text
            R = res.pop("_R"); m = res.pop("_mid_ang"); h = res.pop("_half")
            for (R0, m0, h0, lbl0) in placed:
                if abs(R0 - R) <= max(defaults["ring_snap_tol_mm"], 0.01):
                    if abs(_normalise_deg(m - m0)) < (h + h0 - 1e-6):
                        warnings.append(
                            f"ring r={R}mm: {res['from']}→{res['to']} "
                            f"overlaps {lbl0} angularly")
            placed.append((R, m, h, f"{res['from']}→{res['to']}"))
            tot_arc += 1
            tot_seg += res["segments"]
            tot_via += res["vias"]
        else:
            all_ok = False
            for k in ("_R", "_mid_ang", "_half"):
                res.pop(k, None)
        results.append(res)
        if not res.get("success") and halt_on_error:
            break

    wrote = False
    if all_ok and not dry_run:
        _persist(pcb_path, text)
        wrote = True

    return {
        "success": all_ok, "op": "route", "dry_run": dry_run, "wrote": wrote,
        "count": len(results), "arcs": tot_arc,
        "segments": tot_seg, "vias": tot_via,
        "warnings": warnings, "results": results,
    }


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


_OP_DESCRIPTIONS = """\
Operations (pass via ``op`` parameter):

  * ``polar_to_xy`` — Convert ``(ring|r_mm, theta_deg)`` to world XY.
  * ``xy_to_polar`` — Convert world ``(x_mm, y_mm)`` to ``(ring, theta, closest_spoke)``.
  * ``ring_radius`` — Return radius for ring N.
  * ``align_rotation`` — Compute the rotation that aligns a footprint long axis radially/tangentially.
  * ``place_on_ring`` — Place a footprint at ``(ring|r_mm, theta_deg)`` and auto-rotate.
  * ``place_on_spoke`` — Place a footprint at ``(ring|r_mm, spoke_idx|spoke_deg)``; angle is snapped to spoke.
  * ``align_outer_components`` — Bulk-rotate every footprint outside a ring radius (e.g. parking ring) toward radial-out.
  * ``add_polar_arc`` — Draw a tangential arc on a ring (uses ``arc_layer`` by default).
  * ``add_radial_segment`` — Draw a straight radial segment between two rings on a spoke (uses ``radial_layer`` by default).
  * ``add_polar_via`` — Insert a via at a ``(ring, spoke)`` grid intersection.
  * ``list_ring_occupants`` — List footprints sitting on a ring within ±tolerance.
  * ``check_grid_config`` — Sanity-check the configured grid against the PCB's outline.
  * ``route`` — Pin-to-pin polar connection(s). Pass ``connections=[{from,to,ring}, …]``
    (or ``from_ref_pad``/``to_ref_pad``/``ring`` for one). Emits a tangential arc on
    ``arc_layer`` + radial stubs on ``radial_layer`` + vias only where a pad does not
    already reach the layer (THT ``*.Cu`` pads need none). Net is taken automatically
    from the pins (refuses if the two pins differ). Reads + writes the file ONCE for the
    whole list; ``dry_run=True`` previews counts without writing. Default widths 0.5 mm,
    via 0.45/0.2 mm, shortest arc (``direction="long"`` for the far side).
"""


def register_polar_grid_tools(mcp: FastMCP) -> None:
    """Register the ``polar_grid`` umbrella tool with the MCP server."""

    @mcp.tool()
    def polar_grid(
        op: str,
        pcb_path: str = "",
        # Polar grid configuration (all optional, reference defaults apply)
        center_x_mm: float | None = None,
        center_y_mm: float | None = None,
        r_outer_mm: float | None = None,
        r_inner_mm: float | None = None,
        ring_step_mm: float | None = None,
        ring_count_from: str | None = None,
        spoke_count: int | None = None,
        spoke_offset_deg: float | None = None,
        arc_layer: str | None = None,
        radial_layer: str | None = None,
        snap_to_spoke: bool | None = None,
        snap_to_ring: bool | None = None,
        # Operation-specific parameters
        ref: str = "",
        ring: int | None = None,
        r_mm: float | None = None,
        theta_deg: float | None = None,
        x_mm: float | None = None,
        y_mm: float | None = None,
        spoke_idx: int | None = None,
        spoke_deg: float | None = None,
        mode: str = "radial_out",
        long_axis: str = "auto",
        r_min_mm: float | None = None,
        r_max_mm: float | None = None,
        exempt_refs: list[str] | None = None,
        net_name: str = "",
        layer: str | None = None,
        theta_start_deg: float | None = None,
        theta_end_deg: float | None = None,
        width_mm: float = 0.2,
        ring_from: int | None = None,
        ring_to: int | None = None,
        r_from_mm: float | None = None,
        r_to_mm: float | None = None,
        layer_pair: list[str] | None = None,
        size_mm: float = 0.6,
        drill_mm: float = 0.3,
        tolerance_mm: float | None = None,
        dry_run: bool = False,
        # route op
        connections: list[dict[str, Any]] | None = None,
        from_ref_pad: str = "",
        to_ref_pad: str = "",
        arc_width_mm: float = 0.5,
        stub_width_mm: float = 0.5,
        via_size_mm: float = 0.45,
        via_drill_mm: float = 0.2,
        ring_snap_tol_mm: float = 0.02,
        direction: str = "short",
        halt_on_error: bool = True,
    ) -> dict[str, Any]:
        """Polar-coordinate grid helper for circular PCBs.

        Use this for every place-on-circle / arc-routing / radial-stub
        operation on a circular PCB. The 12 operations under ``op``
        replace the ad-hoc Python snippets that polar layouts otherwise
        require (theta computation, ring lookup, snap-to-spoke,
        center-mode arc midpoint, bulk rotation of outer-ring
        components, etc.).

        Defaults match the reference mainboard (centre 148.5, 105; 31
        rings r=13.5..30 step 0.55; 18 spokes every 20°; arc layer
        ``In1.Cu``, radial layer ``In2.Cu``). Override any field to
        target a different board.

        Args:
            op: Which operation to perform — see list below.
            pcb_path: Path to ``.kicad_pcb`` (required for ops that edit
                the file).
            center_x_mm: Polar grid centre X in mm, board coords (config override; default 148.5).
            center_y_mm: Polar grid centre Y in mm, board coords (config override; default 105.0).
            r_outer_mm: Outer (largest) ring radius in mm (config override; default 30.0).
            r_inner_mm: Inner (smallest) ring radius in mm (config override; default 13.5).
            ring_step_mm: Radial spacing between adjacent rings in mm (config override; default 0.55).
            ring_count_from: Whether ring 1 is the ``"outer"`` (default) or ``"inner"`` radius.
            spoke_count: Number of evenly spaced radial spokes (config override; default 18).
            spoke_offset_deg: Angular offset of spoke 0 in degrees (config override; default 0).
            arc_layer: Default copper layer for tangential arcs (config override; default ``In1.Cu``).
            radial_layer: Default copper layer for radial stubs (config override; default ``In2.Cu``).
            snap_to_spoke: Snap angles to the nearest spoke when True (config override; default True).
            snap_to_ring: Snap radii to the nearest ring when True (config override; default True).
            ref: Footprint reference (place_on_ring, place_on_spoke).
            ring: Ring number (1..ring_count) selecting the target radius.
            r_mm: Absolute target radius in mm (alternative to ``ring``).
            theta_deg: Angle from centre, ``(-180, 180]``.
            x_mm: Cartesian X in mm, board coords (xy_to_polar only).
            y_mm: Cartesian Y in mm, board coords (xy_to_polar only).
            spoke_idx: Spoke index selector (place_on_spoke / add_polar_via).
            spoke_deg: Spoke angle in degrees, snapped to the nearest spoke (place_on_spoke / add_polar_via).
            mode: Rotation mode for align_rotation /
                place_on_*. One of ``radial_out``, ``radial_in``,
                ``tangential_ccw``, ``tangential_cw``.
            long_axis: ``"auto"`` (default, footprint-name regex),
                ``"x"`` (caps/resistors), ``"y"`` (SOIC/SOT-23/TO-252).
            r_min_mm: Inner radius bound in mm for align_outer_components.
            r_max_mm: Outer radius bound in mm for align_outer_components.
            exempt_refs: List of refs to skip in
                align_outer_components (e.g. ``["U1"]`` for an ESP
                module).
            net_name: Net name assigned to the added arc / segment / via copper.
            layer: Override copper layer for add_polar_arc / add_radial_segment
                (defaults to ``arc_layer`` / ``radial_layer`` respectively).
            width_mm: Trace width in mm for add_polar_arc / add_radial_segment (default 0.2).
            theta_start_deg: Arc start angle in degrees (add_polar_arc).
            theta_end_deg: Arc end angle in degrees (add_polar_arc).
            ring_from: Start ring number of a radial segment.
            r_from_mm: Start radius in mm of a radial segment (alternative to ``ring_from``).
            ring_to: End ring number of a radial segment.
            r_to_mm: End radius in mm of a radial segment (alternative to ``ring_to``).
            layer_pair: For add_polar_via (default ``[arc_layer,
                radial_layer]``).
            size_mm: Via pad diameter in mm for add_polar_via (default 0.6).
            drill_mm: Via drill diameter in mm for add_polar_via (default 0.3).
            tolerance_mm: Match radius for list_ring_occupants
                (default ring_step/2).
            dry_run: For align_outer_components and route — plan/preview
                without writing the file.
            connections: For ``route`` — list of
                ``{from, to, ring|r_mm, [direction]}`` dicts to route in
                one read/write pass (e.g.
                ``[{"from": "U_DRV1.5", "to": "C9.1", "ring": 20}]``).
            from_ref_pad: Source pad address ``"REF.PAD"`` for a single
                ``route`` connection instead of ``connections`` (e.g.
                ``"U_DRV1.5"``); combine with ``ring``/``r_mm``.
            to_ref_pad: Destination pad address ``"REF.PAD"`` for a single
                ``route`` connection; combine with ``ring``/``r_mm``.
            arc_width_mm: Arc trace width for ``route`` (mm, default 0.5).
            stub_width_mm: Radial-stub width for ``route`` (mm, default
                0.5).
            via_size_mm: Via pad diameter in mm for ``route`` (default 0.45).
            via_drill_mm: Via drill diameter in mm for ``route`` (default 0.2).
            ring_snap_tol_mm: If a pad sits within this radius of the
                target ring (mm, default 0.02) the radial stub collapses
                and one via drops the pad straight onto the arc layer.
            direction: Arc direction for ``route`` — ``"short"`` (default,
                shorter arc) or ``"long"`` (the far side).
            halt_on_error: For ``route`` — stop at the first failed
                connection (default True) or continue and collect all
                results.

        Returns:
            ``{success, op, ...result fields}``. On error,
            ``{success: False, error: <msg>}``.

        Note on ring numbering: ``ring_count_from="outer"`` (reference
        default) makes ring 1 = ``r_outer`` and ring N =
        ``r_inner``. Counter-intuitive but matches the in-board ring
        labels on Dwgs.User. Switch to ``"inner"`` for the opposite.
        """
        pcb_path = to_local_path(pcb_path)
        cfg = _config_from_kwargs(locals())

        if op == "polar_to_xy":
            if theta_deg is None:
                return {"success": False, "error": "theta_deg required"}
            return _op_polar_to_xy(cfg, ring, r_mm, theta_deg)
        if op == "xy_to_polar":
            if x_mm is None or y_mm is None:
                return {"success": False, "error": "x_mm and y_mm required"}
            return _op_xy_to_polar(cfg, x_mm, y_mm)
        if op == "ring_radius":
            if ring is None:
                return {"success": False, "error": "ring required"}
            return _op_ring_radius(cfg, ring)
        if op == "align_rotation":
            if theta_deg is not None:
                pos = _op_polar_to_xy(cfg, ring, r_mm, theta_deg)
                tx, ty = pos["x_mm"], pos["y_mm"]
            elif x_mm is not None and y_mm is not None:
                tx, ty = x_mm, y_mm
            else:
                return {"success": False, "error": "Provide (x_mm,y_mm) or (ring/r_mm,theta_deg)"}
            la = long_axis if long_axis != "auto" else "x"
            return _op_align_rotation(cfg, tx, ty, mode, la)
        if op == "place_on_ring":
            if not pcb_path or not ref or theta_deg is None:
                return {"success": False, "error": "pcb_path, ref, theta_deg required"}
            return _op_place_on_ring(pcb_path, cfg, ref, ring, r_mm,
                                     theta_deg, mode, long_axis)
        if op == "place_on_spoke":
            if not pcb_path or not ref:
                return {"success": False, "error": "pcb_path, ref required"}
            return _op_place_on_spoke(pcb_path, cfg, ref, spoke_idx,
                                      spoke_deg, ring, r_mm, mode, long_axis)
        if op == "align_outer_components":
            if not pcb_path:
                return {"success": False, "error": "pcb_path required"}
            rmin = r_min_mm if r_min_mm is not None else cfg.r_outer_mm + 1
            rmax = r_max_mm if r_max_mm is not None else 1000.0
            return _op_align_outer_components(pcb_path, cfg, rmin, rmax,
                                              mode, exempt_refs or [], dry_run)
        if op == "add_polar_arc":
            if not pcb_path or not net_name or theta_start_deg is None or theta_end_deg is None:
                return {"success": False, "error": "pcb_path, net_name, theta_start_deg, theta_end_deg required"}
            if ring is None and r_mm is None:
                return {"success": False, "error": "ring or r_mm required"}
            return _op_add_polar_arc(pcb_path, cfg, net_name, layer, ring, r_mm,
                                     theta_start_deg, theta_end_deg, width_mm)
        if op == "add_radial_segment":
            if not pcb_path or not net_name or theta_deg is None:
                return {"success": False, "error": "pcb_path, net_name, theta_deg required"}
            if (ring_from is None and r_from_mm is None) or (ring_to is None and r_to_mm is None):
                return {"success": False, "error": "ring_from/r_from_mm and ring_to/r_to_mm required"}
            return _op_add_radial_segment(pcb_path, cfg, net_name, theta_deg,
                                          ring_from, ring_to, r_from_mm, r_to_mm,
                                          layer, width_mm)
        if op == "add_polar_via":
            if not pcb_path or not net_name:
                return {"success": False, "error": "pcb_path, net_name required"}
            if ring is None and r_mm is None:
                return {"success": False, "error": "ring or r_mm required"}
            return _op_add_polar_via(pcb_path, cfg, net_name, ring, r_mm,
                                     spoke_idx, spoke_deg, layer_pair,
                                     size_mm, drill_mm)
        if op == "list_ring_occupants":
            if not pcb_path:
                return {"success": False, "error": "pcb_path required"}
            if ring is None and r_mm is None:
                return {"success": False, "error": "ring or r_mm required"}
            return _op_list_ring_occupants(pcb_path, cfg, ring, r_mm, tolerance_mm)
        if op == "check_grid_config":
            if not pcb_path:
                return {"success": False, "error": "pcb_path required"}
            return _op_check_grid_config(pcb_path, cfg)
        if op == "route":
            if not pcb_path:
                return {"success": False, "error": "pcb_path required"}
            conns = connections
            if not conns:
                if not from_ref_pad or not to_ref_pad:
                    return {"success": False, "error": (
                        "route needs connections=[{from,to,ring}, …] "
                        "or from_ref_pad + to_ref_pad")}
                conns = [{"from": from_ref_pad, "to": to_ref_pad,
                          "ring": ring, "r_mm": r_mm, "direction": direction}]
            defaults = {
                "ring": ring, "r_mm": r_mm,
                "arc_width_mm": arc_width_mm, "stub_width_mm": stub_width_mm,
                "via_size_mm": via_size_mm, "via_drill_mm": via_drill_mm,
                "ring_snap_tol_mm": ring_snap_tol_mm, "direction": direction,
            }
            return _op_route(pcb_path, cfg, conns, defaults,
                             dry_run, halt_on_error)
        return {
            "success": False,
            "error": f"Unknown op {op!r}",
            "available_ops": [
                "polar_to_xy", "xy_to_polar", "ring_radius", "align_rotation",
                "place_on_ring", "place_on_spoke", "align_outer_components",
                "add_polar_arc", "add_radial_segment", "add_polar_via",
                "list_ring_occupants", "check_grid_config", "route",
            ],
        }
