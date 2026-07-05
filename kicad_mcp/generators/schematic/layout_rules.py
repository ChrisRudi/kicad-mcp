# SPDX-License-Identifier: GPL-3.0-or-later
"""Schaltplan-Layout-Regeln — das wartbare, zentrale Regel-Set.

Single Source of Truth dafür, WELCHE Konventionen der Schaltplan-Generator
einhält. Jede Regel steht hier einmal als Datensatz (Aussage + Begründung +
Beleg + wo erzwungen + Ausnahmen + Status + Phase), damit sie auffindbar,
review-bar und wartbar ist.

Diese 10 Regeln sind NICHT erfunden, sondern aus echten, professionell
gezeichneten KiCad-Referenz-Schaltbildern abgeleitet (die offiziellen
KiCad-Demos ``sallen_key`` und ``rectifier``): angesehen, Konventionen notiert,
als Regeln formuliert. ``derived_from`` nennt je Regel den Beleg.

Die Durchsetzung: geometrische Regeln (Phase GEOMETRY/FINISH mit ``enforcer``)
fährt die listen-getriebene Engine ``schematic.place._enforce_layout_rules`` ab;
die übrigen (Phase PLACEMENT) sind intrinsisch im Platzierer/Router bzw. noch
PLANNED. **Darüber** sitzt der ganzheitliche Erzwinger
``schematic.layout_optimizer``: eine echte Such-Schleife, die den fertigen
Schaltplan gegen die am Profi-Goldstandard geeichte Metrik
(``schematic.layout_measure.badness``) optimiert und mehrere dieser Regeln
GEMEINSAM auf das messbare Optimum treibt (kein Überlappen von Bauteilen UND
Labels, Labels weg vom Bauteil, keine Kreuzungen) — dort, wo eine Einzelregel
lokal gegen eine andere arbeitet. Pure/stdlib — headless importierbar,
unit-getestet ohne KiCad.
"""

from __future__ import annotations

from dataclasses import dataclass

# Status — wie weit ist die Regel real umgesetzt?
ENFORCED = "enforced"   # implementiert UND verifiziert (Messung/Render)
PARTIAL = "partial"     # teilweise umgesetzt (Minimalform / Heuristik)
PLANNED = "planned"     # aus der Referenz abgeleitet, noch nicht umgesetzt

_VALID_STATUS = (ENFORCED, PARTIAL, PLANNED)

# Durchsetzungs-Phase — steuert die listen-getriebene Engine.
#   PLACEMENT  intrinsisch im Platzierer/Router (kein separater Nachlauf)
#   GEOMETRY   Post-Placement-Nachlauf in einer Fixpunkt-Schleife (bis stabil)
#   FINISH     genau einmal ganz am Ende
PLACEMENT = "placement"
GEOMETRY = "geometry"
FINISH = "finish"

_VALID_PHASE = (PLACEMENT, GEOMETRY, FINISH)


@dataclass(frozen=True)
class LayoutRule:
    """Eine aus echten Referenz-Schaltbildern abgeleitete Layout-Regel.

    key          stabile id
    title        Kurzname
    rule         die Regel-Aussage (was gilt), knapp und prüfbar
    rationale    warum — welche Lesbarkeits-/Konventions-Absicht dahinter steht
    derived_from Beleg: was im Referenz-Schaltbild diese Regel zeigt
    enforced_in  ``modul.funktion``-Verweise, die die Regel durchsetzen (leer
                 bei PLANNED)
    exemptions   Fälle, in denen die Regel bewusst NICHT greift
    status       ENFORCED | PARTIAL | PLANNED
    phase        PLACEMENT | GEOMETRY | FINISH — für die Engine
    enforcer     Name des mechanischen Enforcers (``spacing`` | ``grid_snap``)
                 oder leer (intrinsisch/PLANNED)
    """
    key: str
    title: str
    rule: str
    rationale: str
    derived_from: str
    enforced_in: tuple[str, ...] = ()
    exemptions: tuple[str, ...] = ()
    status: str = PLANNED
    phase: str = PLACEMENT
    enforcer: str = ""


# Reihenfolge = die 10 abgeleiteten Regeln, wie in der Analyse nummeriert.
RULES: tuple[LayoutRule, ...] = (
    LayoutRule(
        key="signal_flow_ltr",
        title="Signalfluss links → rechts",
        rule="Der Signalweg verläuft von links (Quelle/Eingang) nach rechts "
             "(Last/Ausgang); Ein-/Ausgangsstecker sitzen an den Blatträndern.",
        rationale="Ein Schaltbild liest man wie einen Text — Eingang links, "
                  "Ausgang rechts.",
        derived_from="sallen_key: V1→R1→R2→U1→'lowpass'; rectifier: "
                     "V1→R1→D1→'rect_out'.",
        enforced_in=("schematic.defrag_place (Connectors am Rand)",
                     "schematic.place._compute_net_roles (Source/Sink)"),
        status=PARTIAL,
    ),
    LayoutRule(
        key="power_rails",
        title="Versorgung oben, Masse unten — als Schienen",
        rule="Das Schaltbild spannt sich zwischen einer oberen Versorgungs- "
             "und einer unteren Masse-Ebene auf; Versorgung wird oben, Masse "
             "unten geführt.",
        rationale="Auf einen Blick erkennbar, wo Versorgung und Masse liegen.",
        derived_from="rectifier: GND-Rückleitung ist die untere waagrechte "
                     "Leitung; sallen_key: VDD oben, VSS/GND unten.",
        enforced_in=("schematic.route._place_power_symbol (GND↓ / Supply↑)",),
        exemptions=("volle Rail-Struktur (durchgehende obere/untere Schiene) "
                    "noch nicht — bisher nur die Symbol-Richtung",),
        status=PARTIAL,
    ),
    LayoutRule(
        key="series_horizontal_shunt_vertical",
        title="Reihe horizontal, Quer-nach-Masse vertikal",
        rule="Bauteile IM Signalpfad (in Reihe) liegen horizontal; Bauteile "
             "nach Masse (Quer/Shunt: Abblock-Cs, Last, Filter-C) stehen "
             "vertikal und verbinden die Signalschiene mit der Masse unten.",
        rationale="Der wichtigste Struktur-Unterschied zu 'geclustert': echte "
                  "Schaltbilder reihen entlang einer Schiene mit senkrechten "
                  "Abzweigen.",
        derived_from="rectifier: R1/D1 waagrecht in der Kette, C1 und Last-R2 "
                     "senkrecht runter zu GND; sallen_key: R1/R2 waagrecht, "
                     "C2 senkrecht nach GND.",
        status=PLANNED,
    ),
    LayoutRule(
        key="ic_in_signal_direction",
        title="ICs zeigen in Signalrichtung",
        rule="ICs werden so orientiert, dass Eingänge links und Ausgänge "
             "rechts liegen (das OpAmp-Dreieck zeigt nach rechts); "
             "Versorgungspins vertikal (V+ oben, V− unten).",
        rationale="Passt zum Links→rechts-Signalfluss und hält die "
                  "Versorgung an der Rail-Konvention.",
        derived_from="sallen_key: U1-Dreieck zeigt rechts, Eingänge 1/2 links, "
                     "Ausgang 5 rechts, V+ (3) oben, V− (4) unten.",
        enforced_in=("schematic.place._assign_rotation (Heuristik)",),
        status=PARTIAL,
    ),
    LayoutRule(
        key="orthogonal_on_grid",
        title="Nur orthogonale Drähte auf dem Raster",
        rule="Leitungen laufen ausschließlich waagrecht/senkrecht mit "
             "rechtwinkligen Knicken; alle Bauteile und Knicke liegen auf dem "
             "Schaltplan-Raster. Keine Diagonalen.",
        rationale="Diagonale/off-grid-Drähte sind das klarste Kennzeichen "
                  "eines 'maschinellen', unfertigen Schaltbilds.",
        derived_from="beide Referenzen: nicht eine einzige diagonale Leitung, "
                     "alles rechtwinklig und rastergebunden.",
        enforced_in=("schematic.route._emit_wires_and_labels (A*, orthogonal)",
                     "schematic.place (finaler Grid-Snap)"),
        status=ENFORCED,
        phase=FINISH,
        enforcer="grid_snap",
    ),
    LayoutRule(
        key="generous_spacing",
        title="Großzügige, sichtbare Drahtlängen — nie Pin-an-Pin, kein Überlappen",
        rule="Bauteile stehen weit genug auseinander, dass zwischen ihnen "
             "sichtbare Leitung liegt (≥ 5 mm, Ziel eher 10–20 mm); kein "
             "Bauteil überlappt ein anderes — und auch die Referenz/Wert-"
             "Beschriftung (R1 / 1k) zweier Bauteile liegt nicht übereinander.",
        rationale="Luft zwischen den Bauteilen macht den Plan lesbar; "
                  "Pin-an-Pin, Körper- ODER Text-Überlappung ist unlesbar.",
        derived_from="beide Referenzen: überall großzügiger Abstand, lange "
                     "sichtbare Leitungen, nichts klebt aneinander.",
        enforced_in=("schematic.place._enforce_min_wire (≥ 5 mm)",
                     "common.geometry.force_no_overlap (kein Überlappen)",
                     "schematic.layout_optimizer.optimize (garantiert kein "
                     "Bauteil-, Label- UND kein Referenz/Wert-Text-Überlappen am "
                     "fertigen Blatt — layout_measure.annot_overlaps)"),
        exemptions=("Power-Pins gehen über Symbole; zwei Pins desselben ICs "
                    "sind fixiert",),
        # ENFORCED: der Optimierer treibt comp_overlaps UND label_overlaps auf
        # allen 10 Demo-Schaltungen messbar auf 0 (layout_measure am Profi-
        # Goldstandard geeicht). Die 10–20-mm-Ziel-Länge bleibt aspirativ.
        status=ENFORCED,
        phase=GEOMETRY,
        enforcer="spacing",
    ),
    LayoutRule(
        key="power_symbols_and_io_labels",
        title="Power als Symbole, Ein-/Ausgänge als Netz-Labels",
        rule="Versorgung/Masse werden als Power-Symbole (GND/VDD/VSS) gesetzt, "
             "nicht als durchgezogene Rails-Drähte; Ein-/Ausgänge tragen "
             "sprechende Netz-Labels an ihren Enden — die vom Bauteil WEG in "
             "freien Raum zeigen, nicht in einen Nachbarkörper.",
        rationale="Weniger kreuzende Leitungen, klar benannte Schnittstellen; "
                  "ein Label, das in ein Bauteil ragt, ist unlesbar.",
        derived_from="beide Referenzen: GND/VDD/VSS als Symbole; Labels "
                     "'signal_in'/'lowpass'/'rect_out' an den Enden, alle nach "
                     "außen zeigend.",
        enforced_in=("schematic.route._place_power_symbol",
                     "schematic.route._place_label_with_stub",
                     "schematic.layout_optimizer.optimize (treibt "
                     "label_wrong_dir → 0)"),
        status=ENFORCED,
    ),
    LayoutRule(
        key="ref_value_stacked",
        title="Referenz & Wert konsistent gestapelt",
        rule="Referenz (R1) und Wert (1k) stehen konsistent gestapelt auf "
             "derselben Seite jedes Bauteils, für gleichartige Teile gleich "
             "ausgerichtet.",
        rationale="Einheitliche Beschriftung, schnell erfassbar.",
        derived_from="beide Referenzen: R1 über 1k, C1 über 100n — überall "
                     "gleich.",
        enforced_in=("schematic.builder._emit_symbol_instances",),
        exemptions=("aktuell rechts vom Bauteil statt oben — noch nicht die "
                    "Referenz-Anordnung",),
        status=PARTIAL,
    ),
    LayoutRule(
        key="junctions_at_tees",
        title="Junction-Punkt an jeder 3-Wege-Verbindung",
        rule="An jeder T-/Kreuz-Verbindung von 3+ Leitungen sitzt ein "
             "Junction-Punkt, damit 'verbunden' und 'nur gekreuzt' eindeutig "
             "unterscheidbar sind.",
        rationale="Ohne Junction ist eine Kreuzung mehrdeutig — verbunden oder "
                  "nicht?",
        derived_from="beide Referenzen: grüne Punkte an allen T-Verzweigungen.",
        enforced_in=("schematic.route (Junctions an Mehrfach-Knoten)",),
        status=ENFORCED,
    ),
    LayoutRule(
        key="separate_supply_blocks",
        title="Versorgungs-/Hilfsblöcke getrennt vom Signalpfad",
        rule="Stromversorgung und Hilfsschaltungen werden als eigener, "
             "räumlich getrennter Block gezeichnet, nicht in den Signalpfad "
             "gemischt.",
        rationale="Der Signalpfad bleibt sauber lesbar; Versorgung stört nicht.",
        derived_from="sallen_key: die Quellen V2/V3 als eigener Cluster oben "
                     "rechts, getrennt von der Filter-Signalkette.",
        status=PLANNED,
    ),
)


def all_rules() -> tuple[LayoutRule, ...]:
    """Alle 10 abgeleiteten Layout-Regeln, in Analyse-Reihenfolge."""
    return RULES


def get(key: str) -> LayoutRule | None:
    """Die Regel mit ``key``, oder ``None``."""
    return next((r for r in RULES if r.key == key), None)


def by_status(status: str) -> list[LayoutRule]:
    """Regeln mit dem gegebenen Status."""
    return [r for r in RULES if r.status == status]


def by_phase(phase: str) -> list[LayoutRule]:
    """Regeln einer Durchsetzungs-Phase, in Listen-Reihenfolge — die
    listen-getriebene Engine iteriert genau hierüber."""
    return [r for r in RULES if r.phase == phase]


def validate() -> None:
    """Integritäts-Check — wirft ``ValueError`` bei Verstoß."""
    keys = [r.key for r in RULES]
    if len(keys) != len(set(keys)):
        raise ValueError("Doppelter Regel-Key in layout_rules.RULES")
    if len(RULES) != 10:
        raise ValueError(f"Erwartet 10 abgeleitete Regeln, sind {len(RULES)}")
    for r in RULES:
        if not (r.key and r.key.islower()):
            raise ValueError(f"Regel-Key ungültig: {r.key!r}")
        if r.status not in _VALID_STATUS:
            raise ValueError(f"Regel '{r.key}': Status {r.status!r} ungültig")
        if r.phase not in _VALID_PHASE:
            raise ValueError(f"Regel '{r.key}': Phase {r.phase!r} ungültig")
        if len(r.rule) < 20 or len(r.rationale) < 15 or not r.derived_from:
            raise ValueError(f"Regel '{r.key}': rule/rationale/derived_from "
                             "zu knapp")
        if r.status != PLANNED and not r.enforced_in:
            raise ValueError(f"Regel '{r.key}': enforced_in fehlt (nicht PLANNED)")
