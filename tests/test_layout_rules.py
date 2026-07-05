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
    assert rule.title and rule.rule and rule.rationale
    assert rule.enforced_in  # jede Regel sagt, WO sie durchgesetzt wird
    assert rule.status in lr._VALID_STATUS


def test_core_rules_present():
    keys = {r.key for r in lr.all_rules()}
    for expected in ("no_overlap", "min_wire", "wire_along_pin_exit",
                     "gnd_down_vcc_up", "connectors_outermost", "no_labels",
                     "pin_swap_passives", "no_wire_through_parts"):
        assert expected in keys, f"Kern-Regel fehlt: {expected}"
    # Regel 1 (tight_cluster) und astar_route wurden bewusst entfernt/ersetzt.
    assert "tight_cluster" not in keys and "astar_route" not in keys


def test_get_and_status_helpers():
    assert lr.get("min_wire") is not None
    assert lr.get("gibtsnicht") is None
    # alle Status-Buckets zusammen = alle Regeln
    total = sum(len(lr.by_status(s)) for s in lr._VALID_STATUS)
    assert total == len(lr.all_rules())


def test_phases_are_assigned_as_expected():
    phase = {r.key: r.phase for r in lr.all_rules()}
    assert phase["no_overlap"] == lr.GEOMETRY
    assert phase["min_wire"] == lr.GEOMETRY
    assert phase["wire_along_pin_exit"] == lr.GEOMETRY
    assert phase["grid_snap"] == lr.FINISH
    # der Rest ist intrinsisch im Platzierer/Router
    assert phase["gnd_down_vcc_up"] == lr.PLACEMENT
    assert phase["pin_swap_passives"] == lr.PLACEMENT


def test_by_phase_partitions_all_rules():
    total = sum(len(lr.by_phase(p)) for p in lr._VALID_PHASE)
    assert total == len(lr.all_rules())


def test_engine_is_list_driven_over_geometry_phase():
    # Der Motor (place._enforce_layout_rules) existiert und die GEOMETRY-Regeln,
    # die einen eigenen Nachlauf brauchen, sind darin abgedeckt.
    from kicad_mcp.generators.schematic import place
    assert hasattr(place, "_enforce_layout_rules")
    geo = {r.key for r in lr.by_phase(lr.GEOMETRY)}
    # min_wire + no_overlap brauchen je einen Enforcer; wire_along_pin_exit ist
    # eine Facette von min_wire (kein eigener Schritt).
    assert {"no_overlap", "min_wire"} <= geo


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
