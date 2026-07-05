# SPDX-License-Identifier: GPL-3.0-or-later
"""Das Schaltplan-Layout-Regel-Set (``generators/schematic/layout_rules.py``)
ist die Single Source der Konventionen. Diese Tests halten es wohlgeformt und
verankern, dass die Kern-Regeln (Überlappung, Mindest-Draht, Pin-Richtung, …)
darin stehen — damit die Liste nicht still verrottet."""

from __future__ import annotations

import pytest

from kicad_mcp.generators.schematic import layout_rules as lr


def test_validate_passes():
    lr.validate()


def test_keys_unique():
    keys = [r.key for r in lr.all_rules()]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("rule", lr.all_rules(), ids=lambda r: r.key)
def test_rule_is_fully_specified(rule):
    assert rule.title and rule.rule and rule.rationale and rule.derived_from
    assert rule.status in lr._VALID_STATUS
    # umgesetzte Regeln sagen, WO sie durchgesetzt werden; PLANNED darf leer sein
    if rule.status != lr.PLANNED:
        assert rule.enforced_in


def test_exactly_the_ten_derived_rules():
    keys = [r.key for r in lr.all_rules()]
    assert keys == [
        "signal_flow_ltr", "power_rails", "series_horizontal_shunt_vertical",
        "ic_in_signal_direction", "orthogonal_on_grid", "generous_spacing",
        "power_symbols_and_io_labels", "ref_value_stacked", "junctions_at_tees",
        "separate_supply_blocks",
    ]
    # die alten (erfundenen) Keys sind bewusst raus
    for old in ("no_overlap", "min_wire", "tight_cluster", "gnd_down_vcc_up",
                "pin_swap_passives", "grid_snap", "no_wire_through_parts"):
        assert old not in keys


def test_every_rule_names_its_reference():
    # Jede Regel ist aus einem echten Schaltbild abgeleitet — Beleg pflichtig.
    for r in lr.all_rules():
        assert r.derived_from, f"Regel '{r.key}' ohne Beleg (derived_from)"


def test_get_and_status_helpers():
    assert lr.get("generous_spacing") is not None
    assert lr.get("gibtsnicht") is None
    # alle Status-Buckets zusammen = alle Regeln
    total = sum(len(lr.by_status(s)) for s in lr._VALID_STATUS)
    assert total == len(lr.all_rules())


def test_phases_and_enforcers_are_assigned():
    r = {x.key: x for x in lr.all_rules()}
    assert r["generous_spacing"].phase == lr.GEOMETRY
    assert r["generous_spacing"].enforcer == "spacing"
    assert r["orthogonal_on_grid"].phase == lr.FINISH
    assert r["orthogonal_on_grid"].enforcer == "grid_snap"
    # die Struktur-Regeln sind (noch) intrinsisch/PLANNED, kein Enforcer
    assert r["series_horizontal_shunt_vertical"].phase == lr.PLACEMENT
    assert r["series_horizontal_shunt_vertical"].enforcer == ""


def test_by_phase_partitions_all_rules():
    total = sum(len(lr.by_phase(p)) for p in lr._VALID_PHASE)
    assert total == len(lr.all_rules())


def test_engine_is_driven_by_enforcer_field():
    # Der Motor existiert und die GEOMETRY/FINISH-Regeln nennen die Enforcer,
    # die er kennt (spacing, grid_snap).
    from kicad_mcp.generators.schematic import place
    assert hasattr(place, "_enforce_layout_rules")
    used = {x.enforcer for x in lr.all_rules() if x.enforcer}
    assert used <= {"spacing", "grid_snap"}
    assert "spacing" in used and "grid_snap" in used


def test_enforced_rules_point_at_real_code():
    # Für die maschinell durchgesetzten Kern-Regeln existiert die genannte
    # Funktion wirklich (Schutz gegen umbenannte/gelöschte Enforcement-Stellen).
    from kicad_mcp.generators.common import geometry
    from kicad_mcp.generators.schematic import place, route
    assert hasattr(geometry, "force_no_overlap")
    assert hasattr(geometry, "_resolve_overlaps")
    assert hasattr(place, "_enforce_min_wire")
    assert hasattr(place, "MIN_WIRE_MM") and place.MIN_WIRE_MM >= 5.0
    assert hasattr(route, "_place_power_symbol")
    assert hasattr(route, "_stub_direction")
