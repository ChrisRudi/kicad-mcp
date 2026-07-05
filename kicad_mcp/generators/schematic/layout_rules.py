# SPDX-License-Identifier: GPL-3.0-or-later
"""Schaltplan-Layout-Regeln — das wartbare, zentrale Regel-Set.

Single Source of Truth dafür, WELCHE Konventionen der Schaltplan-Generator
einhält. Jede Regel steht hier einmal als Datensatz (Aussage + Begründung +
wo erzwungen + Ausnahmen + Status), damit sie auffindbar, review-bar und
wartbar ist — statt verstreut in Kommentaren über place.py/route.py/geometry.py.

Absicht (Nutzer-Vorgabe): „Alle Regeln … müssen als eigenes wartbares Set in die
Generatoren." Dieses Modul IST dieses Set. Die eigentliche Durchsetzung lebt
(noch) in den genannten Funktionen; ``enforced_in`` verweist darauf. Ein
späterer Refactor kann die Logik gegen diese Keys zentralisieren, ohne dass die
Regel-Liste selbst wandert.

Pure/stdlib — importiert headless, unit-getestet ohne KiCad
(``tests/test_layout_rules.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

# Status-Werte.
ENFORCED = "enforced"   # implementiert UND verifiziert (Messung im Test/Render)
PARTIAL = "partial"     # implementiert, aber mit bekannten Randfällen
PLANNED = "planned"     # spezifiziert, noch nicht umgesetzt

_VALID_STATUS = (ENFORCED, PARTIAL, PLANNED)


@dataclass(frozen=True)
class LayoutRule:
    """Eine Schaltplan-Layout-Regel.

    key         stabile id
    title       Kurzname
    rule        die Regel-Aussage (was gilt), knapp und prüfbar
    rationale   warum — welche Lesbarkeits-/Konventions-Absicht dahinter steht
    enforced_in Liste ``modul.funktion``-Verweise, die die Regel durchsetzen
    exemptions  Fälle, in denen die Regel bewusst NICHT greift
    status      ENFORCED | PARTIAL | PLANNED
    """
    key: str
    title: str
    rule: str
    rationale: str
    enforced_in: tuple[str, ...]
    exemptions: tuple[str, ...] = ()
    status: str = ENFORCED


# Reihenfolge = logischer Ablauf: verstehen → platzieren → verdrahten →
# Abstände/Konventionen → Feinschliff.
RULES: tuple[LayoutRule, ...] = (
    LayoutRule(
        key="tight_cluster",
        title="Eng ums IC clustern, gedreht für kürzeste Drähte",
        rule="Bauteile werden dicht um ihr IC gruppiert und so gedreht, dass "
             "die Verbindungen möglichst kurz und kreuzungsarm werden.",
        rationale="Kompakte, wie von Hand gezeichnete Blöcke statt "
                  "hingewürfelter Streuung.",
        enforced_in=("schematic.defrag_place.incremental_place_and_score",
                     "schematic.constraint_solver.solve_placement (allow_greedy=False)"),
    ),
    LayoutRule(
        key="smart_rotation",
        title="Pin-bewusste Rotation",
        rule="Die Rotation eines Bauteils wird so gewählt, dass seine "
             "verbundenen Pins zu ihren Partnern zeigen (kürzeste Drähte).",
        rationale="Ohne passende Drehung entstehen unnötige Bögen und "
                  "Kreuzungen.",
        enforced_in=("schematic.place._classify/_assign_rotation",
                     "schematic.defrag_place._best_rotation"),
    ),
    LayoutRule(
        key="no_labels",
        title="Echte Drähte statt Netz-Labels",
        rule="Verbindungen werden als gezeichnete Leitungen ausgeführt, nicht "
             "als Netz-Labels (für kleine Schaltungen).",
        rationale="Labels zerreißen kleine Schaltpläne optisch; gezeichnete "
                  "Drähte sind direkt lesbar.",
        enforced_in=("schematic.route._emit_wires_and_labels",),
        exemptions=("Power-Netze → Power-Symbole (GND/VCC)",
                    "sehr lange / hoch-fan-out Netze → Label (A*-Fallback)"),
    ),
    LayoutRule(
        key="connectors_outermost",
        title="Stecker außen, Leitung nach innen",
        rule="Ein-/Ausgangs-Stecker sitzen am äußersten Rand; die Leitung "
             "läuft von dort nach innen zur Schaltung (Signalfluss "
             "links→rechts).",
        rationale="Konvention: man findet Ein-/Ausgang am Blattrand.",
        enforced_in=("schematic.defrag_place (Connectors zuletzt, am nächsten "
                     "Rand)",),
    ),
    LayoutRule(
        key="gnd_down_vcc_up",
        title="GND unten, Versorgung oben",
        rule="Ground-Symbole zeigen IMMER nach unten, Versorgungs-Symbole "
             "(VCC/+5V/…) IMMER nach oben — unabhängig von der Pin-Richtung.",
        rationale="KiCad-/Schaltplan-Standard; sofort lesbar, wo Masse und "
                  "Versorgung sind.",
        enforced_in=("schematic.route._place_power_symbol (direction erzwungen)",),
    ),
    LayoutRule(
        key="no_overlap",
        title="Bauteile überlappen nie",
        rule="Kein Bauteil-Rahmen überlappt einen anderen — garantiert.",
        rationale="Überlappende Symbole sind unlesbar und ERC-gefährlich.",
        enforced_in=("common.geometry._resolve_overlaps (rotations-bewusst)",
                     "common.geometry.force_no_overlap (harte Garantie)"),
    ),
    LayoutRule(
        key="min_wire",
        title="Mindestens 5 mm Leitung je Verbindung",
        rule="Zwischen zwei direkt verdrahteten Pins VERSCHIEDENER Bauteile "
             "liegen immer ≥ 5 mm (MIN_WIRE_MM) sichtbare Leitung — nie "
             "Pin-an-Pin ohne Draht.",
        rationale="Ohne sichtbaren Draht sieht es aus, als klebten Bauteile "
                  "direkt aneinander.",
        enforced_in=("schematic.place._enforce_min_wire",),
        exemptions=("Power-Pins (gehen über GND/VCC-Symbole, kein direkter "
                    "Draht)",
                    "zwei Pins DESSELBEN ICs (durch Symbol-Geometrie fixiert)"),
    ),
    LayoutRule(
        key="wire_along_pin_exit",
        title="Leitung folgt der Pin-Austrittsrichtung",
        rule="Die 5-mm-Leitung verläuft entlang der Richtung, in der der "
             "Anschluss aus dem Bauteilkörper austritt (geradlinig aus dem "
             "Pin), nicht schräg.",
        rationale="Ein Draht, der seitlich aus einem nach unten zeigenden Pin "
                  "abknickt, wirkt falsch; die Leitung soll dem Pin folgen.",
        enforced_in=("schematic.place._enforce_min_wire (_RETREAT via "
                     "route._stub_direction)",),
        exemptions=("Partner nicht auf der Pin-Achse → Fallback trennt "
                    "zusätzlich direkt (Garantie ≥ 5 mm hat Vorrang)",),
        status=PARTIAL,
    ),
    LayoutRule(
        key="ref_value_right",
        title="Referenz & Wert rechts vom Bauteil",
        rule="Reference (z. B. R1) und Value (z. B. 10k) stehen rechts neben "
             "dem Bauteil, auseinandergezogen.",
        rationale="Einheitliche, lesbare Beschriftung.",
        enforced_in=("schematic.builder._emit_symbol_instances (Rule R12)",),
    ),
    LayoutRule(
        key="astar_route",
        title="Drähte um Bauteile herum, nie hindurch",
        rule="Leitungen werden per A* um Bauteil-Rahmen geführt und nie durch "
             "einen Körper gezeichnet.",
        rationale="Drähte durch Symbole sind mehrdeutig und unlesbar.",
        enforced_in=("schematic.route._emit_wires_and_labels (A*)",),
    ),
    LayoutRule(
        key="grid_snap",
        title="Alles auf dem Raster",
        rule="Alle Bauteil-Positionen werden abschließend auf das Schaltplan-"
             "Raster (HALF_GRID) gesnappt.",
        rationale="Off-Grid-Pins verhindern saubere, orthogonale Drähte.",
        enforced_in=("schematic.place.place_schematic (finaler _snap)",),
    ),
)


def all_rules() -> tuple[LayoutRule, ...]:
    """Alle Layout-Regeln, in logischer Reihenfolge."""
    return RULES


def get(key: str) -> LayoutRule | None:
    """Die Regel mit ``key``, oder ``None``."""
    return next((r for r in RULES if r.key == key), None)


def by_status(status: str) -> list[LayoutRule]:
    """Regeln mit dem gegebenen Status."""
    return [r for r in RULES if r.status == status]


def validate() -> None:
    """Integritäts-Check — wirft ``ValueError`` bei Verstoß. Vom Test genutzt,
    damit kein halbgarer Eintrag ins Set kommt."""
    keys = [r.key for r in RULES]
    if len(keys) != len(set(keys)):
        raise ValueError("Doppelter Regel-Key in layout_rules.RULES")
    for r in RULES:
        if not (r.key and r.key.islower()):
            raise ValueError(f"Regel-Key ungültig: {r.key!r}")
        if r.status not in _VALID_STATUS:
            raise ValueError(f"Regel '{r.key}': Status {r.status!r} ungültig")
        if len(r.rule) < 20 or len(r.rationale) < 15:
            raise ValueError(f"Regel '{r.key}': rule/rationale zu knapp")
        if not r.enforced_in:
            raise ValueError(f"Regel '{r.key}': enforced_in fehlt")
