# SPDX-License-Identifier: GPL-3.0-or-later
"""PCB-side geometry helpers — counterpart to :mod:`sch_geometry` for the
``.kicad_pcb`` side.

KiCad PCB conventions:
  * Coordinates are in millimetres with **Y growing downwards** (screen
    coords). This is the inverse of the lib-symbol convention used by
    :mod:`sch_geometry`.
  * A footprint's ``(at x y rot)`` header stores the user's intended
    *visual* rotation in degrees, counter-clockwise positive. Because the
    on-screen Y axis points downward, the linear transform that maps a
    pad's local ``(at lx ly)`` offset to its world coordinate is the
    "math clockwise" matrix:

    .. code-block:: text

        | wx |   | cos r   sin r | | lx |   | fp_x |
        |    | = |               | |    | + |      |
        | wy |   | -sin r  cos r | | ly |   | fp_y |

    Using the math-CCW matrix instead (the obvious choice if one forgets
    the Y-flip) places pads 0.4 mm off for a 0402 at 90° rotation and
    eventually disagrees with what DRC reports.
  * Pad *shape* rotation is stored independently in each pad's own
    ``(at lx ly rot)``. Editing only the footprint header rotates body /
    silkscreen / fab but **leaves pad rectangles in their library
    orientation**. To rotate the pad shape together with the body, the
    pad's lokal-rot must be set to the footprint rotation (additively for
    pads whose library entry already has a non-zero rot).
  * Mounting a footprint on B.Cu mirrors its local X-axis (and the
    apparent rotation reverses sign relative to a viewer looking at the
    top side). See :func:`pcb_local_to_world` for the flip handling.

This module exposes pure, side-effect-free helpers used by placement and
routing tools. Numerical I/O is in millimetres unless explicitly suffixed.
"""

from __future__ import annotations

import math
import re
from typing import Iterable


# ---------------------------------------------------------------------------
# Angle utilities
# ---------------------------------------------------------------------------


def wrap_signed(deg: float) -> float:
    """Fold any angle into the half-open interval ``(-180, 180]`` degrees."""
    d = (float(deg) + 180.0) % 360.0 - 180.0
    if d == -180.0:
        d = 180.0
    return d


def phi_short(a: float, b: float) -> float:
    """Return the signed shortest angular delta from ``a`` to ``b`` in
    ``(-180, 180]`` degrees.

    A positive return value means ``b`` is reached by rotating counter-
    clockwise from ``a``; negative means clockwise.
    """
    return wrap_signed(float(b) - float(a))


def short_mid_phi(a: float, b: float) -> float:
    """Return the mid-angle on the *shorter* arc from ``a`` to ``b``."""
    return (float(a) + phi_short(a, b) / 2.0) % 360.0


# ---------------------------------------------------------------------------
# Local ↔ world transforms (the canonical CW formula)
# ---------------------------------------------------------------------------


def pcb_local_to_world(
    anchor: tuple[float, float],
    rot_deg: float,
    lx: float, ly: float,
    flipped: bool = False,
) -> tuple[float, float]:
    """Apply a footprint's pose to a pad-local offset and return world mm.

    Args:
        anchor: ``(fp_x, fp_y)`` — the footprint's ``(at x y …)`` header.
        rot_deg: Footprint rotation in degrees (CCW positive, as stored in
            the file).
        lx, ly: Pad-local ``(at lx ly)`` offset from the library entry.
        flipped: ``True`` if the footprint is on ``B.Cu`` (its X axis is
            mirrored).

    Returns:
        ``(wx, wy)`` world coordinates in mm.
    """
    if flipped:
        lx = -lx
    rr = math.radians(float(rot_deg))
    cr, sr = math.cos(rr), math.sin(rr)
    wx = anchor[0] + lx * cr + ly * sr
    wy = anchor[1] - lx * sr + ly * cr
    return (wx, wy)


def pcb_world_to_local(
    anchor: tuple[float, float],
    rot_deg: float,
    wx: float, wy: float,
    flipped: bool = False,
) -> tuple[float, float]:
    """Inverse of :func:`pcb_local_to_world`: given a world point, return
    the local offset relative to the footprint anchor at the given pose.
    """
    dx = float(wx) - anchor[0]
    dy = float(wy) - anchor[1]
    rr = math.radians(float(rot_deg))
    cr, sr = math.cos(rr), math.sin(rr)
    lx = dx * cr - dy * sr
    ly = dx * sr + dy * cr
    if flipped:
        lx = -lx
    return (lx, ly)


# ---------------------------------------------------------------------------
# Arc geometry — short-way midpoint
# ---------------------------------------------------------------------------


def short_arc_mid_xy(
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
    radius: float | None = None,
) -> tuple[float, float]:
    """Return the midpoint on the *shorter* circular arc from ``start`` to
    ``end`` about ``center``.

    KiCad's ``(arc (start) (mid) (end))`` is parsed so the arc passes from
    start through mid to end. If the supplied mid lies on the longer half
    of the circle, the rendered arc takes the long way around — a common
    bug when constructing arcs from polar coordinates without wrap-aware
    midpoint math. This helper computes the correct short-way mid in
    world coords.

    Args:
        start, end: Arc end-points (world mm). Must lie on a common
            circle around ``center`` (caller's responsibility).
        center: Circle center (world mm). The signed Y axis is the screen
            convention (Y grows downward), but no assumption is required
            because the math is purely planar.
        radius: Optional radius (mm) the midpoint is placed at. When
            ``None`` (default) the radius is inherited from ``start``.
            Pass an explicit value — typically ``(r_start + r_end) / 2``
            — when ``start`` and ``end`` sit at slightly different radii
            (snapped to real pads/vias) so the arc bisects the gap
            instead of hugging the start radius.

    Returns:
        ``(mx, my)`` — the world coordinate of the midpoint on the short
        arc.
    """
    cx, cy = center
    # KiCad screen coords: Y grows downward. atan2 below treats Y as a
    # standard math axis; the sign convention is internally consistent.
    a = math.degrees(math.atan2(start[1] - cy, start[0] - cx))
    b = math.degrees(math.atan2(end[1] - cy, end[0] - cx))
    r = radius if radius is not None else math.hypot(start[0] - cx, start[1] - cy)
    m_deg = (a + phi_short(a, b) / 2.0)
    mr = math.radians(m_deg)
    return (cx + r * math.cos(mr), cy + r * math.sin(mr))


# ---------------------------------------------------------------------------
# Radial / tangential alignment helper
# ---------------------------------------------------------------------------


def align_radial_rotation(
    target: tuple[float, float],
    center: tuple[float, float],
    mode: str = "radial_in",
) -> float:
    """Compute the footprint rotation (degrees, CCW) that aligns its
    long axis to a radial or tangential direction at ``target`` relative
    to a ``center`` point.

    Useful for placing components in circular arrays — a row of capacitors
    around an IC, a ring of LEDs around a PCB center, etc.

    Args:
        target: World position of the footprint (mm). The rotation is
            computed at this point.
        center: World position the rotation is measured against — usually
            the PCB centre or the parent IC anchor.
        mode: One of:
            * ``"radial_in"`` — long axis points toward ``center``.
            * ``"radial_out"`` — long axis points away from ``center``.
            * ``"tangential_ccw"`` — long axis points CCW-tangentially.
            * ``"tangential_cw"`` — long axis points CW-tangentially.

    Returns:
        Rotation in degrees, folded to ``[0, 360)``. Half-turn ambiguity
        for two-pad passives (a 180° flip is electrically identical) is
        not resolved here; callers may wrap to ``[0, 180)`` if desired.

    Raises:
        ValueError: if ``mode`` is not recognised.
    """
    dx = float(target[0]) - float(center[0])
    # Screen Y axis: flip so the angle reads naturally as math-CCW.
    dy_math = -(float(target[1]) - float(center[1]))
    angle_math = math.degrees(math.atan2(dy_math, dx))
    if mode == "radial_out":
        rot = angle_math
    elif mode == "radial_in":
        rot = angle_math + 180.0
    elif mode == "tangential_ccw":
        rot = angle_math + 90.0
    elif mode == "tangential_cw":
        rot = angle_math - 90.0
    else:
        raise ValueError(
            "mode must be one of: radial_in, radial_out, "
            "tangential_ccw, tangential_cw"
        )
    return rot % 360.0


# ---------------------------------------------------------------------------
# Footprint bounding-box from a .kicad_mod file
# ---------------------------------------------------------------------------


_PAD_AT_RE = re.compile(
    r'\(pad\s+"[^"]*"[^()]*?'
    r'\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+[\d.\-]+)?\)'
    r'[^()]*?\(size\s+([\d.\-]+)\s+([\d.\-]+)\)',
    re.DOTALL,
)
_FP_LINE_RE = re.compile(
    r'\(fp_line\s+\(start\s+([\d.\-]+)\s+([\d.\-]+)\)\s+'
    r'\(end\s+([\d.\-]+)\s+([\d.\-]+)\)'
    r'[\s\S]*?\(layer\s+"([^"]+)"\)'
)


def compute_fp_bbox(
    mod_text: str,
    include_layers: Iterable[str] = ("F.Fab", "F.CrtYd", "F.SilkS"),
) -> tuple[float, float, float, float]:
    """Compute the local-frame bounding box of a footprint from its
    ``.kicad_mod`` source.

    Args:
        mod_text: Raw S-expression text of the ``.kicad_mod``.
        include_layers: Footprint-graphics layers whose ``(fp_line …)``
            polylines are folded into the bbox. Pads (always copper-
            relevant) are always included.

    Returns:
        ``(xmin, ymin, xmax, ymax)`` in the footprint's local-coordinate
        system (mm). If no pads or graphics are found, returns
        ``(0, 0, 0, 0)`` so callers can centre on the footprint origin.
    """
    xs: list[float] = []
    ys: list[float] = []
    for m in _PAD_AT_RE.finditer(mod_text):
        px, py, sw, sh = (float(g) for g in m.groups())
        xs.extend((px - sw / 2.0, px + sw / 2.0))
        ys.extend((py - sh / 2.0, py + sh / 2.0))
    allow = set(include_layers)
    for m in _FP_LINE_RE.finditer(mod_text):
        if m.group(5) in allow:
            xs.extend((float(m.group(1)), float(m.group(3))))
            ys.extend((float(m.group(2)), float(m.group(4))))
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_center(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Geometric centre of a bbox."""
    xmin, ymin, xmax, ymax = bbox
    return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
