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

Die Spec-JSONs liegen unter ``kicad_mcp/resources/data/demo_kits/`` im
mcp-Root (Bundle bzw. env-Override); ``spec_path()``/``spec_exists()`` lösen
den Ort auf, der Build-Schritt im Plan markiert ehrlich, ob die Spec da ist.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import demo_kits
from . import superfeatures as sf

def _spec_dir() -> Path:
    """Wo die Bausatz-Specs liegen — über den kanonischen mcp-root-Resolver
    (env-Override → Bundle ``<plugin>/mcp``). Der frühere Dev-Checkout-Pfad
    (``parents[1]/kicad_mcp``) existiert im INSTALLIERTEN Plugin nicht — das
    Feld sah dadurch fälschlich „Spec noch nicht gebaut" und die Demo lief
    nur als Vorschau. Im Repo greift derselbe Resolver auf den
    Bundle-Spiegel ``plugin/mcp/``."""
    from . import server_manager
    return (Path(server_manager.default_mcp_root())
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
    return _spec_dir() / kit.spec_file


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
