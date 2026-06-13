# SPDX-License-Identifier: GPL-3.0-or-later
"""
Schematic-side geometry helpers — counterpart to the PCB ``compute_pad_world_positions``
math, but operating on ``.kicad_sch`` symbol/pin/wire structures.

KiCad schematic conventions:
  * Coordinates are in millimetres with Y growing downwards.
  * Symbol-internal rotation is restricted to the discrete set
    ``{0, 90, 180, 270}`` degrees. Free rotation is achieved by rotating
    the symbol's anchor coordinate (and re-snapping the symbol's internal
    angle to the nearest 90°).
  * The optional ``(mirror …)`` modifier on a symbol instance is one of
    ``y`` (mirrored about the X-axis), ``x`` (mirrored about the Y-axis)
    or absent.
  * Pin coordinates inside a ``lib_symbols`` entry are relative to the
    symbol origin and use a separate convention with Y growing upwards;
    the schematic-instance transformation is therefore
    ``world = symbol.at + R(symbol.rot) · M(symbol.mirror) · pin.local_with_y_flip``.

This module exposes pure, side-effect-free helpers used by the ``sch_patch``
toolchain. All numerical I/O is in millimetres unless explicitly suffixed.
"""

from __future__ import annotations

import math
from typing import Iterable


# ---------------------------------------------------------------------------
# Basic 2-D transforms
# ---------------------------------------------------------------------------


def rotate_point(
    x: float, y: float, angle_deg: float, pivot: tuple[float, float] = (0.0, 0.0)
) -> tuple[float, float]:
    """Rotate ``(x, y)`` around ``pivot`` by ``angle_deg`` (CCW)."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    px, py = pivot
    dx, dy = x - px, y - py
    return (px + ca * dx - sa * dy, py + sa * dx + ca * dy)


def snap_to_90(angle_deg: float) -> int:
    """Snap any angle to the nearest 90°-multiple in ``{0, 90, 180, 270}``.

    Uses half-up rounding (45° → 90°, 135° → 180°), avoiding Python's
    default banker's-rounding which would map exact half-steps to the
    nearest even multiple instead.
    """
    a = ((math.floor((float(angle_deg) + 45.0) / 90.0) * 90) % 360 + 360) % 360
    return int(a)


def residual_after_snap(angle_deg: float) -> float:
    """Signed residual ``angle_deg − snap_to_90(angle_deg)`` in degrees,
    folded to the half-open interval ``(-45, 45]``.
    """
    res = angle_deg - snap_to_90(angle_deg)
    while res > 45.0:
        res -= 90.0
    while res <= -45.0:
        res += 90.0
    return res


# ---------------------------------------------------------------------------
# Pin world-position resolution
# ---------------------------------------------------------------------------


def pin_world_xy(
    sym_x: float,
    sym_y: float,
    sym_rot_deg: int,
    sym_mirror: str | None,
    pin_local_x: float,
    pin_local_y: float,
) -> tuple[float, float]:
    """Return the world coordinates of a pin given the placed symbol's
    transformation and the pin's local position from the ``lib_symbols``
    definition.

    Args:
        sym_x, sym_y: Symbol instance ``(at x y rot)`` anchor (mm).
        sym_rot_deg: Symbol internal rotation, one of 0/90/180/270.
        sym_mirror: ``"x"``, ``"y"`` or ``None``.
        pin_local_x, pin_local_y: Pin position from the ``lib_symbols``
            definition (KiCad convention — Y grows upwards).
    """
    # Flip Y to match schematic convention (lib symbols have Y up).
    lx, ly = float(pin_local_x), -float(pin_local_y)

    # Apply mirror first (kicad applies mirror before rotation).
    if sym_mirror == "y":
        lx = -lx
    elif sym_mirror == "x":
        ly = -ly

    # Apply discrete symbol rotation.
    rot = ((int(round(sym_rot_deg)) % 360) + 360) % 360
    if rot == 0:
        rx, ry = lx, ly
    elif rot == 90:
        rx, ry = -ly, lx
    elif rot == 180:
        rx, ry = -lx, -ly
    elif rot == 270:
        rx, ry = ly, -lx
    else:
        # Non-90° rotation — generic transform (used for diagnostics on
        # malformed files). Schematic format does not support this.
        rx, ry = rotate_point(lx, ly, rot)

    return (sym_x + rx, sym_y + ry)


# ---------------------------------------------------------------------------
# Bounding-box helpers
# ---------------------------------------------------------------------------


def bbox_of_points(
    points: Iterable[tuple[float, float]],
) -> tuple[float, float, float, float]:
    """Return ``(xmin, ymin, xmax, ymax)`` over a list of points.

    Raises ``ValueError`` if the iterable is empty.
    """
    xs: list[float] = []
    ys: list[float] = []
    for p in points:
        xs.append(p[0])
        ys.append(p[1])
    if not xs:
        raise ValueError("bbox_of_points requires at least one point")
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_center(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Return ``(cx, cy)`` — geometric centre of a bbox tuple."""
    xmin, ymin, xmax, ymax = bbox
    return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)


# ---------------------------------------------------------------------------
# Pin-grid snap for half-pitch passive symbols
# ---------------------------------------------------------------------------


SCH_GRID_MM = 2.54

# KiCad's default schematic placement grid for symbols, wires, junctions and
# labels. The 2.54 mm grid above (used by half-pitch passives) is the *unit*
# spacing between two adjacent pin sockets — but symbol anchors, wire end-
# points and labels are conventionally snapped to this finer 1.27 mm half-
# grid so that pin sockets always coincide with wire vertices.
SCH_PLACE_GRID_MM = 1.27


def snap_to_grid(
    x: float, y: float, grid: float = SCH_PLACE_GRID_MM
) -> tuple[float, float]:
    """Round ``(x, y)`` to the nearest multiple of ``grid``.

    Default grid is 1.27 mm — KiCad's standard schematic placement grid.
    Use this on every user-supplied coordinate before it lands in a wire,
    label or symbol instance, so the schematic does not accumulate sub-grid
    drift that ERC reports as ``endpoint_off_grid`` warnings.
    """
    nx = round(float(x) / grid) * grid
    ny = round(float(y) / grid) * grid
    return round(nx, 4), round(ny, 4)


# Symbols whose pin pitch is an odd multiple of half-grid (1.27 mm) and whose
# pins therefore land off-grid when the symbol centre is itself on the 2.54
# grid. To put both pins on-grid the centre must be offset by 1.27 mm in the
# axis perpendicular to the pin orientation.
#
# Pitches:
#   * ``Device:*_Small`` — 2.54 mm pitch (pins at centre ± 1.27)
#   * ``Device:C`` / ``Device:R`` / ``Device:L`` — 7.62 mm pitch (pins at ± 3.81)
#
# Both families need the same +1.27 half-grid offset.
HALF_GRID_OFFSET_LIBS = {
    "Device:C",
    "Device:C_Small",
    "Device:CP",
    "Device:CP_Small",
    "Device:R",
    "Device:R_Small",
    "Device:R_US",
    "Device:L",
    "Device:L_Small",
    "Device:D",
    "Device:D_Small",
    "Device:LED",
    "Device:LED_Small",
}


def needs_half_grid_offset(lib_id: str) -> bool:
    """Return True if ``lib_id`` belongs to the half-pitch passive family
    that requires a 1.27 mm centre offset for pins to land on the 2.54 grid.
    """
    return lib_id in HALF_GRID_OFFSET_LIBS


def snap_for_pin_grid(
    x: float, y: float, lib_id: str, rotation_deg: int
) -> tuple[float, float, bool]:
    """Snap ``(x, y)`` so that both pins of a half-pitch passive symbol
    land on the 2.54 mm schematic grid.

    For ``rotation_deg in {0, 180}`` (vertical pin orientation) Y is
    snapped to ``(N + 0.5) × 2.54``. For ``{90, 270}`` (horizontal) X is
    snapped instead. Symbols not in :data:`HALF_GRID_OFFSET_LIBS` are
    returned unchanged.

    Returns ``(x, y, snapped)`` where ``snapped`` is True if either
    coordinate moved by more than 1 µm.
    """
    if not needs_half_grid_offset(lib_id):
        return float(x), float(y), False
    rot = ((int(round(float(rotation_deg))) % 360) + 360) % 360
    nx, ny = float(x), float(y)
    if rot in (0, 180):
        ny = round((ny - SCH_GRID_MM / 2.0) / SCH_GRID_MM) * SCH_GRID_MM + SCH_GRID_MM / 2.0
    else:
        nx = round((nx - SCH_GRID_MM / 2.0) / SCH_GRID_MM) * SCH_GRID_MM + SCH_GRID_MM / 2.0
    moved = abs(nx - float(x)) > 1e-6 or abs(ny - float(y)) > 1e-6
    return nx, ny, moved
