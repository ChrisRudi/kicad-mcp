# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure geometry behind ``center_item_clearance`` — nearest-copper clearance
centering for a via.

Side-effect-free and KiCad-free: every input is a plain millimetre float, so
the obstacle model and the two target solvers are fully unit-testable headless
(no ``kipy`` / ``pcbnew``). The live tool in :mod:`ipc_interact_tools` reads the
board, builds the obstacles, calls :func:`solve_target`, then drags the via.

Model
-----
The moving via is a disc of radius ``via_radius`` whose centre we are free to
place. Each piece of foreign copper (net ≠ the via's net) is an *obstacle*
exposing ``probe(px, py) -> (gap, ux, uy)`` where

  * ``gap`` is the signed distance from the point to the obstacle's copper edge
    — positive when the point is outside the copper, negative inside; and
  * ``(ux, uy)`` is the unit vector from that nearest edge toward the point,
    i.e. the direction that *increases* the gap.

The edge-to-edge clearance between the via and the obstacle is therefore
``gap - via_radius``. Because ``via_radius`` is the same constant offset on
every obstacle, the solvers can work on ``gap`` directly and the tool subtracts
``via_radius`` only when it reports clearances.

Two solvers
-----------
* ``equalize`` — the canonical "centre between two walls" move. For the two
  nearest, roughly-opposed walls it slides the via along the away-direction by
  ``(g2 - g1) / (1 - dot)`` so the two gaps become equal (closed form, exact for
  straight copper; ``dot`` is the cosine between the two away-directions, so the
  step reduces to ``(g2 - g1) / 2`` for perfectly opposed walls — the proposal's
  ``(C₁−C₂)/2`` step). Iterated a few times so it also converges for finite or
  slightly skewed walls.
* ``maximize`` — projected soft-min gradient ascent on ``min_i gap_i``: walk up
  the direction that increases the smallest gap until the active gaps balance
  (the local Voronoi / in-circle vertex) or the per-call step budget is spent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


def _norm(dx: float, dy: float) -> tuple[float, float, float]:
    """Return ``(length, ux, uy)`` for the vector ``(dx, dy)``.

    For a (near-)zero vector returns ``(0.0, 0.0, 0.0)`` so callers can detect
    the degenerate "point sits exactly on the edge" case and pick a fallback
    direction themselves.
    """
    h = math.hypot(dx, dy)
    if h <= 1e-12:
        return 0.0, 0.0, 0.0
    return h, dx / h, dy / h


def _closest_on_segment(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float,
) -> tuple[float, float]:
    """Closest point on the segment ``(x1,y1)-(x2,y2)`` to ``(px,py)``."""
    vx, vy = x2 - x1, y2 - y1
    seg2 = vx * vx + vy * vy
    if seg2 <= 1e-18:  # degenerate segment → the point itself
        return x1, y1
    t = ((px - x1) * vx + (py - y1) * vy) / seg2
    t = max(0.0, min(1.0, t))
    return x1 + t * vx, y1 + t * vy


class Obstacle(Protocol):
    """A foreign-copper element probed for clearance against a moving point."""

    net: str
    kind: str
    uuid: str

    def probe(self, px: float, py: float) -> tuple[float, float, float]:
        """Return ``(gap, ux, uy)`` — see module docstring."""


@dataclass(frozen=True)
class SegmentObstacle:
    """A track: a capsule of half-width ``half_width`` around its centreline."""

    x1: float
    y1: float
    x2: float
    y2: float
    half_width: float
    net: str = ""
    uuid: str = ""
    kind: str = "track"

    def probe(self, px: float, py: float) -> tuple[float, float, float]:
        cx, cy = _closest_on_segment(px, py, self.x1, self.y1, self.x2, self.y2)
        h, ux, uy = _norm(px - cx, py - cy)
        if h == 0.0:
            # On the centreline: push out along the segment normal.
            _, dx, dy = _norm(self.x2 - self.x1, self.y2 - self.y1)
            ux, uy = -dy, dx
        return h - self.half_width, ux, uy


@dataclass(frozen=True)
class CircleObstacle:
    """A via or round pad: a disc of radius ``radius``."""

    cx: float
    cy: float
    radius: float
    net: str = ""
    uuid: str = ""
    kind: str = "via"

    def probe(self, px: float, py: float) -> tuple[float, float, float]:
        h, ux, uy = _norm(px - self.cx, py - self.cy)
        if h == 0.0:
            ux, uy = 1.0, 0.0  # arbitrary but deterministic
        return h - self.radius, ux, uy


@dataclass(frozen=True)
class RectObstacle:
    """A pad approximated by its axis-aligned world bounding box.

    Exact for rectangular pads aligned to the axes and conservative (the bbox
    encloses the real copper) for rotated, oval or rounded pads — clearance is
    therefore never over-reported for those.
    """

    cx: float
    cy: float
    half_w: float
    half_h: float
    net: str = ""
    uuid: str = ""
    kind: str = "pad"

    def probe(self, px: float, py: float) -> tuple[float, float, float]:
        dx = abs(px - self.cx) - self.half_w
        dy = abs(py - self.cy) - self.half_h
        sx = 1.0 if px >= self.cx else -1.0
        sy = 1.0 if py >= self.cy else -1.0
        if dx <= 0.0 and dy <= 0.0:
            # Inside the box: nearest edge is along the axis of least penetration.
            if dx >= dy:  # closer to a vertical edge
                return dx, sx, 0.0
            return dy, 0.0, sy
        ox, oy = max(dx, 0.0), max(dy, 0.0)
        gap, ux, uy = _norm(sx * ox, sy * oy)
        if gap == 0.0:  # exactly on a face/corner
            ux, uy = sx, sy
        return gap, ux, uy


# ---------------------------------------------------------------------------
# Solvers
# ---------------------------------------------------------------------------


def _probe_all(
    obstacles: list[Obstacle], px: float, py: float,
) -> list[tuple[float, float, float]]:
    return [o.probe(px, py) for o in obstacles]


def nearest_two(
    obstacles: list[Obstacle], px: float, py: float,
) -> list[tuple[float, float, float]]:
    """The two probes with the smallest gap at ``(px, py)`` (ascending)."""
    probes = sorted(_probe_all(obstacles, px, py), key=lambda p: p[0])
    return probes[:2]


def _min_gap(obstacles: list[Obstacle], px: float, py: float) -> float:
    return min(p[0] for p in _probe_all(obstacles, px, py))


def solve_maximize(
    px: float, py: float, obstacles: list[Obstacle],
    max_step: float, iters: int = 160,
) -> tuple[float, float]:
    """Soft-min gradient ascent on ``min_i gap_i`` with monotone step control.

    Each iteration walks up the soft-min gradient — the direction that grows the
    smallest gap. A trial step is *accepted* only if it strictly increases the
    minimum gap; otherwise the step is halved and retried, so the minimum gap
    never decreases and the point converges onto the optimum instead of
    oscillating across it (a too-large step would overshoot the peak). Opposed
    walls cancel near the corridor centre (the gradient vanishes → it stops); a
    single wall or an open corner is escaped straight until the displacement
    budget ``max_step`` is spent. Returns the new ``(x, y)``.
    """
    if not obstacles:
        return px, py
    x, y = px, py
    budget = max(0.0, float(max_step))
    step = budget / 4.0
    temp = 0.05  # soft-min temperature (mm) — sharp enough to balance two walls
    best = _min_gap(obstacles, x, y)
    for _ in range(iters):
        if budget <= 1e-9 or step <= 1e-6:
            break
        probes = _probe_all(obstacles, x, y)
        gmin = min(p[0] for p in probes)
        gx = gy = wsum = 0.0
        for gap, ux, uy in probes:
            w = math.exp(-(gap - gmin) / temp)
            gx += w * ux
            gy += w * uy
            wsum += w
        if wsum > 0.0:
            gx /= wsum
            gy /= wsum
        mag, dx, dy = _norm(gx, gy)
        if mag < 1e-3:  # gradients balanced → local optimum
            break
        s = min(step, budget)
        nx, ny = x + dx * s, y + dy * s
        ngmin = _min_gap(obstacles, nx, ny)
        if ngmin > best + 1e-9:  # accept: minimum gap strictly grew
            x, y, best = nx, ny, ngmin
            budget -= s
        else:  # flat or overshot the optimum → halve the step and retry
            step *= 0.5
    return x, y


def solve_equalize(
    px: float, py: float, obstacles: list[Obstacle],
    max_step: float, iters: int = 32,
) -> tuple[float, float]:
    """Centre the point between the two nearest, roughly-opposed walls.

    Each iteration slides the point along the away-direction of the nearer wall
    by ``(g2 - g1) / (1 - dot)`` — the move that equalises the two gaps to first
    order (exact for straight copper). Iterated so it also converges for finite
    or slightly skewed walls. Falls back to :func:`solve_maximize` when the two
    nearest obstacles are not opposed (a corner / open region), where an
    equal-gap slide is not well defined. Returns the new ``(x, y)``.
    """
    if len(obstacles) < 2:
        return solve_maximize(px, py, obstacles, max_step)
    (_, n0x, n0y), (_, n1x, n1y) = nearest_two(obstacles, px, py)
    if (n0x * n1x + n0y * n1y) > -0.2:  # not a corridor
        return solve_maximize(px, py, obstacles, max_step)

    x, y = px, py
    budget = max(0.0, float(max_step))
    for _ in range(iters):
        (g1, u1x, u1y), (g2, u2x, u2y) = nearest_two(obstacles, x, y)
        denom = 1.0 - (u1x * u2x + u1y * u2y)  # 1 - cos(angle between aways)
        if denom < 1e-3:
            break
        t = (g2 - g1) / denom  # ≥ 0 (gaps sorted ascending)
        if abs(t) < 1e-5 or budget <= 1e-9:
            break
        s = min(t, budget)
        x += u1x * s
        y += u1y * s
        budget -= s
    return x, y


def solve_target(
    px: float, py: float, obstacles: list[Obstacle],
    mode: str, max_step: float,
) -> tuple[float, float]:
    """Dispatch to :func:`solve_equalize` (``mode="equalize"``) or
    :func:`solve_maximize` (``mode="maximize"``). Unknown modes raise
    ``ValueError`` so the caller can surface a clean error."""
    if mode == "equalize":
        return solve_equalize(px, py, obstacles, max_step)
    if mode == "maximize":
        return solve_maximize(px, py, obstacles, max_step)
    raise ValueError(f"unknown mode {mode!r} (use 'equalize' or 'maximize')")


def clearances_at(
    obstacles: list[Obstacle], px: float, py: float, via_radius: float,
) -> list[dict]:
    """Per-obstacle edge-to-edge clearance ``gap - via_radius`` at ``(px, py)``.

    Returns a list of ``{uuid, kind, net, clearance_mm}`` sorted tightest-first,
    ready to splice into the tool's before/after report.
    """
    out = []
    for o in obstacles:
        gap = o.probe(px, py)[0]
        out.append({
            "uuid": o.uuid,
            "kind": o.kind,
            "net": o.net,
            "clearance_mm": round(gap - via_radius, 4),
        })
    out.sort(key=lambda d: d["clearance_mm"])
    return out


def min_clearance(
    obstacles: list[Obstacle], px: float, py: float, via_radius: float,
) -> float | None:
    """Smallest edge-to-edge clearance at ``(px, py)`` (``None`` if no
    obstacles)."""
    if not obstacles:
        return None
    return round(min(o.probe(px, py)[0] for o in obstacles) - via_radius, 4)
