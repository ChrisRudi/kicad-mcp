# SPDX-License-Identifier: GPL-3.0-or-later
"""Demo-Runner-Gerüst — löst einen Bausatz zu einer geordneten Schritt-Liste auf.

Der Runner ist die Brücke zwischen der Bausatz-Registry (``demo_kits.py``, das
*was*) und der Ausführung (das *wie*): er baut aus einem gewählten Bausatz einen
deterministischen **Plan** — erst „Schaltplan anlegen", dann je Super-Skill ein
Schritt mit dem kanonischen Prompt aus ``superfeatures.py`` und der Begründung
aus der Registry.

Bewusst rein und ausführungsfrei (planend), damit es headless unit-getestet
wird. Die eigentliche Ausführung (Prompt an den Chat-Bridge dispatchen, auf die
Antwort warten, nächster Schritt) und das GUI-Dropdown hängen sich später an
``plan()`` — sie fügen keine neue Logik hinzu, sie *fahren* den Plan ab.

Offen (bewusst, laut Scope „erst Registry+Gerüst, ohne Schaltpläne"): die
``.kicad_sch``-Spec-JSONs unter ``kicad_mcp/resources/data/demo_kits/`` gibt es
noch nicht. ``spec_path()``/``spec_exists()`` zeigen den erwarteten Ort; der
Build-Schritt im Plan markiert ehrlich, ob die Spec schon da ist.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import demo_kits
from . import superfeatures as sf

# Wo die (separat zu bauenden) Bausatz-Schaltplan-Specs liegen werden.
_SPEC_DIR = (Path(__file__).resolve().parents[1]
             / "kicad_mcp" / "resources" / "data" / "demo_kits")

# Schritt-Arten.
STEP_BUILD = "build_schematic"   # den hinterlegten Schaltplan anlegen
STEP_SKILL = "skill"             # einen Super-Skill aufrufen


@dataclass(frozen=True)
class DemoStep:
    """Ein Schritt im Demo-Ablauf.

    kind         STEP_BUILD | STEP_SKILL
    label        Kurztitel für die Fortschritts-Anzeige
    detail       eine erklärende Zeile (bei Skills: warum hier — aus rationale)
    prompt       nur STEP_SKILL: der kanonische Chat-Prompt des Skills
    feature_key  nur STEP_SKILL: der Super-Skill-Key
    """
    kind: str
    label: str
    detail: str
    prompt: str = ""
    feature_key: str = ""


def spec_path(kit: demo_kits.DemoKit) -> Path:
    """Erwarteter Pfad der Schaltplan-Spec eines Bausatzes (existiert ggf. noch
    nicht)."""
    return _SPEC_DIR / kit.spec_file


def spec_exists(kit: demo_kits.DemoKit) -> bool:
    """Ist die Schaltplan-Spec dieses Bausatzes schon gebaut?"""
    return spec_path(kit).is_file()


def plan(kit_key: str) -> list[DemoStep]:
    """Einen Bausatz zu seiner geordneten Schritt-Liste auflösen.

    Schritt 0 legt den hinterlegten Schaltplan an; danach folgt je
    Pipeline-Skill ein Schritt mit seinem kanonischen Prompt (aus
    ``superfeatures``) und der Bausatz-Begründung. Wirft ``KeyError`` bei
    unbekanntem Bausatz und ``ValueError``, falls die Registry inkonsistent ist
    (der Runner validiert sie beim Auflösen)."""
    demo_kits.validate()
    kit = demo_kits.get(kit_key)
    if kit is None:
        avail = ", ".join(k.key for k in demo_kits.all_kits())
        raise KeyError(f"Unbekannter Bausatz '{kit_key}'. Vorhanden: {avail}")

    have_spec = spec_exists(kit)
    build_detail = (f"Hinterlegte Schaltung '{kit.title}' als .kicad_sch anlegen "
                    f"und öffnen ({kit.spec_file})")
    if not have_spec:
        build_detail += " — Spec noch nicht gebaut (folgt)"
    steps: list[DemoStep] = [
        DemoStep(kind=STEP_BUILD,
                 label=f"Schaltplan anlegen: {kit.title}",
                 detail=build_detail),
    ]
    for feature_key in kit.pipeline:
        feat = sf.get(feature_key)
        # validate() hat die Existenz schon garantiert; feat ist nicht None.
        steps.append(DemoStep(
            kind=STEP_SKILL,
            label=feat.name,
            detail=kit.rationale[feature_key],
            prompt=feat.prompt,
            feature_key=feature_key,
        ))
    return steps


def describe(kit_key: str) -> str:
    """Menschenlesbare Vorschau des Ablaufs (für Log/Diagnose) — nummerierte
    Schritte, Skills mit ihrer Hier-Begründung."""
    lines = []
    for i, step in enumerate(plan(kit_key)):
        if step.kind == STEP_BUILD:
            lines.append(f"{i}. 📐 {step.label}")
        else:
            lines.append(f"{i}. {step.label} — {step.detail}")
    return "\n".join(lines)
