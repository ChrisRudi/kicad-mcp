# SPDX-License-Identifier: GPL-3.0-or-later
"""Demo-Kits aus Circuit-Blocks komponieren — EINE Quelle für beide Welten.

Verschmilzt die zwei bisherigen Orte (Nutzer-Auftrag): die datenblatt-
geprüfte IC-Applikationsschaltung lebt genau EINMAL als Circuit-Block
(``resources/data/circuit_blocks/<name>.json``, v1.1-Schema plus
``connect``/``part_name``-Zusatzfelder); ein Demo-Kit ist nur noch ein
REZEPT (``resources/data/demo_kits/recipes/<key>.json``): welcher Block,
welche Referenzen, welche Stecker drumherum, welche Netz-Reihenfolge.

Die eingecheckten ``demo_kits/<key>.json`` sind BUILD-ARTEFAKTE dieses
Composers (Muster wie der Plugin-Bundle-Spiegel): ``scripts/
compose_demo_kits.py`` regeneriert sie, ``tests/test_kit_compose.py`` ist
der Drift-Wächter — Block/Rezept ändern, Script laufen lassen, fertig.
Alle Konsumenten (Demo-Knopf, Gates, Tests) lesen weiter die Kit-JSONs.

Rezept-Format (bewusst klein):
    project_name, description, board
    blocks:      [{id, block, ic_ref, refs: {peripheral-id → Kit-Ref}}]
    extra_parts: vollständige Part-Dicts (Stecker, Testpunkte)
    nets:        [{name, type, connections: ["J1:1", "@<block-id>", …]}]
                 — ``@id`` expandiert zu den Anschlüssen dieses Blocks an
                 diesem Netz (IC-Pins in Block-Pin-Reihenfolge, dann
                 Peripherie in Block-Reihenfolge). Deterministisch.
"""

from __future__ import annotations

import glob
import json
import os

_DATA = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "resources", "data")
BLOCKS_DIR = os.path.join(_DATA, "circuit_blocks")
KITS_DIR = os.path.join(_DATA, "demo_kits")
RECIPES_DIR = os.path.join(KITS_DIR, "recipes")


def load_block(name: str) -> dict:
    """Block laden: nackter Name → ``circuit_blocks/<name>.json``; Pfade
    (absolut/relativ mit Trenner) werden direkt gelesen."""
    path = name
    if not os.path.sep in name and not name.endswith(".json"):
        path = os.path.join(BLOCKS_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _block_parts(block: dict, ic_ref: str, refs: dict) -> list[dict]:
    """Alle Bauteile eines Blocks als Kit-Part-Dicts (Referenzen gemappt)."""
    parts = [{
        "ref": ic_ref,
        "name": block["chip"],
        "value": block.get("value", block["chip"]),
        "footprint": block["kicad_footprint"],
        "pins": [{"num": str(p["num"]), "name": p["name"],
                  "type": p.get("type", "passive")}
                 for p in block["pins"]],
        "_pcb_group": block.get("ic_pcb_group", "main_ic"),
    }]
    for per in block.get("peripherals", []):
        ref = refs.get(per["id"])
        if not ref:
            raise ValueError(
                f"Rezept mappt Peripherie '{per['id']}' auf keine Referenz")
        pin_names = per.get("pin_names") or ["P1", "P2"]
        parts.append({
            "ref": ref,
            "name": per["part_name"],
            "value": per["value"],
            "footprint": per["kicad_footprint"],
            "pins": [{"num": str(i + 1), "name": pin_names[i],
                      "type": "passive"} for i in range(len(pin_names))],
            "_pcb_group": per.get("pcb_group", "passive"),
        })
    return parts


def _block_net_members(block: dict, ic_ref: str,
                       refs: dict) -> dict[str, list[str]]:
    """Je Block-Netzname die Anschlüsse ("REF:pad") — IC-Pins zuerst
    (Block-Pin-Reihenfolge), dann Peripherie (Block-Reihenfolge)."""
    members: dict[str, list[str]] = {}
    for p in block["pins"]:
        net = p.get("net", p["name"])   # Default: Netz heißt wie der Pin
        if not net:                      # "" = bewusst unbeschaltet (LM386 1/8)
            continue
        members.setdefault(net, []).append(f"{ic_ref}:{p['num']}")
    for per in block.get("peripherals", []):
        ref = refs[per["id"]]
        for pad, net in per["connect"].items():
            members.setdefault(net, []).append(f"{ref}:{pad}")
    return members


def compose_kit(recipe: dict) -> dict:
    """Ein Rezept zu einem vollständigen Kit-Spec-Dict expandieren."""
    parts: list[dict] = []
    members_by_block: dict[str, dict[str, list[str]]] = {}
    for binst in recipe.get("blocks", []):
        block = load_block(binst["block"])
        parts.extend(_block_parts(block, binst["ic_ref"], binst["refs"]))
        members_by_block[binst["id"]] = _block_net_members(
            block, binst["ic_ref"], binst["refs"])
    parts.extend(recipe.get("extra_parts", []))

    nets: list[dict] = []
    for net in recipe.get("nets", []):
        conns: list[str] = []
        for entry in net["connections"]:
            if entry.startswith("@"):
                bid = entry[1:]
                conns.extend(
                    members_by_block.get(bid, {}).get(net["name"], []))
            else:
                conns.append(entry)
        nets.append({"name": net["name"],
                     "type": net.get("type", "signal"),
                     "connections": conns})
    return {
        "project_name": recipe["project_name"],
        "description": recipe["description"],
        "board": recipe["board"],
        "parts": parts,
        "nets": nets,
    }


def compose_all(recipes_dir: str | None = None,
                kits_dir: str | None = None) -> list[str]:
    """Alle Rezepte zu Kit-JSONs ausschreiben; Rückgabe: Kit-Keys."""
    recipes_dir = recipes_dir or RECIPES_DIR
    kits_dir = kits_dir or KITS_DIR
    written = []
    for path in sorted(glob.glob(os.path.join(recipes_dir, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            recipe = json.load(fh)
        spec = compose_kit(recipe)
        key = recipe["project_name"]
        out = os.path.join(kits_dir, f"{key}.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        written.append(key)
    return written
