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
