# SPDX-License-Identifier: GPL-3.0-or-later
"""
Schematic routing: A* wire routing, label placement, pin extraction.

Strangler-fig extraction from schematic_builder.py — all functions
copied verbatim (no behaviour changes).

Callers:
  - schematic_builder.py   (all functions re-exported for backward compat)
  - schematic/rotate.py    (_optimal_rotation uses _extract_pin_positions)
  - tests                  (_should_wire_net, _should_wire_power_net, _is_feedback_net, _astar_route, _build_obstacle_set, etc.)
"""


# pylint: disable=unsubscriptable-object  # find_node() returns list|None; if-checks suffice
import heapq  # noqa: F401 — used by _astar_route
import math
import logging
from functools import lru_cache

from ..sexpr import SExpr, uid, FONT_SIZE, PIN_SPACING, SYM_HALF_WIDTH, PIN_LENGTH
from ..common.constants import (
    WIRE_CLEARANCE, POWER_CLEARANCE, WIRE_MAX_LENGTH, WIRE_MAX_PINS,
    LABEL_STUB_LEN,
)
from ..common.routing import ROUTE_GRID, _rasterize_path_cells, _l_path_clear, _try_lbend
from ..common.connectivity import _build_mst_edges
from ..common.bbox import _get_symbol_bbox
from ..symbol_lib import resolve_lib_id
from ..symbol_cache import get_real_symbol
from ...utils.sexpr_parser import parse_sexpr, find_node

logger = logging.getLogger(__name__)

# ── Power symbol support ───────────────────────────────────────────────────

# Map net names to KiCad power symbol lib_ids.
# "ground" = pin points UP at rotation 0 (GND bars below).
# "supply" = pin points DOWN at rotation 0 (bar above).
POWER_SYMBOL_MAP: dict[str, tuple[str, str]] = {
    "GND":   ("power:GND",   "ground"),
    "GNDA":  ("power:GNDA",  "ground"),
    "GNDPWR":("power:GNDPWR","ground"),
    "VSS":   ("power:VSS",   "ground"),
    "VCC":   ("power:VCC",   "supply"),
    "VDD":   ("power:VDD",   "supply"),
    "VEE":   ("power:VEE",   "supply"),
    "+5V":   ("power:+5V",   "supply"),
    "+3V3":  ("power:+3V3",  "supply"),
    "+3.3V": ("power:+3V3",  "supply"),
    "+9V":   ("power:+9V",   "supply"),
    "+12V":  ("power:+12V",  "supply"),
    "+24V":  ("power:+24V",  "supply"),
    "-5V":   ("power:-5V",   "ground"),
    "-12V":  ("power:-12V",  "ground"),
}

# Rotation by stub direction so the symbol graphic faces away from the wire.
_GROUND_ROTATION = {"down": 0, "up": 180, "right": 90, "left": 270}
_SUPPLY_ROTATION = {"up": 0, "down": 180, "right": 270, "left": 90}

_pwr_ref_counter: int = 0


def get_power_symbol_info(net_name: str) -> tuple[str, str] | None:
    """Return (lib_id, sym_type) for a recognised power net, else None."""
    return POWER_SYMBOL_MAP.get(net_name) or POWER_SYMBOL_MAP.get(net_name.upper())


# ── Wiring decision helpers ─────────────────────────────────────────────────


def _should_wire_net(
    net: dict, parts: list[dict], max_dist: float, pin_count: int
) -> bool:
    """Decide if a net should use wires or labels.

    Uses template-based Elektor wiring rules when available,
    falls back to distance/pin-count heuristic.
    """
    try:
        from ..template_matcher import should_use_label
        if should_use_label(net, parts, distance_mm=max_dist):
            return False
    except Exception:
        pass

    # Fallback: original heuristic
    return pin_count <= WIRE_MAX_PINS and max_dist <= WIRE_MAX_LENGTH


def _should_wire_power_net(
    parts: list[dict], pins: list[tuple], max_dist: float,
) -> bool:
    """Score-based decision whether a power net should use wires or labels.

    Elektor mixed approach: power nets use wires where practical, with a
    single global label for the net name. Only fall back to all-labels
    when the net is very large or spread across the entire sheet.

    Factors (Elektor-derived):
    - Fewer parts → prefer wires (simple circuits wire everything)
    - Fewer pins in net → prefer wires (short nets are easy to wire)
    - Shorter distance → prefer wires
    """
    part_count = len(parts)
    pin_count = len(pins)

    # Part count factor: 1.0 at <=10 parts, 0.0 at >=40 parts, linear between
    part_score = max(0.0, min(1.0, (40 - part_count) / 30))

    # Pin count factor: 1.0 at 2 pins, 0.0 at >=12 pins
    pin_score = max(0.0, min(1.0, (12 - pin_count) / 10))

    # Distance factor: 1.0 at <=30mm, 0.0 at >=90mm
    dist_score = max(0.0, min(1.0, (90 - max_dist) / 60))

    # Weighted combination: distance and pins matter more than total parts
    # (Elektor schematics wire power even in complex circuits when pins are close)
    wire_score = 0.3 * part_score + 0.35 * dist_score + 0.35 * pin_score

    return wire_score > 0.45


# ── A* Wire routing ──────────────────────────────────────────────────────────

BEND_COST = 3.0  # extra cost for changing direction (fewer bends = cleaner)
# Elektor insight: signal wires prefer horizontal flow (L→R)
VERTICAL_PENALTY = 0.3  # extra cost per vertical grid step (favors horizontal routing)


def _build_obstacle_set(
    parts: list[dict], clearance: float = WIRE_CLEARANCE,
) -> set[tuple[int, int]]:
    """Build set of grid cells blocked by components.

    Grid coordinates are (col, row) = (x/ROUTE_GRID, y/ROUTE_GRID).
    clearance: mm of extra margin around each component bounding box.
    """
    blocked: set[tuple[int, int]] = set()
    for part in parts:
        if "_place_x" not in part:
            continue
        cx, cy = part["_place_x"], part["_place_y"]
        w, h = _get_symbol_bbox(part)
        x1 = int((cx - w / 2 - clearance) / ROUTE_GRID) - 1
        x2 = int((cx + w / 2 + clearance) / ROUTE_GRID) + 1
        y1 = int((cy - h / 2 - clearance) / ROUTE_GRID) - 1
        y2 = int((cy + h / 2 + clearance) / ROUTE_GRID) + 1
        for gx in range(x1, x2 + 1):
            for gy in range(y1, y2 + 1):
                blocked.add((gx, gy))
    return blocked


def _cells_owned_by(part: dict, clearance: float = WIRE_CLEARANCE) -> set[tuple[int, int]]:
    """Return the set of grid cells belonging to a single component's bounding box."""
    cells: set[tuple[int, int]] = set()
    cx, cy = part["_place_x"], part["_place_y"]
    w, h = _get_symbol_bbox(part)
    x1 = int((cx - w / 2 - clearance) / ROUTE_GRID) - 1
    x2 = int((cx + w / 2 + clearance) / ROUTE_GRID) + 1
    y1 = int((cy - h / 2 - clearance) / ROUTE_GRID) - 1
    y2 = int((cy + h / 2 + clearance) / ROUTE_GRID) + 1
    for gx in range(x1, x2 + 1):
        for gy in range(y1, y2 + 1):
            cells.add((gx, gy))
    return cells


def _carve_pin_corridors(
    obstacles: set[tuple[int, int]],
    parts: list[dict],
    pin_refs: list[str],
) -> set[tuple[int, int]]:
    """Return a COPY of the obstacle set with corridors carved for specific pins.

    Only carves corridors for pins in pin_refs (e.g., ["U1:7", "R1:2"]).
    This prevents accidentally opening corridors through other components.
    """
    result = set(obstacles)  # shallow copy — ints are immutable
    part_map = {p["ref"]: p for p in parts if "_place_x" in p}

    for pin_ref in pin_refs:
        if ":" not in pin_ref:
            continue
        ref, pin_id = pin_ref.split(":", 1)
        part = part_map.get(ref)
        if not part:
            continue

        sx, sy = part["_place_x"], part["_place_y"]
        w, h = _get_symbol_bbox(part)
        cx_g = round(sx / ROUTE_GRID)
        cy_g = round(sy / ROUTE_GRID)

        lib_id = resolve_lib_id(part)
        pin_pos = _extract_pin_positions(lib_id, part)

        # Find the pin position — match by pin number or name
        local = pin_pos.get(pin_id)
        if local is None:
            # Try matching user pin name → number
            for p in part.get("pins", []):
                if p.get("name") == pin_id or p.get("num") == pin_id:
                    local = pin_pos.get(str(p["num"]))
                    break
        if local is None:
            continue

        px = round((sx + local[0]) / ROUTE_GRID)
        py = round((sy + local[1]) / ROUTE_GRID)

        # Bounding box of THIS component
        clearance = WIRE_CLEARANCE
        bx1 = int((sx - w / 2 - clearance) / ROUTE_GRID) - 1
        bx2 = int((sx + w / 2 + clearance) / ROUTE_GRID) + 1
        by1 = int((sy - h / 2 - clearance) / ROUTE_GRID) - 1
        by2 = int((sy + h / 2 + clearance) / ROUTE_GRID) + 1

        dx = 1 if px >= cx_g else -1
        dy = 1 if py >= cy_g else -1

        # Only remove cells that belong to THIS component's bounding box
        own_cells = _cells_owned_by(part)

        # Horizontal corridor (1 cell wide)
        gx = px
        while bx1 <= gx <= bx2:
            if (gx, py) in own_cells:
                result.discard((gx, py))
            gx += dx

        # Vertical corridor (1 cell wide)
        gy = py
        while by1 <= gy <= by2:
            if (px, gy) in own_cells:
                result.discard((px, gy))
            gy += dy

    return result


def _astar_route(
    start: tuple[float, float],
    end: tuple[float, float],
    obstacles: set[tuple[int, int]],
    pin_cells: set[tuple[int, int]],
    power: bool = False,
) -> list[tuple[float, float]] | None:
    """Find a Manhattan path from start to end avoiding obstacles using A*.

    Returns list of waypoints [(x1,y1), (x2,y2), ...] or None if no path.
    Pin cells (start/end component cells) are not treated as blocked.

    When power=True, vertical movement is preferred (power rails run vertically
    per Elektor convention) instead of the default horizontal preference.
    """
    # Convert to grid coordinates
    sc = (round(start[0] / ROUTE_GRID), round(start[1] / ROUTE_GRID))
    ec = (round(end[0] / ROUTE_GRID), round(end[1] / ROUTE_GRID))

    if sc == ec:
        return [start, end]

    # Ensure start and end cells + neighborhood are passable.
    # ±2 cells balances reachability vs accidental bridging of
    # adjacent IC pins (2.54mm apart = 2 grid cells).
    for cx, cy in [sc, ec]:
        for ddx in range(-2, 3):
            for ddy in range(-2, 3):
                pin_cells.add((cx + ddx, cy + ddy))

    # A* with Manhattan heuristic and bend penalty
    # State: (col, row, last_direction)  direction: 0=none, 1=horiz, 2=vert
    open_set: list[tuple[float, tuple[int, int, int]]] = []
    heapq.heappush(open_set, (0.0, (sc[0], sc[1], 0)))
    came_from: dict[tuple[int, int, int], tuple[int, int, int] | None] = {(sc[0], sc[1], 0): None}
    g_score: dict[tuple[int, int, int], float] = {(sc[0], sc[1], 0): 0.0}

    # Adaptive step limit based on Manhattan distance between start and end.
    # Minimum 5000 steps (short routes), scales with distance, max 25000.
    manhattan = abs(sc[0] - ec[0]) + abs(sc[1] - ec[1])
    max_steps = min(25000, max(5000, manhattan * 12))
    steps = 0

    while open_set and steps < max_steps:
        steps += 1
        _, (cx, cy, last_dir) = heapq.heappop(open_set)

        if (cx, cy) == ec:
            # Reconstruct path
            path_grid = []
            state: tuple[int, int, int] | None = (cx, cy, last_dir)
            while state is not None:
                path_grid.append((state[0], state[1]))
                state = came_from.get(state)
            path_grid.reverse()
            # Simplify: remove collinear points, then eliminate jogs
            simplified = _simplify_grid_path(path_grid)
            smoothed = _smooth_jogs(simplified, obstacles)
            return [(round(x * ROUTE_GRID, 2), round(y * ROUTE_GRID, 2))
                    for x, y in smoothed]

        # Neighbors: 4-connected grid (Manhattan)
        for dx, dy, new_dir in [(1, 0, 1), (-1, 0, 1), (0, 1, 2), (0, -1, 2)]:
            nx, ny = cx + dx, cy + dy
            cell = (nx, ny)

            # Allow start and end cells even if blocked
            if cell in obstacles and cell not in pin_cells:
                continue

            # Cost: base + bend penalty + direction penalty
            # Signals prefer horizontal (L→R flow), power prefers vertical (rail convention)
            move_cost = 1.0
            if last_dir != 0 and new_dir != last_dir:
                move_cost += BEND_COST
            if power:
                if new_dir == 1:  # horizontal costs more for power
                    move_cost += VERTICAL_PENALTY
            else:
                if new_dir == 2:  # vertical costs more for signals
                    move_cost += VERTICAL_PENALTY

            new_state = (nx, ny, new_dir)
            new_g = g_score.get((cx, cy, last_dir), float('inf')) + move_cost

            if new_g < g_score.get(new_state, float('inf')):
                g_score[new_state] = new_g
                h = abs(nx - ec[0]) + abs(ny - ec[1])  # Manhattan heuristic
                heapq.heappush(open_set, (new_g + h, new_state))
                came_from[new_state] = (cx, cy, last_dir)

    return None  # No path found


def _simplify_grid_path(path_grid: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Remove collinear intermediate points from a grid path (keep bends only)."""
    if len(path_grid) <= 2:
        return list(path_grid)

    simplified = [path_grid[0]]
    for i in range(1, len(path_grid) - 1):
        prev = path_grid[i - 1]
        curr = path_grid[i]
        nxt = path_grid[i + 1]
        d1 = (curr[0] - prev[0], curr[1] - prev[1])
        d2 = (nxt[0] - curr[0], nxt[1] - curr[1])
        if d1 != d2:
            simplified.append(curr)
    simplified.append(path_grid[-1])
    return simplified


def _smooth_jogs(
    path: list[tuple[int, int]],
    obstacles: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Remove unnecessary jogs from a simplified grid path.

    A jog is a pattern like A→B→C→D where B→C is a short detour that can be
    eliminated by connecting A directly to D via a single L-bend (A→corner→D).

    Only replaces a jog if the L-path is obstacle-free.
    """
    if len(path) <= 3:
        return path

    result = [path[0]]
    i = 0

    while i < len(path) - 1:
        # Try to skip the next two intermediate points (eliminate jog)
        if i + 3 < len(path):
            a = path[i]
            d = path[i + 3]

            # Try two L-bend options: (a.x, d.y) and (d.x, a.y)
            corner1 = (a[0], d[1])
            corner2 = (d[0], a[1])

            if _l_path_clear(a, corner1, d, obstacles):
                # Replace jog with L-path via corner1
                if corner1 != a and corner1 != d:
                    result.append(corner1)
                result.append(d)
                i += 3
                continue
            elif _l_path_clear(a, corner2, d, obstacles):
                if corner2 != a and corner2 != d:
                    result.append(corner2)
                result.append(d)
                i += 3
                continue

        # No jog elimination possible, keep next point
        i += 1
        result.append(path[i])

    return result


# ── Feedback detection ──────────────────────────────────────────────────────

FEEDBACK_X_THRESHOLD = 40.0  # mm — X-span beyond this qualifies for feedback check
FEEDBACK_MIN_SKIPPED = 2     # minimum skipped intermediate parts to confirm feedback


def _is_feedback_net(net: dict, nets: list[dict], parts: list[dict]) -> bool:
    """Detect if a net is a feedback path.

    Two detection modes:
    1. Direct: same component ref appears 2+ times in the net (e.g. op-amp output→input).
    2. Indirect: net spans a large X range (> FEEDBACK_X_THRESHOLD) AND skips over
       intermediate components that are NOT part of this net.  This indicates the net
       bridges across signal-flow stages — characteristic of feedback loops.
       Only applies to signal nets — power nets are excluded.

    Rule: Only long feedback paths should use labels. Short local feedback
    loops are clearer as direct wires.
    """
    connections = net.get("connections", [])
    if len(connections) < 2:
        return False

    # Direct feedback: same component ref with multiple pins in net
    ref_counts: dict[str, int] = {}
    for conn in connections:
        ref = conn.split(":")[0] if ":" in conn else ""
        if ref:
            ref_counts[ref] = ref_counts.get(ref, 0) + 1

    for ref, count in ref_counts.items():
        if count >= 2:
            ref_positions = [part.get("_place_x") for part in parts if part.get("ref") == ref]
            if ref_positions:
                local_span = max(ref_positions) - min(ref_positions)
                if local_span > FEEDBACK_X_THRESHOLD:
                    return True

    # Power nets don't have signal-flow feedback
    if net.get("type", "signal") == "power":
        return False

    # Indirect feedback: check X-positions of connected components
    ref_x: dict[str, float] = {}
    for part in parts:
        x = part.get("_place_x")
        if x is not None:
            ref_x[part["ref"]] = x

    # Collect refs in this net
    net_refs = set()
    for conn in connections:
        ref = conn.split(":")[0] if ":" in conn else ""
        if ref:
            net_refs.add(ref)

    x_positions = [ref_x[r] for r in net_refs if r in ref_x]
    if len(x_positions) < 2:
        return False

    x_max = max(x_positions)
    x_min = min(x_positions)
    x_span = x_max - x_min

    if x_span <= FEEDBACK_X_THRESHOLD:
        return False

    # Count parts whose X position falls between x_min and x_max
    # but are NOT connected by this net.  Skipped parts indicate
    # the net bridges over intermediate signal-flow stages.
    skipped = 0
    for part in parts:
        r = part["ref"]
        if r in net_refs:
            continue
        px = part.get("_place_x")
        if px is not None and x_min < px < x_max:
            skipped += 1

    return skipped >= FEEDBACK_MIN_SKIPPED and x_span > WIRE_MAX_LENGTH / 2


# ── Label stub placement ────────────────────────────────────────────────────

def _stub_direction(
    pin_x: float, pin_y: float, sym_x: float, sym_y: float,
) -> str:
    """Determine wire-stub direction from pin relative to component center.

    Returns one of 'right', 'left', 'up', 'down'.
    The stub points outward — away from the component center.
    """
    dx = pin_x - sym_x
    dy = pin_y - sym_y
    if abs(dx) >= abs(dy):
        return "right" if dx >= 0 else "left"
    else:
        return "down" if dy >= 0 else "up"


# Label angle for each stub direction (KiCad convention):
#   0 = text to the right (connection on left)
# 180 = text to the left  (connection on right)
#  90 = text upward        (connection at bottom)
# 270 = text downward      (connection at top)
_LABEL_ANGLE = {"right": 0, "left": 180, "up": 90, "down": 270}

_DIR_VEC = {"right": (1.0, 0.0), "left": (-1.0, 0.0),
            "up": (0.0, -1.0), "down": (0.0, 1.0)}


def _point_in_any_body(
    x: float, y: float, bodies: list[tuple[float, float, float, float]],
) -> bool:
    """Liegt ``(x, y)`` in einem Bauteil-Rahmen (Liste (cx, cy, hw, hh))?"""
    for cx, cy, hw, hh in bodies:
        if abs(x - cx) < hw and abs(y - cy) < hh:
            return True
    return False


def _free_stub_direction(
    pin_x: float, pin_y: float, sym_x: float, sym_y: float,
    bodies: list[tuple[float, float, float, float]],
) -> str:
    """Wähle eine Auswärts-Richtung, deren LABEL in FREIEM Raum landet.

    Nutzer-Regel „alle Netlabels müssen vom Bauteil weg zeigen": die natürliche
    Auswärts-Richtung (weg vom eigenen Bauteil-Zentrum) wird zuerst versucht;
    zeigt sie in einen NACHBAR-Körper, werden die anderen drei geprüft und die
    erste genommen, deren Label-Anker samt kurzer Sonde in KEINEM Bauteilkörper
    liegt. Findet sich keine freie, bleibt es bei der natürlichen (kein
    schlechterer Fallback als vorher). ``bodies`` schließt das eigene Bauteil
    ein — so wird auch eine Richtung „quer über den eigenen Körper" verworfen."""
    natural = _stub_direction(pin_x, pin_y, sym_x, sym_y)
    order = [natural] + [d for d in ("right", "left", "up", "down")
                         if d != natural]
    for d in order:
        vx, vy = _DIR_VEC[d]
        lx, ly = pin_x + vx * LABEL_STUB_LEN, pin_y + vy * LABEL_STUB_LEN
        # Sonde ein Stück ÜBER den Label-Anker hinaus (der Text ragt nach außen)
        px, py = lx + vx * LABEL_STUB_LEN, ly + vy * LABEL_STUB_LEN
        if not _point_in_any_body(lx, ly, bodies) \
                and not _point_in_any_body(px, py, bodies):
            return d
    return natural

# Offset vectors for each stub direction
_STUB_DX = {"right": LABEL_STUB_LEN, "left": -LABEL_STUB_LEN, "up": 0.0, "down": 0.0}
_STUB_DY = {"right": 0.0, "left": 0.0, "up": -LABEL_STUB_LEN, "down": LABEL_STUB_LEN}


def _place_label_with_stub(
    s: SExpr, net_name: str, pin_x: float, pin_y: float,
    lbl_uid: str, wire_uid: str, is_global: bool = False,
    is_hierarchical: bool = False,
    direction: str = "right",
) -> tuple[float, float]:
    """Place a label with a short wire stub to ensure ERC pin connectivity.

    KiCad requires a wire segment between a pin endpoint and a label
    for the ERC to recognize the connection. The stub points outward
    from the component in the given direction.

    Three label kinds, in precedence order: ``is_hierarchical`` →
    hierarchical_label (cross-sheet net on a sub-sheet), ``is_global`` →
    global_label (power rail / sheet-spanning name), otherwise local
    ``(label)``. ``is_hierarchical`` wins over ``is_global`` because a
    cross-sheet signal must use the hierarchical form even if the caller
    flagged it as global; the root sheet symbol carries the matching pin.

    Returns the label position (for tracking).
    """
    lbl_x = round(pin_x + _STUB_DX[direction], 2)
    lbl_y = round(pin_y + _STUB_DY[direction], 2)
    angle = _LABEL_ANGLE[direction]
    s.wire(pin_x, pin_y, lbl_x, lbl_y, wire_uid)
    if is_hierarchical:
        s.hierarchical_label(net_name, lbl_x, lbl_y, lbl_uid, angle=angle)
    elif is_global:
        s.global_label(net_name, lbl_x, lbl_y, lbl_uid, angle=angle)
    else:
        s.net_label(net_name, lbl_x, lbl_y, lbl_uid, angle=angle)
    return (lbl_x, lbl_y)


def _place_power_symbol(
    s: SExpr, net_name: str, pin_x: float, pin_y: float,
    sym_uid: str, wire_uid: str, pin_uid: str,
    lib_id: str, sym_type: str, direction: str = "right",
) -> tuple[float, float]:
    """Place a real KiCad power symbol (e.g. power:GND) with a stub wire.

    Convention (KiCad-Standard, hart erzwungen): Ground-Symbole zeigen IMMER
    nach unten, Versorgungs-Symbole IMMER nach oben — unabhängig davon, wohin
    der Pin zeigt. Der Stub geht entsprechend nach unten (Ground) bzw. oben
    (Supply), das Symbol sitzt darunter/darüber und die Rotation folgt. So
    liest sich das Blatt konventionell (GND unten, VCC oben)."""
    global _pwr_ref_counter
    _pwr_ref_counter += 1

    if sym_type == "ground":
        direction = "down"
    elif sym_type == "supply":
        direction = "up"

    sx = round(pin_x + _STUB_DX[direction], 2)
    sy = round(pin_y + _STUB_DY[direction], 2)

    s.wire(pin_x, pin_y, sx, sy, wire_uid)

    rot = (_GROUND_ROTATION if sym_type == "ground" else _SUPPLY_ROTATION).get(direction, 0)

    s.open("symbol", f'(lib_id "{lib_id}") (at {sx} {sy} {rot}) (unit 1)')
    s.emit("(in_bom no) (on_board yes)")
    s.emit(f'(uuid "{sym_uid}")')
    s.emit(
        f'(property "Reference" "#PWR{_pwr_ref_counter:04d}"'
        f' (at {sx} {round(sy + 2.54, 2)} 0)'
        f' (effects (font (size {FONT_SIZE} {FONT_SIZE})) hide))'
    )
    s.emit(
        f'(property "Value" "{net_name}"'
        f' (at {sx} {round(sy - 2.54, 2)} 0)'
        f' (effects (font (size {FONT_SIZE} {FONT_SIZE})) hide))'
    )
    s.pin_instance("1", pin_uid)
    s.close()

    return (sx, sy)


# ── Main wiring + label emission ────────────────────────────────────────────

def _emit_wires_and_labels(
    s: SExpr, parts: list[dict], nets: list[dict], project_name: str,
    intersheet_nets: set[str] | None = None,
) -> set[tuple[float, float]]:
    """Draw wires and place labels using A* routing.

    Rules applied:
    1. A* routes wires around component bounding boxes (never through)
    2. Labels replace wires when A* finds no path or distance is too long
    3. Feedback paths always use labels
    4. Power nets use real KiCad power symbols (power:GND, power:VCC, …)
       when a matching symbol exists, otherwise fall back to global labels
    5. Every label/symbol gets a wire stub from the pin for ERC connectivity
    6. Signal nets named in ``intersheet_nets`` get hierarchical labels
       (not local) so the cross-sheet pin on the parent root-sheet symbol
       matches. Power nets are unaffected — they cross sheets via global
       labels / power symbols, not the hierarchical mechanism.
    """
    global _pwr_ref_counter
    _pwr_ref_counter = 0
    _hier_names: set[str] = set(intersheet_nets or ())

    def _place_power_or_label(
        net_name: str, ax: float, ay: float, direction: str,
    ) -> tuple[float, float]:
        """Place a power symbol if a matching KiCad lib exists, else global label."""
        nonlocal label_count, wire_count
        pwr_info = get_power_symbol_info(net_name)
        l_uid = uid(f"{project_name}_pwr_{net_name}_{label_count}")
        w_uid = uid(f"{project_name}_stub_{net_name}_{label_count}_{wire_count}")
        p_uid = uid(f"{project_name}_pwrpin_{net_name}_{label_count}")
        label_count += 1
        wire_count += 1
        if pwr_info:
            lib_id, sym_type = pwr_info
            return _place_power_symbol(
                s, net_name, ax, ay, l_uid, w_uid, p_uid,
                lib_id, sym_type, direction=direction,
            )
        else:
            return _place_label_with_stub(
                s, net_name, ax, ay, l_uid, w_uid,
                is_global=True, direction=direction,
            )

    pin_to_net: dict[str, dict] = {}
    for net in nets:
        for conn in net.get("connections", []):
            pin_to_net[conn] = net

    _pin_pos_cache: dict[
        tuple[str, int, str], dict[str, tuple[float, float]]
    ] = {}
    labeled: set[tuple[float, float]] = set()
    wire_count = 0
    label_count = 0

    # Build obstacle grids — signal nets use standard clearance,
    # power nets use wider clearance for cleaner separation
    obstacles = _build_obstacle_set(parts, WIRE_CLEARANCE)
    power_obstacles = _build_obstacle_set(parts, POWER_CLEARANCE)
    # Minimal obstacle set for L-bend fallback (core bounding boxes only, no clearance)
    lbend_obstacles = _build_obstacle_set(parts, WIRE_CLEARANCE / 2)

    # Bauteil-Rahmen (rotations-bewusst) für die freie Label-Richtung: ein Label
    # soll vom Bauteil WEG in freien Raum zeigen, nicht in einen Nachbarn.
    _bodies: list[tuple[float, float, float, float]] = []
    for _p in parts:
        if "_place_x" not in _p:
            continue
        _w, _h = _get_symbol_bbox(_p)
        if int(_p.get("_rotation", 0)) in (90, 270):
            _w, _h = _h, _w
        _bodies.append((round(_p["_place_x"], 2), round(_p["_place_y"], 2),
                        _w / 2.0, _h / 2.0))

    def _dir(px: float, py: float, cx: float, cy: float) -> str:
        """Freie Auswärts-Richtung fürs Label an Pin (px,py) von Bauteil (cx,cy)."""
        return _free_stub_direction(px, py, cx, cy, _bodies)

    # Collect pin cells that should not be blocked (start/end of wires)
    all_pin_cells: set[tuple[int, int]] = set()

    # Collect all pin endpoints per net (including extra units)
    net_pins: dict[str, list[tuple[float, float, str, str]]] = {}

    for part in parts:
        ref = part["ref"]
        lib_id = resolve_lib_id(part)
        sym_x = round(part.get("_place_x", 50.8), 2)
        sym_y = round(part.get("_place_y", 38.1), 2)

        # Cache key bundles lib_id with the part's orientation — the
        # extracted local pin coords depend on both rotation and mirror
        # (K1/K5/K6). A flat lib_id cache would return rotated/mirrored
        # positions to the next instance with a different orientation.
        rot_key = int(part.get("_rotation", 0))
        mir_key = part.get("_mirror") or ""
        cache_key = (lib_id, rot_key, mir_key)
        if cache_key not in _pin_pos_cache:
            _pin_pos_cache[cache_key] = _extract_pin_positions(lib_id, part)
        pin_positions = _pin_pos_cache[cache_key]

        extra_units = part.get("_extra_units", [])
        extra_pin_nums: dict[str, tuple[float, float, float, float]] = {}
        for eu in extra_units:
            for ep in eu.get("pins", []):
                extra_pin_nums[ep["num"]] = (eu["x"], eu["y"], ep["x"], ep["y"])

        real_sym = get_real_symbol(lib_id)
        real_pins = _pins_from_real_symbol(real_sym) if real_sym else {}
        user_to_real = _map_user_to_real_pins(part, real_pins) if real_pins else {}

        for pin in part.get("pins", []):
            conn_by_name = f"{ref}:{pin['name']}"
            conn_by_num = f"{ref}:{pin['num']}"
            net = pin_to_net.get(conn_by_name) or pin_to_net.get(conn_by_num)
            if not net:
                continue
            pin_num = str(pin["num"])
            real_num = user_to_real.get(pin_num, pin_num)

            if real_num in extra_pin_nums:
                ux, uy, local_x, local_y = extra_pin_nums[real_num]
                abs_x = round(ux + local_x, 2)
                abs_y = round(uy + local_y, 2)
            else:
                local_pos = pin_positions.get(pin_num)
                if not local_pos:
                    continue
                abs_x = round(sym_x + local_pos[0], 2)
                abs_y = round(sym_y + local_pos[1], 2)

            net_name = net["name"]
            net_pins.setdefault(net_name, []).append((abs_x, abs_y, ref, real_num, sym_x, sym_y))
            labeled.add((abs_x, abs_y))
            all_pin_cells.add((round(abs_x / ROUTE_GRID), round(abs_y / ROUTE_GRID)))

    # Route each net
    for net in nets:
        net_name = net["name"]
        net_type = net.get("type", "signal")
        pins = net_pins.get(net_name, [])

        if not pins:
            continue

        # Power nets: score-based decision (parts, pins, distance)
        if net_type == "power":
            max_power_dist = 0.0
            for i, (ax, ay, *_rest) in enumerate(pins):
                for bx, by, *_rest2 in pins[i + 1:]:
                    d = math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)
                    max_power_dist = max(max_power_dist, d)

            wire_power = _should_wire_power_net(parts, pins, max_power_dist)

            if wire_power and len(pins) >= 2:
                # Simple circuit: wire the power net like a signal net
                # Place one global label at the first pin with stub
                ax0, ay0, ref0, pnum0, sx0, sy0 = pins[0]
                d0 = _dir(ax0, ay0, sx0, sy0)
                _place_power_or_label(net_name, ax0, ay0, d0)

                # Wire between pins using MST
                mst_edges = _build_mst_edges(pins)
                for pi, pj in mst_edges:
                    start = (pins[pi][0], pins[pi][1])
                    end = (pins[pj][0], pins[pj][1])
                    route_obs = _carve_pin_corridors(
                        power_obstacles, parts,
                        [f"{pins[pi][2]}:{pins[pi][3]}", f"{pins[pj][2]}:{pins[pj][3]}"])
                    path = _astar_route(start, end, route_obs, all_pin_cells, power=True)
                    if path and len(path) >= 2:
                        for j in range(len(path) - 1):
                            w_uid = uid(f"{project_name}_wire_{net_name}_{wire_count}")
                            wire_count += 1
                            s.wire(path[j][0], path[j][1], path[j+1][0], path[j+1][1], w_uid)
                        path_cells = _rasterize_path_cells(path)
                        power_obstacles |= path_cells
                        obstacles |= path_cells
                    else:
                        # A* failed — try L-bend before falling back to label
                        l_path = _try_lbend(start, end, lbend_obstacles,
                                            [power_obstacles, obstacles])
                        if l_path:
                            for j in range(len(l_path) - 1):
                                w_uid = uid(f"{project_name}_wire_{net_name}_{wire_count}")
                                wire_count += 1
                                s.wire(l_path[j][0], l_path[j][1], l_path[j+1][0], l_path[j+1][1], w_uid)
                        else:
                            # L-bend also blocked — label the far pin with stub
                            ax, ay, ref, pnum, sx, sy = pins[pj]
                            d = _dir(ax, ay, sx, sy)
                            _place_power_or_label(net_name, ax, ay, d)
            else:
                # Mixed approach (Elektor style): try wiring what we can,
                # label the rest. Even complex power nets benefit from
                # partial wiring where pins are nearby.
                ax0, ay0, ref0, pnum0, sx0, sy0 = pins[0]
                d0 = _dir(ax0, ay0, sx0, sy0)
                _place_power_or_label(net_name, ax0, ay0, d0)

                wired_power = {0}  # first pin has the label
                if len(pins) >= 2:
                    mst_edges = _build_mst_edges(pins)
                    for pi, pj in mst_edges:
                        start = (pins[pi][0], pins[pi][1])
                        end = (pins[pj][0], pins[pj][1])
                        route_obs = _carve_pin_corridors(
                            power_obstacles, parts,
                            [f"{pins[pi][2]}:{pins[pi][3]}", f"{pins[pj][2]}:{pins[pj][3]}"])
                        path = _astar_route(start, end, route_obs, all_pin_cells, power=True)
                        if path and len(path) >= 2:
                            for j in range(len(path) - 1):
                                w_uid = uid(f"{project_name}_wire_{net_name}_{wire_count}")
                                wire_count += 1
                                s.wire(path[j][0], path[j][1], path[j+1][0], path[j+1][1], w_uid)
                            wired_power.add(pi)
                            wired_power.add(pj)
                            path_cells = _rasterize_path_cells(path)
                            power_obstacles |= path_cells
                            obstacles |= path_cells
                        else:
                            # A* failed — try L-bend
                            l_path = _try_lbend(start, end, lbend_obstacles,
                                                [power_obstacles, obstacles])
                            if l_path:
                                for j in range(len(l_path) - 1):
                                    w_uid = uid(f"{project_name}_wire_{net_name}_{wire_count}")
                                    wire_count += 1
                                    s.wire(l_path[j][0], l_path[j][1], l_path[j+1][0], l_path[j+1][1], w_uid)
                                wired_power.add(pi)
                                wired_power.add(pj)

                # Label only unreached pins
                for idx, (ax, ay, ref, pnum, sx, sy) in enumerate(pins):
                    if idx not in wired_power:
                        d = _dir(ax, ay, sx, sy)
                        _place_power_or_label(net_name, ax, ay, d)
            continue

        is_hier = net_name in _hier_names

        if len(pins) == 1:
            ax, ay, ref, pnum, sx, sy = pins[0]
            d = _dir(ax, ay, sx, sy)
            lbl_uid = uid(f"{project_name}_lbl_{net_name}_{ref}_{pnum}_{label_count}")
            w_uid = uid(f"{project_name}_stub_{net_name}_{ref}_{pnum}_{wire_count}")
            label_count += 1
            wire_count += 1
            _place_label_with_stub(
                s, net_name, ax, ay, lbl_uid, w_uid,
                is_hierarchical=is_hier, direction=d,
            )
            continue

        # Feedback paths: always labels with stubs
        if _is_feedback_net(net, nets, parts):
            for ax, ay, ref, pnum, sx, sy in pins:
                d = _dir(ax, ay, sx, sy)
                lbl_uid = uid(f"{project_name}_lbl_{net_name}_{ref}_{pnum}_{label_count}")
                w_uid = uid(f"{project_name}_stub_{net_name}_{ref}_{pnum}_{wire_count}")
                label_count += 1
                wire_count += 1
                _place_label_with_stub(
                    s, net_name, ax, ay, lbl_uid, w_uid,
                    is_hierarchical=is_hier, direction=d,
                )
            continue

        # Check max distance for wiring decision
        max_dist = 0
        for i, (ax, ay, *_rest) in enumerate(pins):
            for bx, by, *_rest2 in pins[i + 1:]:
                d = math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)
                max_dist = max(max_dist, d)

        # Template-based wiring decision (Elektor rules)
        use_wires = _should_wire_net(net, parts, max_dist, len(pins))

        if use_wires:
            # Build MST edges (greedy nearest-neighbor) for shorter total wire length
            mst_edges = _build_mst_edges(pins)

            # A* route each MST edge; track which pins got wired
            _wire_failed = False
            wired_pin_indices = set()
            for pi, pj in mst_edges:
                start = (pins[pi][0], pins[pi][1])
                end = (pins[pj][0], pins[pj][1])

                route_obs = _carve_pin_corridors(
                    obstacles, parts,
                    [f"{pins[pi][2]}:{pins[pi][3]}", f"{pins[pj][2]}:{pins[pj][3]}"])
                path = _astar_route(start, end, route_obs, all_pin_cells)

                if path and len(path) >= 2:
                    for j in range(len(path) - 1):
                        w_uid = uid(f"{project_name}_wire_{net_name}_{wire_count}")
                        wire_count += 1
                        s.wire(path[j][0], path[j][1], path[j + 1][0], path[j + 1][1], w_uid)
                    wired_pin_indices.add(pi)
                    wired_pin_indices.add(pj)
                    # Block routed wire waypoints (full rasterization
                    # blocks too many cells in dense areas — see benchmark)
                    for wp in path:
                        gc = (round(wp[0] / ROUTE_GRID), round(wp[1] / ROUTE_GRID))
                        obstacles.add(gc)
                else:
                    # A* failed — try L-bend fallback
                    l_path = _try_lbend(start, end, lbend_obstacles, [obstacles])
                    if l_path:
                        for j in range(len(l_path) - 1):
                            w_uid = uid(f"{project_name}_wire_{net_name}_{wire_count}")
                            wire_count += 1
                            s.wire(l_path[j][0], l_path[j][1], l_path[j+1][0], l_path[j+1][1], w_uid)
                        wired_pin_indices.add(pi)
                        wired_pin_indices.add(pj)
                    else:
                        _wire_failed = True

            # Only keep labels when the net could not be fully wired.
            if wired_pin_indices and len(wired_pin_indices) < len(pins):
                first_wired = min(wired_pin_indices)
                ax0, ay0, ref0, pnum0, sx0, sy0 = pins[first_wired]
                lbl_uid = uid(f"{project_name}_lbl_{net_name}_{ref0}_{pnum0}_{label_count}")
                label_count += 1
                if is_hier:
                    s.hierarchical_label(net_name, ax0, ay0, lbl_uid)
                else:
                    s.net_label(net_name, ax0, ay0, lbl_uid)

            # Label any pins that couldn't be reached by wire (with stubs)
            for idx, (ax, ay, ref, pnum, sx, sy) in enumerate(pins):
                if idx not in wired_pin_indices:
                    stub_dir = _dir(ax, ay, sx, sy)
                    lbl_uid = uid(f"{project_name}_lbl_{net_name}_{ref}_{pnum}_{label_count}")
                    w_uid = uid(f"{project_name}_stub_{net_name}_{ref}_{pnum}_{wire_count}")
                    label_count += 1
                    wire_count += 1
                    _place_label_with_stub(
                        s, net_name, ax, ay, lbl_uid, w_uid,
                        is_hierarchical=is_hier, direction=stub_dir,
                    )
        else:
            # Per-edge decision: try wiring short MST edges even when
            # the net as a whole was classified as "label" (e.g. long net
            # with some nearby pins).  Threshold: half of WIRE_MAX_LENGTH.
            SHORT_EDGE_THRESHOLD = WIRE_MAX_LENGTH / 2
            wired_label_indices: set[int] = set()
            if len(pins) >= 2:
                mst_edges = _build_mst_edges(pins)
                for pi, pj in mst_edges:
                    start = (pins[pi][0], pins[pi][1])
                    end = (pins[pj][0], pins[pj][1])
                    edge_dist = math.sqrt((start[0] - end[0]) ** 2 + (start[1] - end[1]) ** 2)
                    if edge_dist > SHORT_EDGE_THRESHOLD:
                        continue  # too long — label these pins
                    route_obs = _carve_pin_corridors(
                        obstacles, parts,
                        [f"{pins[pi][2]}:{pins[pi][3]}", f"{pins[pj][2]}:{pins[pj][3]}"])
                    path = _astar_route(start, end, route_obs, all_pin_cells)
                    if path and len(path) >= 2:
                        for j in range(len(path) - 1):
                            w_uid = uid(f"{project_name}_wire_{net_name}_{wire_count}")
                            wire_count += 1
                            s.wire(path[j][0], path[j][1], path[j + 1][0], path[j + 1][1], w_uid)
                        wired_label_indices.add(pi)
                        wired_label_indices.add(pj)
                        for wp in path:
                            gc = (round(wp[0] / ROUTE_GRID), round(wp[1] / ROUTE_GRID))
                            obstacles.add(gc)
                    else:
                        l_path = _try_lbend(start, end, lbend_obstacles, [obstacles])
                        if l_path:
                            for j in range(len(l_path) - 1):
                                w_uid = uid(f"{project_name}_wire_{net_name}_{wire_count}")
                                wire_count += 1
                                s.wire(l_path[j][0], l_path[j][1], l_path[j+1][0], l_path[j+1][1], w_uid)
                            wired_label_indices.add(pi)
                            wired_label_indices.add(pj)

            # Only keep labels when some pins still fall back to labels.
            if wired_label_indices and len(wired_label_indices) < len(pins):
                first_w = min(wired_label_indices)
                ax0, ay0, ref0, pnum0, sx0, sy0 = pins[first_w]
                lbl_uid = uid(f"{project_name}_lbl_{net_name}_{ref0}_{pnum0}_{label_count}")
                label_count += 1
                if is_hier:
                    s.hierarchical_label(net_name, ax0, ay0, lbl_uid)
                else:
                    s.net_label(net_name, ax0, ay0, lbl_uid)

            # Label pins that couldn't be wired
            for idx, (ax, ay, ref, pnum, sx, sy) in enumerate(pins):
                if idx not in wired_label_indices:
                    stub_dir = _dir(ax, ay, sx, sy)
                    lbl_uid = uid(f"{project_name}_lbl_{net_name}_{ref}_{pnum}_{label_count}")
                    w_uid = uid(f"{project_name}_stub_{net_name}_{ref}_{pnum}_{wire_count}")
                    label_count += 1
                    wire_count += 1
                    _place_label_with_stub(
                        s, net_name, ax, ay, lbl_uid, w_uid,
                        is_hierarchical=is_hier, direction=stub_dir,
                    )

    logger.info("Emitted %d wires and %d labels", wire_count, label_count)
    return labeled


# ── Pin position extraction ──────────────────────────────────────────────────

def _rotate_point(x: float, y: float, angle_deg: int) -> tuple[float, float]:
    """Rotate a point around origin by angle in degrees (KiCad convention)."""
    if angle_deg == 0:
        return (x, y)
    rad = math.radians(-angle_deg)  # KiCad uses clockwise rotation
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    return (round(x * cos_a - y * sin_a, 4), round(x * sin_a + y * cos_a, 4))


def _extract_pin_positions(lib_id: str, part: dict) -> dict[str, tuple[float, float]]:
    """Extract pin positions keyed by the user's pin number.

    Applies the schematic-symbol transform (mirror **before** rotation,
    per KiCad's ``SCH_SYMBOL::SetOrientation`` and ``CLAUDE.md``
    §Coord-Systems K5/K6) so that the absolute position calculation
    (sym_x + local_x) works correctly for rotated and/or mirrored
    placements. Pre-fix the mirror property was ignored, which routed
    wires to the wrong side of any LTspice-imported `(mirror …)` symbol.
    """
    raw = get_real_symbol(lib_id)
    if not raw:
        return _pins_from_placeholder(part)

    # _pins_from_real_symbol already applies the lib_symbols Y-flip
    # (the lib frame is Y-up, schematic frame is Y-down). So ``(x, y)``
    # here is the post-Y-flip local position the schematic semantics
    # expect — ready for mirror + rotation.
    real_pins = _pins_from_real_symbol(raw)
    user_pins = part.get("pins", [])
    rotation = int(part.get("_rotation", 0))
    mirror = part.get("_mirror") or None  # "x" / "y" / None

    result: dict[str, tuple[float, float]] = {}

    for pin in user_pins:
        user_num = str(pin["num"])
        user_name = pin.get("name", "")

        # Strategy 1: direct number match
        pos = real_pins.get(user_num)

        # Strategy 2: user pin name matches a real pin number
        if pos is None:
            pos = real_pins.get(user_name)

        # Strategy 3: case-insensitive name match
        if pos is None:
            name_upper = user_name.upper()
            for rp_num, rp_pos in real_pins.items():
                if rp_num.upper() == name_upper:
                    pos = rp_pos
                    break

        if pos is not None:
            lx, ly = pos
            # Mirror BEFORE rotation. ``(mirror x)`` = mirror about the
            # X-axis = Y-negation; ``(mirror y)`` = X-negation. See
            # ``sch_geometry.pin_world_xy``.
            if mirror == "y":
                lx = -lx
            elif mirror == "x":
                ly = -ly
            result[user_num] = _rotate_point(lx, ly, rotation)

    if not result:
        return _pins_from_placeholder(part)

    return result


@lru_cache(maxsize=256)
def _pins_from_real_symbol(raw_sexpr: str) -> dict[str, tuple[float, float]]:
    """Extract pin positions from real KiCad symbol S-expression.

    Returns {pin_num: (x, -y)} — Y is negated because KiCad schematic
    Y-axis points downward, while lib_symbol pin coordinates use
    mathematical Y-axis (upward).

    Reines Parsen desselben (nun gecachten) Symbol-Texts — memoisiert, weil
    Emit/Routing es pro lib_id dutzendfach anfragt (der Text ist stabil).
    """
    tree = parse_sexpr(raw_sexpr)
    pins: dict[str, tuple[float, float]] = {}

    def _walk(node: list) -> None:
        if not isinstance(node, list) or not node:
            return
        if node[0] == "pin":
            at = find_node(node, "at")
            num = find_node(node, "number")
            if at and num and len(at) >= 3 and len(num) >= 2:
                # Negate Y: lib_symbol uses math Y (up), schematic uses screen Y (down)
                pins[str(num[1])] = (float(at[1]), -float(at[2]))
        for child in node:
            if isinstance(child, list):
                _walk(child)

    _walk(tree)
    return pins


def _map_user_to_real_pins(part: dict, real_pins: dict[str, tuple[float, float]]) -> dict[str, str]:
    """Map user pin numbers to real KiCad pin numbers.

    Returns: {user_pin_num: real_pin_num}
    """
    mapping: dict[str, str] = {}
    for pin in part.get("pins", []):
        user_num = str(pin["num"])
        user_name = pin.get("name", "")

        if user_num in real_pins:
            mapping[user_num] = user_num
        elif user_name in real_pins:
            mapping[user_num] = user_name
        else:
            name_upper = user_name.upper()
            for rp_num in real_pins:
                if rp_num.upper() == name_upper:
                    mapping[user_num] = rp_num
                    break
            else:
                mapping[user_num] = user_num  # fallback
    return mapping


def _pins_from_placeholder(part: dict) -> dict[str, tuple[float, float]]:
    pins_list = part.get("pins", [])
    n = len(pins_list)
    result: dict[str, tuple[float, float]] = {}
    for i, pin in enumerate(pins_list):
        y = round((n - 1) * FONT_SIZE - i * PIN_SPACING, 4)
        x = round(-(SYM_HALF_WIDTH + PIN_LENGTH), 4)
        result[str(pin["num"])] = (x, y)
    return result
