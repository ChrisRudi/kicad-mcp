# SPDX-License-Identifier: GPL-3.0-or-later
"""Die Demo-Bausatz-Registry ist die Single Source der Zuordnung Projekt→Skills
(Demo-Menü + Bausatzsystem). Diese Tests halten sie wohlgeformt und — der Kern —
stellen sicher, dass **jeder** der 34 Super-Skills in mindestens einem Bausatz
real vorkommt. So kann kein Skill aus dem Schaufenster fallen."""

from __future__ import annotations

import pytest

from plugin import demo_kits as dk
from plugin import demo_runner as dr
from plugin import superfeatures as sf


def test_validate_passes():
    # Wirft bei jeder Inkonsistenz (unbekannter Key, rationale-Drift, Lücke).
    dk.validate()


def test_keys_are_unique():
    keys = [k.key for k in dk.all_kits()]
    assert len(keys) == len(set(keys))


def test_audio_amp_leads():
    # Der Audioverstärker ist das Nutzer-Beispiel → führt das Menü.
    assert dk.all_kits()[0].key == "audio_amp"


def test_roughly_ten_kits():
    assert 8 <= len(dk.all_kits()) <= 12


@pytest.mark.parametrize("kit", dk.all_kits(), ids=lambda k: k.key)
def test_every_kit_is_fully_specified(kit):
    assert kit.key and kit.key.islower()
    assert kit.title and kit.summary and kit.spec_file.endswith(".json")
    # „ca. 5 Skills" — 4 bis 6 pro Bausatz.
    assert 4 <= len(kit.pipeline) <= 6, f"{kit.key}: {len(kit.pipeline)} Skills"
    # rationale deckt die Pipeline exakt ab, jede Begründung ist echt.
    assert set(kit.rationale) == set(kit.pipeline)
    assert all(len(kit.rationale[fk]) >= 20 for fk in kit.pipeline)


@pytest.mark.parametrize("kit", dk.all_kits(), ids=lambda k: k.key)
def test_pipeline_keys_are_real_superfeatures(kit):
    valid = {f.key for f in sf.all_features()}
    assert all(fk in valid for fk in kit.pipeline)


def test_all_34_super_skills_are_covered():
    # DER Wächter: die 10 Projekte decken zusammen jeden Super-Skill ab.
    assert dk.uncovered_skills() == frozenset(), (
        f"Nicht abgedeckte Super-Skills: {sorted(dk.uncovered_skills())} — "
        "jedem Skill ein Zuhause geben oder eine Pipeline erweitern.")
    assert dk.covered_skills() == {f.key for f in sf.all_features()}


def test_get_resolves_and_missing_is_none():
    assert dk.get("audio_amp") is not None
    assert dk.get("gibtsnicht") is None


# --- Runner-Gerüst ---------------------------------------------------------


def test_plan_starts_with_build_then_one_step_per_skill():
    kit = dk.get("audio_amp")
    steps = dr.plan("audio_amp")
    assert len(steps) == 1 + len(kit.pipeline)
    assert steps[0].kind == dr.STEP_BUILD
    skill_steps = steps[1:]
    assert [s.feature_key for s in skill_steps] == list(kit.pipeline)
    assert all(s.kind == dr.STEP_SKILL for s in skill_steps)


def test_plan_skill_steps_carry_the_canonical_prompt():
    for step in dr.plan("usb_sensor_hub"):
        if step.kind == dr.STEP_SKILL:
            feat = sf.get(step.feature_key)
            assert step.prompt == feat.prompt and step.prompt
            assert step.detail  # die Hier-Begründung


def test_plan_unknown_kit_raises():
    with pytest.raises(KeyError):
        dr.plan("gibtsnicht")


def test_spec_not_built_yet_is_flagged_in_build_step():
    # Scope: die Schaltplan-Specs kommen später — der Build-Schritt sagt das
    # ehrlich, statt eine fertige Spec vorzutäuschen.
    for kit in dk.all_kits():
        if not dr.spec_exists(kit):
            build = dr.plan(kit.key)[0]
            assert "noch nicht gebaut" in build.detail


def test_describe_is_numbered_and_nonempty():
    text = dr.describe("led_ring")
    assert text.startswith("0.")
    assert "⊙ LED-Ring" in text
