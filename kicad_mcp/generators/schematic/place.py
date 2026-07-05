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
    except Exception as exc:
        logger.debug("Constraint-Solver fehlgeschlagen — inkrementelle Platzierung: %s", exc)

    if not solver_used:
        # Incremental place+score: like a human, one part at a time
        from .defrag_place import incremental_place_and_score
        placed_refs |= incremental_place_and_score(parts, nets, placed_refs, inline_gap, vertical_gap)

    # Ensure all parts have placement coordinates
    placed_refs |= {p["ref"] for p in parts if "_place_x" in p}

    # 11+12. Post-Placement-Regeln listen-getrieben durchsetzen (Überlappung,
    # Mindest-Draht, Pin-Richtung, Grid-Snap) — die Reihenfolge/Auswahl steht im
    # wartbaren Regel-Set ``layout_rules``, nicht hier fest verdrahtet.
    _enforce_layout_rules(parts, nets)
    return parts


# ── Listen-getriebene Regel-Durchsetzung ────────────────────────────────────

def _orient_power_passives(parts: list[dict], nets: list[dict]) -> None:
    """Nutzer-Regel: Geht ein R, C oder L an GND/VCC (Power-Netz), steht er
    SENKRECHT (rot 0/180 — Pins oben/unten), das Power-Symbol direkt drüber
    bzw. drunter. So zeichnet es jedes Profi-Schaltbild (Pull-up, Abblock-C).
    Zusätzlich ``_rot_locked``, damit der Layout-Optimierer die Konvention
    nicht wieder wegdreht."""
    power_pins: set[str] = set()
    for net in nets:
        if net.get("type") == "power":
            for conn in net.get("connections", []):
                power_pins.add(conn.split(":")[0])
    for p in parts:
        ref = p.get("ref", "")
        prefix = "".join(c for c in ref if c.isalpha())
        if prefix in ("R", "C", "L") and ref in power_pins \
                and len(p.get("pins", [])) == 2:
            if int(p.get("_rotation", 0)) in (90, 270):
                p["_rotation"] = 0
            p["_rot_locked"] = True


def _enforce_layout_rules(parts: list[dict], nets: list[dict]) -> None:
    """Setzt die Post-Placement-Regeln aus ``layout_rules`` durch — die Liste
    ist die Single Source der Reihenfolge/Auswahl, dieser Code nur der Motor.

    GEOMETRY-Regeln laufen in einer Fixpunkt-Schleife (bis kein Regel-Schritt
    mehr etwas bewegt — Abstand ↔ Überlappung wechselwirken), FINISH-Regeln
    genau einmal am Ende. Die Auswahl steuert das ``enforcer``-Feld der Regeln
    im Set; eine neue geometrische Regel = Eintrag mit passendem ``enforcer``,
    keine Pipeline-Chirurgie."""
    from ..common.geometry import force_no_overlap
    from . import layout_rules as rules

    # Power-Passives senkrecht stellen, BEVOR Abstand/Überlappung final laufen
    # (die Fixpunkt-Schleife räumt etwaige Dreh-Folgen gleich mit auf).
    _orient_power_passives(parts, nets)

    # sanfter Vorlauf (kleine Verschiebungen, bevor die harte Garantie greift)
    for _ in range(OVERLAP_PASSES):
        if not _resolve_overlaps(parts):
            break

    def _spacing() -> bool:
        # Regel ``generous_spacing``: ≥ 5 mm Draht UND kein Überlappen. Erst
        # Überlappung auflösen, dann Mindest-Draht ZULETZT — so endet eine
        # Runde nie auf einem frisch erzeugten <5-mm-Verstoß (Konvergenz).
        moved_ov = force_no_overlap(parts)
        moved_wire = _enforce_min_wire(parts, nets)
        return moved_ov or moved_wire

    def _grid_snap() -> bool:
        # Regel ``orthogonal_on_grid`` (FINISH-Teil): alles aufs Raster.
        moved = False
        for part in parts:
            if "_place_x" not in part:
                continue
            nx, ny = _snap(part["_place_x"]), _snap(part["_place_y"])
            if nx != part["_place_x"] or ny != part["_place_y"]:
                moved = True
            part["_place_x"], part["_place_y"] = nx, ny
        return moved

    # ``enforcer``-Name (im Regel-Set) → mechanische Funktion.
    enforcers = {"spacing": _spacing, "grid_snap": _grid_snap}

    geometry = [r for r in rules.by_phase(rules.GEOMETRY)
                if r.enforcer in enforcers]
    for _ in range(12):  # Fixpunkt: bis keine GEOMETRY-Regel mehr etwas bewegt
        changed = False
        for rule in geometry:
            if enforcers[rule.enforcer]():
                changed = True
        if not changed:
            break

    for rule in rules.by_phase(rules.FINISH):
        fn = enforcers.get(rule.enforcer)
        if fn:
            fn()


#: Minimale sichtbare Leitungslänge zwischen zwei verbundenen Bauteil-Pins (mm).
#: 2 Grid — nie Pin direkt an Pin ohne Draht.
MIN_WIRE_MM = 5.08


def _enforce_min_wire(parts: list[dict], nets: list[dict],
                      min_wire: float = MIN_WIRE_MM) -> bool:
    """Schiebe verbundene Signal-Pins auf ≥ ``min_wire`` auseinander (ein Pass).

    Nur SIGNAL-Netze (Power-Pins verbinden über Symbole, nicht Pin-an-Pin) und
    nur Paare auf VERSCHIEDENEN Bauteilen (zwei Pins desselben ICs sind durch
    die Symbol-Geometrie fixiert). Verschoben wird das „Blatt" (weniger
    Verbindungen) entlang der Pin-zu-Pin-Achse, sodass eine sichtbare Leitung
    entsteht. Gibt True zurück, solange etwas verschoben wurde — der Aufrufer
    wiederholt und garantiert dazwischen Überlappungsfreiheit."""
    import math
    from .route import _extract_pin_positions, _stub_direction
    from ..symbol_lib import resolve_lib_id

    # Bewegungsvektor je Pin-Austrittsrichtung: wir schieben das Blatt ENTGEGEN
    # der Richtung, in der sein Pin aus dem Körper austritt — so verläuft die
    # entstehende Leitung geradlinig entlang der Pin-Achse (Nutzer-Regel:
    # „die 5 mm schließen sich an die Austrittsrichtung der Anschlüsse an").
    _RETREAT = {"right": (-1.0, 0.0), "left": (1.0, 0.0),
                "up": (0.0, 1.0), "down": (0.0, -1.0)}

    placed = {p["ref"]: p for p in parts if "_place_x" in p}
    conn_count: dict[str, int] = defaultdict(int)
    for net in nets:
        for c in net.get("connections", []):
            conn_count[c.split(":", 1)[0]] += 1

    def _world(ref: str) -> dict:
        p = placed.get(ref)
        if not p:
            return {}
        loc = _extract_pin_positions(resolve_lib_id(p), p)
        return {k: (p["_place_x"] + v[0], p["_place_y"] + v[1])
                for k, v in loc.items()}

    wpos = {r: _world(r) for r in placed}

    def _xy(ref: str, pn: str):
        d = wpos.get(ref)
        return (d.get(pn) or d.get(str(pn))) if d else None

    moved = False
    for net in nets:
        if net.get("type") == "power":
            continue
        conns = net.get("connections", [])
        for i in range(len(conns)):
            ra, _, pa = conns[i].partition(":")
            a = _xy(ra, pa)
            if not a:
                continue
            for j in range(i + 1, len(conns)):
                rb, _, pb = conns[j].partition(":")
                if ra == rb:
                    continue  # gleiches Bauteil — nicht trennbar
                b = _xy(rb, pb)
                if not b:
                    continue
                dx, dy = b[0] - a[0], b[1] - a[1]
                dist = math.hypot(dx, dy)
                if dist >= min_wire - 0.01:
                    continue
                # das Blatt (weniger Verbindungen) weichen lassen
                leaf = rb if conn_count[rb] <= conn_count[ra] else ra
                leaf_pin = (rb, pb) if leaf == rb else (ra, pa)
                lp = placed[leaf]
                need = min_wire - dist
                # Primär: entlang der Pin-Austrittsrichtung des Blatts schieben,
                # damit die Leitung geradlinig aus dem Pin läuft.
                pw = _xy(*leaf_pin)
                mx, my = _RETREAT.get(
                    _stub_direction(pw[0], pw[1],
                                    lp["_place_x"], lp["_place_y"]), (0.0, 0.0))
                lp["_place_x"] = _snap(lp["_place_x"] + mx * need)
                lp["_place_y"] = _snap(lp["_place_y"] + my * need)
                wpos[leaf] = _world(leaf)
                # Fallback-Garantie: liegt der Pin danach immer noch zu nah
                # (Partner nicht auf der Achse), zusätzlich direkt auseinander.
                nb = _xy(*leaf_pin)
                ndist = math.hypot(nb[0] - a[0], nb[1] - a[1]) if leaf == rb \
                    else math.hypot(nb[0] - b[0], nb[1] - b[1])
                if ndist < min_wire - 0.01:
                    ox, oy = (a if leaf == rb else b)
                    vx, vy = nb[0] - ox, nb[1] - oy
                    vd = math.hypot(vx, vy) or 1.0
                    extra = min_wire - ndist
                    lp["_place_x"] = _snap(lp["_place_x"] + vx / vd * extra)
                    lp["_place_y"] = _snap(lp["_place_y"] + vy / vd * extra)
                    wpos[leaf] = _world(leaf)
                moved = True
    return moved


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


