# SPDX-License-Identifier: GPL-3.0-or-later
"""Super-feature registry — the single source of truth for the roadmap.

These are the "things KiCad can *never* do" — features that need an LLM's
semantic understanding (intent, function, external knowledge) on top of KiCad's
geometry+netlist. The chat panel renders one button per entry (see
``chat_dialog._build_superfeature_bar``) and ``docs/superfeatures.md`` is the
human-readable narrative of the same list.

Living document: when a feature ships, flip its ``status`` from ``SOON`` to
``SHIPPED`` here (and wire its ``key`` in the panel handler). A ``SOON`` button
is already fully written — hover shows the pitch, a click prints the "coming
soon" description — so the GUI advertises the roadmap from day one.

Pure/stdlib only, so it imports headless and is unit-tested without wx/KiCad.
"""

from __future__ import annotations

from dataclasses import dataclass

# Status values.
SHIPPED = "shipped"   # live, button runs it
SOON = "soon"         # designed + described, not built yet (coming soon)

_VALID_STATUS = (SHIPPED, SOON)


@dataclass(frozen=True)
class SuperFeature:
    """One roadmap entry.

    key      stable id (used by the panel click handler / when wiring it live)
    label    the GUI button text, emoji + short name
    name     full human name
    status   SHIPPED | SOON
    tooltip  hover text — one or two sentences, the "what & why"
    moat     one line: *why KiCad can't do this* (the reason it's a super-feature)
    selection_aware  cross-cutting contract: the feature also runs *scoped to the
             current KiCad selection* (via ``ipc_get_selection``), not only
             board-wide. Default True — every super-feature must honour a
             selection, e.g. "check ampacity of the selected traces",
             "datasheet-diff the selected IC", "untangle the selected parts".
    """
    key: str
    label: str
    name: str
    status: str
    tooltip: str
    moat: str
    selection_aware: bool = True


# Order = display order. "Entwirren" leads — it is the current focus.
FEATURES: tuple[SuperFeature, ...] = (
    SuperFeature(
        key="untangle",
        label="🧶 Entwirren",
        name="Entwirren — Ratsnest-Entkreuzung fürs Routing",
        status=SOON,
        tooltip=("Ordnet den frisch synchronisierten Bauteil-Haufen so an, dass "
                 "sich die Luftlinien möglichst wenig kreuzen — ein sauberer, "
                 "routbarer Startpunkt. Zeigt erst die Vorschau, ordnet dann in "
                 "einem Zug an."),
        moat=("KiCad hat keinen Erstplatzierer; Kreuzungen zu minimieren "
              "erfordert Reasoning über die ganze Netz-Topologie."),
    ),
    SuperFeature(
        key="scoped_untangle",
        label="🧶 Auswahl entwirren",
        name="Auswahl entwirren",
        status=SOON,
        tooltip=("Nur die markierten Bauteile entkreuzen; der Rest des Boards "
                 "bleibt als fixer Anker stehen."),
        moat="Selektions-bezogene, kreuzungs-minimierende Platzierung fehlt KiCad.",
    ),
    SuperFeature(
        key="bus_radar",
        label="🚌 Bus-Radar",
        name="Bus-Radar — Bus-Teilnehmer finden",
        status=SOON,
        tooltip=("Listet und markiert alle Teilnehmer + Pins eines Busses "
                 "(I²C, SPI, Datenbus …) als *eine* Bedeutungseinheit — nicht "
                 "nur die Einzelnetze."),
        moat=("KiCad kennt Einzelnetze, aber nicht den Bus als semantische "
              "Gruppe."),
    ),
    SuperFeature(
        key="datasheet_diff",
        label="📄 Datenblatt-Abgleich",
        name="Datenblatt-Abgleich",
        status=SOON,
        tooltip=("Zieht das Datenblatt eines ICs und vergleicht deine "
                 "Beschaltung mit der Referenz: Entkopplung, Pin-Beschaltung, "
                 "externe Bauteile, Load-Caps."),
        moat="KiCad weiß nichts von Datenblättern — der Abgleich ist reine Bedeutungs-Arbeit.",
    ),
    SuperFeature(
        key="semantic_erc",
        label="🛡️ Design-Wächter",
        name="Design-Wächter — semantischer ERC",
        status=SOON,
        tooltip=("Prüfung jenseits des ERC: fehlende Pull-ups am I²C, fehlende "
                 "Abblock-Cs nah am IC, unpassende Quarz-Load-Caps, Power-Netz "
                 "ohne Stützung …"),
        moat="KiCads ERC prüft Netz-Syntax, nicht die *Absicht* der Schaltung.",
    ),
    SuperFeature(
        key="test_points",
        label="🔎 Test-Punkt-Wächter",
        name="Test-Punkt-Wächter — probe-bar für Bring-up & Serientest?",
        status=SOON,
        tooltip=("Rankt Netze nach Test-Wichtigkeit (Versorgung, Reset, Clock, "
                 "Bus) und meldet, welche kritischen Netze keinen Prüfpunkt/"
                 "Stecker-Zugang haben — die blinden Flecken für Flying-Probe/"
                 "Nadeladapter und Bring-up. Zeigt Abdeckung in %."),
        moat=("KiCad kennt Netze, aber nicht ihre *Wichtigkeit* für den Test — "
              "das ist Fertigungs-/Bring-up-Wissen."),
    ),
    SuperFeature(
        key="pin_swap",
        label="🔀 Pin-Tausch",
        name="Pin-Tausch — GPIO ans Routing anpassen",
        status=SOON,
        tooltip=("Legt funktional gleichwertige GPIOs/Pins um, damit das "
                 "Routing kreuzungsfrei wird — Schaltplan und PCB werden "
                 "kohärent nachgezogen."),
        moat="KiCad hat kein Konzept funktional austauschbarer Pins (Pinmux).",
    ),
    SuperFeature(
        key="explain_board",
        label="💡 Board erklären",
        name="Board erklären",
        status=SOON,
        tooltip=("Rekonstruiert aus Netzliste + Bauteilen, was das Board tut: "
                 "Funktionsblöcke, Schnittstellen, Stromversorgung."),
        moat="KiCad hat ein Modell der Verbindungen, keines der Funktion.",
    ),
    SuperFeature(
        key="nl_navigation",
        label="🧭 Netz-Navigator",
        name="Netz-Navigator — Fragen in normaler Sprache",
        status=SOON,
        tooltip=("Frag in normaler Sprache: welcher Pin treibt Motor-Enable, "
                 "was liegt sonst auf U1.7 — semantische Netz-/Pin-Suche."),
        moat="KiCad zeigt Netze an, sucht aber nicht nach *Bedeutung*.",
    ),
    SuperFeature(
        key="select_place",
        label="📐 Ausrichten & Anordnen",
        name="Ausrichten & Anordnen",
        status=SOON,
        tooltip=("Markierte Bauteile per Satz ordnen: bündig ausrichten, im "
                 "Raster verteilen, spiegeln, als Array — mit korrekter "
                 "Rotations- und B.Cu-Mathematik."),
        moat="KiCad hat kein sprachgesteuertes, absicht-basiertes Anordnen.",
    ),
    SuperFeature(
        key="polar_board",
        label="⊙ Polar-Board",
        name="Polar-Board — Radial-Layout für runde Boards",
        status=SOON,
        tooltip=("Platzieren und Routen in Polarkoordinaten (Radius + Winkel) "
                 "statt X/Y: LEDs gleichmäßig auf einem Kreis, Stecker rund um "
                 "den Rand, radiale und konzentrische Leiterbahnen."),
        moat=("KiCad rechnet nur kartesisch; runde Boards zwingen sonst zur "
              "Handrechnung von Winkel und Radius."),
    ),
    SuperFeature(
        key="sketch_layer",
        label="🖊️ Skizzen-Layer",
        name="Skizzen-Layer — gemeinsamer Notiz-/Hilfslayer",
        status=SOON,
        tooltip=("Ein verwalteter Hilfslayer als gemeinsames Skizzenblatt: du "
                 "zeichnest Absichten hin, der Agent zeichnet Vorschläge, Marker "
                 "und Geister-Vorschauen — ein Klick zum Ein-/Ausblenden und "
                 "Leeren."),
        moat=("KiCad hat keinen dedizierten, von Mensch UND Agent gemeinsam "
              "genutzten Skizzen-/Vorschau-Kanal."),
    ),
    SuperFeature(
        key="sketch_conductor",
        label="✏️ Skizzen-Dirigent",
        name="Skizzen-Dirigent — gezeichnete Absicht → Kupfer",
        status=SOON,
        tooltip=("Zeichne grob deine Absicht auf einen Markup-Layer — Linie, "
                 "Rechteck, ein mit GND beschrifteter Pfeil — der Agent gießt "
                 "Kupfer oder platziert entsprechend."),
        moat="KiCad interpretiert keine gezeichnete Absicht.",
    ),
    SuperFeature(
        key="watch_mode",
        label="👁️ Mitdenken-Modus",
        name="Mitdenken-Modus — Live-Assistenz beim Routen",
        status=SOON,
        tooltip=("Während du von Hand routest, kommentiert Claude live: "
                 "Clearance-Unterschreitung, fragmentierte Netze, DRC-Risiken."),
        moat="KiCad hat kein mitlaufendes, verstehendes Assistenz-Auge.",
    ),

    # -- Elektrik & Fertigung (DFM) -------------------------------------------
    SuperFeature(
        key="ampacity",
        label="🔥 Stromtragfähigkeit",
        name="Stromtragfähigkeit — Leiterbahn-Breite vs. Strom",
        status=SOON,
        tooltip=("Prüft jede Leiterbahn-Breite gegen den Strom, den ihr Netz "
                 "trägt (IPC-2221), markiert unterdimensionierte Bahnen und "
                 "schlägt passende Breiten vor."),
        moat=("KiCad kennt keine Ströme — wie viel Strom ein Netz führt, steht "
              "in der Design-Absicht, nicht im Layout."),
    ),
    SuperFeature(
        key="xtal_caps",
        label="⌚ Quarz-Load-Caps",
        name="Quarz-Load-Caps — richtige Lastkapazität berechnen",
        status=SOON,
        tooltip=("Berechnet die korrekten Load-Kondensatoren für einen Quarz "
                 "aus dessen Datenblatt-CL und der geschätzten Streukapazität "
                 "(C = 2·(CL − Cstray)) und prüft, ob die verbauten Werte passen."),
        moat=("KiCad kennt weder den CL-Wert eines Quarzes noch die "
              "Load-Cap-Formel."),
    ),
    SuperFeature(
        key="via_cost",
        label="🔩 Via-Optimierung",
        name="Via-Optimierung — Anzahl & Kosten senken",
        status=SOON,
        tooltip=("Senkt Via-Anzahl und Fertigungskosten: findet überflüssige "
                 "Vias, wandelt teure Blind/Buried- in Through-Vias und schlägt "
                 "via-ärmeres Routing vor."),
        moat=("KiCad zählt Vias, bewertet aber ihre Fertigungskosten und "
              "Notwendigkeit nicht."),
    ),
    SuperFeature(
        key="thermal",
        label="🌡️ Thermik",
        name="Thermik — Verlustleistungs-Hotspots",
        status=SOON,
        tooltip=("Findet Verlustleistungs-Hotspots (Regler, MOSFETs, Shunts) "
                 "und schlägt Kühl-Kupfer, Thermal-Vias und Abstände vor."),
        moat="KiCad hat kein Verlustleistungs- oder Wärmemodell.",
    ),
    SuperFeature(
        key="operating_temp",
        label="🌡️ Betriebstemperatur",
        name="Betriebstemperatur — Junction-Temp & Derating-Reserve",
        status=SOON,
        tooltip=("Schätzt die reale Betriebs-/Sperrschichttemperatur je Bauteil "
                 "(Tj = Ta + P·θ) aus Verlustleistung, Umgebungstemperatur und "
                 "Wärmewiderstand — und wie viel Derating-Reserve bleibt."),
        moat=("KiCad hat kein Modell für Wärmewiderstand, Umgebung oder "
              "Verlustleistung."),
    ),
    SuperFeature(
        key="slew_rate",
        label="📐 Slew-Rate",
        name="Slew-Rate — schafft der Verstärker/Treiber das Signal?",
        status=SOON,
        tooltip=("Rechnet, ob ein OpAmp/Treiber die geforderte Signalflanke "
                 "schafft (Slew-Rate-Limit) bzw. die Flankensteilheit digitaler "
                 "Signale — relevant für Verzerrung, Timing und EMV."),
        moat=("KiCad rechnet kein dynamisches Signalverhalten aus Bauteil-Specs."),
    ),
    SuperFeature(
        key="impedance",
        label="〰️ Impedanz",
        name="Impedanz — controlled impedance aus dem Stackup",
        status=SOON,
        tooltip=("Berechnet Breite und Abstand für eine definierte Impedanz "
                 "(USB, Ethernet, RF) aus dem Lagenaufbau."),
        moat="KiCad rechnet keine Impedanz aus Stackup und Geometrie.",
    ),
    SuperFeature(
        key="dfm_check",
        label="🏭 DFM-Check",
        name="DFM-Check — Fertigbarkeit gegen echte Fab-Regeln",
        status=SOON,
        tooltip=("Prüft die Fertigbarkeit gegen die Regeln eines konkreten "
                 "Fertigers (min. Track/Space, Annular Ring, Acid Traps, "
                 "Silk-über-Pad) — nicht nur generisches DRC."),
        moat=("KiCads DRC kennt keine fertiger-spezifischen DFM-Regeln oder "
              "deren Begründung."),
    ),
    SuperFeature(
        key="cost_estimate",
        label="💰 Kosten-Schätzer",
        name="Kosten-Schätzer — was macht das Board teuer",
        status=SOON,
        tooltip=("Grobe Fertigungskosten aus Boardfläche, Lagenzahl, Via-Anzahl "
                 "und BOM — plus was die Kosten treibt."),
        moat="KiCad hat kein Kostenmodell.",
    ),

    # -- Simulation & Beschaffung ---------------------------------------------
    SuperFeature(
        key="simulate",
        label="📈 Simulation",
        name="Simulation — Verhalten & Bandbreite verstehen",
        status=SOON,
        tooltip=("Simuliert das Schaltungsverhalten (z. B. Verstärker-Bandbreite, "
                 "Frequenzgang, Arbeitspunkt) über SPICE und erklärt das Ergebnis "
                 "in Klartext — statt nur Kurven auszuspucken."),
        moat=("KiCad kann ngspice starten, aber weder die *Frage* noch das "
              "*Ergebnis* interpretieren."),
    ),
    SuperFeature(
        key="sim_models",
        label="🧬 SPICE-Modelle",
        name="Simulationsmodelle ergänzen",
        status=SOON,
        tooltip=("Findet und hängt das passende SPICE-Modell je Bauteil an, damit "
                 "die Simulation überhaupt läuft — der lästige manuelle Schritt "
                 "vor jeder Simulation."),
        moat=("KiCad verlangt manuelle Modell-Zuordnung und weiß nicht, welches "
              "Modell zu welchem Bauteil passt."),
    ),
    SuperFeature(
        key="bom_consolidate",
        label="💰 BOM-Konsolidierung",
        name="BOM-Konsolidierung — E-Reihe standardisieren, Feeder sparen",
        status=SOON,
        tooltip=("Fasst fast-gleiche R/C-Werte (10k neben 10,2k neben 9,1k) auf "
                 "Standard-E-Reihen-Werte zusammen — weniger Bestückungs-Feeder "
                 "und günstigere Stückzahlen, ohne ein Bauteil über die Toleranz "
                 "zu verschieben. Schlägt vor, ändert nicht."),
        moat=("KiCad kennt weder E-Reihen noch Feeder/Bestellmengen — das ist "
              "Fertigungs-Wissen über der Netzliste."),
    ),
    SuperFeature(
        key="preferred_parts",
        label="🏭 Fab-Standardteile",
        name="Fab-Standardteile — No-Load-Fee-Teile bevorzugen (JLCPCB/Seeed/…)",
        status=SOON,
        tooltip=("Bestücker verlangen pro Bauteiltyp außerhalb ihrer Hausbibliothek "
                 "eine Feeder-Ladegebühr (JLCPCB Basic vs Extended, Seeed OPL …). "
                 "Mappt jeden R/C-Wert+Bauform auf das Vorzugsteil des Fertigers "
                 "und schätzt die gesparte Gebühr. Fab-agnostisch: ein datierter "
                 "Snapshot je Fertiger."),
        moat=("KiCad hat kein Wissen über Distributoren, Fab-Kataloge, "
              "Lagerbestand oder Ladegebühren."),
    ),
    SuperFeature(
        key="bom_sourcing",
        label="🛒 Bauteil-Sourcing",
        name="Bauteil-Sourcing — Verfügbarkeit, Preis & Alternativen",
        status=SOON,
        tooltip=("Prüft live Verfügbarkeit und Preis gegen Distributoren und "
                 "findet pin-kompatible Alternativen für abgekündigte oder "
                 "nicht-lagernde Teile (der Live-Netz-Teil über die "
                 "offline Fab-Standardteil-Prüfung hinaus)."),
        moat=("KiCad hat kein Wissen über Distributoren, Lagerbestand oder "
              "Preise."),
    ),

    # -- Kreativ / grenzüberschreitend ----------------------------------------
    SuperFeature(
        key="photo_reverse",
        label="📷 Foto→Schaltung",
        name="Foto → Schaltung — reverse-engineer aus einem Bild",
        status=SOON,
        tooltip=("Zieh ein Foto einer echten Platine rein — der Agent erkennt "
                 "Bauteile, Beschriftungen und Leiterbahnen und rekonstruiert "
                 "Netzliste/Schaltplan als Ausgangspunkt."),
        moat="KiCad hat keine Bild-Wahrnehmung — das ist reine Multimodal-Arbeit.",
    ),
    SuperFeature(
        key="datasheet_circuit",
        label="📄 Datenblatt→Schaltung",
        name="Datenblatt → Applikationsschaltung",
        status=SOON,
        tooltip=("Aus dem Datenblatt eines ICs die typische Applikationsschaltung "
                 "generieren (Entkopplung, externe Bauteile, Referenz) — als "
                 "fertigen Schaltungsblock."),
        moat=("KiCad liest keine Datenblätter und kennt keine "
              "Applikationsschaltungen."),
    ),
    SuperFeature(
        key="safety_spacing",
        label="⚡ Sicherheitsabstände",
        name="Sicherheitsabstände — Creepage & Clearance",
        status=SOON,
        tooltip=("Prüft Kriech- und Luftstrecken zwischen Netz-Bereichen "
                 "(Netzspannung ↔ Kleinspannung) gegen Sicherheitsnormen "
                 "(IEC 62368) — inkl. Slots und Isolationsbarrieren."),
        moat=("KiCad hat kein Isolations-/Spannungsmodell und keine "
              "Sicherheitsnormen."),
    ),
    SuperFeature(
        key="firmware_map",
        label="💾 Firmware-Pinmap",
        name="Firmware-Pinmap — Pinbelegung als Code exportieren",
        status=SOON,
        tooltip=("Exportiert die MCU-Pinbelegung als Firmware-Header/Config "
                 "(C, DeviceTree, ESPHome …) — schlägt die Brücke Hardware ↔ "
                 "Software, in beide Richtungen konsistent."),
        moat=("KiCad hat kein Modell der Firmware-Seite; die Pin-Semantik lebt "
              "außerhalb des Layouts."),
    ),
    SuperFeature(
        key="mlcc_derating",
        label="📉 MLCC-Derating",
        name="MLCC-Derating — echte Kapazität unter DC-Bias",
        status=SOON,
        tooltip=("Rechnet die *effektive* Kapazität eines Keramik-Cs unter "
                 "DC-Bias und Temperatur (der berüchtigte DC-Bias-Effekt): ein "
                 "10 µF/6,3 V an 5 V ist real oft nur ~4 µF."),
        moat=("KiCad kennt nur den Nennwert, nicht das Spannungs-/Temperatur- "
              "Verhalten realer Bauteile."),
    ),
    SuperFeature(
        key="silk_cleanup",
        label="🔤 Silk-Aufräumen",
        name="Silkscreen aufräumen — Referenzen lesbar machen",
        status=SOON,
        tooltip=("Rückt Reference-Designatoren so, dass sie lesbar sind: nicht "
                 "unter Bauteilen/Pads, konsistent orientiert, nah am richtigen "
                 "Teil — die mühsame Fleißarbeit am Ende jedes Layouts."),
        moat=("KiCad kann Text verschieben, aber nicht *Lesbarkeit* beurteilen."),
    ),
)


def all_features() -> tuple[SuperFeature, ...]:
    """Every roadmap feature, in display order."""
    return FEATURES


def by_status(status: str) -> list[SuperFeature]:
    """Features with the given ``status`` (``SHIPPED`` / ``SOON``)."""
    return [f for f in FEATURES if f.status == status]


def get(key: str) -> SuperFeature | None:
    """The feature with ``key``, or ``None``."""
    return next((f for f in FEATURES if f.key == key), None)
