# SPDX-License-Identifier: GPL-3.0-or-later
"""The super-feature registry is the single source of truth for the roadmap
(GUI buttons + docs/superfeatures.md). These tests keep it well-formed so the
panel can render it blindly and no half-specified entry ships."""

from __future__ import annotations

import pytest

from plugin import superfeatures as sf


def test_registry_is_non_empty_and_untangle_leads():
    feats = sf.all_features()
    assert feats, "roadmap must not be empty"
    # 'Entwirren' is the current focus → it leads the list.
    assert feats[0].key == "untangle"


def test_scoped_untangle_is_gone():
    # Selection scoping is the GLOBAL contract of every button (no selection =
    # whole board, selection = only the marked parts) — a separate "Auswahl
    # entwirren" entry would suggest the others can't do it.
    assert sf.get("scoped_untangle") is None


def test_keys_are_unique():
    keys = [f.key for f in sf.all_features()]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("feat", sf.all_features(), ids=lambda f: f.key)
def test_every_feature_is_fully_specified(feat):
    # Nothing half-written may ship — the GUI renders these verbatim.
    assert feat.key and feat.key.islower()
    assert feat.label and feat.name
    assert feat.status in sf._VALID_STATUS
    assert len(feat.tooltip) >= 20, "tooltip must actually explain the feature"
    assert len(feat.moat) >= 10, "each super-feature states why KiCad can't do it"


def test_all_features_are_selection_aware():
    # Cross-cutting contract: every super-feature can act on the KiCad selection,
    # not only board-wide.
    assert all(f.selection_aware for f in sf.all_features())


def test_by_status_partitions_the_registry():
    shipped = sf.by_status(sf.SHIPPED)
    soon = sf.by_status(sf.SOON)
    assert len(shipped) + len(soon) == len(sf.all_features())


def test_get_resolves_and_missing_is_none():
    assert sf.get("untangle") is not None
    assert sf.get("does-not-exist") is None


# --- shipped features: the button dispatches a real, well-formed prompt --------

# The features whose backing MCP tools have shipped — keep in sync when the
# next one goes live. Value = the tool name its click-prompt must invoke.
SHIPPED_TOOL = {
    "untangle": "evaluate_layout",
    "semantic_erc": "audit_design",
    "bus_radar": "list_bus_members",
    "test_points": "audit_test_points",
    "bom_consolidate": "consolidate_bom",
    "preferred_parts": "suggest_preferred_parts",
    "via_cost": "via_promote",
    "sketch_conductor": "ipc_markup_to_tracks",
    "datasheet_diff": "review_ic_against_datasheet",
    "explain_board": "analyze_pcb_nets",
    "polar_board": "polar_grid",
    "sketch_layer": "ipc_list_markers",
}


def test_shipped_set_matches_the_delivered_tools():
    shipped = {f.key for f in sf.by_status(sf.SHIPPED)}
    assert shipped == set(SHIPPED_TOOL), (
        "SHIPPED drifted: every shipped feature needs a delivered backing "
        "tool (and a prompt) — update SHIPPED_TOOL alongside superfeatures.py")


@pytest.mark.parametrize("feat", sf.by_status(sf.SHIPPED), ids=lambda f: f.key)
def test_shipped_prompt_names_its_tool_and_respects_the_rules(feat):
    # A shipped button must dispatch a real instruction ...
    assert len(feat.prompt) >= 80, "shipped feature needs a canonical prompt"
    # ... that names its backing tool (no guessing in the agent) ...
    assert SHIPPED_TOOL[feat.key] in feat.prompt
    # ... and honours the anti-toolcall-explosion rule: it states the
    # no-render prohibition explicitly ("Kein/kein pcb_render").
    assert "ein pcb_render" in feat.prompt


@pytest.mark.parametrize("feat", sf.by_status(sf.SOON), ids=lambda f: f.key)
def test_soon_features_carry_no_prompt(feat):
    # A SOON button prints the pitch — a leftover prompt would suggest it is
    # wired when it is not.
    assert feat.prompt == ""
