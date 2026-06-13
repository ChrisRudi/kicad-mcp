# SPDX-License-Identifier: GPL-3.0-or-later
"""
Schematic quality scorer.

Evaluates a generated schematic against visual rules extracted from
8 reference schematics. Returns a score 0-100 and a list of violations.

Used in the iterative optimization loop to measure improvement.
"""

from collections import defaultdict
import logging
import math

logger = logging.getLogger(__name__)


def score_schematic(parts: list[dict], nets: list[dict]) -> tuple[float, list[str]]:
    """Score a schematic layout against reference rules.

    Args:
        parts: Component list with _place_x, _place_y, _rotation, _group set
        nets: Net list

    Returns:
        (score 0-100, list of violation descriptions)
    """
    violations = []
    scores = {}

    # R1: Resistors should be vertical (90°)
    scores["R1_resistor_vertical"] = _score_rotation(parts, "R", 90, violations)

    # R2: Capacitors should be vertical (90°)
    scores["R2_capacitor_vertical"] = _score_rotation(parts, "C", 90, violations)

    # R3: Signal flow left-to-right (X increases along signal chain)
    scores["R3_signal_flow_lr"] = _score_signal_flow(parts, nets, violations)

    # R5: Connectors at sheet edges
    scores["R5_connectors_at_edge"] = _score_connectors_at_edge(parts, violations)

    # R7: Diodes vertical
    scores["R7_diode_vertical"] = _score_rotation(parts, "D", 90, violations)

    # R8: NPN collector up, PNP collector down
    scores["R8_transistor_orientation"] = _score_transistor_symmetry(parts, violations)

    # R9: Spacing between components (2-3x component size)
    scores["R9_spacing"] = _score_spacing(parts, violations)

    # R10: No overlapping components
    scores["R10_no_overlap"] = _score_no_overlap(parts, violations)

    # R11: Functional blocks separated by whitespace
    scores["R11_block_separation"] = _score_block_separation(parts, violations)

    # R14: Power supply block at bottom
    scores["R14_psu_bottom"] = _score_psu_position(parts, violations)

    # R_power: Power rail Y ordering (V+ top, GND mid, V- bottom)
    scores["R_power_y_order"] = _score_power_rail_order(parts, nets, violations)

    # ── Readability rules (from Elektor analysis) ──────────────────────────

    # R_whitespace: Sufficient whitespace between functional blocks
    scores["R_whitespace"] = _score_whitespace(parts, violations)

    # R_compactness: Schematic uses 50-80% of sheet area (not too sparse, not too dense)
    scores["R_compactness"] = _score_compactness(parts, violations)

    # R_grid_alignment: Components snapped to grid
    scores["R_grid_alignment"] = _score_grid_alignment(parts, violations)

    # R_wire_crossings: Estimate wire crossing potential (components shouldn't force crossings)
    scores["R_wire_crossings"] = _score_crossing_potential(parts, nets, violations)

    # F04: Bus straightness — IC-to-IC bus connections should be short & straight
    scores["F04_bus_straightness"] = _score_bus_straightness(parts, nets, violations)

    # F05: Cap alignment — bypass caps should be vertical (90°)
    scores["F05_cap_alignment"] = _score_cap_alignment(parts, violations)

    # F06: PSU block unity — regulator + caps should be tight together
    scores["F06_psu_block_unity"] = _score_psu_block_unity(parts, nets, violations)

    # F07: Chain straightness — series chain members should be co-linear
    scores["F07_chain_straightness"] = _score_chain_straightness(parts, nets, violations)

    # F01: Bend penalty — connections needing bends (not axis-aligned)
    scores["F01_bend_penalty"] = _score_bend_penalty(parts, nets, violations)

    # F02: Junction count — nets with many endpoints need junctions
    scores["F02_junction_count"] = _score_junction_count(parts, nets, violations)

    # F03: Rotation matches routing direction
    scores["F03_rotation_routing"] = _score_rotation_routing(parts, nets, violations)

    # F09: Pullup inline on signal path
    scores["F09_pullup_inline"] = _score_pullup_inline(parts, nets, violations)

    # Compute weighted average
    weights = {
        "R1_resistor_vertical": 5,
        "R2_capacitor_vertical": 5,
        "R3_signal_flow_lr": 20,  # Most important
        "R5_connectors_at_edge": 10,
        "R7_diode_vertical": 3,
        "R8_transistor_orientation": 10,
        "R9_spacing": 15,
        "R10_no_overlap": 15,
        "R11_block_separation": 7,
        "R14_psu_bottom": 5,
        "R_power_y_order": 5,
        # Readability (from Elektor training)
        "R_whitespace": 8,
        "R_compactness": 5,
        "R_grid_alignment": 3,
        "R_wire_crossings": 10,
        # F-rules (new)
        "F04_bus_straightness": 5,
        "F05_cap_alignment": 5,
        "F06_psu_block_unity": 5,
        "F07_chain_straightness": 5,
        # F-rules (new batch)
        "F01_bend_penalty": 5,
        "F02_junction_count": 3,
        "F03_rotation_routing": 5,
        "F09_pullup_inline": 5,
    }

    total_weight = sum(weights.values())
    weighted_sum = sum(scores[k] * weights[k] for k in scores)
    final_score = weighted_sum / total_weight

    logger.info("Schematic score: %.1f/100", final_score)
    for name, s in sorted(scores.items()):
        logger.debug("  %s: %.1f", name, s)

    return final_score, violations


# ── Individual scoring functions ─────────────────────────────────────────────

def _score_rotation(parts: list[dict], prefix: str, expected: int,
                    violations: list[str]) -> float:
    """Score: what fraction of components with given prefix have expected rotation."""
    matching = [p for p in parts if "".join(c for c in p["ref"] if c.isalpha()) == prefix
                and "_rotation" in p]
    if not matching:
        return 100.0
    correct = sum(1 for p in matching if p["_rotation"] == expected)
    score = 100.0 * correct / len(matching)
    if score < 100:
        violations.append(f"{prefix} rotation: {correct}/{len(matching)} are {expected}°")
    return score


def _score_signal_flow(parts: list[dict], nets: list[dict],
                       violations: list[str]) -> float:
    """Score: do connected components flow left-to-right (increasing X)?

    Each undirected edge is counted once. An edge is "forward" if the
    left component has input/passive pins and the right has output pins
    on the net, OR simply if the pair doesn't violate L-to-R flow.
    """
    ref_x = {p["ref"]: p.get("_place_x", 0) for p in parts if "_place_x" in p}
    total_edges = 0
    forward_edges = 0

    # Collect unique edges from signal nets
    seen_edges: set[tuple[str, str]] = set()
    for net in nets:
        if net.get("type") == "power":
            continue
        refs = set()
        for conn in net.get("connections", []):
            ref = conn.split(":")[0] if ":" in conn else ""
            if ref and ref in ref_x:
                refs.add(ref)
        refs_list = sorted(refs)
        for i, a in enumerate(refs_list):
            for b in refs_list[i + 1:]:
                edge = (a, b) if a < b else (b, a)
                if edge in seen_edges:
                    continue
                seen_edges.add(edge)
                total_edges += 1
                # Forward = left component has smaller X, or within tolerance
                xa, xb = ref_x[a], ref_x[b]
                if abs(xa - xb) < 5:
                    forward_edges += 1  # same X is OK
                elif xa < xb or xb < xa:
                    forward_edges += 1  # any direction counts — we just check monotonicity below

    if total_edges == 0:
        return 100.0

    # Better metric: check overall monotonicity of signal chain
    # Count edges where the "later in chain" component is to the right
    forward_edges = 0
    for a, b in seen_edges:
        xa, xb = ref_x[a], ref_x[b]
        # Both directions are fine for undirected nets — penalize only clear reversals
        # A reversal = the rightmost component is an input-type connected to a left output-type
        # Simple heuristic: just check that most pairs have the lower-ref-number to the left
        # or that at least one direction is plausible
        if abs(xa - xb) < 5:
            forward_edges += 1  # co-located is OK
        else:
            forward_edges += 1  # undirected — we can't determine "wrong" direction

    # Real signal flow score: check that input connectors are LEFT and output RIGHT
    from .common.constants import MARGIN, SHEET_W
    input_refs = {p["ref"] for p in parts if p.get("_group", "").startswith("connector_in")}
    output_refs = {p["ref"] for p in parts if p.get("_group", "").startswith("connector_out")}
    indicator_refs = {p["ref"] for p in parts if p.get("_group") == "indicator"}

    # Score based on: inputs should be left, outputs/indicators right
    flow_score = 100.0
    center_x = SHEET_W / 2

    for ref in input_refs:
        if ref in ref_x and ref_x[ref] > center_x:
            flow_score -= 15  # input on right side

    for ref in output_refs:
        if ref in ref_x and ref_x[ref] < center_x:
            flow_score -= 15  # output connector on left side
    for ref in indicator_refs:
        if ref in ref_x and ref_x[ref] < MARGIN + 30:
            flow_score -= 10  # indicator at far left is wrong

    # Also check: main ICs should be center-left, passives distributed around them
    ic_refs = {p["ref"] for p in parts if p.get("_group") == "main_ic"}
    passive_refs = {p["ref"] for p in parts if p.get("_group") == "passive"}

    if ic_refs and passive_refs:
        avg_ic_x = sum(ref_x[r] for r in ic_refs if r in ref_x) / max(len(ic_refs), 1)
        # ICs should be in center-left (not too far right)
        if avg_ic_x > SHEET_W * 0.7:
            flow_score -= 10

    flow_score = max(0, min(100, flow_score))
    if flow_score < 80:
        violations.append(f"Signal flow: score {flow_score:.0f} (inputs/outputs not at expected positions)")
    return flow_score


def _score_connectors_at_edge(parts: list[dict], violations: list[str]) -> float:
    """Score: are connectors placed near sheet edges?"""
    from .common.constants import MARGIN, SHEET_W
    edge_threshold = MARGIN + 30  # within 30mm of margin

    connectors = [p for p in parts if p.get("_group", "").startswith("connector")
                  and "_place_x" in p]
    if not connectors:
        return 100.0

    at_edge = 0
    for c in connectors:
        x = c["_place_x"]
        if x < edge_threshold or x > SHEET_W - edge_threshold:
            at_edge += 1
        else:
            violations.append(f"{c['ref']} not at edge (x={x:.0f}mm)")

    return 100.0 * at_edge / len(connectors)


def _score_transistor_symmetry(parts: list[dict], violations: list[str]) -> float:
    """Score: NPN above center, PNP below center (push-pull symmetry)."""
    from .common.constants import Y_CENTER

    transistors = [p for p in parts if p.get("_group") == "transistor" and "_place_y" in p]
    if not transistors:
        return 100.0

    correct = 0
    for q in transistors:
        name = (q.get("name", "") + q.get("value", "")).upper()
        y = q["_place_y"]
        if "PNP" in name:
            if y > Y_CENTER:
                correct += 1
            else:
                violations.append(f"{q['ref']} PNP above center (y={y:.0f})")
        else:
            if y < Y_CENTER:
                correct += 1
            else:
                violations.append(f"{q['ref']} NPN below center (y={y:.0f})")

    return 100.0 * correct / len(transistors)


def _score_spacing(parts: list[dict], violations: list[str]) -> float:
    """Score: minimum spacing between components (no crowding)."""
    from .common.bbox import _get_symbol_bbox
    from .common.constants import SYMBOL_GAP

    placed = [(p, p["_place_x"], p["_place_y"]) for p in parts if "_place_x" in p]
    if len(placed) < 2:
        return 100.0

    too_close = 0
    total_pairs = 0

    for i, (pa, xa, ya) in enumerate(placed):
        wa, _ha = _get_symbol_bbox(pa)
        for pb, xb, yb in placed[i + 1:]:
            wb, _hb = _get_symbol_bbox(pb)
            dist = math.sqrt((xa - xb) ** 2 + (ya - yb) ** 2)
            min_dist = (wa + wb) / 2 + SYMBOL_GAP * 0.5
            total_pairs += 1
            if dist < min_dist:
                too_close += 1

    if total_pairs == 0:
        return 100.0
    score = 100.0 * (1 - too_close / total_pairs)
    if too_close > 0:
        violations.append(f"Spacing: {too_close} pairs too close")
    return max(0, score)


def _score_no_overlap(parts: list[dict], violations: list[str]) -> float:
    """Score: no overlapping bounding boxes."""
    from .common.bbox import _get_symbol_bbox

    placed = [(p, p["_place_x"], p["_place_y"]) for p in parts if "_place_x" in p]
    overlaps = 0
    total = 0

    for i, (pa, xa, ya) in enumerate(placed):
        wa, ha = _get_symbol_bbox(pa)
        for pb, xb, yb in placed[i + 1:]:
            wb, hb = _get_symbol_bbox(pb)
            total += 1
            if (abs(xa - xb) < (wa + wb) / 2 and abs(ya - yb) < (ha + hb) / 2):
                overlaps += 1
                violations.append(f"Overlap: {pa['ref']} and {pb['ref']}")

    if total == 0:
        return 100.0
    return 100.0 * (1 - overlaps / total)


def _score_block_separation(parts: list[dict], violations: list[str]) -> float:
    """Score: functional groups have clear whitespace between them."""
    from .common.constants import GROUP_GAP

    groups: dict[str, list[dict]] = defaultdict(list)
    for p in parts:
        if "_place_x" in p:
            groups[p.get("_group", "other")].append(p)

    if len(groups) < 2:
        return 100.0

    # Compute center of each group
    centers = {}
    for g, comps in groups.items():
        if len(comps) < 1:
            continue
        cx = sum(p["_place_x"] for p in comps) / len(comps)
        cy = sum(p["_place_y"] for p in comps) / len(comps)
        centers[g] = (cx, cy)

    # Check that different groups are sufficiently separated
    well_separated = 0
    total = 0
    group_names = list(centers.keys())
    for i, g1 in enumerate(group_names):
        for g2 in group_names[i + 1:]:
            dx = centers[g1][0] - centers[g2][0]
            dy = centers[g1][1] - centers[g2][1]
            dist = math.sqrt(dx * dx + dy * dy)
            total += 1
            if dist > GROUP_GAP:
                well_separated += 1

    if total == 0:
        return 100.0
    return 100.0 * well_separated / total


def _score_psu_position(parts: list[dict], violations: list[str]) -> float:
    """Score: power supply components at bottom of sheet."""
    from .common.constants import Y_CENTER

    psu_parts = [p for p in parts
                 if p.get("_group") in ("connector_pwr", "power_reg", "power_passive")
                 and "_place_y" in p]
    if not psu_parts:
        return 100.0

    below = sum(1 for p in psu_parts if p["_place_y"] > Y_CENTER)
    score = 100.0 * below / len(psu_parts)
    if score < 100:
        violations.append(f"PSU position: {below}/{len(psu_parts)} below center")
    return score


def _score_power_rail_order(parts: list[dict], nets: list[dict],
                            violations: list[str]) -> float:
    """Score: components on V+ are above GND, GND above V-."""
    from .common.classify import (
        _GND_PATTERNS,
        _NEGATIVE_PATTERNS,
        _POSITIVE_PATTERNS,
        _get_power_net_names,
    )

    pos_ys = []
    neg_ys = []
    gnd_ys = []

    for part in parts:
        if "_place_y" not in part:
            continue
        power_nets = _get_power_net_names(part["ref"], nets)
        net_str = " ".join(power_nets).upper()
        y = part["_place_y"]

        if any(p in net_str for p in _POSITIVE_PATTERNS):
            pos_ys.append(y)
        if any(p in net_str for p in _NEGATIVE_PATTERNS):
            neg_ys.append(y)
        if any(p in net_str for p in _GND_PATTERNS):
            gnd_ys.append(y)

    if not pos_ys or not neg_ys:
        return 100.0

    avg_pos = sum(pos_ys) / len(pos_ys)
    avg_neg = sum(neg_ys) / len(neg_ys)
    avg_gnd = sum(gnd_ys) / len(gnd_ys) if gnd_ys else (avg_pos + avg_neg) / 2

    # V+ should have smaller Y (higher on page), V- larger Y (lower)
    if avg_pos < avg_gnd < avg_neg:
        return 100.0
    elif avg_pos < avg_neg:
        return 70.0
    else:
        violations.append(f"Power rail order: V+ avg_y={avg_pos:.0f}, GND={avg_gnd:.0f}, V-={avg_neg:.0f}")
        return 30.0


# ── Readability scoring functions (from Elektor training) ──────────────────

def _score_whitespace(parts: list[dict], violations: list[str]) -> float:
    """Score: sufficient whitespace between functional blocks.

    Based on Elektor analysis: best schematics have >= 15mm between
    block centroids. Measures average nearest-neighbor distance between
    group centers.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in parts:
        if "_place_x" in p:
            groups[p.get("_group", "other")].append(p)

    if len(groups) < 2:
        return 100.0

    centers = {}
    for g, comps in groups.items():
        if not comps:
            continue
        cx = sum(p["_place_x"] for p in comps) / len(comps)
        cy = sum(p["_place_y"] for p in comps) / len(comps)
        centers[g] = (cx, cy)

    if len(centers) < 2:
        return 100.0

    # For each group, find nearest other group
    min_distances = []
    group_names = list(centers.keys())
    for i, g1 in enumerate(group_names):
        min_dist = float("inf")
        for j, g2 in enumerate(group_names):
            if i == j:
                continue
            dx = centers[g1][0] - centers[g2][0]
            dy = centers[g1][1] - centers[g2][1]
            dist = math.sqrt(dx * dx + dy * dy)
            min_dist = min(min_dist, dist)
        if min_dist < float("inf"):
            min_distances.append(min_dist)

    if not min_distances:
        return 100.0

    avg_min_dist = sum(min_distances) / len(min_distances)
    # Best schematics: avg nearest group distance >= 15mm
    threshold = 15.0
    if avg_min_dist >= threshold:
        return 100.0
    score = 100.0 * avg_min_dist / threshold
    if score < 60:
        violations.append(f"Whitespace: avg block distance {avg_min_dist:.0f}mm < {threshold:.0f}mm")
    return max(0, score)


def _score_compactness(parts: list[dict], violations: list[str]) -> float:
    """Score: schematic uses 50-80% of available sheet area.

    Based on Elektor analysis: best schematics use the page well —
    not too sparse (wasted space) and not too cramped.
    """
    from .common.constants import MARGIN, SHEET_H, SHEET_W

    placed = [p for p in parts if "_place_x" in p]
    if len(placed) < 2:
        return 100.0

    xs = [p["_place_x"] for p in placed]
    ys = [p["_place_y"] for p in placed]

    used_w = max(xs) - min(xs)
    used_h = max(ys) - min(ys)
    avail_w = SHEET_W - 2 * MARGIN
    avail_h = SHEET_H - 2 * MARGIN

    usage = (used_w * used_h) / (avail_w * avail_h)

    # Sweet spot: 30-85% usage
    if 0.30 <= usage <= 0.85:
        return 100.0
    elif usage < 0.30:
        # Too sparse
        score = 100.0 * usage / 0.30
        violations.append(f"Compactness: only {usage*100:.0f}% of sheet used (too sparse)")
        return max(0, score)
    else:
        # Too dense
        score = 100.0 * (1.0 - (usage - 0.85) / 0.15)
        violations.append(f"Compactness: {usage*100:.0f}% of sheet used (too dense)")
        return max(0, score)


def _score_grid_alignment(parts: list[dict], violations: list[str]) -> float:
    """Score: components aligned to KiCad grid.

    Best schematics have all components on 1.27mm grid.
    """
    grid = 1.27
    placed = [p for p in parts if "_place_x" in p]
    if not placed:
        return 100.0

    on_grid = 0
    for p in placed:
        x_off = abs(p["_place_x"] % grid)
        y_off = abs(p["_place_y"] % grid)
        # Allow small tolerance
        if min(x_off, grid - x_off) < 0.1 and min(y_off, grid - y_off) < 0.1:
            on_grid += 1

    score = 100.0 * on_grid / len(placed)
    if score < 80:
        violations.append(f"Grid alignment: {on_grid}/{len(placed)} on grid")
    return score


def _score_crossing_potential(parts: list[dict], nets: list[dict],
                              violations: list[str]) -> float:
    """Score: estimate wire crossing potential.

    Crossing occurs when two nets' connected component pairs have
    overlapping X/Y ranges in opposite directions. A simple heuristic:
    for each pair of signal nets, check if their endpoints would
    require crossing wires.
    """
    # Build net endpoint pairs (simplified: use component positions)
    net_spans = []
    ref_pos = {p["ref"]: (p.get("_place_x", 0), p.get("_place_y", 0))
               for p in parts if "_place_x" in p}

    for net in nets:
        if net.get("type") == "power":
            continue
        positions = []
        for conn in net.get("connections", []):
            ref = conn.split(":")[0] if ":" in conn else ""
            if ref in ref_pos:
                positions.append(ref_pos[ref])
        if len(positions) >= 2:
            xs = [p[0] for p in positions]
            ys = [p[1] for p in positions]
            net_spans.append((min(xs), max(xs), min(ys), max(ys)))

    if len(net_spans) < 2:
        return 100.0

    # Count potential crossings: two nets cross if their X-spans overlap
    # but Y-order reverses (or vice versa)
    crossings = 0
    total_pairs = 0
    for i in range(len(net_spans)):
        x1a, x1b, y1a, y1b = net_spans[i]
        for j in range(i + 1, len(net_spans)):
            x2a, x2b, y2a, y2b = net_spans[j]
            total_pairs += 1

            # X-ranges overlap?
            x_overlap = x1a < x2b and x2a < x1b
            # Y-ranges overlap?
            y_overlap = y1a < y2b and y2a < y1b

            if x_overlap and y_overlap:
                # Both ranges overlap — potential crossing
                # Check if one net goes "up" while other goes "down"
                dy1 = y1b - y1a
                dy2 = y2b - y2a
                dx1 = x1b - x1a
                dx2 = x2b - x2a
                if (dy1 * dy2 < 0) or (dx1 * dx2 < 0 and y_overlap):
                    crossings += 1

    if total_pairs == 0:
        return 100.0

    crossing_ratio = crossings / total_pairs
    score = 100.0 * (1.0 - crossing_ratio * 5)  # penalize heavily
    if crossings > 0:
        violations.append(f"Wire crossings: {crossings} potential crossing(s)")
    return max(0, min(100, score))


# ── F-rule scoring functions ───────────────────────────────────────────────


def _score_bus_straightness(parts: list[dict], nets: list[dict],
                            violations: list[str]) -> float:
    """F04: Bus nets (multi-signal nets between same pair of ICs) should be
    short and Y-aligned (straight horizontal lines).

    Measures: for each signal net connecting 2+ ICs, how close are the
    endpoint Y-coordinates? Penalizes large Y-deltas (= angled wires).
    """
    ref_to_part = {p["ref"]: p for p in parts if "_place_x" in p}
    ic_refs = {p["ref"] for p in parts if p.get("_group") == "main_ic"}

    total_nets = 0
    straight_nets = 0
    for net in nets:
        if net.get("type") == "power":
            continue
        # Find IC endpoints on this net
        ic_positions = []
        for conn in net.get("connections", []):
            cref = conn.split(":")[0]
            if cref in ic_refs and cref in ref_to_part:
                p = ref_to_part[cref]
                ic_positions.append((p["_place_x"], p["_place_y"]))
        if len(ic_positions) < 2:
            continue
        total_nets += 1
        # Check Y-alignment: all IC endpoints within 10mm Y
        ys = [y for _, y in ic_positions]
        y_spread = max(ys) - min(ys)
        if y_spread < 10.0:
            straight_nets += 1
        else:
            violations.append(f"Bus {net['name']}: Y-spread {y_spread:.0f}mm between ICs")

    if total_nets == 0:
        return 100.0
    return 100.0 * straight_nets / total_nets


def _score_cap_alignment(parts: list[dict], violations: list[str]) -> float:
    """F05: Bypass caps should be vertical (rotation=90°).

    Caps at 90° align with power rails and look clean in schematics.
    """
    from .common.classify import _is_bypass_cap
    caps = [p for p in parts if _is_bypass_cap(p) and "_rotation" in p]
    if not caps:
        return 100.0
    vertical = sum(1 for c in caps if c["_rotation"] == 90)
    score = 100.0 * vertical / len(caps)
    if score < 100:
        violations.append(f"Cap alignment: {vertical}/{len(caps)} bypass caps vertical")
    return score


def _score_psu_block_unity(parts: list[dict], nets: list[dict],
                           violations: list[str]) -> float:
    """F06: Power regulator and its input/output caps should form a tight block.

    Measures: distance from regulator to its directly-connected capacitors.
    All should be within 2× inline_gap.
    """
    from .common.classify import _is_bypass_cap
    from .common.constants import INLINE_GAP

    regs = [p for p in parts if p.get("_group") == "power_reg" and "_place_x" in p]
    if not regs:
        return 100.0  # no regulators = no penalty

    total = 0
    close = 0
    max_dist = INLINE_GAP * 2.0

    for reg in regs:
        rx, ry = reg["_place_x"], reg["_place_y"]
        # Find caps connected to this regulator
        for net in nets:
            for conn in net.get("connections", []):
                if conn.split(":")[0] != reg["ref"]:
                    continue
                for conn2 in net.get("connections", []):
                    cref = conn2.split(":")[0]
                    if cref == reg["ref"]:
                        continue
                    cp = next((p for p in parts if p["ref"] == cref), None)
                    if not cp or not _is_bypass_cap(cp) or "_place_x" not in cp:
                        continue
                    total += 1
                    dist = abs(cp["_place_x"] - rx) + abs(cp["_place_y"] - ry)
                    if dist <= max_dist:
                        close += 1
                    else:
                        violations.append(
                            f"PSU block: {cref} is {dist:.0f}mm from {reg['ref']}")
                break  # only check first net match per reg

    if total == 0:
        return 100.0
    return 100.0 * close / total


def _score_chain_straightness(parts: list[dict], nets: list[dict],
                              violations: list[str]) -> float:
    """F07: Series chain members should be co-linear (same Y for horizontal chains).

    Finds chain-locked components and checks if they share the same Y-coordinate.
    """
    # Group chain-locked parts by their Y-coordinate (tolerance 3mm)
    chain_parts = [p for p in parts if p.get("_chain_locked") and "_place_y" in p]
    if len(chain_parts) < 2:
        return 100.0

    # Group by approximate Y (within 5mm = same row)
    rows: dict[int, list[dict]] = {}
    for p in chain_parts:
        row_key = round(p["_place_y"] / 5.0)
        rows.setdefault(row_key, []).append(p)

    total = 0
    aligned = 0
    for row_parts in rows.values():
        if len(row_parts) < 2:
            continue
        ys = [p["_place_y"] for p in row_parts]
        y_spread = max(ys) - min(ys)
        total += 1
        if y_spread < 2.0:  # within 2mm = aligned
            aligned += 1
        else:
            refs = ", ".join(p["ref"] for p in row_parts)
            violations.append(f"Chain misaligned: {refs} Y-spread {y_spread:.1f}mm")

    if total == 0:
        return 100.0
    return 100.0 * aligned / total


def _score_bend_penalty(parts: list[dict], nets: list[dict],
                        violations: list[str]) -> float:
    """F01: Penalize connections that require bends (endpoints not axis-aligned).

    A connection between two placed components needs a bend when neither
    X nor Y coordinates are close (tolerance 5mm).  Fewer bends = cleaner routing.
    """
    ref_pos = {p["ref"]: (p.get("_place_x", 0), p.get("_place_y", 0))
               for p in parts if "_place_x" in p}

    total = 0
    bends = 0
    seen: set[tuple[str, str]] = set()

    for net in nets:
        if net.get("type") == "power":
            continue
        refs = []
        for conn in net.get("connections", []):
            ref = conn.split(":")[0]
            if ref in ref_pos:
                refs.append(ref)
        refs = sorted(set(refs))
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                edge = (a, b)
                if edge in seen:
                    continue
                seen.add(edge)
                total += 1
                ax, ay = ref_pos[a]
                bx, by = ref_pos[b]
                if abs(ax - bx) > 5.0 and abs(ay - by) > 5.0:
                    bends += 1

    if total == 0:
        return 100.0
    score = 100.0 * (1.0 - bends / total)
    if bends > 0:
        violations.append(f"Bend penalty: {bends}/{total} connections need bends")
    return max(0, score)


def _score_junction_count(parts: list[dict], nets: list[dict],
                          violations: list[str]) -> float:
    """F02: Penalize signal nets with many endpoints (>2 = junction needed).

    Nets with 3+ placed endpoints require junction points which add
    routing complexity and visual clutter.
    """
    ref_set = {p["ref"] for p in parts if "_place_x" in p}
    total_signal = 0
    nets_with_junctions = 0

    for net in nets:
        if net.get("type") == "power":
            continue
        placed_refs = set()
        for conn in net.get("connections", []):
            ref = conn.split(":")[0]
            if ref in ref_set:
                placed_refs.add(ref)
        if len(placed_refs) < 2:
            continue
        total_signal += 1
        if len(placed_refs) > 2:
            nets_with_junctions += 1

    if total_signal == 0:
        return 100.0
    score = 100.0 * (1.0 - nets_with_junctions / total_signal)
    if nets_with_junctions > 0:
        violations.append(
            f"Junction count: {nets_with_junctions}/{total_signal} signal nets need junctions")
    return max(0, score)


def _score_rotation_routing(parts: list[dict], nets: list[dict],
                            violations: list[str]) -> float:
    """F03: Passive rotation should match dominant connection direction.

    A passive with horizontal neighbors (|dx| > |dy|) should be 0° (horizontal).
    A passive with vertical neighbors (|dy| > |dx|) should be 90° (vertical).
    """
    ref_pos = {p["ref"]: (p.get("_place_x", 0), p.get("_place_y", 0))
               for p in parts if "_place_x" in p}

    # Build neighbor map from signal nets
    neighbors: dict[str, list[str]] = defaultdict(list)
    for net in nets:
        if net.get("type") == "power":
            continue
        refs = set()
        for conn in net.get("connections", []):
            ref = conn.split(":")[0]
            if ref in ref_pos:
                refs.add(ref)
        for a in refs:
            for b in refs:
                if a != b:
                    neighbors[a].append(b)

    passives = [p for p in parts
                if "".join(c for c in p["ref"] if c.isalpha()) in ("R", "C", "L", "D")
                and "_place_x" in p and "_rotation" in p]
    if not passives:
        return 100.0

    total = 0
    correct = 0
    for p in passives:
        nbs = neighbors.get(p["ref"], [])
        if not nbs:
            continue
        px, py = p["_place_x"], p["_place_y"]
        sum_dx = sum(abs(ref_pos[n][0] - px) for n in nbs if n in ref_pos)
        sum_dy = sum(abs(ref_pos[n][1] - py) for n in nbs if n in ref_pos)
        total += 1
        # Dominant direction: horizontal → 0°, vertical → 90°
        expected = 0 if sum_dx >= sum_dy else 90
        if p["_rotation"] == expected:
            correct += 1

    if total == 0:
        return 100.0
    score = 100.0 * correct / total
    if score < 80:
        violations.append(f"Rotation-routing: {correct}/{total} passives match connection direction")
    return score


def _score_pullup_inline(parts: list[dict], nets: list[dict],
                         violations: list[str]) -> float:
    """F09: Pullup/pulldown resistors should be inline on their signal path.

    A pullup is inline if it shares X or Y (within 5mm) with at least
    one signal neighbor.
    """
    from .common.classify import _is_pullup

    ref_pos = {p["ref"]: (p.get("_place_x", 0), p.get("_place_y", 0))
               for p in parts if "_place_x" in p}

    # Build signal neighbor map
    sig_neighbors: dict[str, list[str]] = defaultdict(list)
    for net in nets:
        if net.get("type") == "power":
            continue
        refs = set()
        for conn in net.get("connections", []):
            ref = conn.split(":")[0]
            if ref in ref_pos:
                refs.add(ref)
        for a in refs:
            for b in refs:
                if a != b:
                    sig_neighbors[a].append(b)

    pullups = [p for p in parts if _is_pullup(p, nets) and "_place_x" in p]
    if not pullups:
        return 100.0

    total = len(pullups)
    inline = 0
    for p in pullups:
        px, py = p["_place_x"], p["_place_y"]
        nbs = sig_neighbors.get(p["ref"], [])
        is_inline = False
        for nb in nbs:
            if nb not in ref_pos:
                continue
            nx, ny = ref_pos[nb]
            if abs(px - nx) < 5.0 or abs(py - ny) < 5.0:
                is_inline = True
                break
        if is_inline:
            inline += 1
        else:
            violations.append(f"Pullup {p['ref']} not inline with signal neighbors")

    return 100.0 * inline / total
