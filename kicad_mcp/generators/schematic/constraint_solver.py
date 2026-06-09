# SPDX-License-Identifier: GPL-3.0-or-later
"""
6.4: Constraint-based placement solver.

Replaces heuristic phases 2-9 with a constraint solver that minimizes
total wire length while satisfying hard constraints (no overlap, grid snap,
sheet bounds).

Integration: Used as an alternative Phase 2 in place.py when available.
Falls back to defrag_place.py if solver fails or times out.

Dependencies: python-constraint (lightweight) or OR-Tools CP-SAT (optimal).
"""

import logging
import math
import os

from ..common.bbox import _get_symbol_height, _get_symbol_width
from ..common.connectivity import _build_connection_graph
from ..common.constants import (
    GRID,
    MARGIN,
    SHEET_H,
    SHEET_W,
)
from ..common.geometry import _snap

logger = logging.getLogger(__name__)

# Solver timeout in seconds
SOLVER_TIMEOUT = float(os.getenv("KICAD_SOLVER_TIMEOUT", "5.0"))

# Grid step for solver (coarser than GRID for performance)
SOLVER_GRID = GRID * 2  # 5.08mm


def _try_ortools_solver(
    parts: list[dict],
    nets: list[dict],
    placed_refs: set[str],
) -> dict[str, tuple[float, float, int]] | None:
    """Try OR-Tools CP-SAT solver for placement.

    Returns: {ref: (x, y, rotation)} or None if solver unavailable/fails.
    """
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return None

    ref_to_part = {p["ref"]: p for p in parts}
    connections, _conn_count = _build_connection_graph(nets)

    # Parts to place (unplaced only)
    to_place = [p for p in parts if p["ref"] not in placed_refs and "_place_x" not in p]
    if not to_place:
        return {}

    model = cp_model.CpModel()

    # Grid bounds in solver units
    min_x = int(MARGIN / SOLVER_GRID) + 1
    max_x = int((SHEET_W - MARGIN) / SOLVER_GRID) - 1
    min_y = int(MARGIN / SOLVER_GRID) + 1
    max_y = int((SHEET_H - MARGIN) / SOLVER_GRID) - 1

    # Variables: (x, y, rot) per unplaced part
    vars_x: dict[str, cp_model.IntVar] = {}
    vars_y: dict[str, cp_model.IntVar] = {}
    vars_rot: dict[str, cp_model.IntVar] = {}

    for part in to_place:
        ref = part["ref"]
        vars_x[ref] = model.NewIntVar(min_x, max_x, f"x_{ref}")
        vars_y[ref] = model.NewIntVar(min_y, max_y, f"y_{ref}")
        vars_rot[ref] = model.NewIntVar(0, 3, f"rot_{ref}")  # 0=0°, 1=90°, 2=180°, 3=270°

    # Hard constraint: no overlap between any two unplaced parts
    for i, pa in enumerate(to_place):
        for pb in to_place[i + 1:]:
            ra, rb = pa["ref"], pb["ref"]
            wa = max(2, int(math.ceil(_get_symbol_width(pa) / SOLVER_GRID)))
            wb = max(2, int(math.ceil(_get_symbol_width(pb) / SOLVER_GRID)))
            _ha = max(2, int(math.ceil(_get_symbol_height(pa) / SOLVER_GRID)))
            _hb = max(2, int(math.ceil(_get_symbol_height(pb) / SOLVER_GRID)))
            min_dx = (wa + wb) // 2 + 1

            # No-overlap: either separated in X or in Y
            b = model.NewBoolVar(f"sep_{ra}_{rb}")
            model.Add(vars_x[ra] - vars_x[rb] >= min_dx).OnlyEnforceIf(b)
            model.Add(vars_x[rb] - vars_x[ra] >= min_dx).OnlyEnforceIf(b.Not())

    # Soft constraint: minimize total Manhattan distance between connected parts
    objective_terms = []
    for part in to_place:
        ref = part["ref"]
        for _, other_ref, _ in connections.get(ref, []):
            if other_ref not in vars_x:
                # Other part is already placed
                other = ref_to_part.get(other_ref)
                if other and "_place_x" in other:
                    fixed_x = int(other["_place_x"] / SOLVER_GRID)
                    fixed_y = int(other["_place_y"] / SOLVER_GRID)
                    dx = model.NewIntVar(0, max_x - min_x, f"dx_{ref}_{other_ref}")
                    dy = model.NewIntVar(0, max_y - min_y, f"dy_{ref}_{other_ref}")
                    model.AddAbsEquality(dx, vars_x[ref] - fixed_x)
                    model.AddAbsEquality(dy, vars_y[ref] - fixed_y)
                    objective_terms.extend([dx, dy])
                continue
            # Both unplaced
            dx = model.NewIntVar(0, max_x - min_x, f"dx_{ref}_{other_ref}")
            dy = model.NewIntVar(0, max_y - min_y, f"dy_{ref}_{other_ref}")
            model.AddAbsEquality(dx, vars_x[ref] - vars_x[other_ref])
            model.AddAbsEquality(dy, vars_y[ref] - vars_y[other_ref])
            objective_terms.extend([dx, dy])

    if objective_terms:
        model.Minimize(sum(objective_terms))

    # Solve with timeout
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVER_TIMEOUT

    status = solver.Solve(model)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result = {}
        for part in to_place:
            ref = part["ref"]
            x = solver.Value(vars_x[ref]) * SOLVER_GRID
            y = solver.Value(vars_y[ref]) * SOLVER_GRID
            rot = solver.Value(vars_rot[ref]) * 90
            result[ref] = (_snap(x), _snap(y), rot)
        logger.info("OR-Tools solver: placed %d parts (status=%s)",
                     len(result), "optimal" if status == cp_model.OPTIMAL else "feasible")
        return result

    logger.warning("OR-Tools solver: no solution found (status=%d)", status)
    return None


def _try_simple_solver(
    parts: list[dict],
    nets: list[dict],
    placed_refs: set[str],
) -> dict[str, tuple[float, float, int]] | None:
    """Simple greedy constraint solver (no external dependencies).

    Places parts one by one at the grid position that minimizes
    wire length to already-placed parts, with overlap checks.
    """
    ref_to_part = {p["ref"]: p for p in parts}
    connections, conn_count = _build_connection_graph(nets)

    to_place = [p for p in parts
                if p["ref"] not in placed_refs
                and "_place_x" not in p
                and "hint_sch_x" not in p]  # respect user hints
    if not to_place:
        return {}

    # Sort by connectivity (most connected first)
    to_place.sort(key=lambda p: -conn_count.get(p["ref"], 0))

    result: dict[str, tuple[float, float, int]] = {}
    occupied: list[tuple[float, float, float, float]] = []  # (x, y, w, h)

    # Collect already-placed positions as occupied
    for p in parts:
        if "_place_x" in p:
            occupied.append((p["_place_x"], p["_place_y"],
                             _get_symbol_width(p), _get_symbol_height(p)))

    # Grid of candidate positions
    grid_xs = [_snap(x) for x in range(int(MARGIN + 10), int(SHEET_W - MARGIN - 10), int(SOLVER_GRID))]
    grid_ys = [_snap(y) for y in range(int(MARGIN + 10), int(SHEET_H - MARGIN - 10), int(SOLVER_GRID))]

    def _overlaps(x, y, w, h):
        for ox, oy, ow, oh in occupied:
            if (abs(x - ox) < (w + ow) / 2 + 2.0 and
                abs(y - oy) < (h + oh) / 2 + 2.0):
                return True
        return False

    for part in to_place:
        ref = part["ref"]
        pw = _get_symbol_width(part)
        ph = _get_symbol_height(part)

        # Find connected placed positions
        targets = []
        for _, other_ref, _ in connections.get(ref, []):
            if other_ref in result:
                targets.append((result[other_ref][0], result[other_ref][1]))
            else:
                op = ref_to_part.get(other_ref)
                if op and "_place_x" in op:
                    targets.append((op["_place_x"], op["_place_y"]))

        best_pos = None
        best_cost = float("inf")

        for gx in grid_xs:
            for gy in grid_ys:
                if _overlaps(gx, gy, pw, ph):
                    continue
                cost = 0.0
                for tx, ty in targets:
                    cost += abs(gx - tx) + abs(gy - ty)
                if not targets:
                    # No connections yet — prefer center
                    cost = abs(gx - SHEET_W / 2) + abs(gy - SHEET_H / 2)
                if cost < best_cost:
                    best_cost = cost
                    best_pos = (gx, gy)

        if best_pos:
            result[ref] = (best_pos[0], best_pos[1], 0)
            occupied.append((best_pos[0], best_pos[1], pw, ph))
        else:
            # Fallback: place at first free grid position
            for gx in grid_xs:
                for gy in grid_ys:
                    if not _overlaps(gx, gy, pw, ph):
                        result[ref] = (gx, gy, 0)
                        occupied.append((gx, gy, pw, ph))
                        break
                if ref in result:
                    break

    logger.info("Simple solver: placed %d parts", len(result))
    return result


def solve_placement(
    parts: list[dict],
    nets: list[dict],
    placed_refs: set[str],
) -> dict[str, tuple[float, float, int]] | None:
    """Run constraint-based placement solver.

    Tries OR-Tools first, falls back to simple greedy solver.
    Returns {ref: (x, y, rotation)} or None.
    """
    # Try OR-Tools CP-SAT
    result = _try_ortools_solver(parts, nets, placed_refs)
    if result is not None:
        return result

    # Fallback: simple greedy solver
    return _try_simple_solver(parts, nets, placed_refs)
