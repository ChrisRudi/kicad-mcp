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
    WIRE_CLEARANCE, WIRE_MAX_LENGTH, WIRE_MAX_PINS,
    LABEL_STUB_LEN, PIN_STUB_LEN,
)
from ..common.routing import ROUTE_GRID, _l_path_clear, _try_lbend
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


def _normalize_power_name(net_name: str) -> str | None:
    """Häufige Rail-Schreibweisen auf einen Map-Schlüssel normalisieren.

    Die Kits nennen Rails ``P5V`` / ``P3V3`` / ``5V`` / ``3V3`` / ``3.3V`` —
    ohne Normalisierung landen die als TEXT-Label statt als kompaktes
    KiCad-Power-Symbol (so zeichnen es die Profi-Referenzen). Gibt einen
    passenden ``POWER_SYMBOL_MAP``-Schlüssel zurück oder ``None``."""
    import re as _re
    s = net_name.strip().upper()
    if s in POWER_SYMBOL_MAP:
        return s
    # führendes P vor einer Ziffer (P5V, P3V3) wegnehmen
    m = _re.match(r'^P(\d.*)$', s)
    if m:
        s = m.group(1)
    # 3V3 / 3.3V / 3.3 → +3V3 ; 5V / 5 → +5V ; …
    s = s.replace("V", "V").replace("_", "")
    canon = {
        "5V": "+5V", "5": "+5V",
        "3V3": "+3V3", "3.3V": "+3V3", "3.3": "+3V3", "3V": "+3V3",
        "1V8": "+3V3", "1.8V": "+3V3",  # kein +1V8-Symbol → nächstbestes Supply
        "9V": "+9V", "12V": "+12V", "24V": "+24V",
    }
    key = canon.get(s)
    if key:
        return key
    # „+5V" bleibt +5V etc.
    if s in POWER_SYMBOL_MAP:
        return s
    return None


def get_power_symbol_info(net_name: str) -> tuple[str, str] | None:
    """Return (lib_id, sym_type) for a recognised power net, else None."""
    direct = POWER_SYMBOL_MAP.get(net_name) or POWER_SYMBOL_MAP.get(net_name.upper())
    if direct:
        return direct
    norm = _normalize_power_name(net_name)
    return POWER_SYMBOL_MAP.get(norm) if norm else None


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
        # Rotation-bewusst: ein um 90/270° gedrehtes Bauteil (waagrechter R,
        # gekippter IC) hat vertauschte Breite/Höhe. Ohne diesen Swap modelliert
        # der Router einen waagrechten Widerstand als schmal-hohes Hindernis und
        # zieht einen waagrechten Bus MITTEN durch den Körper — genau die „Busse
        # über die Bauteile". Metrik & Label-Richtung tun den Swap längst.
        if int(part.get("_rotation", 0)) in (90, 270):
            w, h = h, w
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
    if int(part.get("_rotation", 0)) in (90, 270):
        w, h = h, w
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


def _pin_stub_point(
    pin_x: float, pin_y: float, sym_x: float, sym_y: float,
) -> tuple[float, float]:
    """Kurzer axialer Stub-Endpunkt AUSWÄRTS vom Bauteil an einem Pin.

    Der Stub zeigt entlang der Pin-Achse (dominante Richtung vom Bauteil-Zentrum
    weg) um ``PIN_STUB_LEN`` nach außen. Von diesem Punkt startet der A*-Draht —
    außerhalb des Körpers, sodass nie ein Bus quer durch das eigene Bauteil
    gezogen wird, und der Pin einen sichtbaren Anschluss-Stummel bekommt."""
    d = _stub_direction(pin_x, pin_y, sym_x, sym_y)
    vx, vy = _DIR_VEC[d]
    return (round(pin_x + vx * PIN_STUB_LEN, 2),
            round(pin_y + vy * PIN_STUB_LEN, 2))


def _point_in_any_body(
    x: float, y: float, bodies: list[tuple[float, float, float, float]],
    margin: float = 0.0,
) -> bool:
    """Liegt ``(x, y)`` in einem Bauteil-Rahmen (Liste (cx, cy, hw, hh)),
    optional um ``margin`` (Pin-/Beschriftungs-Zone) erweitert?"""
    for cx, cy, hw, hh in bodies:
        if abs(x - cx) < hw + margin and abs(y - cy) < hh + margin:
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
    stub_len: float | None = None,
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
    ln = LABEL_STUB_LEN if stub_len is None else stub_len
    vx, vy = _DIR_VEC[direction]
    lbl_x = round(pin_x + vx * ln, 2)
    lbl_y = round(pin_y + vy * ln, 2)
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
    stub_len: float | None = None,
) -> tuple[float, float]:
    """Place a real KiCad power symbol (e.g. power:GND) with a stub wire.

    Convention (KiCad-Standard, hart erzwungen): Ground-Symbole zeigen IMMER
    nach unten, Versorgungs-Symbole IMMER nach oben — unabhängig davon, wohin
    der Pin zeigt. Der Stub geht entsprechend nach unten (Ground) bzw. oben
    (Supply), das Symbol sitzt darunter/darüber und die Rotation folgt. So
    liest sich das Blatt konventionell (GND unten, VCC oben)."""
    global _pwr_ref_counter
    _pwr_ref_counter += 1

    # Konvention erzwingen (GND unten, Versorgung oben) — außer der Aufrufer
    # hat bereits eine vertikale Ausweich-Richtung verhandelt (Konfliktfall).
    if direction not in ("up", "down"):
        direction = "down" if sym_type == "ground" else "up"

    ln = LABEL_STUB_LEN if stub_len is None else stub_len
    vx, vy = _DIR_VEC[direction]
    sx = round(pin_x + vx * ln, 2)
    sy = round(pin_y + vy * ln, 2)

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

    # ── Segment-Registry: verhindert Kurzschlüsse zwischen Netzen ──────────
    # KiCad verbindet Drähte an zusammenfallenden ENDPUNKTEN und an Endpunkt-
    # auf-Segment-Berührungen. Zwei Netze, deren Routen sich einen Rasterpunkt
    # als Knick teilen, sind deshalb ein Kurzschluss — der Netzlisten-Roundtrip
    # deckte genau das flächendeckend auf. Jede emittierte Leitung wird hier
    # mit Netz-Namen registriert; jeder Kandidat (Route/Stub) wird vor dem
    # Emittieren gegen FREMDE Segmente geprüft.
    _segs: list[tuple[float, float, float, float, str]] = []
    _pt_net: dict[tuple[float, float], str] = {}
    # Pin-Positionen mit Netz-Zugehörigkeit — VOR jeder Emission befüllt. Ein
    # Stub/Draht, der auf einem FREMDEN Pin endet (oder über ihn läuft), ist
    # ein Kurzschluss, den die Segment-Registry allein nicht sieht (der
    # VCC-Stub von R2:1 endete exakt auf C2:2s GND-Pin — 5.08 mm Raster-Pech).
    _pin_pts: dict[tuple[float, float], str] = {}

    def _r2(v: float) -> float:
        return round(v, 2)

    def _on_seg(px: float, py: float, x1, y1, x2, y2) -> bool:
        """Liegt (px,py) AUF dem axialen Segment (inkl. Endpunkte)?"""
        if abs(y1 - y2) < 0.01:   # horizontal
            return (abs(py - y1) < 0.01
                    and min(x1, x2) - 0.01 <= px <= max(x1, x2) + 0.01)
        if abs(x1 - x2) < 0.01:   # vertikal
            return (abs(px - x1) < 0.01
                    and min(y1, y2) - 0.01 <= py <= max(y1, y2) + 0.01)
        return False

    def _collinear_overlap(a, b) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        if abs(ay1 - ay2) < 0.01 and abs(by1 - by2) < 0.01 \
                and abs(ay1 - by1) < 0.01:
            return min(max(ax1, ax2), max(bx1, bx2)) \
                - max(min(ax1, ax2), min(bx1, bx2)) > 0.05
        if abs(ax1 - ax2) < 0.01 and abs(bx1 - bx2) < 0.01 \
                and abs(ax1 - bx1) < 0.01:
            return min(max(ay1, ay2), max(by1, by2)) \
                - max(min(ay1, ay2), min(by1, by2)) > 0.05
        return False

    def _seg_conflicts(x1, y1, x2, y2, net: str) -> bool:
        """Kollidiert das Kandidaten-Segment elektrisch mit einem FREMDEN Netz?
        (Endpunkt teilt Punkt / Endpunkt auf fremdem Segment / fremder Endpunkt
        auf Kandidat / kollineare Überlappung.)"""
        for p in ((_r2(x1), _r2(y1)), (_r2(x2), _r2(y2))):
            owner = _pt_net.get(p)
            if owner is not None and owner != net:
                return True
            pin_owner = _pin_pts.get(p)
            if pin_owner is not None and pin_owner != net:
                return True
        # fremder PIN mitten auf dem Kandidaten-Segment → Kurzschluss
        for pp, pnet in _pin_pts.items():
            if pnet != net and _on_seg(pp[0], pp[1], x1, y1, x2, y2):
                return True
        for fx1, fy1, fx2, fy2, fnet in _segs:
            if fnet == net:
                continue
            if _collinear_overlap((x1, y1, x2, y2), (fx1, fy1, fx2, fy2)):
                return True
            if _on_seg(x1, y1, fx1, fy1, fx2, fy2) \
                    or _on_seg(x2, y2, fx1, fy1, fx2, fy2):
                return True
            if _on_seg(fx1, fy1, x1, y1, x2, y2) \
                    or _on_seg(fx2, fy2, x1, y1, x2, y2):
                return True
        return False

    def _seg_through_body_core(x1, y1, x2, y2) -> bool:
        """Quert das Segment das INNERE eines Bauteil-Körpers? (Kern-Zone =
        Rahmen minus Pin-Reichweite; kleine Bauteile ohne Kern zählen nicht.)
        A* meidet Hindernis-Zellen, aber geöffnete Start/Ziel-Nachbarschaften
        und Optimizer-Verschiebungen ließen vereinzelt Routen durch Körper
        (led_ring: „Draht quert WS2812B")."""
        for cx, cy, hw, hh in _bodies:
            chw, chh = hw - 2.84, hh - 2.84
            if chw <= 0.3 or chh <= 0.3:
                continue
            xmin, xmax = cx - chw, cx + chw
            ymin, ymax = cy - chh, cy + chh
            dx, dy = x2 - x1, y2 - y1
            t0, t1 = 0.0, 1.0
            ok = True
            for p, q in ((-dx, x1 - xmin), (dx, xmax - x1),
                         (-dy, y1 - ymin), (dy, ymax - y1)):
                if abs(p) < 1e-9:
                    if q < 0:
                        ok = False
                        break
                else:
                    r = q / p
                    if p < 0:
                        t0 = max(t0, r)
                    else:
                        t1 = min(t1, r)
            if ok and t0 < t1 - 1e-6:
                return True
        return False

    def _path_conflicts(path: list[tuple[float, float]], net: str) -> bool:
        for j in range(len(path) - 1):
            if _seg_conflicts(path[j][0], path[j][1],
                              path[j + 1][0], path[j + 1][1], net):
                return True
            if _seg_through_body_core(path[j][0], path[j][1],
                                      path[j + 1][0], path[j + 1][1]):
                return True
        return False

    def _register_seg(x1, y1, x2, y2, net: str) -> None:
        _segs.append((x1, y1, x2, y2, net))
        _pt_net.setdefault((_r2(x1), _r2(y1)), net)
        _pt_net.setdefault((_r2(x2), _r2(y2)), net)

    def _wire_reg(x1, y1, x2, y2, uid_str: str, net: str) -> None:
        s.wire(x1, y1, x2, y2, uid_str)
        _register_seg(x1, y1, x2, y2, net)

    def _stub_dir_free(ax, ay, sx, sy, net: str,
                       length: float) -> tuple[str, float] | None:
        """Auswärts-Richtung für einen Label-Stub — ELEKTRISCH sicher zuerst.

        Zweistufig: (1) Richtung ohne Fremd-Netz-Kontakt UND außerhalb aller
        Körper; (2) zur Not eine Richtung, die nur ästhetisch stört (Körper),
        aber elektrisch sauber ist. Die Körper-Bboxen enthalten die
        Pin-Reichweite — eine Stub-Spitze 5 mm vor dem Pin liegt dadurch fast
        immer „im Körper", weshalb der frühere Ein-Stufen-Check regelmäßig auf
        die Natural-Richtung MIT Fremd-Kontakt zurückfiel (der CTRL/OUT-
        Kurzschluss im 555-Kit: OUT-Stub quer durch C2s Pin)."""
        natural = _free_stub_direction(ax, ay, sx, sy, _bodies)
        order = [natural] + [d for d in ("right", "left", "up", "down")
                             if d != natural]
        tip_free: tuple[str, float] | None = None
        any_clean: tuple[str, float] | None = None
        # feine Längen zuerst mit dabei: ein 1.27-mm-Stub erreicht den
        # Nachbar-Stub (2.54-Raster) nicht — fast immer konfliktfrei.
        for ln in (length, 2.54, 1.27, length * 1.5, length * 2.0):
            for d in order:
                vx, vy = _DIR_VEC[d]
                tx, ty = _r2(ax + vx * ln), _r2(ay + vy * ln)
                if _seg_conflicts(ax, ay, tx, ty, net):
                    continue
                # Zone wie die Metrik: Körper + Pin-Zone (2.84) — ein Label
                # in der Pin-Nummern-Spalte zählt dort als Überdeckung.
                tip_in = _point_in_any_body(tx, ty, _bodies, margin=2.84)
                # Sonde HINTER dem Anker: der Text ragt in Winkel-Richtung über
                # den Anker hinaus — liegt die Sonde in einem Körper/der
                # Pin-Zone, schreibt das Label „ins Bauteil rein" (der
                # USB-Hub-Befund: Labels längs durch die Pin-Nummern-Spalte).
                px, py = _r2(tx + vx * length), _r2(ty + vy * length)
                probe_in = _point_in_any_body(px, py, _bodies, margin=2.84)
                if not tip_in and not probe_in:
                    return (d, ln)
                if not tip_in and tip_free is None:
                    tip_free = (d, ln)
                if any_clean is None:
                    any_clean = (d, ln)
        if tip_free is not None:
            return tip_free
        if any_clean is not None:
            return any_clean
        # KEINE elektrisch saubere Richtung: None → der Aufrufer setzt das
        # Label OHNE Stub direkt an den Pin (immer sicher). Der frühere
        # Natural-Fallback erzeugte hier Fremd-Netz-Kontakt (USB_DM/USB_DP:
        # zwei Nachbar-Stubs kollinear übereinander → Kurzschluss).
        return None

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
            # Konfliktfeste Stub-Länge/Richtung: die konventionelle Richtung
            # (GND unten, Versorgung oben) zuerst in drei Längen, dann die
            # Gegenrichtung — zwei Power-Stubs GESTAPELTER Bauteile lagen sonst
            # kollinear übereinander (der VCC/GND-Kurzschluss C3:1↔R1:2).
            want = "down" if sym_type == "ground" else "up"
            opposite = "up" if want == "down" else "down"
            chosen: tuple[str, float] | None = None
            for d in (want, opposite):
                vx, vy = _DIR_VEC[d]
                for ln in (LABEL_STUB_LEN, LABEL_STUB_LEN / 2,
                           LABEL_STUB_LEN * 1.5, 0.635):
                    tx, ty = _r2(ax + vx * ln), _r2(ay + vy * ln)
                    if not _seg_conflicts(ax, ay, tx, ty, net_name):
                        chosen = (d, ln)
                        break
                if chosen:
                    break
            if chosen is None:
                # 0.635 mm erreicht kein fremdes Element auf dem 1.27er-Raster
                chosen = (want, 0.635)
            anchor = _place_power_symbol(
                s, net_name, ax, ay, l_uid, w_uid, p_uid,
                lib_id, sym_type,
                direction=chosen[0], stub_len=chosen[1],
            )
        else:
            # Global-Label-Zweig (Rails ohne KiCad-Symbol, z. B. VIN): Richtung
            # und Länge GENAUSO verhandeln wie beim Symbol — der ungeprüfte
            # Stub endete sonst auf einem Fremd-Pin (VIN-Stub auf U1:5/FB).
            df = _stub_dir_free(ax, ay, ax - _DIR_VEC[direction][0],
                                ay - _DIR_VEC[direction][1], net_name,
                                LABEL_STUB_LEN)
            if df is None:
                df = (direction, 0.635)
            anchor = _place_label_with_stub(
                s, net_name, ax, ay, l_uid, w_uid,
                is_global=True, direction=df[0], stub_len=df[1],
            )
        _register_seg(ax, ay, anchor[0], anchor[1], net_name)
        return anchor

    def _label_with_stub_reg(net_name: str, ax: float, ay: float,
                             lbl_uid: str, w_uid: str, *,
                             is_hierarchical: bool = False,
                             direction: str = "right",
                             stub_len: float | None = None) -> tuple[float, float]:
        anchor = _place_label_with_stub(
            s, net_name, ax, ay, lbl_uid, w_uid,
            is_hierarchical=is_hierarchical, direction=direction,
            stub_len=stub_len,
        )
        _register_seg(ax, ay, anchor[0], anchor[1], net_name)
        return anchor

    def _label_pin_safe(net_name: str, ax: float, ay: float, sx: float,
                        sy: float, lbl_uid: str, w_uid: str, *,
                        is_hierarchical: bool = False) -> None:
        """Label mit Stub in elektrisch freier Richtung; findet sich KEINE,
        Label ohne Stub direkt am Pin (verbindet am Pin-Ende, kann nie ein
        fremdes Netz berühren)."""
        df = _stub_dir_free(ax, ay, sx, sy, net_name, LABEL_STUB_LEN)
        if df is None:
            # Ein Label OHNE Draht verbindet im Netzlister nicht — als
            # allerletzter Ausweg der kürzestmögliche Stub in der natürlichen
            # Richtung (0.635 mm erreicht kein fremdes 1.27er-Raster-Element).
            df = (_stub_direction(ax, ay, sx, sy), 0.635)
        d, ln = df
        _label_with_stub_reg(net_name, ax, ay, lbl_uid, w_uid,
                             is_hierarchical=is_hierarchical,
                             direction=d, stub_len=ln)

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

    # Pin-Stubs: jeder A*-verdrahtete Pin bekommt eine kurze axiale Leitung nach
    # außen; der A*-Draht startet erst an DEREN Spitze (außerhalb des Körpers).
    # ``_stub_done`` dedupliziert pro Pin-Position über alle MST-Kanten/Netze.
    _stub_done: set[tuple[float, float]] = set()

    def _emit_pin_stub(ax: float, ay: float, sx: float, sy: float,
                       net: str) -> tuple[float, float] | None:
        """Kurzen Pin-Stub emittieren (einmal je Pin) und dessen Spitze liefern.
        Von der Spitze aus routet A* — nie ein Bus quer durchs eigene Bauteil.
        Kollidiert der Stub elektrisch mit einem fremden Netz → ``None``
        (die Kante fällt dann auf ein Label zurück, kein Kurzschluss)."""
        nonlocal wire_count
        tip = _pin_stub_point(ax, ay, sx, sy)
        import os as _os
        if (ax, ay) in _stub_done:
            if _os.environ.get("STUBDBG"): print(f"STUBDBG stub-dedupe {net} ({ax},{ay})")
            return tip
        if _seg_conflicts(ax, ay, tip[0], tip[1], net):
            if _os.environ.get("STUBDBG"): print(f"STUBDBG stub-CONFLICT {net} ({ax},{ay})->{tip}")
            return None
        _stub_done.add((ax, ay))
        w_uid = uid(f"{project_name}_pinstub_{ax}_{ay}_{wire_count}")
        wire_count += 1
        _wire_reg(ax, ay, tip[0], tip[1], w_uid, net)
        return tip

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
        user_to_real = _map_user_to_real_pins(part, real_pins, real_sym) if real_pins else {}

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
            _pin_pts[(_r2(abs_x), _r2(abs_y))] = net_name
            all_pin_cells.add((round(abs_x / ROUTE_GRID), round(abs_y / ROUTE_GRID)))

    # Route each net — Power-Netze ZUERST: ihre Symbole+Stubs sind fixe
    # Geometrie, die Signal-Routen weichen ihnen dann per Registry aus
    # (umgekehrt könnte ein GND-Stub blind auf eine Signal-Route fallen).
    for net in sorted(nets, key=lambda n: 0 if n.get("type") == "power" else 1):
        net_name = net["name"]
        net_type = net.get("type", "signal")
        pins = net_pins.get(net_name, [])

        if not pins:
            continue

        # Power-Netze: ein Power-Symbol (bzw. Global-Label) an JEDEM Pin —
        # exakt wie die Profi-Referenzen (GND-Symbol unter jedem GND-Pin,
        # VCC-Pfeil über jedem Versorgungs-Pin). KiCads Netzliste vereint sie
        # global über den Symbol-/Label-Namen. Die frühere Misch-Strategie
        # („ein Label + MST-Drähte") hinterließ bei jedem fehlgeschlagenen
        # Routing-Ast eine INSEL ohne Namen → GND zerfiel in Teil-Netze
        # (Netzlisten-Roundtrip deckte es auf). Nebeneffekt: die vielen langen
        # Power-Drähte quer übers Blatt entfallen komplett.
        if net_type == "power":
            for ax, ay, ref, pnum, sx, sy in pins:
                d = _dir(ax, ay, sx, sy)
                _place_power_or_label(net_name, ax, ay, d)
            continue

        is_hier = net_name in _hier_names

        if len(pins) == 1:
            ax, ay, ref, pnum, sx, sy = pins[0]
            lbl_uid = uid(f"{project_name}_lbl_{net_name}_{ref}_{pnum}_{label_count}")
            w_uid = uid(f"{project_name}_stub_{net_name}_{ref}_{pnum}_{wire_count}")
            label_count += 1
            wire_count += 1
            _label_pin_safe(net_name, ax, ay, sx, sy, lbl_uid, w_uid,
                            is_hierarchical=is_hier)
            continue

        # Feedback paths: always labels with stubs
        if _is_feedback_net(net, nets, parts):
            for ax, ay, ref, pnum, sx, sy in pins:
                lbl_uid = uid(f"{project_name}_lbl_{net_name}_{ref}_{pnum}_{label_count}")
                w_uid = uid(f"{project_name}_stub_{net_name}_{ref}_{pnum}_{wire_count}")
                label_count += 1
                wire_count += 1
                _label_pin_safe(net_name, ax, ay, sx, sy, lbl_uid, w_uid,
                                is_hierarchical=is_hier)
            continue

        # Check max distance for wiring decision
        max_dist = 0
        for i, (ax, ay, *_rest) in enumerate(pins):
            for bx, by, *_rest2 in pins[i + 1:]:
                d = math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)
                max_dist = max(max_dist, d)

        # Template-based wiring decision (Elektor rules)
        use_wires = _should_wire_net(net, parts, max_dist, len(pins))

        # Verdrahtung (beide Modi vereinheitlicht): MST-Kanten von Stub-Spitze
        # zu Stub-Spitze. Jede Kandidaten-Route wird gegen die Registry geprüft
        # — kollidiert sie elektrisch mit einem FREMDEN Netz, wird sie
        # verworfen (kein Kurzschluss; die Selbstheilung unten übernimmt).
        # Im Label-Modus (use_wires=False) werden nur kurze Kanten verdrahtet.
        SHORT_EDGE_THRESHOLD = WIRE_MAX_LENGTH / 2
        edges_ok: list[tuple[int, int]] = []
        if len(pins) >= 2:
            mst_edges = _build_mst_edges(pins)
            for pi, pj in mst_edges:
                pin_i = (pins[pi][0], pins[pi][1])
                pin_j = (pins[pj][0], pins[pj][1])
                if not use_wires:
                    edge_dist = math.hypot(pin_i[0] - pin_j[0], pin_i[1] - pin_j[1])
                    if edge_dist > SHORT_EDGE_THRESHOLD:
                        continue  # zu lang — diese Pins bekommen Labels
                start = _pin_stub_point(pin_i[0], pin_i[1], pins[pi][4], pins[pi][5])
                end = _pin_stub_point(pin_j[0], pin_j[1], pins[pj][4], pins[pj][5])
                path = _astar_route(start, end, obstacles, set())
                if not (path and len(path) >= 2) \
                        or _path_conflicts(path, net_name):
                    # L-Bend gegen das VOLLE Hindernis-Set — das frühere
                    # halbe-Clearance-Set ließ den Fallback quer durch kleine
                    # Bauteile winkeln; scheitert er jetzt, heilt das Label.
                    path = _try_lbend(start, end, obstacles, [obstacles])
                    if path and _path_conflicts(path, net_name):
                        path = None
                if not path or len(path) < 2:
                    continue
                tip_i = _emit_pin_stub(pin_i[0], pin_i[1],
                                       pins[pi][4], pins[pi][5], net_name)
                tip_j = _emit_pin_stub(pin_j[0], pin_j[1],
                                       pins[pj][4], pins[pj][5], net_name)
                if tip_i is None or tip_j is None:
                    import os as _os
                    if _os.environ.get("STUBDBG"): print(f"STUBDBG edge-drop {net_name} {pin_i} {pin_j} tips={tip_i},{tip_j}")
                    continue  # Stub kollidiert mit Fremd-Netz → Label-Heilung
                import os as _os
                if _os.environ.get("STUBDBG"): print(f"STUBDBG edge-OK {net_name} {pin_i}->{pin_j} start={start} end={end}")
                for j in range(len(path) - 1):
                    w_uid = uid(f"{project_name}_wire_{net_name}_{wire_count}")
                    wire_count += 1
                    _wire_reg(path[j][0], path[j][1],
                              path[j + 1][0], path[j + 1][1], w_uid, net_name)
                edges_ok.append((pi, pj))
                # Block routed wire waypoints (full rasterization
                # blocks too many cells in dense areas — see benchmark)
                for wp in path:
                    obstacles.add((round(wp[0] / ROUTE_GRID),
                                   round(wp[1] / ROUTE_GRID)))

        # Selbstheilung: das Soll-Netz muss EIN zusammenhängendes Gebilde
        # ergeben. Union-Find über die erfolgreich verdrahteten Kanten; zerfällt
        # das Netz in mehrere Komponenten, bekommt JEDE ein gleichnamiges Label
        # (KiCad vereint gleichnamige lokale Labels blattweit) — verdrahtete
        # Komponenten direkt am Pin (dort endet der Stub), einzelne unverdrahtete
        # Pins per Label+Stub. Vorher wurde nur EIN Label gesetzt und Rest-Inseln
        # blieben namenlos → „Netz zerfällt in N Teile" im Netzlisten-Roundtrip.
        parent = list(range(len(pins)))

        def _find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for a, b in edges_ok:
            parent[_find(a)] = _find(b)
        comps: dict[int, list[int]] = {}
        for i in range(len(pins)):
            comps.setdefault(_find(i), []).append(i)

        if len(comps) > 1:
            wired_set = {i for e in edges_ok for i in e}
            for comp in comps.values():
                idx = comp[0]
                ax, ay, ref, pnum, sx, sy = pins[idx]
                lbl_uid = uid(f"{project_name}_lbl_{net_name}_{ref}_{pnum}_{label_count}")
                label_count += 1
                if idx in wired_set:
                    # Label an der PIN-STUB-SPITZE (Draht-Endpunkt → verbunden),
                    # nicht am Pin selbst: der Pin liegt IN der Pin-Zone des
                    # Bauteils (Metrik: „Label auf/an Bauteil"), und der Text
                    # zeigte in die Schaltung statt nach außen.
                    d_out = _stub_direction(ax, ay, sx, sy)
                    tx, ty = _pin_stub_point(ax, ay, sx, sy)
                    if is_hier:
                        s.hierarchical_label(net_name, tx, ty, lbl_uid,
                                             angle=_LABEL_ANGLE[d_out])
                    else:
                        s.net_label(net_name, tx, ty, lbl_uid,
                                    angle=_LABEL_ANGLE[d_out])
                else:
                    w_uid = uid(f"{project_name}_stub_{net_name}_{ref}_{pnum}_{wire_count}")
                    wire_count += 1
                    _label_pin_safe(net_name, ax, ay, sx, sy, lbl_uid, w_uid,
                                    is_hierarchical=is_hier)

    # Junction-Punkte: an jedem Punkt, an dem ≥3 Draht-Enden zusammentreffen,
    # und an jedem T-Abzweig (Draht-Ende auf dem INNEREN eines anderen Drahts)
    # — Nutzer-Regel „wenn aus einer geraden Leitung eine Leitung abzweigt,
    # muss ein Punkt das kennzeichnen"; für KiCads Konnektivität am T Pflicht.
    # NETZ-BEWUSST: ein Punkt bekommt nur dann einen Junction-Punkt, wenn alle
    # dort beteiligten Segmente zum SELBEN Netz gehören — ein Punkt zwischen
    # zwei Netzen wäre ein Kurzschluss, den ein Junction erst „festnageln"
    # würde (Defense-in-Depth zur Registry).
    end_count: dict[tuple[float, float], int] = {}
    pt_nets: dict[tuple[float, float], set[str]] = {}
    for x1, y1, x2, y2, n1 in _segs:
        for p in ((_r2(x1), _r2(y1)), (_r2(x2), _r2(y2))):
            end_count[p] = end_count.get(p, 0) + 1
            pt_nets.setdefault(p, set()).add(n1)
    junctions: set[tuple[float, float]] = {
        p for p, c in end_count.items()
        if c >= 3 and len(pt_nets.get(p, set())) == 1}
    for p in end_count:
        if p in junctions:
            continue
        for fx1, fy1, fx2, fy2, fnet in _segs:
            if p in ((_r2(fx1), _r2(fy1)), (_r2(fx2), _r2(fy2))):
                continue
            if _on_seg(p[0], p[1], fx1, fy1, fx2, fy2) \
                    and pt_nets.get(p) == {fnet}:
                junctions.add(p)
                break
    for k, (jx, jy) in enumerate(sorted(junctions)):
        s.junction(jx, jy, uid(f"{project_name}_junc_{k}"))

    logger.info("Emitted %d wires, %d labels, %d junctions",
                wire_count, label_count, len(junctions))
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
        # Platzhalter-Symbole durchlaufen DENSELBEN Transform wie echte
        # (Mirror vor Rotation) — vorher wurden ihre rohen Lib-Koordinaten
        # unrotiert zurückgegeben (gedrehter Platzhalter = alle Pins falsch).
        base = _pins_from_placeholder(part)
        rotation = int(part.get("_rotation", 0))
        mirror = part.get("_mirror") or None
        out: dict[str, tuple[float, float]] = {}
        for num, (lx, ly) in base.items():
            if mirror == "y":
                lx = -lx
            elif mirror == "x":
                ly = -ly
            out[num] = _rotate_point(lx, ly, rotation)
        return out

    # _pins_from_real_symbol already applies the lib_symbols Y-flip
    # (the lib frame is Y-up, schematic frame is Y-down). So ``(x, y)``
    # here is the post-Y-flip local position the schematic semantics
    # expect — ready for mirror + rotation.
    real_pins = _pins_from_real_symbol(raw)
    user_pins = part.get("pins", [])
    rotation = int(part.get("_rotation", 0))
    mirror = part.get("_mirror") or None  # "x" / "y" / None

    result: dict[str, tuple[float, float]] = {}

    # Eine Quelle für user→real: dieselbe Zuordnung, die Emission
    # (pin_instance) und Netzlisten-Vergleich (build_pin_aliases) nutzen —
    # Namens-Match vor Nummern-Match, gestapelte Namen konfliktfrei verteilt.
    u2r = _map_user_to_real_pins(part, real_pins, raw)

    for pin in user_pins:
        user_num = str(pin["num"])
        real_num = u2r.get(user_num)
        # ungemappte Pins bleiben ehrlich OHNE Position (→ „offen" im
        # Roundtrip) statt auf eine kollidierende Nummer zurückzufallen
        pos = real_pins.get(real_num) if real_num is not None else None

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
def _pin_names_from_real_symbol(raw_sexpr: str) -> dict[str, tuple[str, ...]]:
    """``{PIN-NAME (upper): (Pin-Nummern…)}`` eines Lib-Symbols.

    Mehrfach vergebene Namen bleiben als GRUPPE erhalten (gestapelte GND-Pins:
    DRV8871 hat GND auf 1/7/9). Sie ganz zu verwerfen ließ Kit-Pins mit diesem
    Namen auf den Nummern-Match zurückfallen — der bei abweichender Kit-
    Nummerierung den FALSCHEN Funktions-Pin traf (Kit-GND(8) landete auf dem
    realen OUT2(8) → GND/OUT2-Kurzschluss im Netzlisten-Roundtrip)."""
    tree = parse_sexpr(raw_sexpr)
    seen: dict[str, list[str]] = {}

    def _aliases(raw_name: str) -> list[str]:
        # KiCad-Dekoration entfernen (Aktiv-Low ``~{RST}`` → RST) und
        # Mehrfach-Funktionen (``TXD0/MODE0``) in Einzel-Aliase zerlegen —
        # Kits schreiben den nackten Funktionsnamen.
        clean = raw_name.upper().replace("~{", "").replace("}", "").replace("~", "")
        parts_ = [p.strip() for p in clean.split("/") if p.strip()]
        out = [clean] if clean else []
        out += [p for p in parts_ if p != clean]
        return out

    def _walk(node: list) -> None:
        if not isinstance(node, list) or not node:
            return
        if node[0] == "pin":
            nm = find_node(node, "name")
            num = find_node(node, "number")
            if nm and num and len(nm) >= 2 and len(num) >= 2:
                for key in _aliases(str(nm[1])):
                    if key != "~":
                        lst = seen.setdefault(key, [])
                        if str(num[1]) not in lst:
                            lst.append(str(num[1]))
        for child in node:
            if isinstance(child, list):
                _walk(child)

    _walk(tree)
    return {k: tuple(v) for k, v in seen.items()}


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


def _map_user_to_real_pins(part: dict, real_pins: dict[str, tuple[float, float]],
                           raw_sexpr: str | None = None) -> dict[str, str]:
    """Map user pin numbers to real KiCad pin numbers.

    Returns: {user_pin_num: real_pin_num}. Namens-Match ZUERST — der Pin-NAME
    ist die Design-Absicht des Kits, die Nummer ist paket-abhängig (motor_
    driver: Kit-Pin 7 „IN1" ≠ realer Pin 7 = GND). Bei gestapelten Namen
    (GND auf 1/7/9) bevorzugt die eigene Nummer, sonst die erste unbelegte.
    Danach Nummern-Match, Name-als-Nummer, Groß/Klein-Toleranz.
    """
    name_groups: dict[str, tuple[str, ...]] = {}
    if raw_sexpr:
        name_groups = _pin_names_from_real_symbol(raw_sexpr)
    mapping: dict[str, str] = {}
    used: set[str] = set()

    _NAME_SYNONYMS = {
        # Kits schreiben Datenblatt-Worte, Symbole Kurzzeichen (TNY268:
        # Kit „DRAIN/SOURCE" vs. Symbol „D"/„S")
        "DRAIN": "D", "SOURCE": "S", "GATE": "G",
        "COLLECTOR": "C", "EMITTER": "E", "BASE": "B",
        "ANODE": "A", "CATHODE": "K",
    }

    def _name_keys(user_name: str) -> list[str]:
        """Suchschlüssel fürs Namens-Matching: exakt, dann Aktiv-Low-Präfix
        toleriert (Kit „NRST" ↔ Symbol „~{RST}" → Alias RST), dann
        Datenblatt-Synonym (DRAIN → D)."""
        u = user_name.upper()
        keys = [u]
        if len(u) > 2 and u[0] in ("N", "/", "#"):
            keys.append(u.lstrip("/#") if u[0] in ("/", "#") else u[1:])
        syn = _NAME_SYNONYMS.get(u)
        if syn:
            keys.append(syn)
        return keys

    # Pass 1: Namens-Matches (binden zuerst, damit Nummern-Kollisionen sie
    # nicht wegschnappen)
    for pin in part.get("pins", []):
        user_num = str(pin["num"])
        user_name = str(pin.get("name", "") or "")
        cands: tuple[str, ...] = ()
        for key in (_name_keys(user_name) if user_name else []):
            cands = name_groups.get(key, ())
            if cands:
                break
        if cands:
            if user_num in cands and user_num not in used:
                pick = user_num
            else:
                pick = next((c for c in cands if c not in used), cands[0])
            mapping[user_num] = pick
            used.add(pick)

    # Pass 2: Rest über Nummer / Name-als-Nummer — nur auf UNBELEGTE reale
    # Pins. Ist die eigene Nummer schon von einem Namens-Match beansprucht
    # (Kit-Pin 17 „NRST" vs. reales TXD0=17), bleibt der Pin lieber
    # ungemappt (→ ehrlich „offen" im Roundtrip) als falsch verbunden.
    for pin in part.get("pins", []):
        user_num = str(pin["num"])
        if user_num in mapping:
            continue
        user_name = str(pin.get("name", "") or "")
        if user_num in real_pins and user_num not in used:
            mapping[user_num] = user_num
            used.add(user_num)
        elif user_name in real_pins and user_name not in used:
            mapping[user_num] = user_name
            used.add(user_name)
        else:
            name_upper = user_name.upper()
            for rp_num in real_pins:
                if rp_num.upper() == name_upper and rp_num not in used:
                    mapping[user_num] = rp_num
                    used.add(rp_num)
                    break
    return mapping


def _pins_from_placeholder(part: dict) -> dict[str, tuple[float, float]]:
    """Pin-Positionen der Platzhalter-Box — im BLATT-Rahmen (Y-down).

    Muss exakt zu ``builder._emit_placeholder_symbol`` passen, das die Pins in
    LIB-Koordinaten (Y-up) bei ``y_lib = (n-1)·FONT − i·SPACING`` zeichnet.
    Der Y-Flip hier (wie in ``_pins_from_real_symbol``) fehlte früher — alle
    Drähte/Stubs eines Platzhalter-ICs dockten an den vertikal GESPIEGELTEN,
    also falschen Pins an (Pin 1 am Platz von Pin n): die Kurzschluss-Cluster
    der Netzlisten-Roundtrips bei T1/MP1584/MCU_MAC."""
    pins_list = part.get("pins", [])
    n = len(pins_list)
    result: dict[str, tuple[float, float]] = {}
    for i, pin in enumerate(pins_list):
        y = round(i * PIN_SPACING - (n - 1) * FONT_SIZE, 4)
        x = round(-(SYM_HALF_WIDTH + PIN_LENGTH), 4)
        result[str(pin["num"])] = (x, y)
    return result
