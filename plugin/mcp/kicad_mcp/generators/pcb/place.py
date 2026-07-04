# SPDX-License-Identifier: GPL-3.0-or-later
"""PCB placement logic — incremental place+score (like a human).

Same rule structure as schematic/defrag_place.py:
  Phase 1: ICs (anchor, center)
  Phase 2: Power regulators (compact block)
  Phase 3: Bypass caps (directly at IC pin)
  Phase 4: Series chains (along current flow)
  Phase 5: Crystal + loadcaps (tight, mm range)
  Phase 6: Remaining passives (cost-scored)
  Phase 7: Bus elements (parallel)
  Phase 8: Connectors (board edge)
  Phase 9: Rest fill (fallback)
  Post:    Overlap/clearance + FD-refine

Caller: pcb_builder.build_pcb()
"""

from collections import defaultdict
import logging
import math

from ..common.bbox import _fp_size
from ..common.chain_detect import find_series_chains
from ..common.classify import (
    _classify_component,
    _is_bypass_cap,
    _is_pullup,
    _map_bypass_caps_round_robin,
)
from ..common.fd_refine import _fd_pcb_refine
from ..common.placement_cost import build_ref_to_nets, placement_cost

logger = logging.getLogger(__name__)


def _compute_pcb_placement(
    parts: list[dict], nets: list[dict], board_w: float, board_h: float,
) -> dict[str, tuple[float, float, int]]:
    """Incremental PCB placement — same rules as schematic, physical interpretation.

    Each component is placed at the position with lowest routing cost.
    """
    margin = 5.0
    x_min, y_min = margin, margin
    x_max, y_max = board_w - margin, board_h - margin
    cx, cy = board_w / 2, board_h / 2

    result: dict[str, tuple[float, float, int]] = {}
    occupied: list[tuple[str, float, float, float, float]] = []
    ref_to_part = {p["ref"]: p for p in parts}

    MIN_GAP = 2.0

    # Build connectivity
    connectivity_raw: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for net in nets:
        refs_in_net = set()
        for conn in net.get("connections", []):
            ref = conn.split(":")[0]
            if ref and ref in ref_to_part:
                refs_in_net.add(ref)
        for a in refs_in_net:
            for b in refs_in_net:
                if a != b:
                    connectivity_raw[a].append((b, net["name"]))

    conn_count = {ref: len(conns) for ref, conns in connectivity_raw.items()}
    ref_net_index = build_ref_to_nets(nets)

    # Classify (preserve pre-set _group)
    groups: dict[str, list[dict]] = defaultdict(list)
    for part in parts:
        part["_pcb_group"] = part.get("_group") or _classify_component(part)
        groups[part["_pcb_group"]].append(part)

    def _placed_positions() -> dict[str, tuple[float, float]]:
        return {ref: (x, y) for ref, (x, y, _) in result.items()}

    def _placed_centroid() -> tuple[float, float]:
        """Centroid of all already-placed components (like schematic defrag_place).

        Falls back to board center if nothing placed yet.
        """
        pos = _placed_positions()
        if not pos:
            return cx, cy
        xs = [x for x, _ in pos.values()]
        ys = [y for _, y in pos.values()]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def _place(ref: str, x: float, y: float, rot: int = 0):
        part = ref_to_part.get(ref)
        if not part or ref in result:
            return
        w, h = _fp_size(part)
        if rot in (90, 270):
            w, h = h, w
        x = max(x_min + w / 2, min(x, x_max - w / 2))
        y = max(y_min + h / 2, min(y, y_max - h / 2))

        # Push away from overlaps
        for _attempt in range(10):
            collision = False
            for _, ox, oy, ow, oh in occupied:
                gap_x = (w + ow) / 2 + MIN_GAP
                gap_y = (h + oh) / 2 + MIN_GAP
                if abs(x - ox) < gap_x and abs(y - oy) < gap_y:
                    collision = True
                    overlap_x = gap_x - abs(x - ox)
                    overlap_y = gap_y - abs(y - oy)
                    if overlap_x < overlap_y:
                        x += (gap_x + 0.5) * (1 if x >= ox else -1)
                    else:
                        y += (gap_y + 0.5) * (1 if y >= oy else -1)
            if not collision:
                break

        x = max(x_min + w / 2, min(x, x_max - w / 2))
        y = max(y_min + h / 2, min(y, y_max - h / 2))
        occupied.append((ref, x, y, w, h))
        result[ref] = (round(x, 2), round(y, 2), rot)

    def _pick_best_pos(ref: str, candidates: list[tuple[float, float, int]]) -> tuple[float, float, int]:
        """Pick candidate with lowest placement cost."""
        pos = _placed_positions()
        best = candidates[0]
        best_cost = float("inf")
        for x, y, rot in candidates:
            cost = placement_cost(ref, x, y, pos, ref_net_index)
            if cost < best_cost:
                best_cost = cost
                best = (x, y, rot)
        return best

    # ── Phase 0: explizite Positions-Hints (Vorlagen/Demo) ───────────
    # Ein Teil mit ``hint_pcb_x``/``hint_pcb_y`` wird EXAKT dort platziert
    # (kein Auto-Layout, keine Kollisions-Verschiebung) — so bleibt ein
    # bewusst gestaltetes Board (z. B. die Demo-Vorlage) sauber. Board-lokale
    # mm (0..board_w/0..board_h); die restlichen Teile fügt das Auto-Layout
    # drumherum ein.
    for part in parts:
        if part["ref"] in result:
            continue
        if "hint_pcb_x" in part and "hint_pcb_y" in part:
            hx, hy = float(part["hint_pcb_x"]), float(part["hint_pcb_y"])
            hrot = int(part.get("hint_pcb_rot", 0))
            w, h = _fp_size(part)
            if hrot in (90, 270):
                w, h = h, w
            result[part["ref"]] = (round(hx, 2), round(hy, 2), hrot)
            occupied.append((part["ref"], hx, hy, w, h))

    # ── Phase 1: ICs — heaviest at center (R01) ──────────────────────
    ics = sorted(groups.get("main_ic", []), key=lambda p: -conn_count.get(p["ref"], 0))
    if len(ics) == 1:
        _place(ics[0]["ref"], cx, cy)
    elif len(ics) == 2:
        gap = 20.0
        _place(ics[0]["ref"], cx - gap / 2, cy)
        _place(ics[1]["ref"], cx + gap / 2, cy)
    elif len(ics) >= 3:
        total_w = sum(_fp_size(ic)[0] for ic in ics) + 8.0 * (len(ics) - 1)
        start_x = cx - total_w / 2
        for ic in ics:
            w, _ = _fp_size(ic)
            _place(ic["ref"], start_x + w / 2, cy)
            start_x += w + 8.0

    # ── Phase 2: Power regulators — compact block (R02) ──────────────
    reg_x, reg_y = x_min + 10, y_min + 8
    for reg in groups.get("power_reg", []):
        if reg["ref"] not in result:
            _place(reg["ref"], reg_x, reg_y)
            reg_x += 15

    # ── Phase 3: Bypass caps — directly at IC pin (R03, F05) ─────────
    ic_refs = {p["ref"] for p in ics}
    ic_refs |= {p["ref"] for p in groups.get("power_reg", [])}
    all_caps = [p for p in parts if _is_bypass_cap(p)]
    ic_list_sorted = sorted(
        [r for r in ic_refs if r in result],
        key=lambda r: result[r][0],
    )
    cap_assignment = _map_bypass_caps_round_robin(all_caps, ic_list_sorted)

    for cap in all_caps:
        if cap["ref"] in result:
            continue
        ic_ref = cap_assignment.get(cap["ref"])
        if ic_ref and ic_ref in result:
            ic_x, ic_y, _ = result[ic_ref]
            w_ic, h_ic = _fp_size(ref_to_part[ic_ref])
            # Candidates: 4 sides of IC, tight (3mm)
            candidates = [
                (ic_x + w_ic / 2 + 3.0, ic_y, 90),
                (ic_x - w_ic / 2 - 3.0, ic_y, 90),
                (ic_x, ic_y - h_ic / 2 - 3.0, 0),
                (ic_x, ic_y + h_ic / 2 + 3.0, 0),
            ]
            x, y, rot = _pick_best_pos(cap["ref"], candidates)
            _place(cap["ref"], x, y, rot)
        else:
            pcx, pcy = _placed_centroid()
            _place(cap["ref"], pcx + 15, pcy - 10, 90)

    # ── Phase 4: Series chains — along current flow (R04) ────────────
    chains = find_series_chains(parts, nets, ic_refs)
    for chain in chains:
        ic_ref = chain["ic_ref"]
        if ic_ref not in result:
            continue
        ic_x, ic_y, _ = result[ic_ref]
        w_ic, _ = _fp_size(ref_to_part[ic_ref])
        chain_x = ic_x + w_ic / 2 + 5.0
        for cref in chain["refs"]:
            if cref not in result:
                prefix = "".join(c for c in cref if c.isalpha())
                rot = 90 if prefix in ("R", "C", "L", "D") else 0
                _place(cref, chain_x, ic_y, rot)
                chain_x += 5.0

    # ── Phase 5: Crystal + loadcaps — tight (R05, F08) ───────────────
    for part in parts:
        if part["ref"] in result:
            continue
        prefix = "".join(c for c in part["ref"] if c.isalpha())
        name_val = (part.get("name", "") + part.get("value", "")).upper()
        if prefix != "Y" and "CRYSTAL" not in name_val and "XTAL" not in name_val:
            continue
        if ics:
            ic_x, ic_y, _ = result.get(ics[0]["ref"], (cx, cy, 0))
            w_ic, h_ic = _fp_size(ics[0])
            _place(part["ref"], ic_x - w_ic / 2 - 6, ic_y + 4)

    # ── Phase 5.5: Pullups inline on signal path (F09) ───────────────
    for part in parts:
        if part["ref"] in result:
            continue
        if not _is_pullup(part, nets):
            continue
        pos = _placed_positions()
        neighbors = [(nb, nn) for nb, nn in connectivity_raw.get(part["ref"], []) if nb in pos]
        if neighbors:
            nx = sum(pos[nb][0] for nb, _ in neighbors) / len(neighbors)
            ny = sum(pos[nb][1] for nb, _ in neighbors) / len(neighbors)
            candidates = [
                (nx + 4.0, ny, 90),
                (nx - 4.0, ny, 90),
                (nx, ny + 4.0, 0),
                (nx, ny - 4.0, 0),
            ]
            x, y, rot = _pick_best_pos(part["ref"], candidates)
            _place(part["ref"], x, y, rot)

    # ── Phase 6: Remaining passives — cost-scored (R06) ──────────────
    for _ in range(3):
        unplaced = [p for p in parts if p["ref"] not in result
                    and p["_pcb_group"] in ("passive", "indicator", "transistor")]
        unplaced.sort(key=lambda p: -conn_count.get(p["ref"], 0))

        for part in unplaced:
            ref = part["ref"]
            if ref in result:
                continue
            pos = _placed_positions()
            neighbors = [(nb, nn) for nb, nn in connectivity_raw.get(ref, []) if nb in pos]
            prefix = "".join(c for c in ref if c.isalpha())
            rot = 90 if prefix in ("R", "C", "L", "D") else 0

            if neighbors:
                nx = sum(pos[nb][0] for nb, _ in neighbors) / len(neighbors)
                ny = sum(pos[nb][1] for nb, _ in neighbors) / len(neighbors)
                # Use dynamic centroid of placed parts (like schematic defrag_place)
                pcx, pcy = _placed_centroid()
                dx = nx - pcx
                dy = ny - pcy
                dist = math.sqrt(dx * dx + dy * dy) or 1
                offset = 8.0
                # F03: test both rotations (0° and 90°) for passives
                rots = [0, 90] if prefix in ("R", "C", "L", "D") else [rot]
                candidates = []
                for r in rots:
                    candidates.extend([
                        (nx + offset * dx / dist, ny + offset * dy / dist, r),
                        (nx - offset * dx / dist, ny - offset * dy / dist, r),
                        (nx + offset, ny, r),
                        (nx, ny + offset, r),
                    ])
                x, y, rot = _pick_best_pos(ref, candidates)
                _place(ref, x, y, rot)

    # ── Phase 7: Bus elements — handled by cost in Phase 6 ───────────
    # (Bus alignment is achieved through _placement_cost pulling bus
    # endpoints together)

    # ── Phase 8: Connectors at board edge (R08) ──────────────────────
    pwr_y = y_min + 8
    for conn in groups.get("connector_pwr", []):
        if conn["ref"] not in result:
            _place(conn["ref"], x_min + 4, pwr_y)
            pwr_y += 15

    for conn in groups.get("connector_in", []):
        if conn["ref"] not in result:
            _place(conn["ref"], x_min + 4, pwr_y)
            pwr_y += 15

    out_y = y_min + 8
    for conn in groups.get("connector_out", []):
        if conn["ref"] not in result:
            _place(conn["ref"], x_max - 4, out_y)
            out_y += 15

    # Indicators near their driver, toward edge
    for ind in groups.get("indicator", []):
        if ind["ref"] in result:
            continue
        pos = _placed_positions()
        neighbors = [(nb, nn) for nb, nn in connectivity_raw.get(ind["ref"], []) if nb in pos]
        if neighbors:
            nx = sum(pos[nb][0] for nb, _ in neighbors) / len(neighbors)
            ny = sum(pos[nb][1] for nb, _ in neighbors) / len(neighbors)
            pcx, _ = _placed_centroid()
            edge_x = x_max - 5 if nx > pcx else x_min + 5
            _place(ind["ref"], edge_x, ny, 0)
        else:
            _place(ind["ref"], x_max - 5, y_min + 8, 0)

    # ── Phase 9: Rest fill (R09) ─────────────────────────────────────
    fill_x, fill_y = x_min + 8, y_max - 8
    for part in parts:
        if part["ref"] not in result:
            prefix = "".join(c for c in part["ref"] if c.isalpha())
            rot = 90 if prefix in ("R", "C", "L") else 0
            _place(part["ref"], fill_x, fill_y, rot)
            fill_x += 10
            if fill_x > x_max - 8:
                fill_x = x_min + 8
                fill_y -= 10

    # ── Post: Force-directed refinement ──────────────────────────────
    _fd_pcb_refine(result, connectivity_raw, ref_to_part, parts,
                   x_min, y_min, x_max, y_max, occupied)

    return result
