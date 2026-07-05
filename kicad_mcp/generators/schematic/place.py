# SPDX-License-Identifier: GPL-3.0-or-later
"""
Schematic Placement Pipeline — Net-Chain approach with Template-Dominanz.

Clear pipeline:
  1. Classify all parts
  2. Template-Placement (dominant — places everything it recognizes)
  3. Place connectors at sheet edges
  4. Place main ICs center-left
  5. Place bypass caps near their IC
  6. Net-Chain walk: place passives inline along signal nets
  7. Place transistors, indicators, remaining
  8. Smart Rotation (pin-aware)
  9. Force-Directed Refinement
 10. Overlap Resolution + final grid snap

Callers:
  - auto_place.py          (re-exports place_schematic as auto_place)
  - schematic_builder.py   (via auto_place)
  - generation_tools.py    (via schematic_builder → auto_place)

Extracted from auto_place.py.
"""

from collections import defaultdict
import logging

from ..common.classify import (
    _assign_rotation,
    _classify,
)
from ..common.constants import (
    INLINE_GAP,
    OVERLAP_PASSES,
    SHEET_H,
    VERTICAL_GAP,
)
from ..common.geometry import _resolve_overlaps, _snap

logger = logging.getLogger(__name__)


def _is_analog_signal_chain(parts: list[dict]) -> bool:
    """Detect simple analog amplifier-style schematics that need more spacing."""
    has_input = False
    has_output = False
    has_signal_ic = False

    for part in parts:
        group = part.get("_group", "")
        if group == "connector_in":
            has_input = True
        elif group == "connector_out":
            has_output = True

        if part.get("ref", "").startswith(("U", "IC")):
            token = f'{part.get("name", "")} {part.get("value", "")}'.upper()
            if any(key in token for key in (
                "LM358", "LM324", "TL07", "TL08", "NE5532", "OPA", "OPAMP",
                "MCP60", "LF353", "COMPARATOR",
            )):
                has_signal_ic = True

    return has_input and has_output and has_signal_ic


def _build_signal_ref_graph(nets: list[dict]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for net in nets:
        if net.get("type") == "power":
            continue
        refs = [conn.split(":")[0] for conn in net.get("connections", []) if ":" in conn]
        refs = [ref for ref in refs if ref]
        for i, ref_a in enumerate(refs):
            for ref_b in refs[i + 1:]:
                graph[ref_a].add(ref_b)
                graph[ref_b].add(ref_a)
    return graph


def _shortest_ref_path(
    graph: dict[str, set[str]], start_refs: set[str], goal_refs: set[str]
) -> list[str]:
    if not start_refs or not goal_refs:
        return []

    queue: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for start in start_refs:
        queue.append((start, [start]))
        seen.add(start)

    while queue:
        ref, path = queue.pop(0)
        if ref in goal_refs:
            return path
        for nb in sorted(graph.get(ref, ())):
            if nb not in seen:
                seen.add(nb)
                queue.append((nb, path + [nb]))
    return []


# ── Main placement pipeline ─────────────────────────────────────────────────

def place_schematic(parts: list[dict], nets: list[dict]) -> list[dict]:
    """Place components using template-first, then net-chain approach.

    Pipeline:
      0. Template-Placement (dominant)
      1. Classify and assign rotations
      2. Place connectors at sheet edges
      3. Place main ICs center-left
      3b. Place bypass caps near IC
      4. Net-Chain walk (signal nets from IC outward)
      4b. Multi-level net-chain (up to 3 levels deep)
      7. Place transistors
      8. Place indicators (LEDs)
      9. Place remaining unplaced components
      9.5. Smart Rotation (pin-aware)
     10. Force-Directed Refinement
     11. Overlap Resolution
     12. Final grid snap
    """
    ref_to_part = {p["ref"]: p for p in parts}

    # 1. Classify and assign rotations (preserve pre-set _group)
    for part in parts:
        if "_group" not in part:
            part["_group"] = _classify(part)
        part["_rotation"] = _assign_rotation(part)

    analog_mode = _is_analog_signal_chain(parts)
    analog_factor = 1.25 if analog_mode else 1.0
    inline_gap = INLINE_GAP * analog_factor
    vertical_gap = VERTICAL_GAP * analog_factor

    # 0. Phase 0: Template-based placement (before Net-Chain)
    placed_refs = _apply_template_placement(parts, nets)
    placed_refs.copy()

    # Pre-place parts with user hints (before solver or incremental)
    for part in parts:
        if "hint_sch_x" in part and "_place_x" not in part:
            from ..common.geometry import _snap as _snap_h
            part["_place_x"] = _snap_h(part["hint_sch_x"])
            part["_place_y"] = _snap_h(part.get("hint_sch_y", SHEET_H / 2))
            placed_refs.add(part["ref"])

    # ── 6.4: Try constraint solver first, fall back to incremental ─────
    solver_used = False
    try:
        from .constraint_solver import solve_placement
        solver_result = solve_placement(parts, nets, placed_refs)
        if solver_result:
            for ref, (x, y, rot) in solver_result.items():
                p = ref_to_part.get(ref)
                if p and ref not in placed_refs:
                    p["_place_x"] = x
                    p["_place_y"] = y
                    if rot:
                        p["_rotation"] = rot
                    placed_refs.add(ref)
            solver_used = True
            logger.info("Constraint solver placed %d parts", len(solver_result))
    except Exception:
        pass

    if not solver_used:
        # Incremental place+score: like a human, one part at a time
        from .defrag_place import incremental_place_and_score
        placed_refs |= incremental_place_and_score(parts, nets, placed_refs, inline_gap, vertical_gap)

    # Ensure all parts have placement coordinates
    placed_refs |= {p["ref"] for p in parts if "_place_x" in p}

    # 11. Resolve overlaps — sanft (kleine Verschiebungen), dann garantiert.
    for _ in range(OVERLAP_PASSES):
        if not _resolve_overlaps(parts):
            break
    # 11b. Harte Garantie: kein Bauteil liegt mehr über einem anderen (der
    # sanfte Schritt kann bei riesigen Symbolen oszillieren).
    from ..common.geometry import force_no_overlap
    force_no_overlap(parts)

    # 12. Final grid snap
    for part in parts:
        if "_place_x" in part:
            part["_place_x"] = _snap(part["_place_x"])
            part["_place_y"] = _snap(part["_place_y"])

    return parts


# ── Template-based placement ────────────────────────────────────────────────

def _apply_template_placement(parts: list[dict], nets: list[dict]) -> set[str]:
    """Try to place components using templates. Returns set of placed refs."""
    try:
        from ..template_matcher import match_templates
    except Exception:
        return set()

    matches = match_templates(parts, nets)
    if not matches:
        return set()

    best = matches[0]
    if best.confidence < 0.65:
        logger.debug("Best template match %s too weak (%.2f)", best.template_id, best.confidence)
        return set()
    logger.info(
        "Template match: %s (confidence=%.2f, %d components)",
        best.template_id, best.confidence, len(best.placement),
    )

    placed = set()
    ref_to_part = {p["ref"]: p for p in parts}

    for ref, (x, y, rotation) in best.placement.items():
        part = ref_to_part.get(ref)
        if part:
            part["_place_x"] = x
            part["_place_y"] = y
            if rotation:
                part["_rotation"] = rotation
            placed.add(ref)

    logger.info("Template placed %d/%d components", len(placed), len(parts))
    return placed


# ── Pin-Aware Rotation + Routability (from schematic.rotate) ────────────────


