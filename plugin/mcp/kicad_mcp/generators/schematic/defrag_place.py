# SPDX-License-Identifier: GPL-3.0-or-later
"""
Connectivity-driven "defragmentation" placement.

Algorithm:
  1. IC with most connections → sheet center
  2. All small parts (R/C/L/D/SW/Y) directly connected → place tight around IC
  3. Next component with most remaining connections → place nearby
  4. Its small connected parts → place tight around it
  5. Repeat until all placed
  6. Connectors last — at the edge nearest their connections

Like disk defrag: densest cluster first, then expand outward.

Caller: place.py (replaces phases 2-9, keeps template/classify/FD/overlap)
"""

from collections import defaultdict
import logging

from ..common.bbox import _get_symbol_height, _get_symbol_width
from ..common.bus_detect import find_bus_groups
from ..common.chain_detect import find_series_chains
from ..common.classify import _is_bypass_cap
from ..common.connectivity import _build_connection_graph
from ..common.constants import (
    GRID,
    IC_X,
    IC_Y,
    INLINE_GAP,
    MARGIN,
    SHEET_H,
    SHEET_W,
    VERTICAL_GAP,
)
from ..common.geometry import _snap
from ..common.pin_analysis import find_pin_side, get_pin_sides
from ..symbol_cache import get_real_symbol
from ..symbol_lib import resolve_lib_id

logger = logging.getLogger(__name__)

# 4.4: Configurable placement cost weights (env-override)
import os as _os

COST_WIRE_LENGTH  = float(_os.getenv("KICAD_COST_WIRE",    "1.0"))
COST_OVERLAP      = float(_os.getenv("KICAD_COST_OVERLAP", "50.0"))
COST_PARTNER_DIST = float(_os.getenv("KICAD_COST_PARTNER", "2.0"))

# 5.2: Net-based placement direction cost
NET_DIR_COST = float(_os.getenv("KICAD_COST_NET_DIR", "10.0"))
NET_PRIORITY = {"power": 3, "clock": 2, "signal": 1}  # routing order


def _compute_net_roles(
    parts: list[dict], nets: list[dict],
) -> dict[str, float]:
    """Compute a source/sink score for each ref based on actual net connections.

    Returns: {ref: score} where score < 0 means "closer to input sources"
    (should be left) and score > 0 means "closer to output sinks" (should be
    right). Score 0 means neutral.

    Algorithm: propagate roles from known connectors through shared nets.
    Input connectors are sources (-1), output connectors are sinks (+1).
    Parts sharing nets with sources inherit a fraction of that role.
    """
    _ref_to_part = {p["ref"]: p for p in parts}

    # Seed: connectors get fixed roles
    role: dict[str, float] = {}
    for p in parts:
        g = p.get("_group", "")
        if g == "connector_in":
            role[p["ref"]] = -1.0
        elif g == "connector_out":
            role[p["ref"]] = 1.0
        elif g == "power_reg":
            role[p["ref"]] = 0.0  # neutral horizontally

    # Build ref→nets index
    ref_nets: dict[str, list[dict]] = defaultdict(list)
    for net in nets:
        if net.get("type") == "power":
            continue  # power nets don't carry signal direction
        for conn in net.get("connections", []):
            ref = conn.split(":")[0] if ":" in conn else ""
            if ref:
                ref_nets[ref].append(net)

    # Propagate: for each unseeded part, average the roles of its net partners
    # Two passes are enough for typical chain depths
    for _pass in range(2):
        for p in parts:
            ref = p["ref"]
            if ref in role:
                continue
            partner_roles = []
            for net in ref_nets.get(ref, []):
                for conn in net.get("connections", []):
                    other_ref = conn.split(":")[0] if ":" in conn else ""
                    if other_ref and other_ref != ref and other_ref in role:
                        partner_roles.append(role[other_ref])
            if partner_roles:
                role[ref] = sum(partner_roles) / len(partner_roles)

    return role


    # _get_pin_sides and find_pin_side are now in common/pin_analysis.py


# ---------------------------------------------------------------------------
# Incremental Place+Score  (like a human: place one part, score, next)
# ---------------------------------------------------------------------------

def _build_ref_to_nets(nets: list[dict]) -> dict[str, list[tuple[str, list[tuple[str, str]]]]]:
    """Pre-compute ref → [(my_pin, [(other_ref, other_pin), ...])] index.

    For each ref, collects the nets it participates in and the other
    endpoints on those nets.  Avoids scanning all nets per cost call.
    """
    # net_index: ref → list of (my_pin, other_endpoints)
    idx: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {}
    for net in nets:
        conns = net.get("connections", [])
        parsed = []
        for conn in conns:
            if ":" in conn:
                parsed.append(conn.split(":", 1))
        for cref, cpname in parsed:
            others = [(r, p) for r, p in parsed if r != cref]
            idx.setdefault(cref, []).append((cpname, others))
    return idx


def _placement_cost(
    ref: str,
    x: float, y: float, rot: int,
    ref_to_part: dict[str, dict],
    ref_net_index: dict[str, list[tuple[str, list[tuple[str, str]]]]],
    placed_refs: set[str],
    pin_cache: dict[str, dict[str, tuple[float, float]]],
    net_roles: dict[str, float] | None = None,
) -> float:
    """Cost = total Manhattan distance from this part's pins to connected placed pins.

    Lower is better.  Only considers nets where at least one other endpoint
    is already placed.
    """
    from .route import _extract_pin_positions

    part = ref_to_part[ref]
    lib_id = resolve_lib_id(part)

    # Temporarily set rotation to compute local pin positions
    orig_rot = part.get("_rotation", 0)
    part["_rotation"] = rot
    local_pins = _extract_pin_positions(lib_id, part)
    part["_rotation"] = orig_rot

    # Build name→num lookup once
    name_to_num = {}
    for pp in part.get("pins", []):
        name_to_num[pp.get("name", "")] = str(pp.get("num", ""))

    cost = 0.0
    partner_count = 0
    for my_pname, others in ref_net_index.get(ref, []):
        # Resolve my pin position
        my_pos = local_pins.get(my_pname) or local_pins.get(name_to_num.get(my_pname, ""))
        if my_pos is None:
            continue
        abs_x = x + my_pos[0]
        abs_y = y + my_pos[1]

        for other_ref, other_pname in others:
            if other_ref not in placed_refs:
                continue
            other_part = ref_to_part.get(other_ref)
            if not other_part or "_place_x" not in other_part:
                continue
            other_local = pin_cache.get(other_ref)
            if other_local is None:
                other_local = _extract_pin_positions(resolve_lib_id(other_part), other_part)
                pin_cache[other_ref] = other_local
            opos = other_local.get(other_pname)
            if opos is None:
                for pp in other_part.get("pins", []):
                    if pp.get("name") == other_pname:
                        opos = other_local.get(str(pp.get("num", "")))
                        break
            if opos is None:
                continue
            ox = other_part["_place_x"] + opos[0]
            oy = other_part["_place_y"] + opos[1]
            # 4.4: configurable cost weights
            cost += COST_WIRE_LENGTH * (abs(abs_x - ox) + abs(abs_y - oy))
            partner_count += 1

    # 4.4: Overlap penalty — heavily penalise positions inside existing parts
    w_self, h_self = _get_symbol_width(part), _get_symbol_height(part)
    for other_ref in placed_refs:
        if other_ref == ref:
            continue
        op = ref_to_part.get(other_ref)
        if not op or "_place_x" not in op:
            continue
        wo, ho = _get_symbol_width(op), _get_symbol_height(op)
        dx = abs(x - op["_place_x"])
        dy = abs(y - op["_place_y"])
        min_dx = (w_self + wo) / 2 + 2.0
        min_dy = (h_self + ho) / 2 + 2.0
        if dx < min_dx and dy < min_dy:
            cost += COST_OVERLAP

    # 4.4: Bonus for being near partners (more partners = better)
    if partner_count > 1:
        cost -= COST_PARTNER_DIST * partner_count

    # 5.2: Net-direction penalty — based on Source/Sink analysis
    # Parts connected to input sources should be left (low x),
    # parts connected to output sinks should be right (high x).
    if net_roles is not None:
        role = net_roles.get(ref, 0.0)
        mid_x = SHEET_W / 2
        if role < -0.2 and x > mid_x:
            # Source-side part on right half → penalty scaled by role strength
            cost += NET_DIR_COST * abs(role)
        elif role > 0.2 and x < mid_x:
            # Sink-side part on left half → penalty scaled by role strength
            cost += NET_DIR_COST * abs(role)
    else:
        # Fallback: group-based penalty (legacy)
        group = part.get("_group", "")
        if group == "connector_in" and x > SHEET_W / 2 or group == "connector_out" and x < SHEET_W / 2:
            cost += NET_DIR_COST

    # Power regulation: lower half preference
    if part.get("_group") == "power_reg" and y < SHEET_H / 2:
        cost += NET_DIR_COST * 0.5

    return cost


def incremental_place_and_score(
    parts: list[dict],
    nets: list[dict],
    placed_refs: set[str],
    inline_gap: float = INLINE_GAP,
    vertical_gap: float = VERTICAL_GAP,
) -> set[str]:
    """Place components one-by-one, each at the position with lowest wire cost.

    Like a human: pick part → find best spot near connections → rotate for
    shortest wires → lock → next part.

    Returns set of newly placed refs.
    """
    from .route import _extract_pin_positions

    ref_to_part = {p["ref"]: p for p in parts}
    newly_placed: set[str] = set()
    pin_cache: dict[str, dict[str, tuple[float, float]]] = {}

    connections, conn_count = _build_connection_graph(nets)
    ref_net_index = _build_ref_to_nets(nets)

    # 5.2: Precompute source/sink roles for net-based direction penalty
    net_roles = _compute_net_roles(parts, nets)

    def _is_placed(ref: str) -> bool:
        return ref in placed_refs or ref in newly_placed

    def _do_place(ref: str, x: float, y: float, rot: int | None = None) -> None:
        p = ref_to_part.get(ref)
        if p and not _is_placed(ref):
            p["_place_x"] = _snap(x)
            p["_place_y"] = _snap(y)
            if rot is not None:
                p["_rotation"] = rot
            newly_placed.add(ref)
            # Update pin cache
            lib_id = resolve_lib_id(p)
            pin_cache[ref] = _extract_pin_positions(lib_id, p)

    def _best_rotation(ref: str, x: float, y: float) -> int:
        """Try 4 rotations, return the one with lowest placement cost.

        For 2-pin parts, computes pin positions once at rot=0 and applies
        rotation math directly to avoid 4× _extract_pin_positions calls.
        """
        part = ref_to_part[ref]
        pins = part.get("pins", [])
        if len(pins) != 2:
            return int(part.get("_rotation", 0))

        current = placed_refs | newly_placed

        # Get pin positions at rotation 0
        lib_id = resolve_lib_id(part)
        orig_rot = part.get("_rotation", 0)
        part["_rotation"] = 0
        base_pins = _extract_pin_positions(lib_id, part)
        part["_rotation"] = orig_rot

        if not base_pins:
            return int(orig_rot)

        # Build name→num lookup
        name_to_num = {}
        for pp in pins:
            name_to_num[pp.get("name", "")] = str(pp.get("num", ""))

        # Collect placed endpoints for this ref's nets
        targets = []  # (my_pin_key, other_abs_x, other_abs_y)
        for my_pname, others in ref_net_index.get(ref, []):
            pkey = my_pname if my_pname in base_pins else name_to_num.get(my_pname, "")
            if pkey not in base_pins:
                continue
            for other_ref, other_pname in others:
                if other_ref not in current:
                    continue
                other_part = ref_to_part.get(other_ref)
                if not other_part or "_place_x" not in other_part:
                    continue
                other_local = pin_cache.get(other_ref)
                if other_local is None:
                    other_local = _extract_pin_positions(resolve_lib_id(other_part), other_part)
                    pin_cache[other_ref] = other_local
                opos = other_local.get(other_pname)
                if opos is None:
                    for pp in other_part.get("pins", []):
                        if pp.get("name") == other_pname:
                            opos = other_local.get(str(pp.get("num", "")))
                            break
                if opos is None:
                    continue
                targets.append((pkey, other_part["_place_x"] + opos[0],
                                other_part["_place_y"] + opos[1]))

        if not targets:
            return int(orig_rot)

        # Rotation math: (bx, by) at rot=0 → rotated positions
        import math
        best_rot = int(orig_rot)
        best_cost = float("inf")
        for rot in (0, 90, 180, 270):
            rad = math.radians(-rot)  # KiCad rotation is clockwise
            cos_r, sin_r = round(math.cos(rad)), round(math.sin(rad))
            cost = 0.0
            for pkey, ox, oy in targets:
                bx, by = base_pins[pkey]
                rx = bx * cos_r - by * sin_r
                ry = bx * sin_r + by * cos_r
                cost += abs(x + rx - ox) + abs(y + ry - oy)
            if cost < best_cost:
                best_cost = cost
                best_rot = rot

        return best_rot

    def _candidate_positions(ref: str) -> list[tuple[float, float]]:
        """Generate 2-4 candidate positions for a component."""
        candidates = []
        current = placed_refs | newly_placed
        neighbor_positions = []
        for _, other_ref, _ in connections.get(ref, []):
            other = ref_to_part.get(other_ref)
            if other and "_place_x" in other and other_ref in current:
                neighbor_positions.append((other["_place_x"], other["_place_y"]))

        if not neighbor_positions:
            return [(_snap(MARGIN + 10), _snap(IC_Y))]

        # Candidate 1: Centroid of connected placed parts
        cx = sum(x for x, _ in neighbor_positions) / len(neighbor_positions)
        cy = sum(y for _, y in neighbor_positions) / len(neighbor_positions)

        # Candidate 2-5: offset from centroid in 4 directions
        candidates.append((_snap(cx + inline_gap), _snap(cy)))
        candidates.append((_snap(cx - inline_gap), _snap(cy)))
        candidates.append((_snap(cx), _snap(cy + inline_gap)))
        candidates.append((_snap(cx), _snap(cy - inline_gap)))

        # Pin-side: if connected to an IC, use pin-side position
        for _, other_ref, _ in connections.get(ref, []):
            other = ref_to_part.get(other_ref)
            if not other or other.get("_group") != "main_ic" or "_place_x" not in other:
                continue
            if other_ref not in current:
                continue
            pin_pos = get_pin_sides(other, resolve_lib_id, get_real_symbol)
            side = find_pin_side(other_ref, ref, other, connections, nets, pin_pos)
            ox, oy = other["_place_x"], other["_place_y"]
            pw = _get_symbol_width(other)
            ph = _get_symbol_height(other)
            if side == "right":
                candidates.append((_snap(ox + pw/2 + inline_gap), _snap(oy)))
            elif side == "left":
                candidates.append((_snap(ox - pw/2 - inline_gap), _snap(oy)))
            elif side == "top":
                candidates.append((_snap(ox), _snap(oy - ph/2 - inline_gap)))
            else:
                candidates.append((_snap(ox), _snap(oy + ph/2 + inline_gap)))
            break

        return candidates

    def _pick_best(ref: str, candidates: list[tuple[float, float]]) -> tuple[float, float, int]:
        """Evaluate candidates, return (x, y, rotation) with lowest cost."""
        current = placed_refs | newly_placed
        best = candidates[0] + (int(ref_to_part[ref].get("_rotation", 0)),)
        best_cost = float("inf")
        for x, y in candidates:
            rot = _best_rotation(ref, x, y)
            cost = _placement_cost(ref, x, y, rot, ref_to_part, ref_net_index, current, pin_cache, net_roles)
            if cost < best_cost:
                best_cost = cost
                best = (x, y, rot)
        return best

    # ── Phase 1: ICs (anchor everything) ──────────────────────────────
    ic_x, ic_y = IC_X, IC_Y
    ic_count = 0
    for ref in sorted(conn_count.keys(), key=lambda r: -conn_count[r]):
        part = ref_to_part.get(ref)
        if not part or _is_placed(ref) or part.get("_group") != "main_ic":
            continue
        x = ic_x + ic_count * vertical_gap * 2
        _do_place(ref, x, ic_y)
        ic_count += 1

    # ── Phase 2: Power regulators (own block, bottom-left) ────────────
    pwr_x = MARGIN + 30
    pwr_y = SHEET_H - MARGIN - 20
    for ref in sorted(conn_count.keys(), key=lambda r: -conn_count[r]):
        part = ref_to_part.get(ref)
        if not part or _is_placed(ref) or part.get("_group") != "power_reg":
            continue
        _do_place(ref, pwr_x, pwr_y)
        # Place caps connected to this regulator nearby
        cap_dx = inline_gap * 0.6
        for _, other_ref, _ in connections.get(ref, []):
            cp = ref_to_part.get(other_ref)
            if cp and other_ref.startswith("C") and not _is_placed(other_ref):
                rot = _best_rotation(other_ref, pwr_x + cap_dx, pwr_y)
                _do_place(other_ref, pwr_x + cap_dx, pwr_y, rot)
                cap_dx += GRID * 4
        pwr_x += inline_gap * 2

    # ── Phase 3: Bypass caps → assign each to nearest IC ────────────
    ic_ref_set = {r for r in (placed_refs | newly_placed)
                  if ref_to_part.get(r, {}).get("_group") == "main_ic"}

    # Collect bypass caps and assign each to the IC it shares the most
    # exclusive power-net connections with (fewest other ICs on that net).
    bypass_caps = []
    for ref in sorted(conn_count.keys(), key=lambda r: -conn_count[r]):
        part = ref_to_part.get(ref)
        if part and not _is_placed(ref) and _is_bypass_cap(part):
            bypass_caps.append(ref)

    # Round-robin: assign each cap to the IC with fewest caps so far,
    # breaking ties by physical proximity (after ICs are placed).
    ic_list = sorted(ic_ref_set, key=lambda ic: ref_to_part[ic].get("_place_x", 0))
    ic_cap_count: dict[str, int] = dict.fromkeys(ic_list, 0)

    for cap_ref in bypass_caps:
        # Pick IC with fewest caps assigned; tie-break by proximity to cap's
        # connected neighbours (even though they're all on same power net,
        # the IC order gives deterministic 1:1 distribution).
        best_ic = min(
            ic_list,
            key=lambda ic: (ic_cap_count.get(ic, 0), ic_list.index(ic)),
        ) if ic_list else None

        if best_ic:
            ic_part = ref_to_part[best_ic]
            ic_x_pos = ic_part["_place_x"]
            ic_y_pos = ic_part["_place_y"]
            pw = _get_symbol_width(ic_part)
            ph = _get_symbol_height(ic_part)
            # F05: Caps always vertical (rot=90°), placed tight next to IC
            cap_candidates = [
                (_snap(ic_x_pos + pw/2 + GRID * 3), _snap(ic_y_pos)),
                (_snap(ic_x_pos - pw/2 - GRID * 3), _snap(ic_y_pos)),
                (_snap(ic_x_pos), _snap(ic_y_pos + ph/2 + GRID * 3)),
                (_snap(ic_x_pos), _snap(ic_y_pos - ph/2 - GRID * 3)),
            ]
            # Pick position with lowest cost but FORCE rotation=90° (vertical)
            current = placed_refs | newly_placed
            best_pos = cap_candidates[0]
            best_cost = float("inf")
            for cx, cy in cap_candidates:
                cost = _placement_cost(cap_ref, cx, cy, 90, ref_to_part,
                                       ref_net_index, current, pin_cache, net_roles)
                if cost < best_cost:
                    best_cost = cost
                    best_pos = (cx, cy)
            _do_place(cap_ref, best_pos[0], best_pos[1], 90)
            ic_cap_count[best_ic] = ic_cap_count.get(best_ic, 0) + 1
        else:
            candidates = _candidate_positions(cap_ref)
            x, y, rot = _pick_best(cap_ref, candidates)
            _do_place(cap_ref, x, y, rot)

    # ── Phase 4: Series chains (inline from IC pin) ───────────────────
    chains = find_series_chains(parts, nets, ic_ref_set)
    for chain in chains:
        ic = ref_to_part.get(chain["ic_ref"])
        if not ic or "_place_x" not in ic:
            continue
        pin_pos = get_pin_sides(ic, resolve_lib_id, get_real_symbol)
        ic_pin = chain["ic_pin"]
        pos = None
        for pp in ic.get("pins", []):
            if pp.get("name") == ic_pin:
                pos = pin_pos.get(str(pp.get("num", "")))
                break
        if not pos:
            pos = pin_pos.get(ic_pin)

        if pos and pos[0] > 0:
            cx = ic["_place_x"] + _get_symbol_width(ic) / 2 + inline_gap
            cy = ic["_place_y"] + pos[1]
            dx = inline_gap
        elif pos and pos[0] < 0:
            cx = ic["_place_x"] - _get_symbol_width(ic) / 2 - inline_gap
            cy = ic["_place_y"] + pos[1]
            dx = -inline_gap
        else:
            cx = ic["_place_x"] + _get_symbol_width(ic) / 2 + inline_gap
            cy = ic["_place_y"]
            dx = inline_gap

        for cref in chain["refs"]:
            if not _is_placed(cref):
                rot = _best_rotation(cref, cx, cy)
                _do_place(cref, cx, cy, rot)
                p = ref_to_part.get(cref)
                if p:
                    p["_chain_locked"] = True
                cx += dx

    # ── Phase 5: Crystal + Loadcaps ───────────────────────────────────
    for part in parts:
        if _is_placed(part["ref"]) or not part["ref"].startswith("Y"):
            continue
        crystal_nets = []
        for net in nets:
            if net.get("type") == "power":
                continue
            refs_in = set(c.split(":")[0] for c in net.get("connections", []))
            if part["ref"] in refs_in and refs_in & ic_ref_set:
                crystal_nets.append(net)
        if not crystal_nets:
            continue

        ic = ref_to_part.get(list(ic_ref_set)[0]) if ic_ref_set else None
        if not ic or "_place_x" not in ic:
            continue
        xtal_pin_pos = get_pin_sides(ic, resolve_lib_id, get_real_symbol)
        xtal_ys = []
        xtal_side = 0
        for net in crystal_nets:
            for conn in net.get("connections", []):
                ref, pname = conn.split(":", 1) if ":" in conn else (conn, "")
                if ref not in ic_ref_set:
                    continue
                for pp in ic.get("pins", []):
                    if pp.get("name") == pname:
                        ppos = xtal_pin_pos.get(str(pp.get("num", "")))
                        if ppos:
                            xtal_ys.append(ic["_place_y"] + ppos[1])
                            xtal_side += ppos[0]

        if xtal_ys:
            avg_y = sum(xtal_ys) / len(xtal_ys)
            if xtal_side > 0:
                yx = ic["_place_x"] + _get_symbol_width(ic)/2 + inline_gap
            else:
                yx = ic["_place_x"] - _get_symbol_width(ic)/2 - inline_gap
            _do_place(part["ref"], yx, avg_y)
            part["_chain_locked"] = True

            y_pin_pos = get_pin_sides(part, resolve_lib_id, get_real_symbol)
            for net in crystal_nets:
                y_pin_y = None
                for conn in net.get("connections", []):
                    cref, pname = conn.split(":", 1) if ":" in conn else (conn, "")
                    if cref != part["ref"]:
                        continue
                    for pp in part.get("pins", []):
                        if pp.get("name") == pname or str(pp.get("num", "")) == pname:
                            ppos = y_pin_pos.get(str(pp.get("num", "")))
                            if ppos:
                                y_pin_y = avg_y + ppos[1]
                            break
                if y_pin_y is None:
                    y_pin_y = avg_y
                for conn in net.get("connections", []):
                    cref = conn.split(":")[0]
                    cp = ref_to_part.get(cref)
                    if cp and cref.startswith("C") and not _is_placed(cref):
                        _do_place(cref, yx, y_pin_y + GRID * 4, 0)
                        cp["_chain_locked"] = True

    # ── Phase 5.5: Pullups inline on signal line (F09) ──────────────
    # A pullup: R with one pin on a power net, other pin on a signal net.
    # Place it on the signal line between the IC and the other endpoints.
    for ref in sorted(conn_count.keys(), key=lambda r: -conn_count[r]):
        part = ref_to_part.get(ref)
        if not part or _is_placed(ref):
            continue
        if not ref.startswith("R") or len(part.get("pins", [])) != 2:
            continue
        # Check: one pin on power, one on signal
        pin_nets = {}  # pin_name → (net_name, net_type, other_placed_refs)
        for net in nets:
            for conn in net.get("connections", []):
                if ":" not in conn:
                    continue
                cref, pname = conn.split(":", 1)
                if cref != ref:
                    continue
                others_placed = []
                for c2 in net.get("connections", []):
                    r2 = c2.split(":")[0]
                    if r2 != ref and r2 in (placed_refs | newly_placed):
                        op = ref_to_part.get(r2)
                        if op and "_place_x" in op:
                            others_placed.append(r2)
                pin_nets[pname] = (net["name"], net.get("type", "signal"), others_placed)

        if len(pin_nets) != 2:
            continue
        pnames = list(pin_nets.keys())
        types = [pin_nets[p][1] for p in pnames]
        if sorted(types) != ["power", "signal"]:
            continue

        # Find signal-net pin and its placed endpoints
        sig_pin = pnames[0] if pin_nets[pnames[0]][1] == "signal" else pnames[1]
        _, _, sig_others = pin_nets[sig_pin]
        if not sig_others:
            continue

        # Place pullup at midpoint of signal-net endpoints (inline)
        xs = [ref_to_part[r]["_place_x"] for r in sig_others]
        ys = [ref_to_part[r]["_place_y"] for r in sig_others]
        mid_x = sum(xs) / len(xs)
        mid_y = sum(ys) / len(ys)

        # Candidates: between IC and sensors, slight offsets
        candidates = [
            (_snap(mid_x), _snap(mid_y - inline_gap * 0.5)),
            (_snap(mid_x), _snap(mid_y + inline_gap * 0.5)),
            (_snap(mid_x - inline_gap * 0.3), _snap(mid_y)),
            (_snap(mid_x + inline_gap * 0.3), _snap(mid_y)),
        ]
        x, y, rot = _pick_best(ref, candidates)
        _do_place(ref, x, y, rot)
        logger.info("Pullup %s placed inline at (%.1f, %.1f)", ref, x, y)

    # ── Phase 6: Remaining passives (by connectivity, cost-scored) ────
    remaining = []
    for ref in sorted(conn_count.keys(), key=lambda r: -conn_count[r]):
        part = ref_to_part.get(ref)
        if not part or _is_placed(ref):
            continue
        if part.get("_group", "").startswith("connector"):
            continue
        remaining.append(ref)

    for ref in remaining:
        candidates = _candidate_positions(ref)
        x, y, rot = _pick_best(ref, candidates)
        _do_place(ref, x, y, rot)

    # ── Phase 7: Bus-aware connectors ─────────────────────────────────
    bus_groups = find_bus_groups(nets)
    bus_placed: set[str] = set()
    for bg in bus_groups:
        ic_refs_all = {p["ref"] for p in parts if p.get("_group") == "main_ic"}
        conn_ref = bg.ref_b if bg.ref_a in ic_refs_all else bg.ref_a
        ic_ref = bg.ref_a if conn_ref == bg.ref_b else bg.ref_b
        if _is_placed(conn_ref) or conn_ref in bus_placed:
            continue
        ic = ref_to_part.get(ic_ref)
        if not ic or "_place_x" not in ic:
            continue

        ic_pin_pos = get_pin_sides(ic, resolve_lib_id, get_real_symbol)

        def _lookup_pin(pin_name):
            pos = ic_pin_pos.get(pin_name)
            if pos is None:
                for pp in ic.get("pins", []):
                    if pp.get("name") == pin_name:
                        pos = ic_pin_pos.get(str(pp.get("num", "")))
                        break
            return pos

        bus_positions = [_lookup_pin(p) for p, _ in bg.pin_pairs]
        bus_positions = [p for p in bus_positions if p is not None]

        if bus_positions:
            target_y = ic["_place_y"] + sum(p[1] for p in bus_positions) / len(bus_positions)
            avg_rx = sum(p[0] for p in bus_positions)
            if avg_rx < 0:
                _do_place(conn_ref, ic["_place_x"] - _get_symbol_width(ic)/2 - inline_gap * 1.5, target_y)
            else:
                _do_place(conn_ref, ic["_place_x"] + _get_symbol_width(ic)/2 + inline_gap * 1.5, target_y)
            bus_placed.add(conn_ref)

    # ── Phase 7b: Bus-centering (6.3) ──────────────────────────────────
    # Align all participants of a detected bus along a vertical axis
    for bg in bus_groups:
        if len(bg.net_names) < 2:
            continue
        # Collect all refs on this bus
        bus_refs = set()
        for net in nets:
            if net["name"] in bg.net_names:
                for conn in net.get("connections", []):
                    r = conn.split(":")[0] if ":" in conn else ""
                    if r and r in ref_to_part and _is_placed(r):
                        bus_refs.add(r)

        if len(bus_refs) < 2:
            continue

        # Find the bus axis X: midpoint between IC and connectors on this bus
        ic_refs_here = {r for r in bus_refs if ref_to_part[r].get("_group") == "main_ic"}
        non_ic = bus_refs - ic_refs_here
        if not ic_refs_here or not non_ic:
            continue

        ic_x_avg = sum(ref_to_part[r]["_place_x"] for r in ic_refs_here) / len(ic_refs_here)
        other_x_avg = sum(ref_to_part[r]["_place_x"] for r in non_ic) / len(non_ic)
        bus_axis_x = _snap((ic_x_avg + other_x_avg) / 2)

        # Move non-IC bus participants toward the bus axis (only X adjustment)
        for r in non_ic:
            p = ref_to_part[r]
            if p.get("_group", "").startswith("connector"):
                continue  # Don't move connectors off the edge
            old_x = p["_place_x"]
            # Gently move toward bus axis (50% blend)
            new_x = _snap(old_x + (bus_axis_x - old_x) * 0.5)
            p["_place_x"] = new_x

    # ── Phase 8: Remaining connectors ─────────────────────────────────
    for part in parts:
        if _is_placed(part["ref"]) or part["ref"] in bus_placed:
            continue
        group = part.get("_group", "")
        if "connector" not in group:
            continue
        ref = part["ref"]
        neighbor_positions = []
        for _, other_ref, _ in connections.get(ref, []):
            other = ref_to_part.get(other_ref)
            if other and "_place_x" in other:
                neighbor_positions.append((other["_place_x"], other["_place_y"]))
        avg_y = sum(y for _, y in neighbor_positions) / len(neighbor_positions) if neighbor_positions else IC_Y

        if group == "connector_in":
            _do_place(ref, MARGIN + 5, avg_y)
        elif group == "connector_out":
            _do_place(ref, SHEET_W - MARGIN - 10, avg_y)
        elif group == "connector_pwr":
            avg_x = sum(x for x, _ in neighbor_positions) / len(neighbor_positions) if neighbor_positions else IC_X
            _do_place(ref, avg_x - inline_gap, SHEET_H - MARGIN - 15)

    # ── Phase 9: Anything still unplaced ──────────────────────────────
    fill_x, fill_y = MARGIN + 10, MARGIN + 10
    for part in parts:
        if not _is_placed(part["ref"]) and "_place_x" not in part:
            _do_place(part["ref"], fill_x, fill_y)
            fill_x += inline_gap
            if fill_x > SHEET_W - MARGIN:
                fill_x = MARGIN + 10
                fill_y += vertical_gap

    logger.info("incremental_place_and_score: placed %d components", len(newly_placed))
    return newly_placed
