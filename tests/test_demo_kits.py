# SPDX-License-Identifier: GPL-3.0-or-later
"""Die Demo-Bausatz-Registry ist die Single Source der Zuordnung Projekt→Skills
(Demo-Menü + Bausatzsystem). Diese Tests halten sie wohlgeformt und — der Kern —
stellen sicher, dass **jeder** der 34 Super-Skills in mindestens einem Bausatz
real vorkommt. So kann kein Skill aus dem Schaufenster fallen."""

from __future__ import annotations

import json

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


# --- Menü-Abschnitte + Anzeige-Helfer (Hover-Vorschau) ---------------------


def test_sections_partition_all_kits():
    section_keys = {s for s, _ in dk.SECTIONS}
    seen = []
    for sect_key in section_keys:
        seen.extend(dk.by_section(sect_key))
    # jeder Bausatz genau einem (bekannten) Abschnitt zugeordnet
    assert {k.key for k in seen} == {k.key for k in dk.all_kits()}
    assert all(k.section in section_keys for k in dk.all_kits())


def test_by_section_keeps_registry_order():
    for sect_key, _ in dk.SECTIONS:
        kits = dk.by_section(sect_key)
        idx = [dk.all_kits().index(k) for k in kits]
        assert idx == sorted(idx)


@pytest.mark.parametrize("kit", dk.all_kits(), ids=lambda k: k.key)
def test_pipeline_items_match_pipeline(kit):
    items = dk.pipeline_items(kit)
    assert len(items) == len(kit.pipeline)
    for (label, why), fk in zip(items, kit.pipeline):
        assert label == sf.get(fk).label  # Anzeige-Label aus superfeatures
        assert why == kit.rationale[fk]


@pytest.mark.parametrize("kit", dk.all_kits(), ids=lambda k: k.key)
def test_hover_preview_shows_count_and_all_skill_labels(kit):
    prev = dk.hover_preview(kit)
    assert f"{len(kit.pipeline)} Skills" in prev
    assert kit.summary in prev
    for fk in kit.pipeline:
        assert sf.get(fk).label in prev  # jede beteiligte Skill sichtbar


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


def test_describe_is_numbered_and_nonempty():
    text = dr.describe("led_ring")
    assert text.startswith("0.")
    assert "⊙ LED-Ring" in text


# --- die 10 Schaltplan-Specs (aus freien Referenz-Topologien) --------------


def _load_spec(kit):
    with open(dr.spec_path(kit), encoding="utf-8") as fh:
        return json.load(fh)


def test_every_kit_has_a_spec_file():
    for kit in dk.all_kits():
        assert dr.spec_exists(kit), f"{kit.key}: Spec fehlt ({dr.spec_path(kit)})"


@pytest.mark.parametrize("kit", dk.all_kits(), ids=lambda k: k.key)
def test_spec_validates(kit):
    # Rein (kein KiCad nötig): jede Spec ist eine gültige Generator-Eingabe.
    from kicad_mcp.generators.validator import validate_all
    spec = _load_spec(kit)
    errs = validate_all(spec["parts"], spec["nets"], spec.get("board"))
    assert errs == [], f"{kit.key}: {errs[:6]}"


@pytest.mark.parametrize("kit", dk.all_kits(), ids=lambda k: k.key)
def test_spec_is_minimal(kit):
    # „möglichst minimal": kleine, sekundenschnelle Boards. Obergrenze 20 —
    # eine datenblatt-korrekte Schaltung (ac_dc-Flyback mit BP-Cap + RCD-Klemme;
    # 74HC595-Breakout mit echtem 16-Pin) hat legitim mehr Teile als ein
    # 0805-Blinker, bleibt aber klein.
    spec = _load_spec(kit)
    assert 3 <= len(spec["parts"]) <= 20, f"{kit.key}: {len(spec['parts'])} Teile"
    assert spec.get("description")  # nennt Zweck + Referenzquelle


def _has_fp_libs():
    from kicad_mcp.generators.footprint_lib import _find_fp_dir
    return _find_fp_dir() is not None


@pytest.mark.skipif(not _has_fp_libs(),
                    reason="KiCad-Footprint-Libs nötig (echte Generierung)")
@pytest.mark.parametrize("kit", dk.all_kits(), ids=lambda k: k.key)
def test_spec_builds_schematic_and_board(kit):
    # Gegen echtes KiCad: jede Spec baut Schaltplan + Board mit ECHTEN
    # Footprints (kein Platzhalter-Board).
    from kicad_mcp.generators.schematic.builder import build_schematic
    from kicad_mcp.generators.pcb.builder import build_pcb
    spec = _load_spec(kit)
    sch = build_schematic(spec["parts"], spec["nets"], kit.key)
    pcb = build_pcb(spec["parts"], spec["nets"], spec.get("board"), kit.key)
    assert sch.count("(symbol ") >= len(spec["parts"])
    assert pcb.count("(footprint ") >= len(spec["parts"])


def test_placeholder_warning_logged_once_across_rebuilds(caplog):
    # Feld-Report (0.27.1-Demo): der Layout-Optimierer baut den Schaltplan
    # dutzendfach — EIN fehlendes Symbol (Flyback_Trafo) flutete das
    # Demo-Transkript mit 73 identischen WARNINGs. Dedupe je lib_id.
    from kicad_mcp.generators.schematic import builder
    builder._WARNED_PLACEHOLDERS.clear()
    parts = [{"ref": "T1", "name": "No_Such_Symbol_XYZ", "value": "EE16",
              "pins": [{"num": "1", "name": "P1"}, {"num": "2", "name": "P2"}]}]
    nets = [{"name": "N1", "type": "signal", "connections": ["T1:1", "T1:2"]}]
    with caplog.at_level("WARNING", logger="kicad_mcp.generators.schematic.builder"):
        for _ in range(3):
            builder.build_schematic(
                [dict(p, pins=[dict(x) for x in p["pins"]]) for p in parts],
                [dict(n) for n in nets], "warn_dedupe")
    hits = [r for r in caplog.records
            if "not found in KiCad libraries" in r.message]
    assert len(hits) == 1, [r.message for r in hits]


# ── Reife-Stufen (Phase 1 der Roadmap): eine Quelle, ehrliche Labels ─────────

@pytest.mark.parametrize("kit", dk.all_kits(), ids=lambda k: k.key)
def test_stage_is_derivable_and_badged(kit):
    # Jeder Kit hat eine wohldefinierte Stufe + ein Menü-Symbol (kein Crash,
    # auch wenn der Nutzer später Flags/JSON ändert).
    st = dk.stage(kit)
    assert st in (dk.STAGE_PRIME, dk.STAGE_VERIFIED, dk.STAGE_DRAFT)
    assert dk.stage_badge(kit) in ("⭐", "✅", "🔬")
    # Zwei-Achsen-Definition ist konsistent:
    if kit.board_clean and kit.verified:
        assert st == dk.STAGE_PRIME
    elif kit.board_clean or kit.verified:
        assert st == dk.STAGE_VERIFIED
    else:
        assert st == dk.STAGE_DRAFT


def test_default_stage_is_draft():
    # Ein frisch angelegter Kit ohne gesetzte Flags gilt als Draft — nie
    # versehentlich als fertig verkauft (Robustheit gegen neue/geänderte Kits).
    blank = dk.DemoKit(key="x", title="x", summary="x", section="analog",
                       spec_file="x.json", pipeline=("thermal",),
                       rationale={"thermal": "x"})
    assert blank.board_clean is False and blank.verified is False
    assert dk.stage(blank) == dk.STAGE_DRAFT


def test_board_clean_keys_are_valid_and_nonempty():
    keys = dk.board_clean_keys()
    assert keys, "kein Kit als board_clean markiert?"
    valid = {k.key for k in dk.all_kits()}
    assert set(keys) <= valid


def test_reference_pcb_files_ship_with_project():
    # Kits mit hinterlegter Referenz-Platine (Hand-Route, dichte Fine-Pitch-
    # Boards) MÜSSEN die .kicad_pcb UND die gleichnamige .kicad_pro liefern —
    # sonst liest kicad-cli die Fertigungsregeln nicht und das DRC-Gate
    # (test_pcb_placement) fiele auf Datei-fehlt. Läuft ohne kicad-cli, deckt
    # also auch den gemockten CI-Job ab.
    seen = 0
    for kit in dk.all_kits():
        if not kit.reference_pcb:
            continue
        seen += 1
        pcb = dk.reference_pcb_path(kit)
        assert pcb is not None and pcb.is_file(), \
            f"{kit.key}: Referenz-Platine fehlt ({pcb})"
        pro = pcb.with_suffix(".kicad_pro")
        assert pro.is_file(), \
            f"{kit.key}: .kicad_pro fehlt neben der Referenz-Platine ({pro})"
        # Referenz-Platine impliziert board_clean (die gelieferte saubere
        # Platine IST diese Datei) — sonst ist das Label inkonsistent.
        assert kit.board_clean, \
            f"{kit.key}: reference_pcb gesetzt, aber nicht board_clean"
    assert seen, "kein Kit mit reference_pcb — Test müsste entfernt werden?"


def test_recipe_kits_are_verified():
    # Wer als Circuit-Block+Rezept modelliert ist (Verschmelzung 0.27.0), hat
    # eine datenblatt-geprüfte Schaltung → muss verified sein. Hält Label und
    # Quelle synchron, auch wenn später Rezepte dazukommen.
    import glob
    import os
    from kicad_mcp.generators.circuit_block.kit_compose import RECIPES_DIR
    recipe_keys = {os.path.splitext(os.path.basename(p))[0]
                   for p in glob.glob(os.path.join(RECIPES_DIR, "*.json"))}
    for key in recipe_keys:
        kit = dk.get(key)
        assert kit is not None, f"Rezept ohne Kit: {key}"
        assert kit.verified, (
            f"{key}: hat ein Rezept (datenblatt-geprüfter Block), ist aber "
            "nicht verified — Flag setzen")
