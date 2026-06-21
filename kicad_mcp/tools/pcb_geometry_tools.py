# SPDX-License-Identifier: GPL-3.0-or-later
"""
Headless PCB geometry + routing helpers.

These tools complement ``pcb_patch_tools`` (text-only PCB editing without
SWIG ``pcbnew``) by adding the geometry/routing primitives that the
text-patcher could not previously do correctly:

* **compute_pad_world_positions** — parses every pad in a ``.kicad_pcb``
  and emits its absolute world coordinate, correctly accounting for
  footprint rotation **and** ``B.Cu`` flip (the latter is the bug that
  defeated earlier scripted routing attempts).
* **add_track_to_pcb** — drops a single straight track from one pad to
  another, with optional through-via for layer changes. Uses the same
  geometry pipeline as ``compute_pad_world_positions`` so the endpoints
  always land on the actual pad centres.
* **add_zone_pour_to_pcb** — adds a filled-zone pour bound to a net on a
  given copper layer, defined by a polygon outline. KiCad fills the pour
  the next time the file is opened in the GUI / ``kicad-cli pcb drc``
  is run with ``--fill-zones``.

All three operate on the file system and never touch the SWIG ``pcbnew``
bindings, so they are usable in batch / CI pipelines and free of the
``SwigPyObject`` quirks observed on KiCad 10's Python module.

Copper-adding tools fold a clearance effect-echo into their result (the
``clearance`` key) via the shared clearance engine (``clearance_tools``) —
a post-mutation copper-short check so the agent need not make a separate
verify call. The check runs in the warm pcbnew daemon (a subprocess); the
tools themselves stay pcbnew-free, and when pcbnew is absent the echo is
recorded as ``{checked: False}`` and the edit is otherwise untouched. Pass
``check_clearance=False`` to skip it (e.g. inside a tight placement loop —
verify once at the end instead).
"""

from __future__ import annotations

import json
import math
import os
import re
import uuid as uuid_lib
from dataclasses import dataclass
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from kicad_mcp.cache import get_text, put_text
from kicad_mcp.tools.clearance_tools import (
    attach_clearance, arc_specs, seg_spec, via_spec,
)
from kicad_mcp.utils.path_env import to_local_path
from kicad_mcp.utils.pcb_geometry import pcb_local_to_world
from kicad_mcp.utils.pcb_net_format import ensure_net_tag, pcb_net_format


# ---------------------------------------------------------------------------
# Text-function registry (Universal Callable convention).
# Every file-edit tool here exposes a pure ``fn(pcb_text, **args) ->
# (new_text, result_dict)`` counterpart so the generic ``pcb_batch`` tool
# can chain operations in one open/write cycle.
# ---------------------------------------------------------------------------

PCB_GEOMETRY_TEXT_FNS: dict[str, Callable[..., tuple[str, dict[str, Any]]]] = {}


def _register_text_fn(name: str) -> Callable[[Callable], Callable]:
    def deco(fn: Callable) -> Callable:
        PCB_GEOMETRY_TEXT_FNS[name] = fn
        return fn
    return deco


# ---------------------------------------------------------------------------
# Generic S-expression depth-balanced block walker (kept local to avoid an
# import cycle with pcb_patch_tools — same shape, identical semantics).
# ---------------------------------------------------------------------------


def _block_end(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


_FOOTPRINT_HEADER_RE = re.compile(r'\(footprint\s+"([^"]*)"')
_REF_PROP_RE = re.compile(r'\(property "Reference" "([^"]+)"')
_FIRST_AT_RE = re.compile(r'\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)')
_LAYER_TAG_RE = re.compile(r'\(layer "([^"]+)"\)')
_PAD_HEADER_RE = re.compile(r'\(pad\s+"([^"]+)"')
_PAD_AT_RE = re.compile(r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\s*\)')
_PAD_LAYERS_RE = re.compile(r'\(layers\s+([^\)]*)\)')
_PAD_NET_RE = re.compile(r'\(net\s+(\d+)\s+"([^"]*)"\)')
# String-form pad net tag (KiCad 10 short form, no top-level index table).
_PAD_NET_STR_RE = re.compile(r'\(net\s+"((?:[^"\\]|\\.)*)"\)')


# ---------------------------------------------------------------------------
# Footprint + pad data classes
# ---------------------------------------------------------------------------


@dataclass
class PadInfo:
    name: str           # pad name (e.g. "1", "29", "GND")
    x_mm: float         # absolute world position
    y_mm: float
    layers: list[str]   # raw layer-set, e.g. ["F.Cu", "F.Mask"]
    primary_layer: str  # the first copper layer in the set (or "" if none)
    net_id: int | None
    net_name: str | None


@dataclass
class FootprintInfo:
    ref: str            # reference designator (e.g. "U2")
    fp_x: float         # footprint origin world position
    fp_y: float
    fp_rot: float       # rotation in degrees (counter-clockwise)
    fp_layer: str       # "F.Cu" or "B.Cu" (footprint primary layer)
    pads: dict[str, PadInfo]


def _parse_layers(payload: str) -> list[str]:
    """Pad layer list payload: e.g. ``"F.Cu" "F.Mask" "F.Paste"`` →
    ``["F.Cu", "F.Mask", "F.Paste"]`` (preserves order)."""
    return re.findall(r'"([^"]+)"', payload)


def _primary_copper_layer(layers: list[str], fp_layer: str) -> str:
    """Pick the copper layer the pad is actually on."""
    for L in layers:
        if L.endswith(".Cu"):
            return L
    # ``"*.Cu"`` wildcards (THT / NPTH pads): copper layer is determined by
    # the surrounding footprint layer.
    if any(L == "*.Cu" or L.startswith("*.") for L in layers):
        return fp_layer
    return ""


def _transform_pad_world(
    fp_x: float, fp_y: float, fp_rot: float, fp_layer: str,
    pad_rel_x: float, pad_rel_y: float,
) -> tuple[float, float]:
    """Convert pad-relative coordinates into absolute world coordinates.

    KiCad stores pad ``(at)`` relative to the footprint origin **after**
    ``FOOTPRINT::Flip`` has already mirrored the X axis for B.Cu
    placements. By the time we read the file, the on-disk ``pad_rel_x``
    IS the post-flip value — no further mirror is applied here.
    Rotation is applied in the same direction regardless of side.

    Bug (closed): the previous version passed ``flipped=(fp_layer ==
    "B.Cu")`` to :func:`pcb_local_to_world`, which mirrored the X axis a
    *second* time. The result disagreed with DRC and pcbnew by the full
    pad-pitch — pin 1 and pin 16 of a SOIC-16 were swapped in
    ``compute_pad_world_positions`` output. Reproducer captured in
    ``TestPadWorldTransform.test_bcu_realistic_soic_pin1`` (reference
    U_597 at fp=(129.102, 96.525) rot=-113.6°, file-pad-1 at
    (-2.475, -4.445) → world (134.166, 96.037) per DRC, not
    (132.184, 100.573)).

    The math matches KiCad's internal ``RotatePoint(point, angle)`` (see
    ``libs/kimath/include/geometry/rotation.h``) which applies a *math-CW*
    rotation matrix:

    .. code-block:: text

        wx = lx · cos(rot) + ly · sin(rot)
        wy = -lx · sin(rot) + ly · cos(rot)

    KiCad's screen Y axis grows downward, so a "math-CW" formula produces
    the visual-CCW result users expect (``rot=90°`` rotates the +X axis
    visually UP — i.e. into smaller screen-y). Earlier text-only routing
    attempts that applied the *math-CCW* formula instead disagreed with
    DRC by 0.4 mm for a 0402 at 90° rotation; this is fixed by delegating
    to :func:`kicad_mcp.utils.pcb_geometry.pcb_local_to_world`, the single
    canonical local-to-world helper shared across the codebase.
    """
    # NOTE: ``flipped=False`` regardless of layer — the on-disk pad
    # rel-position has already been mirrored by KiCad when the
    # footprint was flipped to B.Cu. Do NOT mirror again here.
    del fp_layer  # consulted only by the (closed) double-flip bug
    return pcb_local_to_world(
        (fp_x, fp_y), fp_rot, pad_rel_x, pad_rel_y,
        flipped=False,
    )


def _parse_pad_block(
    block: str, fp_x: float, fp_y: float, fp_rot: float, fp_layer: str,
) -> PadInfo | None:
    name_m = _PAD_HEADER_RE.match(block)
    if not name_m:
        return None
    at_m = _PAD_AT_RE.search(block)
    if not at_m:
        return None
    rel_x = float(at_m.group(1))
    rel_y = float(at_m.group(2))
    layers_m = _PAD_LAYERS_RE.search(block)
    layers = _parse_layers(layers_m.group(1)) if layers_m else []
    primary = _primary_copper_layer(layers, fp_layer)
    net_m = _PAD_NET_RE.search(block)
    if net_m:
        nid = int(net_m.group(1))
        nname = net_m.group(2)
    else:
        # String-form short tag — no numeric index in this PCB.
        str_m = _PAD_NET_STR_RE.search(block)
        nid = None
        nname = str_m.group(1) if str_m else None
    wx, wy = _transform_pad_world(fp_x, fp_y, fp_rot, fp_layer, rel_x, rel_y)
    return PadInfo(
        name=name_m.group(1), x_mm=wx, y_mm=wy, layers=layers,
        primary_layer=primary, net_id=nid, net_name=nname,
    )


def _iter_footprints(pcb_text: str):
    """Yield every ``(footprint …)`` block start/end span (top-level only)."""
    pos = 0
    while True:
        idx = pcb_text.find("(footprint", pos)
        if idx < 0:
            return
        end = _block_end(pcb_text, idx)
        yield idx, end
        pos = end


def _parse_footprint(block: str) -> FootprintInfo | None:
    ref_m = _REF_PROP_RE.search(block)
    if not ref_m:
        return None
    ref = ref_m.group(1)
    at_m = _FIRST_AT_RE.search(block)
    fp_x = float(at_m.group(1)) if at_m else 0.0
    fp_y = float(at_m.group(2)) if at_m else 0.0
    fp_rot = float(at_m.group(3)) if at_m and at_m.group(3) else 0.0
    layer_m = _LAYER_TAG_RE.search(block)
    fp_layer = layer_m.group(1) if layer_m else "F.Cu"

    pads: dict[str, PadInfo] = {}
    k = 0
    while k < len(block):
        if block.startswith("(pad ", k) or block.startswith("(pad\t", k):
            end = _block_end(block, k)
            info = _parse_pad_block(
                block[k:end], fp_x, fp_y, fp_rot, fp_layer,
            )
            if info is not None:
                pads[info.name] = info
            k = end
        else:
            k += 1
    return FootprintInfo(
        ref=ref, fp_x=fp_x, fp_y=fp_y, fp_rot=fp_rot,
        fp_layer=fp_layer, pads=pads,
    )


def _index_footprints(pcb_text: str) -> dict[str, FootprintInfo]:
    """Build ``{ref: FootprintInfo}`` for every footprint in the PCB."""
    out: dict[str, FootprintInfo] = {}
    for s, e in _iter_footprints(pcb_text):
        info = _parse_footprint(pcb_text[s:e])
        if info is not None:
            out[info.ref] = info
    return out


# ---------------------------------------------------------------------------
# Net-id lookup — delegated to ``kicad_mcp.utils.pcb_net_format``.
# ---------------------------------------------------------------------------


def _ensure_net(pcb_text: str, net_name: str) -> tuple[str, int]:
    """Return ``(updated_text, net_id)`` for an **index-form** PCB.

    Kept for backwards compatibility with tests / external callers that
    assume the indexed net table. Inside this module the format-aware
    :func:`kicad_mcp.utils.pcb_net_format.ensure_net_tag` is used —
    that helper falls back to indexed form when the PCB is already in
    indexed form, so the behaviour observed by such callers is
    unchanged.
    """
    new_text, _tag, _fmt, idx = ensure_net_tag(pcb_text, net_name)
    if idx is None:
        # String-form PCB — fabricate an index 0 so legacy callers get a
        # consistent int. Real string-form workflows should go through
        # ``ensure_net_tag`` directly.
        return new_text, 0
    return new_text, idx


# ---------------------------------------------------------------------------
# S-expression emitters for tracks / vias / zones
# ---------------------------------------------------------------------------


def _segment_block(
    p1: tuple[float, float], p2: tuple[float, float],
    width_mm: float, layer: str, net_tag: str,
) -> str:
    return (
        "\t(segment\n"
        f"\t\t(start {p1[0]:.6f} {p1[1]:.6f})\n"
        f"\t\t(end {p2[0]:.6f} {p2[1]:.6f})\n"
        f"\t\t(width {width_mm:.6f})\n"
        f'\t\t(layer "{layer}")\n'
        f"\t\t{net_tag}\n"
        f'\t\t(uuid "{uuid_lib.uuid4()}")\n'
        "\t)\n"
    )


def _circumradius(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    """Return the circumcircle radius of triangle ``(a, b, c)`` in mm.

    Used for diagnostics in ``add_arc_to_pcb`` when the caller supplied
    an explicit midpoint. Falls back to ``0.0`` for collinear points
    (which would also fail to define an arc and is therefore the right
    "I cannot tell you a radius" sentinel).
    """
    ax, ay = a; bx, by = b; cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return 0.0
    ux = ((ax * ax + ay * ay) * (by - cy)
          + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx)
          + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    return math.hypot(ax - ux, ay - uy)


def _arc_block(
    start: tuple[float, float],
    mid: tuple[float, float],
    end: tuple[float, float],
    width_mm: float, layer: str, net_tag: str,
) -> str:
    return (
        "\t(arc\n"
        f"\t\t(start {start[0]:.6f} {start[1]:.6f})\n"
        f"\t\t(mid {mid[0]:.6f} {mid[1]:.6f})\n"
        f"\t\t(end {end[0]:.6f} {end[1]:.6f})\n"
        f"\t\t(width {width_mm:.6f})\n"
        f'\t\t(layer "{layer}")\n'
        f"\t\t{net_tag}\n"
        f'\t\t(uuid "{uuid_lib.uuid4()}")\n'
        "\t)\n"
    )


def _via_block(
    pos: tuple[float, float], net_tag: str,
    drill_mm: float = 0.3, size_mm: float = 0.6,
    layer_pair: tuple[str, str] = ("F.Cu", "B.Cu"),
) -> str:
    # KiCad reads the via TYPE from an explicit token after "(via", NOT from
    # the (layers ...) pair. Without it a buried/blind via loads as a plain
    # through via. Outer copper = F.Cu / B.Cu: both outer -> through (no
    # token); exactly one outer -> "blind"; none (inner<->inner) -> "buried".
    _outer = {"F.Cu", "B.Cu"}
    _n_outer = sum(1 for _l in layer_pair if _l in _outer)
    _via_open = "\t(via\n" if _n_outer == 2 else (
        "\t(via blind\n" if _n_outer == 1 else "\t(via buried\n"
    )
    return (
        _via_open
        + f"\t\t(at {pos[0]:.6f} {pos[1]:.6f})\n"
        f"\t\t(size {size_mm:.6f})\n"
        f"\t\t(drill {drill_mm:.6f})\n"
        f'\t\t(layers "{layer_pair[0]}" "{layer_pair[1]}")\n'
        f"\t\t{net_tag}\n"
        f'\t\t(uuid "{uuid_lib.uuid4()}")\n'
        "\t)\n"
    )


def _zone_block(
    net_tag: str, net_name: str, layer: str,
    polygon_xy: list[tuple[float, float]],
    include_net_name_line: bool = True,
) -> str:
    pts_lines = "\n".join(f"\t\t\t\t(xy {x:.6f} {y:.6f})" for x, y in polygon_xy)
    net_name_line = (
        f'\t\t(net_name "{net_name}")\n' if include_net_name_line else ""
    )
    return (
        "\t(zone\n"
        f"\t\t{net_tag}\n"
        f"{net_name_line}"
        f'\t\t(layer "{layer}")\n'
        f'\t\t(uuid "{uuid_lib.uuid4()}")\n'
        "\t\t(hatch edge 0.5)\n"
        '\t\t(connect_pads (clearance 0.2))\n'
        '\t\t(min_thickness 0.2)\n'
        '\t\t(filled_areas_thickness no)\n'
        "\t\t(polygon\n"
        "\t\t\t(pts\n"
        f"{pts_lines}\n"
        "\t\t\t)\n"
        "\t\t)\n"
        "\t)\n"
    )


def _insert_before_root_close(pcb_text: str, blob: str) -> str:
    """Insert ``blob`` just before the final closing ``)`` of the
    ``(kicad_pcb …)`` root expression."""
    last = pcb_text.rstrip().rfind(")")
    return pcb_text[:last] + blob + pcb_text[last:]


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pure text-mutation functions (Universal Callable companions).
# ---------------------------------------------------------------------------


@_register_text_fn("add_arc_to_pcb")
def add_arc_to_pcb_text(
    pcb_text: str,
    start_x_mm: float, start_y_mm: float,
    end_x_mm: float, end_y_mm: float,
    layer: str,
    net_name: str,
    width_mm: float = 0.25,
    center_x_mm: float = float("nan"),
    center_y_mm: float = float("nan"),
    mid_x_mm: float = float("nan"),
    mid_y_mm: float = float("nan"),
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``add_arc_to_pcb``."""
    from kicad_mcp.utils.pcb_geometry import short_arc_mid_xy

    center_given = not (math.isnan(center_x_mm) or math.isnan(center_y_mm))
    mid_given = not (math.isnan(mid_x_mm) or math.isnan(mid_y_mm))
    if center_given == mid_given:
        return pcb_text, {
            "success": False,
            "error": "Specify exactly one of (center_x_mm, center_y_mm) "
                     "or (mid_x_mm, mid_y_mm).",
        }

    start = (float(start_x_mm), float(start_y_mm))
    end = (float(end_x_mm), float(end_y_mm))

    if center_given:
        center = (float(center_x_mm), float(center_y_mm))
        r_start = math.hypot(start[0] - center[0], start[1] - center[1])
        r_end = math.hypot(end[0] - center[0], end[1] - center[1])
        # Real endpoints snapped to pads/vias rarely sit at exactly the
        # same radius — sub-µm float drift, or a pad a few µm off the
        # nominal ring. Accept up to ±50 µm of mismatch and place the arc
        # on the *mean* radius so it bisects the gap. Beyond that the two
        # points clearly don't share a circle and the request is rejected.
        _RADIUS_TOL_MM = 0.050
        if abs(r_start - r_end) > _RADIUS_TOL_MM:
            return pcb_text, {
                "success": False,
                "error": (
                    "start and end must be equidistant from centre "
                    f"(within {_RADIUS_TOL_MM * 1000:.0f} µm) — got "
                    f"r(start)={r_start:.4f}, r(end)={r_end:.4f}, "
                    f"Δ={abs(r_start - r_end) * 1000:.1f} µm"
                ),
            }
        radius = 0.5 * (r_start + r_end)
        mid = short_arc_mid_xy(start, end, center, radius=radius)
    else:
        mid = (float(mid_x_mm), float(mid_y_mm))
        radius = _circumradius(start, mid, end)

    if start == end:
        return pcb_text, {
            "success": False,
            "error": "start and end coincide — degenerate arc.",
        }

    new_text, net_tag, net_fmt, net_id = ensure_net_tag(pcb_text, net_name)
    new_text = _insert_before_root_close(
        new_text,
        _arc_block(start, mid, end, float(width_mm), layer, net_tag),
    )
    return new_text, {
        "success": True,
        "arc_added": 1,
        "net_id": net_id,
        "net_name": net_name,
        "net_format": net_fmt,
        "layer": layer,
        "start": {"x_mm": round(start[0], 4), "y_mm": round(start[1], 4)},
        "mid": {"x_mm": round(mid[0], 4), "y_mm": round(mid[1], 4)},
        "end": {"x_mm": round(end[0], 4), "y_mm": round(end[1], 4)},
        "radius_mm": round(radius, 4),
        "width_mm": round(float(width_mm), 4),
    }


@_register_text_fn("add_via_to_pcb")
def add_via_to_pcb_text(
    pcb_text: str,
    x_mm: float, y_mm: float,
    net_name: str,
    layer_pair: list[str] | None = None,
    size_mm: float = 0.6,
    drill_mm: float = 0.3,
) -> tuple[str, dict[str, Any]]:
    """Pure text transform behind ``add_via_to_pcb``."""
    if layer_pair is None:
        pair = ("F.Cu", "B.Cu")
    else:
        if (not isinstance(layer_pair, list)
                or len(layer_pair) != 2
                or not all(isinstance(L, str) for L in layer_pair)):
            return pcb_text, {
                "success": False,
                "error": "layer_pair must be a list of exactly two "
                         "layer-name strings",
            }
        if layer_pair[0] == layer_pair[1]:
            return pcb_text, {
                "success": False,
                "error": "layer_pair must reference two distinct layers",
            }
        pair = (layer_pair[0], layer_pair[1])

    new_text, net_tag, net_fmt, net_id = ensure_net_tag(pcb_text, net_name)

    new_text = _insert_before_root_close(
        new_text,
        _via_block(
            (float(x_mm), float(y_mm)), net_tag,
            drill_mm=float(drill_mm),
            size_mm=float(size_mm),
            layer_pair=pair,
        ),
    )
    return new_text, {
        "success": True,
        "at": {"x_mm": round(float(x_mm), 4),
               "y_mm": round(float(y_mm), 4)},
        "net_id": net_id,
        "net_name": net_name,
        "net_format": net_fmt,
        "layer_pair": list(pair),
        "size_mm": round(float(size_mm), 4),
        "drill_mm": round(float(drill_mm), 4),
    }


def register_pcb_geometry_tools(mcp: FastMCP) -> None:
    """Register headless geometry/routing tools with the MCP server."""

    @mcp.tool()
    def compute_pad_world_positions(pcb_path: str) -> dict[str, Any]:
        """Read a ``.kicad_pcb`` and return absolute world coordinates of
        every pad, accounting for footprint rotation and ``B.Cu`` flip.

        Use as a building block for any external routing logic that needs
        to know where pads actually sit on the board — the rotation+flip
        math is the piece that earlier text-only routing attempts got
        wrong.

        Args:
            pcb_path: Path to a ``.kicad_pcb``.

        Returns:
            Dict with ``success``, ``footprints`` (ref → list of pad dicts),
            ``footprint_count`` and ``pad_count``.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        text = get_text(pcb_path)
        idx = _index_footprints(text)
        out_fps: dict[str, list[dict[str, Any]]] = {}
        pad_count = 0
        for ref, fp in idx.items():
            entries = []
            for pname, pad in fp.pads.items():
                entries.append(
                    {
                        "pin": pname,
                        "x_mm": round(pad.x_mm, 4),
                        "y_mm": round(pad.y_mm, 4),
                        "primary_layer": pad.primary_layer,
                        "layers": pad.layers,
                        "net_id": pad.net_id,
                        "net_name": pad.net_name,
                    }
                )
                pad_count += 1
            out_fps[ref] = entries
        return {
            "success": True,
            "pcb_path": pcb_path,
            "footprint_count": len(idx),
            "pad_count": pad_count,
            "footprints": out_fps,
        }

    @mcp.tool()
    def add_track_to_pcb(
        pcb_path: str,
        ref1: str, pin1: str,
        ref2: str, pin2: str,
        layer: str = "",
        width_mm: float = 0.25,
        with_via: bool = True,
        net_name: str = "",
        via_layers: list[str] | None = None,
        via_size_mm: float = 0.6,
        via_drill_mm: float = 0.3,
        check_clearance: bool = True,
    ) -> dict[str, Any]:
        """Insert a straight track between two pads in a ``.kicad_pcb``.

        Pad world positions are recomputed via the same flip-aware
        transformation that ``compute_pad_world_positions`` exposes, so the
        track endpoints always land on the actual pad centres, even when
        either footprint sits on B.Cu.

        Use this instead of emitting the ``(segment …)`` block manually —
        the helper looks up pad world coords correctly (rotation + flip
        aware), validates that both pads exist, and threads the net
        through.

        Args:
            pcb_path: Path to a ``.kicad_pcb``.
            ref1, pin1: Source pad.
            ref2, pin2: Destination pad.
            layer: Track layer (default: source pad's primary copper layer).
            width_mm: Track width.
            with_via: If true and the two pads sit on different copper
                layers, drop a via at the destination point. Its layer
                pair is controlled by ``via_layers``.
            net_name: Override net assignment. Default: take net from the
                source pad's net tag (must be net-tagged via
                ``patch_pcb_nets_from_netlist`` first); if absent and not
                supplied, the track is dropped on net 0 (no-connect).
            via_layers: Two-element list ``[outer, inner]`` (or any pair)
                naming the via's layer pair. Default ``None`` =
                through-via on ``["F.Cu", "B.Cu"]``. Use e.g.
                ``["In1.Cu", "In2.Cu"]`` for a buried via that connects
                only the two inner layers without disturbing F.Cu /
                B.Cu — useful for inner-layer-switch routing on dense
                4-layer boards. **Manufacturing caveat**: most low-cost
                fab houses (JLCPCB / PCBWay basic) charge extra for
                blind / buried vias; verify your fab supports the
                layer pair you request before relying on this in
                production.
            via_size_mm: Via diameter in mm (default 0.6).
            via_drill_mm: Via drill diameter in mm (default 0.3).
            check_clearance: If True (default), run the clearance engine on
                the new track/via and fold the result into the ``clearance``
                key. Set False to skip (e.g. mid-loop; verify once at the end).

        Returns:
            Dict with ``success``, ``segments_added``, ``vias_added``,
            ``net_id``, ``layer``, ``via_layers`` (the actual pair used,
            or ``None`` if no via), ``from``/``to`` (with world coords), and
            ``clearance`` (engine effect-echo: ``{checked, ok,
            violation_count, violations}``).
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        text = get_text(pcb_path)

        idx = _index_footprints(text)
        fp1 = idx.get(ref1)
        fp2 = idx.get(ref2)
        if fp1 is None or fp2 is None:
            return {
                "success": False,
                "error": f"Footprint(s) not found: "
                         f"{ref1 if fp1 is None else ''} "
                         f"{ref2 if fp2 is None else ''}".strip(),
            }
        pad1 = fp1.pads.get(pin1)
        pad2 = fp2.pads.get(pin2)
        if pad1 is None or pad2 is None:
            return {
                "success": False,
                "error": (
                    f"Pad not found: {ref1}.{pin1}"
                    f"{' (ok)' if pad1 else ' (missing)'} / "
                    f"{ref2}.{pin2}"
                    f"{' (ok)' if pad2 else ' (missing)'}"
                ),
            }

        # Decide net.
        if net_name:
            text, net_tag, net_fmt, net_id = ensure_net_tag(text, net_name)
            net_used = net_name
        elif pad1.net_name:
            text, net_tag, net_fmt, net_id = ensure_net_tag(text, pad1.net_name)
            net_used = pad1.net_name
        elif pad2.net_name:
            text, net_tag, net_fmt, net_id = ensure_net_tag(text, pad2.net_name)
            net_used = pad2.net_name
        else:
            net_fmt = pcb_net_format(text)
            net_tag, net_id, net_used = "(net 0)", 0, ""

        # Decide layer.
        chosen_layer = layer or pad1.primary_layer or "F.Cu"

        # Resolve / validate the via layer pair (when applicable).
        via_pair: tuple[str, str] | None = None
        if via_layers is not None:
            if (not isinstance(via_layers, list)
                    or len(via_layers) != 2
                    or not all(isinstance(L, str) for L in via_layers)):
                return {
                    "success": False,
                    "error": "via_layers must be a list of exactly two "
                             "layer-name strings, e.g. "
                             "['In1.Cu', 'In2.Cu']",
                }
            if via_layers[0] == via_layers[1]:
                return {
                    "success": False,
                    "error": "via_layers must reference two distinct layers",
                }
            via_pair = (via_layers[0], via_layers[1])
        else:
            via_pair = ("F.Cu", "B.Cu")

        blob = _segment_block(
            (pad1.x_mm, pad1.y_mm), (pad2.x_mm, pad2.y_mm),
            width_mm, chosen_layer, net_tag,
        )
        vias_added = 0
        chosen_via_layers: tuple[str, str] | None = None
        if with_via and pad1.primary_layer and pad2.primary_layer \
                and pad1.primary_layer != pad2.primary_layer:
            blob += _via_block(
                (pad2.x_mm, pad2.y_mm), net_tag,
                drill_mm=float(via_drill_mm),
                size_mm=float(via_size_mm),
                layer_pair=via_pair,
            )
            vias_added = 1
            chosen_via_layers = via_pair
        text = _insert_before_root_close(text, blob)

        with open(pcb_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        put_text(pcb_path, text)

        result = {
            "success": True,
            "pcb_path": pcb_path,
            "segments_added": 1,
            "vias_added": vias_added,
            "via_layers": list(chosen_via_layers) if chosen_via_layers else None,
            "net_id": net_id,
            "net_name": net_used,
            "net_format": net_fmt,
            "layer": chosen_layer,
            "from": {
                "ref": ref1, "pin": pin1,
                "x_mm": round(pad1.x_mm, 4), "y_mm": round(pad1.y_mm, 4),
                "layer": pad1.primary_layer,
            },
            "to": {
                "ref": ref2, "pin": pin2,
                "x_mm": round(pad2.x_mm, 4), "y_mm": round(pad2.y_mm, 4),
                "layer": pad2.primary_layer,
            },
        }
        items = [seg_spec(pad1.x_mm, pad1.y_mm, pad2.x_mm, pad2.y_mm,
                          net_used, chosen_layer, width_mm)]
        if chosen_via_layers:
            items.append(via_spec(pad2.x_mm, pad2.y_mm, net_used,
                                  chosen_via_layers, via_size_mm))
        return attach_clearance(result, pcb_path, items, enabled=check_clearance)

    @mcp.tool()
    def add_arc_to_pcb(
        pcb_path: str,
        start_x_mm: float, start_y_mm: float,
        end_x_mm: float, end_y_mm: float,
        layer: str,
        net_name: str,
        width_mm: float = 0.25,
        center_x_mm: float = float("nan"),
        center_y_mm: float = float("nan"),
        mid_x_mm: float = float("nan"),
        mid_y_mm: float = float("nan"),
        dry_run: bool = False,
        check_clearance: bool = True,
    ) -> dict[str, Any]:
        """Insert a circular arc segment into a ``.kicad_pcb``.

        Use this for concentric semicircles, fan-outs around a centre,
        and any routing that needs a curved trace — e.g. the inner-
        layer halfcircle connecting two THT pads on opposite sides of
        a coil ring. KiCad arcs are stored as ``(arc (start) (mid)
        (end))`` and the renderer picks the unique circular arc passing
        through all three points; supply the midpoint on the *wrong*
        half of the circle and the arc draws the long way around.
        Use this instead of emitting the arc S-expression yourself.

        Two ways to specify the arc:

        * **Center mode** (recommended) — pass ``(center_x_mm,
          center_y_mm)`` and the tool computes the *short-way* midpoint
          via :func:`kicad_mcp.utils.pcb_geometry.short_arc_mid_xy`,
          eliminating the long-way-around bug. ``start`` and ``end`` may
          differ in radius by up to ±50 µm (real pads/vias are never
          perfectly equidistant); the arc is placed on the *mean*
          radius. A larger mismatch is rejected.
        * **Explicit mid** — pass ``(mid_x_mm, mid_y_mm)`` directly.
          Use when you specifically need the long-way arc; the tool
          forwards the mid you provide.

        Exactly one of the two modes must be specified; passing both
        or neither is an error.

        Args:
            pcb_path: Path to a ``.kicad_pcb`` file.
            start_x_mm, start_y_mm: Arc start point (mm).
            end_x_mm, end_y_mm: Arc end point (mm).
            layer: Copper layer for the arc (e.g. ``"In2.Cu"``).
            net_name: Net the arc belongs to. If the net is not yet in
                the PCB's net table it is added automatically.
            width_mm: Trace width (mm). Default 0.25.
            center_x_mm, center_y_mm: Arc-circle centre (mm). Pass
                both to use *center mode*; leave both as default
                (``nan``) to use explicit-mid mode.
            mid_x_mm, mid_y_mm: Explicit midpoint (mm). Pass both to
                use *explicit-mid mode*; leave both as ``nan`` to use
                center mode.
            dry_run: If True, compute the arc geometry and report it in
                the return value but do not write the file. Default
                False.
            check_clearance: If True (default), run the clearance engine on
                the new arc (approximated by its chords) and fold the result
                into the ``clearance`` key. Skipped on ``dry_run``.

        Returns:
            Dict with ``success``, ``arc_added``, ``net_id``,
            ``start``, ``mid``, ``end``, ``layer``, and ``radius_mm``
            (computed from the start point relative to the chosen
            centre, or from the circle through start/mid/end when in
            explicit-mid mode), plus ``clearance`` (engine effect-echo
            when written). On failure: ``success: False`` and
            ``error``.

        Example:
            Add a 30 mm-radius halfcircle from ``(178.5, 105)`` going
            through the lower half to ``(118.5, 105)`` around the
            board centre ``(148.5, 105)`` on ``In2.Cu`` for net
            ``/JUNCT_P4``:

            >>> add_arc_to_pcb(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     start_x_mm=178.5, start_y_mm=105.0,
            ...     end_x_mm=118.5,  end_y_mm=105.0,
            ...     center_x_mm=148.5, center_y_mm=105.0,
            ...     layer="In2.Cu", net_name="/JUNCT_P4",
            ...     width_mm=0.3,
            ... )

        Idempotency:
            Each call inserts a *new* arc element with a fresh UUID;
            repeated calls add duplicate routing. Use
            ``delete_pcb_routing`` first if you need to re-emit.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        text = get_text(pcb_path)
        new_text, result = add_arc_to_pcb_text(
            text,
            start_x_mm, start_y_mm, end_x_mm, end_y_mm,
            layer, net_name,
            width_mm=width_mm,
            center_x_mm=center_x_mm, center_y_mm=center_y_mm,
            mid_x_mm=mid_x_mm, mid_y_mm=mid_y_mm,
        )
        if not result.get("success"):
            return result
        if not dry_run:
            with open(pcb_path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            put_text(pcb_path, new_text)
        out = {"dry_run": dry_run, **result}
        items = arc_specs(
            (result["start"]["x_mm"], result["start"]["y_mm"]),
            (result["mid"]["x_mm"], result["mid"]["y_mm"]),
            (result["end"]["x_mm"], result["end"]["y_mm"]),
            result["net_name"], result["layer"], result["width_mm"],
        )
        return attach_clearance(out, pcb_path, items,
                                enabled=check_clearance and not dry_run)

    @mcp.tool()
    def add_via_to_pcb(
        pcb_path: str,
        x_mm: float, y_mm: float,
        net_name: str,
        layer_pair: list[str] | None = None,
        size_mm: float = 0.6,
        drill_mm: float = 0.3,
        dry_run: bool = False,
        check_clearance: bool = True,
    ) -> dict[str, Any]:
        """Insert a standalone via into a ``.kicad_pcb`` at a chosen
        world coordinate.

        Use this when a routing strategy needs a layer-switch via at a
        position that is *not* a pad endpoint — typically when a
        radial-then-arc inner-layer routing wants the via at an offset
        from the coil-pad to clear a neighbouring IC's exposed-pad
        footprint, or when a fan-out into a different layer happens
        mid-trace. Use this instead of stitching together a segment
        plus a `with_via` call with dummy endpoints. For more than one via
        use ``add_vias_to_pcb`` (one read+write for the whole tranche).

        Rendert nicht. Für visuelle Kontrolle ``pcb_render`` separat nach
        Abschluss aller Mutationen — nicht pro Via.

        Args:
            pcb_path: Path to a ``.kicad_pcb``.
            x_mm, y_mm: World position (mm).
            net_name: Net the via belongs to. If the net is not yet in
                the PCB's net table it is added automatically. Pass
                ``""`` (empty) to leave the via on net 0 (no-connect).
            layer_pair: Two-element list ``[outer, inner]`` (or any
                pair) naming the via's layer pair. Default ``None`` =
                through-via on ``["F.Cu", "B.Cu"]``. Use e.g.
                ``["In1.Cu", "In2.Cu"]`` for a buried via.
                **Manufacturing caveat**: blind / buried vias usually
                cost extra at low-cost fabs; verify support before
                relying on this.
            size_mm: Via diameter (mm). Default 0.6.
            drill_mm: Via drill (mm). Default 0.3.
            dry_run: If True, compute the via placement and report it
                in the return value but do not write the file. Default
                False.
            check_clearance: If True (default), run the clearance engine on
                the new via and fold the result into the ``clearance`` key.
                Skipped on ``dry_run``. For many vias prefer
                ``add_vias_to_pcb`` (one check for the whole tranche).

        Returns:
            Dict with ``success``, ``at`` (x_mm, y_mm), ``net_id``,
            ``net_name``, ``layer_pair`` (the actual pair used),
            ``size_mm``, ``drill_mm``, ``clearance`` (engine effect-echo
            when written). On failure: ``success: False`` and ``error``.

        Example:
            Insert a buried In1↔In2 via at the radial-to-arc transition
            of a coil-pair routing, on the JUNCT_P4 net:

            >>> add_via_to_pcb(
            ...     pcb_path="/tmp/board.kicad_pcb",
            ...     x_mm=177.4, y_mm=110.6,
            ...     net_name="/JUNCT_P4",
            ...     layer_pair=["In1.Cu", "In2.Cu"],
            ...     size_mm=0.5, drill_mm=0.3,
            ... )
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        text = get_text(pcb_path)
        new_text, result = add_via_to_pcb_text(
            text, x_mm, y_mm, net_name,
            layer_pair=layer_pair,
            size_mm=size_mm, drill_mm=drill_mm,
        )
        if not result.get("success"):
            return result
        if not dry_run:
            with open(pcb_path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            put_text(pcb_path, new_text)
        out = {"dry_run": dry_run, **result}
        items = [via_spec(result["at"]["x_mm"], result["at"]["y_mm"],
                          result["net_name"], result["layer_pair"],
                          result["size_mm"])]
        return attach_clearance(out, pcb_path, items,
                                enabled=check_clearance and not dry_run)

    @mcp.tool()
    def add_vias_to_pcb(
        pcb_path: str,
        vias: list[dict[str, Any]] | str,
        dry_run: bool = False,
        check_clearance: bool = True,
    ) -> dict[str, Any]:
        """Insert MANY vias into a ``.kicad_pcb`` in ONE read+write round.

        The batch variant of ``add_via_to_pcb`` — use this whenever you place
        more than one via (e.g. a 24-via GND stitch) so the whole tranche is a
        single file open/write instead of N calls. Atomic: if any via spec is
        invalid, nothing is written and the failing index is reported.

        Rendert nicht. Für visuelle Kontrolle ``pcb_render`` separat NACH
        Abschluss aller Mutationen aufrufen — nicht pro Via. Connectivity prüft
        man danach einmal mit ``check_connectivity``, nicht pro Via.

        Args:
            pcb_path: Path to a ``.kicad_pcb``.
            vias: List of via specs (or a JSON string of that list). Each spec:
                ``{x_mm, y_mm, net_name, layer_pair?, size_mm?, drill_mm?}`` —
                same fields as ``add_via_to_pcb`` (``layer_pair`` default
                through-via F.Cu/B.Cu; ``size_mm`` 0.6; ``drill_mm`` 0.3).
            dry_run: If True, validate + report every via but write nothing.
            check_clearance: If True (default), run the clearance engine
                ONCE over the whole tranche after writing and fold the result
                into the ``clearance`` key — the per-tranche verify pattern.
                Skipped on ``dry_run``.

        Returns:
            Effect echo so no read-back is needed: ``{success, count}`` (vias
            placed), ``vias`` (per-via result list: at/net_id/net_name/
            layer_pair), ``clearance`` (engine effect-echo for the tranche),
            ``dry_run``. On a bad spec: ``{success: False, error,
            failed_index}`` and the file is untouched.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if isinstance(vias, str):
            try:
                vias = json.loads(vias)
            except Exception as exc:
                return {"success": False,
                        "error": f"vias is not valid JSON: {exc}"}
        if not isinstance(vias, list) or not vias:
            return {"success": False,
                    "error": "vias must be a non-empty list of via specs"}

        text = get_text(pcb_path)
        placed: list[dict[str, Any]] = []
        for i, spec in enumerate(vias):
            if not isinstance(spec, dict):
                return {"success": False, "failed_index": i,
                        "error": f"via #{i} is not an object"}
            try:
                text, result = add_via_to_pcb_text(
                    text,
                    spec.get("x_mm"), spec.get("y_mm"),
                    spec.get("net_name", ""),
                    layer_pair=spec.get("layer_pair"),
                    size_mm=spec.get("size_mm", 0.6),
                    drill_mm=spec.get("drill_mm", 0.3),
                )
            except Exception as exc:
                return {"success": False, "failed_index": i,
                        "error": f"via #{i} failed: {exc}"}
            if not result.get("success"):
                return {"success": False, "failed_index": i,
                        "error": result.get("error", f"via #{i} failed")}
            placed.append(result)

        if not dry_run:
            with open(pcb_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            put_text(pcb_path, text)
        out = {"success": True, "count": len(placed), "vias": placed,
               "dry_run": dry_run}
        items = [via_spec(p["at"]["x_mm"], p["at"]["y_mm"], p["net_name"],
                          p["layer_pair"], p["size_mm"]) for p in placed]
        return attach_clearance(out, pcb_path, items,
                                enabled=check_clearance and not dry_run)

    @mcp.tool()
    def add_zone_pour_to_pcb(
        pcb_path: str,
        net_name: str,
        layer: str,
        polygon_xy_mm: list[list[float]],
        check_clearance: bool = True,
    ) -> dict[str, Any]:
        """Add a copper-pour zone bound to ``net_name`` on ``layer``.

        The pour outline is given as a list of ``[x_mm, y_mm]`` pairs
        (closed implicitly). The zone is inserted with sensible defaults
        (hatched edge, 0.2 mm clearance/min-width). KiCad's GUI / DRC
        engine will compute the actual filled polygon once the file is
        opened.

        Args:
            pcb_path: Path to a ``.kicad_pcb``.
            net_name: Net to bind the pour to (created at the top of the
                PCB if not yet defined).
            layer: KiCad copper layer name (``"F.Cu"`` / ``"B.Cu"`` / ...).
            polygon_xy_mm: List of ``[x_mm, y_mm]`` pairs — at least 3.
            check_clearance: If True (default), run the clearance engine
                (board-wide hard-copper scan, since a poured zone clears
                around foreign nets) and fold the result into the
                ``clearance`` key. Set False to skip.

        Returns:
            Dict with ``success``, ``net_id``, ``layer``, ``vertices``, and
            ``clearance`` (engine effect-echo). Note: zone clearance proper
            is settled by the filler + ``run_drc_check``; this echo flags any
            hard-copper short on the board after the pour was added.
        """
        pcb_path = to_local_path(pcb_path)
        if not os.path.isfile(pcb_path):
            return {"success": False, "error": f"PCB not found: {pcb_path}"}
        if len(polygon_xy_mm) < 3:
            return {"success": False, "error": "Polygon needs at least 3 points."}
        text = get_text(pcb_path)
        text, net_tag, net_fmt, net_id = ensure_net_tag(text, net_name)
        polygon = [(float(x), float(y)) for x, y in polygon_xy_mm]
        # Index-form PCBs use a redundant ``(net_name "...")`` line on zones
        # (matching the SWIG writer); string-form PCBs omit it (the short
        # ``(net "name")`` is self-describing).
        blob = _zone_block(
            net_tag, net_name, layer, polygon,
            include_net_name_line=(net_fmt == "index"),
        )
        text = _insert_before_root_close(text, blob)
        with open(pcb_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        put_text(pcb_path, text)
        result = {
            "success": True,
            "pcb_path": pcb_path,
            "net_id": net_id,
            "net_name": net_name,
            "net_format": net_fmt,
            "layer": layer,
            "vertices": len(polygon),
        }
        return attach_clearance(result, pcb_path, None, enabled=check_clearance)
