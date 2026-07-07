# SPDX-License-Identifier: GPL-3.0-or-later
"""Drift-Wächter der Kit-Komposition (Muster: test_bundle_sync).

Die Demo-Kit-JSONs sind BUILD-ARTEFAKTE aus Circuit-Blocks + Rezepten
(eine Quelle, Nutzer-Auftrag „die zwei Orte verschmelzen"). Wer Block oder
Rezept ändert, lässt ``scripts/compose_demo_kits.py`` laufen — dieser Test
schlägt an, wenn eingechecktes Kit-JSON und Rekomposition auseinanderlaufen
(oder jemand das Artefakt direkt editiert hat).
"""

from __future__ import annotations

import glob
import json
import os

import pytest

from kicad_mcp.generators.circuit_block.kit_compose import (
    BLOCKS_DIR,
    KITS_DIR,
    RECIPES_DIR,
    compose_kit,
    load_block,
)

_RECIPES = sorted(glob.glob(os.path.join(RECIPES_DIR, "*.json")))


@pytest.mark.parametrize("recipe_path", _RECIPES,
                         ids=[os.path.splitext(os.path.basename(p))[0]
                              for p in _RECIPES])
def test_composed_kit_matches_checked_in_json(recipe_path):
    with open(recipe_path, encoding="utf-8") as fh:
        recipe = json.load(fh)
    composed = compose_kit(recipe)
    kit_path = os.path.join(KITS_DIR, f"{recipe['project_name']}.json")
    with open(kit_path, encoding="utf-8") as fh:
        checked_in = json.load(fh)
    assert composed == checked_in, (
        f"{recipe['project_name']}: Kit-JSON ist nicht die Rekomposition — "
        "Quelle sind Block+Rezept; scripts/compose_demo_kits.py laufen lassen")


def test_recipes_exist_for_composed_kits():
    assert _RECIPES, "keine Rezepte gefunden"


@pytest.mark.parametrize("recipe_path", _RECIPES,
                         ids=[os.path.splitext(os.path.basename(p))[0]
                              for p in _RECIPES])
def test_recipe_blocks_resolve_and_map_completely(recipe_path):
    # Jede Peripherie des Blocks muss im Rezept eine Referenz bekommen,
    # jeder connect-Eintrag ein bekanntes Netz des Rezepts treffen.
    with open(recipe_path, encoding="utf-8") as fh:
        recipe = json.load(fh)
    net_names = {n["name"] for n in recipe["nets"]}
    for binst in recipe["blocks"]:
        block = load_block(binst["block"])
        for per in block.get("peripherals", []):
            assert per["id"] in binst["refs"], (binst["block"], per["id"])
            for net in per["connect"].values():
                assert net in net_names, (per["id"], net)
        for pin in block["pins"]:
            net = pin.get("net", pin["name"])
            if net:
                assert net in net_names, (binst["block"], pin["name"], net)


def test_blocks_dir_is_the_single_home():
    # Ort verschmolzen: die Blocks liegen in den ausgelieferten Ressourcen
    # (examples/ ist nur noch Wegweiser + Doku).
    assert os.path.isdir(BLOCKS_DIR)
    assert glob.glob(os.path.join(BLOCKS_DIR, "*.json"))
