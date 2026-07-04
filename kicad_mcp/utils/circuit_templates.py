# SPDX-License-Identifier: GPL-3.0-or-later
"""Nutzer-Vorlagen-Speicher: „ich zeichne eine Schaltung, der MCP merkt sie sich".

KiCad 10 lässt uns keinen Schaltplan zeichnen (keine Eeschema-API — empirisch
bestätigt: leerer Befehlssatz). Also dreht dieser Speicher es um: Der Nutzer
zeichnet den Schaltplan selbst, wir LESEN ihn (`extract`/`_parse_netlist_to_spec`)
und legen ihn als benannte, wiederverwendbare Vorlage ab — Bauteile + Netze im
kompakten Spec-Format (dasselbe wie ``selftest_board.json``). Danach zünden die
vorhandenen Magien: Board generieren (`build_circuit_template`), als Block
einsetzen, oder der Template-Matcher schlägt sie beim nächsten Mal vor.

Getrennt vom gebündelten ``training/templates``-Ordner (der wird bei Updates
überschrieben) — Nutzer-Vorlagen liegen persistent im Nutzer-Zustandsverzeichnis
(``KICAD_MCP_TEMPLATE_DIR`` überschreibt). Pure/stdlib — headless testbar.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

TEMPLATE_DIR_ENV = "KICAD_MCP_TEMPLATE_DIR"


def template_dir() -> str:
    """Persistentes Nutzer-Vorlagen-Verzeichnis (Env-Override zuerst)."""
    override = os.environ.get(TEMPLATE_DIR_ENV, "").strip()
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "kicad-claude", "circuit_templates")
    return os.path.join(os.path.expanduser("~"), ".local", "state",
                        "kicad-claude", "circuit_templates")


def safe_name(name: str) -> str:
    """Vorlagen-Name → dateisicherer Slug (a-z0-9_-), damit kein Pfad-Trick
    greift und der Name eine stabile Datei ergibt."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")
    return slug.lower() or "unbenannt"


def template_path(name: str) -> str:
    return os.path.join(template_dir(), safe_name(name) + ".json")


def save(name: str, spec: dict) -> str:
    """Vorlage als JSON speichern; Pfad zurück. Legt das Verzeichnis an."""
    path = template_path(name)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = dict(spec)
    payload["name"] = name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def load(name: str) -> Optional[dict]:
    """Gespeicherte Vorlage laden, oder None (unbekannt/kaputt)."""
    try:
        with open(template_path(name), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def list_templates() -> list[dict[str, Any]]:
    """Alle gespeicherten Vorlagen mit Kurz-Metadaten (Name, Zählwerte)."""
    tdir = template_dir()
    out: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(tdir))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(tdir, fn), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        out.append({
            "name": data.get("name", fn[:-5]),
            "slug": fn[:-5],
            "description": data.get("description", ""),
            "components": len(data.get("components") or []),
            "nets": len(data.get("nets") or []),
        })
    return out


def to_compact(spec: dict) -> "tuple[list, list]":
    """Gespeicherte Vorlage → (parts, nets) für ``expand_netlist``/Generierung.

    ``components`` = ``[{ref,value,footprint}]``, ``nets`` = ``[{name, pins:
    ["REF.PIN"]}]`` (Netzlisten-Form) → kompakte Generator-Form: parts wie sie
    sind, nets mit ``connections: ["REF:PIN"]`` (Doppelpunkt statt Punkt)."""
    parts = list(spec.get("components") or [])
    nets = []
    for net in spec.get("nets") or []:
        conns = [str(p).replace(".", ":", 1) for p in net.get("pins", [])]
        entry = {"name": net.get("name", ""), "connections": conns}
        if net.get("type"):
            entry["type"] = net["type"]
        nets.append(entry)
    return parts, nets
