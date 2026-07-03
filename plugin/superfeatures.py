# SPDX-License-Identifier: GPL-3.0-or-later
"""Super-feature registry — the single source of truth for the roadmap.

These are the "things KiCad can *never* do" — features that need an LLM's
semantic understanding (intent, function, external knowledge) on top of KiCad's
geometry+netlist. The chat panel renders one button per entry (see
``chat_dialog._build_superfeature_bar``) and ``docs/superfeatures.md`` is the
human-readable narrative of the same list.

Living document: when a feature ships, flip its ``status`` from ``SOON`` to
``SHIPPED`` here AND give it a ``prompt`` — the canonical chat instruction the
button dispatches on click (``chat_dialog._on_superfeature`` sends it like a
typed message, with the current KiCad selection prepended as context). A
``SOON`` button is already fully written — hover shows the pitch, a click
prints the "coming soon" description — so the GUI advertises the roadmap from
day one.

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
    prompt   SHIPPED only: the canonical chat instruction the button sends on
             click. Written to honour the anti-toolcall-explosion rules
             (CLAUDE.md): name the backing MCP tool, no render, respect an
             existing selection, report instead of re-reading state.
    """
    key: str
    label: str
    name: str
    status: str
    tooltip: str
    moat: str
    selection_aware: bool = True
    prompt: str = ""


# Order = display order. "Entwirren" leads — it is the current focus.
FEATURES: tuple[SuperFeature, ...] = (
    SuperFeature(
        key="untangle",
        label="🧶 Entwirren",
        name="Entwirren — Ratsnest-Entkreuzung fürs Routing",
        status=SHIPPED,
        tooltip=("Ordnet den Bauteil-Haufen so an, dass sich die Luftlinien "
                 "möglichst wenig kreuzen — ein sauberer, routbarer "
                 "Startpunkt. Zeigt erst den Plan mit Score, ordnet nach "
                 "deinem Go in einem Zug an."),
        moat=("KiCad hat keinen Erstplatzierer; Kreuzungen zu minimieren "
              "erfordert Reasoning über die ganze Netz-Topologie."),
        prompt=("Entwirren: Ziel ist ein kreuzungsarmer, routbarer "
                "Platzierungs-Startpunkt. Steht oben im Kontext eine Auswahl, "
                "entwirre NUR diese Bauteile (der Rest des Boards ist fixer "
                "Anker), sonst das ganze Board. Ablauf strikt: (1) EINMAL "
                "lesen: list_pcb_footprints und analyze_pcb_nets für die "
                ".kicad_pcb im Projektordner (per Glob finden, nicht "
                "nachfragen). (2) Entwirre IM KOPF: minimiere Kreuzungen der "
                "Signal-Luftlinien (GND/Power zählen nicht — sie werden "
                "Kupferflächen), keine Footprint-Überlappungen, kompakt "
                "bleiben. (3) Prüfe Ist-Stand und deinen Kandidaten mit dem "
                "nicht-mutierenden evaluate_layout (hypothetische Positionen; "
                "maximal 3 Bewertungs-Durchgänge, Board bleibt unberührt). "
                "(4) Zeige mir den Plan kompakt — welches Bauteil wohin (Ref, "
                "(x, y) in mm), Score vorher → nachher (signal_crossings, "
                "overlaps, wirelength_mm) — und zeichne die Zielpositionen "
                "als GEISTER-VORSCHAU auf den Skizzen-Layer: EIN "
                "ipc_draw_markers-Aufruf mit allen Zielen (type=cross, "
                "label_text=Ref). Dann WARTE auf mein Go; ist der Rest "
                "nicht-planar (Layer/Vias nötig), sage das ehrlich. "
                "(5) Erst nach dem Go: alles in EINEM gebündelten Zug über "
                "die Live-Tools umsetzen (Board ist offen: ipc_*-Tools, "
                "Moves bündeln), danach genau EIN check_connectivity und EIN "
                "ipc_clear_markers — die Vorschau wird auch bei Ablehnung "
                "weggeräumt. Kein pcb_render zwischendrin."),
    ),
    # (kein eigenes "Auswahl entwirren"-Feature: Selektion-Scoping ist der
    #  GLOBALE Vertrag jedes Buttons — ohne Auswahl boardweit, mit Auswahl nur
    #  die markierten Bauteile. Das Panel zeigt beim Klick an, was gilt.)
    SuperFeature(
        key="bus_radar",
        label="🚌 Bus-Radar",
        name="Bus-Radar — Bus-Teilnehmer finden",
        status=SHIPPED,
        tooltip=("Listet und markiert alle Teilnehmer + Pins eines Busses "
                 "(I²C, SPI, Datenbus …) als *eine* Bedeutungseinheit — nicht "
                 "nur die Einzelnetze."),
        moat=("KiCad kennt Einzelnetze, aber nicht den Bus als semantische "
              "Gruppe."),
        prompt=("Bus-Radar: Rufe list_bus_members für die .kicad_pcb im "
                "Projektordner auf (per Glob finden, nicht nachfragen; ohne "
                "bus-Parameter = alle Busse). Steht oben im Kontext eine "
                "Auswahl, zeige nur Busse, an denen diese Bauteile hängen. "
                "Liste je Bus die Teilnehmer als Ref.Pin mit den EXAKTEN "
                "Netznamen aus der Tool-Ausgabe. Keine Board-Änderung, kein "
                "pcb_render."),
    ),
    SuperFeature(
        key="datasheet_diff",
        label="📄 Datenblatt-Abgleich",
        name="Datenblatt-Abgleich",
        status=SHIPPED,
        tooltip=("Vergleicht die Beschaltung eines ICs mit seinem Datenblatt "
                 "(PDF unter docs/<Value>.pdf): Entkopplung, Pin-Beschaltung, "
                 "fehlende externe Bauteile. IC markieren und klicken — ohne "
                 "Auswahl zeigt es erst, welche Datenblätter da sind/fehlen."),
        moat="KiCad weiß nichts von Datenblättern — der Abgleich ist reine Bedeutungs-Arbeit.",
        prompt=("Datenblatt-Abgleich: Steht oben im Kontext eine Auswahl mit "
                "einem IC (U-Referenz), rufe review_ic_against_datasheet für "
                "genau dieses IC auf (project_path = die .kicad_pro im "
                "Projektordner, per Glob finden) und mache dann den "
                "eigentlichen Abgleich: vergleiche Pin-Tabelle und "
                "Schaltplan-Ausschnitt mit der Datenblatt-Seite — "
                "Entkopplung, Pin-Beschaltung, fehlende externe Bauteile — "
                "und berichte Befunde mit EXAKTEN Ref-/Netznamen. Ohne "
                "Auswahl: rufe zuerst list_missing_datasheets auf und zeige, "
                "für welche ICs ein PDF unter docs/<Value>.pdf bereitliegt "
                "und welche fehlen (mit Datasheet-URL zum Besorgen), und "
                "frage, welches IC ich reviewen will. Fehlt das PDF des "
                "gewählten ICs, sage das ehrlich statt zu raten. Keine "
                "Board-Änderung, kein pcb_render."),
    ),
    SuperFeature(
        key="semantic_erc",
        label="🛡️ Design-Wächter",
        name="Design-Wächter — semantischer ERC",
        status=SHIPPED,
        tooltip=("Prüfung jenseits des ERC: fehlende Pull-ups am I²C, fehlende "
                 "Abblock-Cs nah am IC, unpassende Quarz-Load-Caps, Power-Netz "
                 "ohne Stützung …"),
        moat="KiCads ERC prüft Netz-Syntax, nicht die *Absicht* der Schaltung.",
        prompt=("Design-Wächter: Rufe audit_design für die .kicad_pcb im "
                "Projektordner auf (per Glob finden, nicht nachfragen). Steht "
                "oben im Kontext eine Auswahl, berichte nur Befunde, die diese "
                "Bauteile/Netze betreffen. Fasse die Befunde nach Schwere "
                "zusammen (kritisch zuerst), nenne Bauteile und Netze mit "
                "ihren EXAKTEN Namen und schlage je Befund die konkrete "
                "Abhilfe vor. Keine Board-Änderung, kein pcb_render."),
    ),
    SuperFeature(
        key="test_points",
        label="🔎 Test-Punkt-Wächter",
        name="Test-Punkt-Wächter — probe-bar für Bring-up & Serientest?",
        status=SHIPPED,
        tooltip=("Rankt Netze nach Test-Wichtigkeit (Versorgung, Reset, Clock, "
                 "Bus) und meldet, welche kritischen Netze keinen Prüfpunkt/"
                 "Stecker-Zugang haben — die blinden Flecken für Flying-Probe/"
                 "Nadeladapter und Bring-up. Zeigt Abdeckung in %."),
        moat=("KiCad kennt Netze, aber nicht ihre *Wichtigkeit* für den Test — "
              "das ist Fertigungs-/Bring-up-Wissen."),
        prompt=("Test-Punkt-Wächter: Rufe audit_test_points für die .kicad_pcb "
                "im Projektordner auf (per Glob finden, nicht nachfragen). "
                "Steht oben im Kontext eine Auswahl, übergib deren Referenzen "
                "als refs-Filter. Berichte die kritische Abdeckung in %, die "
                "blinden Netze mit EXAKTEN Namen und je blindem Netz einen "
                "konkreten Prüfpunkt-Vorschlag (wo, warum dort). Keine "
                "Board-Änderung, kein pcb_render."),
    ),
    SuperFeature(
        key="pin_swap",
        label="🔀 Pin-Tausch",
        name="Pin-Tausch — GPIO ans Routing anpassen",
        status=SHIPPED,
        tooltip=("Legt funktional gleichwertige GPIOs/Pins um, damit das "
                 "Routing kreuzungsfrei wird — Schaltplan und PCB werden "
                 "kohärent nachgezogen."),
        moat="KiCad hat kein Konzept funktional austauschbarer Pins (Pinmux).",
        prompt=(
                "Pin-Tausch: NUR VORSCHLAGEN — nichts ändern ohne mein "
                "ausdrückliches Go. (1) Lies EINMAL list_pcb_footprints und "
                "analyze_pcb_nets für die .kicad_pcb im Projektordner (per "
                "Glob finden); steht oben eine Auswahl, betrachte nur deren "
                "Netze/Bauteile. (2) Finde Routing-Kreuzungen, die sich durch "
                "Umlegen funktional gleichwertiger Pins lösen ließen (GPIOs, "
                "freie Gatter/OpAmp-Hälften) — nutze dein Pinmux-Wissen zum "
                "konkreten Controller und sage ehrlich, wenn du die "
                "Austauschbarkeit eines Pins nicht sicher weißt. (3) Zeige je "
                "Vorschlag: Netz X von Pin A nach Pin B, warum, und was sich "
                "am Schaltplan UND am PCB ändert. (4) Erst nach meinem Go: "
                "Schaltplan über die Schaltplan-Tools (z. B. connect_pins) "
                "ändern — NUR bei geschlossenem Eeschema — und die PCB-Netze "
                "nachziehen; danach EIN check_connectivity. Kein pcb_render.")
    ),
    SuperFeature(
        key="explain_board",
        label="💡 Board erklären",
        name="Board erklären",
        status=SHIPPED,
        tooltip=("Rekonstruiert aus Netzliste + Bauteilen, was das Board tut: "
                 "Funktionsblöcke, Schnittstellen, Stromversorgung. Mit "
                 "Auswahl: erklärt gezielt diesen Teilschaltkreis."),
        moat="KiCad hat ein Modell der Verbindungen, keines der Funktion.",
        prompt=("Board erklären: Lies EINMAL list_pcb_footprints und "
                "analyze_pcb_nets für die .kicad_pcb im Projektordner (per "
                "Glob finden, nicht nachfragen) und rekonstruiere daraus, was "
                "das Board tut: Funktionsblöcke (Versorgung, Controller, "
                "Schnittstellen, Treiber, Sensorik …), wie sie zusammenspielen "
                "und wie Strom und Signale fließen. Steht oben im Kontext "
                "eine Auswahl, erkläre stattdessen gezielt diesen "
                "Teilschaltkreis und seine Rolle im Board. Benenne Bauteile "
                "und Netze mit ihren EXAKTEN Namen. Keine Board-Änderung, "
                "kein pcb_render."),
    ),
    SuperFeature(
        key="nl_navigation",
        label="🧭 Netz-Navigator",
        name="Netz-Navigator — Fragen in normaler Sprache",
        status=SHIPPED,
        tooltip=("Frag in normaler Sprache: welcher Pin treibt Motor-Enable, "
                 "was liegt sonst auf U1.7 — semantische Netz-/Pin-Suche."),
        moat="KiCad zeigt Netze an, sucht aber nicht nach *Bedeutung*.",
        prompt=(
                "Netz-Navigator: Steht oben im Kontext eine Auswahl, erkläre "
                "semantisch, was daran hängt: lies EINMAL analyze_pcb_nets "
                "(bei Bedarf find_tracks_by_net für einzelne Netze) und "
                "beschreibe Funktion und Partner der markierten Pins/Netze in "
                "normaler Sprache — mit EXAKTEN, klickbaren Ref-/Netznamen. "
                "Ohne Auswahl: gib eine kompakte semantische Netz-Landkarte "
                "des Boards (Versorgungen, Busse, auffällige Signale) und "
                "nenne drei Beispiel-Fragen, die ich dir direkt stellen kann "
                "('Welcher Pin treibt …?', 'Was liegt sonst auf U1.7?'). Keine "
                "Board-Änderung, kein pcb_render.")
    ),
    SuperFeature(
        key="select_place",
        label="📐 Ausrichten & Anordnen",
        name="Ausrichten & Anordnen",
        status=SHIPPED,
        tooltip=("Markierte Bauteile per Satz ordnen: bündig ausrichten, im "
                 "Raster verteilen, spiegeln, als Array — mit korrekter "
                 "Rotations- und B.Cu-Mathematik."),
        moat="KiCad hat kein sprachgesteuertes, absicht-basiertes Anordnen.",
        prompt=(
                "Ausrichten & Anordnen: Arbeite auf der Auswahl oben im "
                "Kontext; ist nichts markiert, sage in EINEM Satz, dass ich "
                "zuerst Bauteile markieren soll, und stoppe. Geht die "
                "gewünschte Anordnung nicht aus meiner Nachricht hervor, frage "
                "EINMAL kurz (bündig ausrichten / im Raster verteilen / auf "
                "Kreis / an Bauteil X ausrichten). Dann: Plan zeigen (Ref → "
                "Zielposition (x, y) in mm; KiCad-CW-Rotation und B.Cu- "
                "Spiegelung korrekt — Welt-Koordinaten über "
                "compute_pad_world_positions, nie selbst rechnen) und erst "
                "nach meinem Go alles in EINEM gebündelten Zug über die Live- "
                "Tools umsetzen (ipc_move_items / ipc_set_footprint_pose). "
                "Kein pcb_render.")
    ),
    SuperFeature(
        key="polar_board",
        label="⊙ Polar-Board",
        name="Polar-Board — Radial-Layout für runde Boards",
        status=SHIPPED,
        tooltip=("Platzieren und Routen in Polarkoordinaten (Radius + Winkel) "
                 "statt X/Y: LEDs gleichmäßig auf einem Kreis, Stecker rund um "
                 "den Rand, radiale und konzentrische Leiterbahnen. Der Klick "
                 "zeigt die Grid-Konfiguration und den Workflow; geändert "
                 "wird erst auf dein Go."),
        moat=("KiCad rechnet nur kartesisch; runde Boards zwingen sonst zur "
              "Handrechnung von Winkel und Radius."),
        prompt=("Polar-Board: Prüfe mit EINEM polar_grid-Aufruf "
                "(op=check_grid_config, Referenz-Defaults) die "
                "Polar-Konfiguration für die .kicad_pcb im Projektordner (per "
                "Glob finden) und zeige die resultierenden Parameter "
                "(Zentrum, Ringe/Radien, Speichen). Erkläre dann kurz den "
                "Radial-Workflow: Bauteile auf Ring/Speiche platzieren "
                "(place_on_ring/place_on_spoke), konzentrische Bögen und "
                "radiale Segmente routen (add_polar_arc/add_radial_segment) "
                "— alles in Radius + Winkel statt X/Y. Steht oben im Kontext "
                "eine Auswahl, schlage konkret vor, wie genau diese Bauteile "
                "auf einen Ring kämen (nur Vorschlag). Ändere NICHTS ohne "
                "mein Go. Kein pcb_render."),
    ),
    SuperFeature(
        key="sketch_layer",
        label="🖊️ Skizzen-Layer",
        name="Skizzen-Layer — gemeinsamer Notiz-/Hilfslayer",
        status=SHIPPED,
        tooltip=("Der gemeinsame Skizzen-Layer (User.9, in KiCad sichtbar als \"MCP.Skizze\"): du zeichnest "
                 "Absichten, der Agent Vorschläge und Marker. Der Klick zeigt, "
                 "was drauf liegt, und bietet Legende zeichnen oder Leeren an "
                 "(erst nach deinem Go)."),
        moat=("KiCad hat keinen dedizierten, von Mensch UND Agent gemeinsam "
              "genutzten Skizzen-/Vorschau-Kanal."),
        prompt=("Skizzen-Layer: Lies mit ipc_list_markers, was auf dem "
                "gemeinsamen Skizzen-Layer (User.9) des offenen Boards liegt, "
                "und berichte kompakt, wie viele Marker/Skizzen es sind. "
                "Biete dann an: (a) Legende zeichnen "
                "(ipc_draw_sketch_legend), (b) Layer leeren "
                "(ipc_clear_markers) — beides erst nach meinem Go, nicht "
                "sofort ausführen. Ist der Layer leer, sage das in einem Satz "
                "und erkläre kurz, wofür er da ist (du zeichnest Absichten, "
                "der Agent Vorschläge/Marker). Kein pcb_render."),
    ),
    SuperFeature(
        key="sketch_conductor",
        label="✏️ Skizzen-Dirigent",
        name="Skizzen-Dirigent — gezeichnete Absicht → Kupfer",
        status=SHIPPED,
        tooltip=("Zeichne Linien/Bögen auf den Skizzen-Layer (User.9, in KiCad \"MCP.Skizze\") — ein Klick "
                 "gießt sie als Kupfer-Leiterbahnen auf F.Cu (ein einziger "
                 "Undo-Schritt)."),
        moat="KiCad interpretiert keine gezeichnete Absicht.",
        prompt=("Skizzen-Dirigent: Prüfe zuerst mit ipc_markup_to_tracks und "
                "dry_run=true, was auf dem Markup-Layer User.9 des offenen "
                "Boards liegt. Liegt dort nichts, sage das in EINEM Satz und "
                "stoppe. Sonst setze es mit EINEM zweiten Aufruf "
                "(dry_run=false, Ziel F.Cu, sofern ich nichts anderes sage) in "
                "Kupfer um — das ist ein einziger Undo-Schritt — und berichte "
                "created/skipped aus dem Tool-Result. Kein pcb_render."),
    ),
    SuperFeature(
        key="watch_mode",
        label="👁️ Mitdenken-Modus",
        name="Mitdenken-Modus — Live-Assistenz beim Routen",
        status=SHIPPED,
        tooltip=("Während du von Hand routest, kommentiert Claude live: "
                 "Clearance-Unterschreitung, fragmentierte Netze, DRC-Risiken."),
        moat="KiCad hat kein mitlaufendes, verstehendes Assistenz-Auge.",
        prompt=(
                "Mitdenken-Review: Rufe live_summarize_user_changes auf und "
                "fasse zusammen, was seit dem letzten Stand von Hand am "
                "offenen Board geändert wurde. Bewerte die Änderungen "
                "fachlich: Clearance-Risiken, fragmentierte oder unfertige "
                "Netze, DRC-Gefahren, fragwürdige Track-Breiten — mit EXAKTEN "
                "Refs/Netznamen. Steht oben eine Auswahl, fokussiere darauf. "
                "Gibt es nichts zu berichten, sage das ehrlich in einem Satz. "
                "Ein Klick = ein Review — die IPC-API liefert keine Events, "
                "ein Dauer-Beobachten gibt es also (noch) nicht. Keine Board- "
                "Änderung, kein pcb_render.")
    ),

    # -- Elektrik & Fertigung (DFM) -------------------------------------------
    SuperFeature(
        key="ampacity",
        label="🔥 Stromtragfähigkeit",
        name="Stromtragfähigkeit — Leiterbahn-Breite vs. Strom",
        status=SHIPPED,
        tooltip=("Prüft jede Leiterbahn-Breite gegen den Strom, den ihr Netz "
                 "trägt (IPC-2221), nennt unterdimensionierte Segmente und "
                 "die nötige Breite. Ströme leitet der Agent aus den "
                 "Bauteil-Rollen ab oder fragt nach."),
        moat=("KiCad kennt keine Ströme — wie viel Strom ein Netz führt, steht "
              "in der Design-Absicht, nicht im Layout."),
        prompt=("Stromtragfähigkeit: (1) Rufe check_ampacity OHNE currents für "
                "die .kicad_pcb im Projektordner auf (per Glob finden, nicht "
                "nachfragen) — das liefert das Breiten-Inventar je Netz. "
                "Steht oben im Kontext eine Auswahl, übergib deren Netze als "
                "nets-Filter. (2) Leite aus den Bauteil-Rollen plausible "
                "Ströme für die Power-/Lastnetze ab (Versorgungs-Rails, "
                "Motor-/LED-/Heizer-Treiber; Signalnetze brauchen keine "
                "Prüfung) und SAGE mir deine Annahmen; bist du bei einem "
                "Netz unsicher, frage kurz nach statt zu raten. (3) Rufe "
                "check_ampacity EINMAL mit dem currents-JSON auf und "
                "berichte die Verstöße: Netz, Layer, Ist- vs. nötige Breite "
                "in mm, betroffene Segmente als (x, y)-Koordinaten. "
                "IPC-2221, Temperaturanstieg default 10 K, 1 oz Kupfer — "
                "nenne die Parameter im Bericht. Keine Board-Änderung, kein "
                "pcb_render."),
    ),
    SuperFeature(
        key="xtal_caps",
        label="⌚ Quarz-Load-Caps",
        name="Quarz-Load-Caps — richtige Lastkapazität berechnen",
        status=SHIPPED,
        tooltip=("Berechnet die korrekten Load-Kondensatoren für einen Quarz "
                 "aus dessen Datenblatt-CL und der geschätzten Streukapazität "
                 "(C = 2·(CL − Cstray)) und prüft, ob die verbauten Werte passen."),
        moat=("KiCad kennt weder den CL-Wert eines Quarzes noch die "
              "Load-Cap-Formel."),
        prompt=(
                "Quarz-Load-Caps: (1) Rufe audit_design für die .kicad_pcb im "
                "Projektordner auf (per Glob finden) und nimm daraus die "
                "Quarz-Befunde (fehlende Load-Caps). (2) Für vorhandene Load- "
                "Caps: lies die verbauten Werte aus den Bauteil-Values, hole "
                "das CL des Quarzes aus dem Datenblatt (docs/<Value>.pdf) oder "
                "frage mich danach, und rechne C = 2·(CL − Cstray) mit Cstray "
                "≈ 3–5 pF — Rechenweg und Annahmen offenlegen. (3) Urteil je "
                "Quarz: passt / zu groß / zu klein, mit empfohlenem E-Reihen- "
                "Wert. Steht oben eine Auswahl, prüfe nur deren Quarze. Keine "
                "Board-Änderung, kein pcb_render.")
    ),
    SuperFeature(
        key="via_cost",
        label="🔩 Via-Optimierung",
        name="Via-Optimierung — Anzahl & Kosten senken",
        status=SHIPPED,
        tooltip=("Senkt Via-Anzahl und Fertigungskosten: findet teure "
                 "Blind/Buried-Vias, die sich gefahrlos in Through-Vias wandeln "
                 "lassen. Der Klick liefert den Report; umgesetzt wird erst auf "
                 "dein Go."),
        moat=("KiCad zählt Vias, bewertet aber ihre Fertigungskosten und "
              "Notwendigkeit nicht."),
        prompt=("Via-Optimierung (nur Report): Rufe via_promote mit "
                "dry_run=true für die .kicad_pcb im Projektordner auf (per "
                "Glob finden, nicht nachfragen). Berichte, wie viele "
                "Blind/Buried-Vias sich kollisionsfrei zu Through-Vias wandeln "
                "ließen, auf welchen Netzen (EXAKTE Namen) und was das für die "
                "Fertigung bedeutet. Ändere NICHTS — die Umsetzung machst du "
                "erst nach meinem ausdrücklichen Go. Kein pcb_render."),
    ),
    SuperFeature(
        key="thermal",
        label="🌡️ Thermik",
        name="Thermik — Verlustleistungs-Hotspots",
        status=SHIPPED,
        tooltip=("Findet Verlustleistungs-Hotspots (Regler, MOSFETs, Shunts) "
                 "und schlägt Kühl-Kupfer, Thermal-Vias und Abstände vor."),
        moat="KiCad hat kein Verlustleistungs- oder Wärmemodell.",
        prompt=(
                "Thermik: Lies EINMAL list_pcb_footprints und rufe "
                "audit_power_tree für die .kicad_pcb auf (per Glob finden). "
                "Identifiziere die Verlustleistungs-Kandidaten (Linearregler, "
                "MOSFETs, Shunts, Treiber, Gleichrichter) und schätze je "
                "Kandidat die Verlustleistung aus Rolle und Rails — lege deine "
                "Annahmen offen und frage bei den Top-Kandidaten nach den "
                "realen Strömen, wenn unklar. Schlage je Hotspot konkrete "
                "Maßnahmen vor: Kühl-Kupferfläche, Thermal-Vias "
                "(Anzahl/Raster), Abstand zu empfindlichen Nachbarn — als "
                "Vorschlag mit Koordinaten. KEINE Umsetzung ohne mein Go. "
                "Steht oben eine Auswahl, nur diese Bauteile. Kein pcb_render.")
    ),
    SuperFeature(
        key="operating_temp",
        label="🌡️ Betriebstemperatur",
        name="Betriebstemperatur — Junction-Temp & Derating-Reserve",
        status=SHIPPED,
        tooltip=("Schätzt die reale Betriebs-/Sperrschichttemperatur je Bauteil "
                 "(Tj = Ta + P·θ) aus Verlustleistung, Umgebungstemperatur und "
                 "Wärmewiderstand — und wie viel Derating-Reserve bleibt."),
        moat=("KiCad hat kein Modell für Wärmewiderstand, Umgebung oder "
              "Verlustleistung."),
        prompt=(
                "Betriebstemperatur: Lies EINMAL list_pcb_footprints für die "
                ".kicad_pcb (per Glob finden) und bestimme die thermisch "
                "relevanten Bauteile (Regler, MOSFETs, Leistungswiderstände, "
                "LED-Treiber). Rechne je Bauteil Tj = Ta + P·θJA: P aus "
                "Rolle/Rails geschätzt (Annahmen offenlegen), θJA aus dem "
                "Datenblatt in docs/ oder aus typischen Package-Werten (dann "
                "ehrlich als 'typisch' kennzeichnen), Ta von mir erfragen "
                "(Default 25 °C; Gehäuse?). Berichte je Bauteil Tj und die "
                "Derating-Reserve bis Tj_max und markiere kritische Fälle. "
                "Steht oben eine Auswahl, nur diese Bauteile. Keine Board- "
                "Änderung, kein pcb_render.")
    ),
    SuperFeature(
        key="slew_rate",
        label="📐 Slew-Rate",
        name="Slew-Rate — schafft der Verstärker/Treiber das Signal?",
        status=SHIPPED,
        tooltip=("Rechnet, ob ein OpAmp/Treiber die geforderte Signalflanke "
                 "schafft (Slew-Rate-Limit) bzw. die Flankensteilheit digitaler "
                 "Signale — relevant für Verzerrung, Timing und EMV."),
        moat=("KiCad rechnet kein dynamisches Signalverhalten aus Bauteil-Specs."),
        prompt=(
                "Slew-Rate: Lies EINMAL list_schematic_components für den "
                "Schaltplan im Projektordner (per Glob finden) und finde "
                "Verstärker, Treiber und Komparatoren. Frage nach dem Signal "
                "(Amplitude, Frequenz bzw. Flankenzeit), falls nicht genannt. "
                "Rechne je Stufe, ob die Slew-Rate reicht: SR_nötig ≈ 2π·f·V̂ "
                "für Sinus bzw. ΔV/Δt für Flanken — SR aus dem Datenblatt in "
                "docs/ oder von mir erfragen; Rechenweg und Annahmen "
                "offenlegen. Urteil je Stufe: reicht / Grenzfall / zu langsam, "
                "mit der Konsequenz (Verzerrung, Timing, EMV). Steht oben eine "
                "Auswahl, nur diese Bauteile. Keine Änderung, kein pcb_render.")
    ),
    SuperFeature(
        key="impedance",
        label="〰️ Impedanz",
        name="Impedanz — controlled impedance aus dem Stackup",
        status=SHIPPED,
        tooltip=("Berechnet Breite und Abstand für eine definierte Impedanz "
                 "(USB, Ethernet, RF) aus dem Lagenaufbau."),
        moat="KiCad rechnet keine Impedanz aus Stackup und Geometrie.",
        prompt=(
                "Impedanz: Lies den Lagenaufbau über pcb_eval vom warmen Board "
                "(Stackup: Lagen, Dielektrika-Dicken, εr, Kupferdicken); sind "
                "dort keine Stackup-Daten gepflegt, frage mich nach Lagenzahl, "
                "Dicken und εr. Frage nach dem Ziel, falls unklar (USB 90 Ω "
                "diff, Ethernet 100 Ω diff, RF 50 Ω single-ended …), und "
                "rechne Breite und ggf. Paar-Abstand mit den üblichen "
                "Näherungen (IPC-2141 / Microstrip / Stripline) — Rechenweg "
                "offenlegen. EHRLICH: das sind Näherungsformeln, kein "
                "Feldlöser — für die Fertigung den Stackup-Rechner des "
                "Fertigers gegenprüfen. Steht oben eine Auswahl mit Tracks, "
                "bewerte deren Ist-Breite gegen das Ziel. Keine Änderung, kein "
                "pcb_render.")
    ),
    SuperFeature(
        key="dfm_check",
        label="🏭 DFM-Check",
        name="DFM-Check — Fertigbarkeit gegen echte Fab-Regeln",
        status=SHIPPED,
        tooltip=("Prüft die Fertigbarkeit gegen die Regeln eines konkreten "
                 "Fertigers (min. Track/Space, Annular Ring, Acid Traps, "
                 "Silk-über-Pad) — nicht nur generisches DRC."),
        moat=("KiCads DRC kennt keine fertiger-spezifischen DFM-Regeln oder "
              "deren Begründung."),
        prompt=(
                "DFM-Check: Frage zuerst kurz nach Fertiger und Prozess (z. B. "
                "JLCPCB 2-Lagen Standard), falls nicht genannt. Dann: (1) "
                "run_drc_check für die .kicad_pcb (per Glob finden) für die "
                "generischen Verstöße, (2) get_board_stats für minimale "
                "Breiten, Via-Größen und Lagenzahl, (3) vergleiche gegen die "
                "publizierten Regeln dieses Fertigers aus deinem Wissen (min "
                "Track/Space, Via-Drill/Annular-Ring, Silk-über-Pad, Acid "
                "Traps) — kennzeichne die Regelwerte ehrlich als Wissensstand "
                "mit Datum, nicht als Live-Katalog. Berichte nur echte DFM- "
                "Risiken mit Koordinaten/Refs und dem konkreten Fix. Steht "
                "oben eine Auswahl, nur dieser Bereich. Keine Änderung, kein "
                "pcb_render.")
    ),
    SuperFeature(
        key="cost_estimate",
        label="💰 Kosten-Schätzer",
        name="Kosten-Schätzer — was macht das Board teuer",
        status=SHIPPED,
        tooltip=("Grobe Fertigungskosten aus Boardfläche, Lagenzahl, Via-Anzahl "
                 "und BOM — plus was die Kosten treibt."),
        moat="KiCad hat kein Kostenmodell.",
        prompt=(
                "Kosten-Schätzer: Rufe get_board_stats und analyze_bom für das "
                "Projekt auf (Pfade per Glob finden). Schätze die "
                "Fertigungskosten als Größenordnung: Boardfläche × Lagenzahl "
                "(Basispreis), Via-Anzahl und -Typen (Blind/Buried = teuer), "
                "Sonderprozesse (kleinste Breite/Drill unter Fab-Standard?), "
                "BOM-Seite (Anzahl unterschiedlicher Werte = Feeder-Kosten, "
                "Extended-Teile = Ladegebühren). Zeige die Kostentreiber "
                "sortiert und zwei bis drei konkrete Hebel — z. B. Via- "
                "Optimierung oder BOM-Konsolidierung, dafür gibt es hier "
                "Buttons. Zahlen EHRLICH als Größenordnung kennzeichnen, kein "
                "Live-Preis. Keine Änderung, kein pcb_render.")
    ),

    # -- Simulation & Beschaffung ---------------------------------------------
    SuperFeature(
        key="simulate",
        label="📈 Simulation",
        name="Simulation — Verhalten & Bandbreite verstehen",
        status=SHIPPED,
        tooltip=("Simuliert das Schaltungsverhalten (z. B. Verstärker-Bandbreite, "
                 "Frequenzgang, Arbeitspunkt) über SPICE und erklärt das Ergebnis "
                 "in Klartext — statt nur Kurven auszuspucken."),
        moat=("KiCad kann ngspice starten, aber weder die *Frage* noch das "
              "*Ergebnis* interpretieren."),
        prompt=(
                "Simulation: Rufe extract_schematic_netlist für den "
                "Schaltplan auf (per Glob finden); steht oben eine Auswahl, "
                "analysiere nur diesen Teilschaltkreis. Frage, was mich "
                "interessiert (Arbeitspunkt, Verstärkung/Bandbreite, "
                "Filter-Eckfrequenz, Zeitverhalten), falls unklar. Baue ein "
                "SELBSTSTÄNDIGES SPICE-Deck (Analysen + .print/.measure; "
                "Modelle beilegen oder ehrlich vereinfachen — nenne die "
                "Vereinfachungen) und führe es mit run_spice_sim aus; "
                "erkläre die Zahlen dann in Klartext. Meldet das Tool, dass "
                "ngspice fehlt: analysiere ANALYTISCH (Kleinsignal, RC/RL-"
                "Eckfrequenzen, OpAmp-Idealmodell) mit offenem Rechenweg und "
                "liefere das Deck als Codeblock zum Kopieren. Keine Änderung "
                "am Projekt, kein pcb_render.")
    ),
    SuperFeature(
        key="sim_models",
        label="🧬 SPICE-Modelle",
        name="Simulationsmodelle ergänzen",
        status=SHIPPED,
        tooltip=("Findet und hängt das passende SPICE-Modell je Bauteil an, damit "
                 "die Simulation überhaupt läuft — der lästige manuelle Schritt "
                 "vor jeder Simulation."),
        moat=("KiCad verlangt manuelle Modell-Zuordnung und weiß nicht, welches "
              "Modell zu welchem Bauteil passt."),
        prompt=(
                "SPICE-Modelle: Lies list_schematic_components für den "
                "Schaltplan (per Glob finden); steht oben eine Auswahl, nur "
                "diese Bauteile. Ermittle je aktivem Bauteil (Transistoren, "
                "Dioden, OpAmps, Regler), welches SPICE-Modell passt: suche "
                "per WebSearch nach Hersteller-Modellen (.lib/.subckt) und "
                "liefere je Bauteil Modellname, Download-Link und die "
                "Eintragung für KiCads Simulationsfelder (Sim.Library / "
                "Sim.Name / Sim.Pins) zum Übernehmen. R/L/C brauchen keine "
                "Modelle. Ins Schaltplan-File eintragen nur nach meinem Go und "
                "nur bei geschlossenem Eeschema. Kein pcb_render.")
    ),
    SuperFeature(
        key="bom_consolidate",
        label="💰 BOM-Konsolidierung",
        name="BOM-Konsolidierung — E-Reihe standardisieren, Feeder sparen",
        status=SHIPPED,
        tooltip=("Fasst fast-gleiche R/C-Werte (10k neben 10,2k neben 9,1k) auf "
                 "Standard-E-Reihen-Werte zusammen — weniger Bestückungs-Feeder "
                 "und günstigere Stückzahlen, ohne ein Bauteil über die Toleranz "
                 "zu verschieben. Schlägt vor, ändert nicht."),
        moat=("KiCad kennt weder E-Reihen noch Feeder/Bestellmengen — das ist "
              "Fertigungs-Wissen über der Netzliste."),
        prompt=("BOM-Konsolidierung: Rufe consolidate_bom für die .kicad_pcb "
                "im Projektordner auf (per Glob finden, nicht nachfragen). "
                "Steht oben im Kontext eine Auswahl, berichte nur Vorschläge, "
                "die diese Referenzen betreffen. Zeige die Zusammenlegungen "
                "kompakt: Ist-Werte → E-Reihen-Zielwert, betroffene Refs, "
                "gesparte Feeder/Werte-Typen. Es sind reine VORSCHLÄGE — "
                "ändere nichts am Board. Kein pcb_render."),
    ),
    SuperFeature(
        key="preferred_parts",
        label="🏭 Fab-Standardteile",
        name="Fab-Standardteile — No-Load-Fee-Teile bevorzugen (JLCPCB/Seeed/…)",
        status=SHIPPED,
        tooltip=("Bestücker verlangen pro Bauteiltyp außerhalb ihrer Hausbibliothek "
                 "eine Feeder-Ladegebühr (JLCPCB Basic vs Extended, Seeed OPL …). "
                 "Mappt jeden R/C-Wert+Bauform auf das Vorzugsteil des Fertigers "
                 "und schätzt die gesparte Gebühr. Fab-agnostisch: ein datierter "
                 "Snapshot je Fertiger."),
        moat=("KiCad hat kein Wissen über Distributoren, Fab-Kataloge, "
              "Lagerbestand oder Ladegebühren."),
        prompt=("Fab-Standardteile: Rufe suggest_preferred_parts für die "
                ".kicad_pcb im Projektordner auf (per Glob finden, nicht "
                "nachfragen; Provider jlcpcb, außer ich nenne einen anderen). "
                "Steht oben im Kontext eine Auswahl, berichte nur Mappings für "
                "diese Referenzen. Zeige je Mapping Ist-Teil → Vorzugsteil und "
                "die geschätzte gesparte Ladegebühr, mit Summe am Ende. Reine "
                "Vorschläge — ändere nichts am Board. Kein pcb_render."),
    ),
    SuperFeature(
        key="bom_sourcing",
        label="🛒 Bauteil-Sourcing",
        name="Bauteil-Sourcing — Verfügbarkeit, Preis & Alternativen",
        status=SHIPPED,
        tooltip=("Prüft live Verfügbarkeit und Preis gegen Distributoren und "
                 "findet pin-kompatible Alternativen für abgekündigte oder "
                 "nicht-lagernde Teile (der Live-Netz-Teil über die "
                 "offline Fab-Standardteil-Prüfung hinaus)."),
        moat=("KiCad hat kein Wissen über Distributoren, Lagerbestand oder "
              "Preise."),
        prompt=(
                "Bauteil-Sourcing: Rufe analyze_bom für das Projekt auf (per "
                "Glob finden); steht oben eine Auswahl, nur diese Bauteile. "
                "Prüfe per WebSearch Verfügbarkeit und Preislage der "
                "KRITISCHEN Teile (ICs, Spezialbauteile — nicht jeden "
                "10k-Widerstand) bei Distributoren (LCSC, Mouser, Digi-Key) "
                "und finde für schlecht lieferbare oder abgekündigte Teile "
                "pin-kompatible Alternativen. Liefere je Teil: Status, Preis- "
                "Größenordnung, Quelle mit Link, gegebenenfalls Alternative "
                "mit Begründung (Pinout/Specs). EHRLICH: Web-Momentaufnahme — "
                "vor der Bestellung selbst verifizieren. Keine Änderung, kein "
                "pcb_render.")
    ),

    # -- Kreativ / grenzüberschreitend ----------------------------------------
    SuperFeature(
        key="photo_reverse",
        label="📷 Foto→Schaltung",
        name="Foto → Schaltung — reverse-engineer aus einem Bild",
        status=SHIPPED,
        tooltip=("Zieh ein Foto einer echten Platine rein — der Agent erkennt "
                 "Bauteile, Beschriftungen und Leiterbahnen und rekonstruiert "
                 "Netzliste/Schaltplan als Ausgangspunkt."),
        moat="KiCad hat keine Bild-Wahrnehmung — das ist reine Multimodal-Arbeit.",
        prompt=(
                "Foto → Schaltung: Bitte lege das Platinen-Foto als Bilddatei "
                "in den Projektordner und nenne mir den Dateinamen — Bilder "
                "kann ich über Read ansehen. Dann: (1) Read auf das Bild; "
                "identifiziere Bauteile, Aufdrucke und sichtbare Leiterbahnen; "
                "(2) rekonstruiere daraus Stückliste und Netz-Hypothesen als "
                "Tabelle mit Konfidenz je Verbindung (sichtbar vs. vermutet); "
                "(3) auf Wunsch baue ich daraus per generate_schematic / "
                "add_schematic_symbols einen Schaltplan-Startpunkt — erst nach "
                "deinem Go. Ist kein Bild genannt, sage in einem Satz, wie es "
                "geht, und stoppe. EHRLICH: verdeckte Leiterbahnen und "
                "Innenlagen kann ein Foto nicht zeigen. Kein pcb_render.")
    ),
    SuperFeature(
        key="datasheet_circuit",
        label="📄 Datenblatt→Schaltung",
        name="Datenblatt → Applikationsschaltung",
        status=SHIPPED,
        tooltip=("Aus dem Datenblatt eines ICs die typische Applikationsschaltung "
                 "generieren (Entkopplung, externe Bauteile, Referenz) — als "
                 "fertigen Schaltungsblock."),
        moat=("KiCad liest keine Datenblätter und kennt keine "
              "Applikationsschaltungen."),
        prompt=(
                "Datenblatt → Schaltung: Nenne mir das IC oder lege sein "
                "Datenblatt als docs/<Value>.pdf ins Projekt. Dann: (1) "
                "extract_circuit_from_pdf auf die Seite mit der "
                "Applikationsschaltung (bzw. extract_pdf_tables für die Pin- "
                "Tabelle), (2) zeige den erkannten Schaltungsblock (Bauteile, "
                "Werte, Verbindungen) als Vorschau, (3) erst nach deinem Go: "
                "apply_circuit_block in den Schaltplan — NUR bei geschlossenem "
                "Eeschema (KiCad 10 hat kein Live-Schaltplan-API). Ist kein "
                "IC/PDF genannt, sage in einem Satz, was ich brauche, und "
                "stoppe. Kein pcb_render.")
    ),
    SuperFeature(
        key="protection_class",
        label="🔌 Schutzklassen",
        name="Schutzklassen — Isolationskonzept nach IEC 61140/60664",
        status=SHIPPED,
        tooltip=("Prüft das Isolationskonzept des Geräts: Schutzklasse I/II/"
                 "III bestimmen, geforderte Kriech-/Luftstrecken je "
                 "Spannungsgrenze aus dem IEC-60664-Snapshot holen "
                 "(get_safety_spacing) und gegen die Ist-Abstände stellen."),
        moat=("KiCad kennt weder Schutzklassen noch Normtabellen — welche "
              "Abstände eine 230-V-Grenze braucht, ist reines Norm-Wissen."),
        prompt=("Schutzklassen-Review: (1) Kläre die Schutzklasse des Geräts "
                "nach IEC 61140 — Klasse I (geerdet, Basisisolierung), "
                "Klasse II (doppelte/verstärkte Isolierung, keine Erde), "
                "Klasse III (SELV/PELV) — frage mich, falls nicht aus dem "
                "Design erkennbar. (2) Bestimme aus analyze_pcb_nets die "
                "Spannungs-Domänen (Netz/HV, PE/geerdet, SELV) und die "
                "Grenzen dazwischen. (3) Hole die GEFORDERTEN Abstände je "
                "Grenze mit get_safety_spacing (working_voltage_v, "
                "nominal_mains_v; insulation=basic für Klasse-I-Grenzen "
                "gegen PE, reinforced für Klasse-II-Grenzen zu berührbaren "
                "Teilen; Default PD 2, Materialgruppe IIIa für FR-4, OVC II "
                "— nenne die gewählten Parameter). Die Werte kommen aus dem "
                "datierten IEC-60664-Snapshot des Tools, NICHT aus deinem "
                "Gedächtnis. (4) Miss die IST-Abstände an den kritischen "
                "Übergängen (center_item_clearance bzw. Track-/Pad-"
                "Koordinaten) und urteile je Grenze: erfüllt / verletzt, "
                "mit Ist vs. Soll in mm und Koordinaten. EHRLICH: "
                "Ingenieurs-Vorprüfung, keine Zertifizierung; Kriechwege "
                "über Slots nähere ich nur geometrisch. Steht oben eine "
                "Auswahl, nur dieser Bereich. Keine Änderung, kein "
                "pcb_render."),
    ),
    SuperFeature(
        key="safety_spacing",
        label="⚡ Sicherheitsabstände",
        name="Sicherheitsabstände — Creepage & Clearance",
        status=SHIPPED,
        tooltip=("Prüft Kriech- und Luftstrecken zwischen Netz-Bereichen "
                 "(Netzspannung ↔ Kleinspannung) gegen Sicherheitsnormen "
                 "(IEC 62368) — inkl. Slots und Isolationsbarrieren."),
        moat=("KiCad hat kein Isolations-/Spannungsmodell und keine "
              "Sicherheitsnormen."),
        prompt=(
                "Sicherheitsabstände: (1) Bestimme aus analyze_pcb_nets die "
                "Spannungs-Domänen (Netzspannung/HV vs. Kleinspannung — aus "
                "Netznamen und Bauteilrollen; frage nach der Arbeitsspannung, "
                "wenn unklar). (2) Prüfe die kritischen Übergänge: Luftstrecke "
                "als kürzeste Distanz zwischen den Domänen — miss gezielt mit "
                "center_item_clearance bzw. aus den Track-/Pad-Koordinaten — "
                "und die Kriechstrecke entlang der Oberfläche inkl. "
                "Slots/Fräsungen. (3) Hole die GEFORDERTEN Werte mit "
                "get_safety_spacing (Arbeitsspannung, nominal_mains_v, "
                "Verschmutzungsgrad, Materialgruppe, insulation) — datierter "
                "IEC-60664-Snapshot statt Gedächtnis — und vergleiche. "
                "EHRLICH: das ist eine Ingenieurs-Vorprüfung, keine "
                "Zertifizierung. Befunde mit Koordinaten. Steht oben eine "
                "Auswahl, nur dieser Bereich. Keine Änderung, kein pcb_render.")
    ),
    SuperFeature(
        key="firmware_map",
        label="💾 Firmware-Pinmap",
        name="Firmware-Pinmap — Pinbelegung als Code exportieren",
        status=SHIPPED,
        tooltip=("Exportiert die MCU-Pinbelegung als Firmware-Header/Config "
                 "(C, DeviceTree, ESPHome …) — schlägt die Brücke Hardware ↔ "
                 "Software, in beide Richtungen konsistent."),
        moat=("KiCad hat kein Modell der Firmware-Seite; die Pin-Semantik lebt "
              "außerhalb des Layouts."),
        prompt=(
                "Firmware-Pinmap: Rufe extract_schematic_netlist auf (per Glob "
                "finden) und baue für den Controller (Auswahl oben = dieser; "
                "sonst der Haupt-Controller des Boards) die Pinbelegung Pin → "
                "Netz → Funktion. Frage kurz nach dem Zielformat, falls nicht "
                "genannt: C-Header (#define), DeviceTree-Overlay oder ESPHome- "
                "YAML. Erzeuge den Export als Codeblock zum Kopieren, mit den "
                "Netznamen als Kommentar, und liefere Konsistenz-Hinweise mit "
                "(z. B. UART-TX auf einem Input-only-Pin, Strapping-Pins "
                "belegt). Keine Datei-Schreibung, keine Board-Änderung, kein "
                "pcb_render.")
    ),
    SuperFeature(
        key="mlcc_derating",
        label="📉 MLCC-Derating",
        name="MLCC-Derating — echte Kapazität unter DC-Bias",
        status=SHIPPED,
        tooltip=("Rechnet die *effektive* Kapazität eines Keramik-Cs unter "
                 "DC-Bias und Temperatur (der berüchtigte DC-Bias-Effekt): ein "
                 "10 µF/6,3 V an 5 V ist real oft nur ~4 µF."),
        moat=("KiCad kennt nur den Nennwert, nicht das Spannungs-/Temperatur- "
              "Verhalten realer Bauteile."),
        prompt=(
                "MLCC-Derating: Lies EINMAL list_pcb_footprints (per Glob "
                "finden) und finde die Keramik-Kondensatoren samt Value, "
                "Bauform und dem Netz, an dem sie hängen; leite die DC-Bias- "
                "Spannung aus dem Rail-Namen ab (3V3, 5V, 12V … — frage bei "
                "unklaren Rails nach). Schätze je kritischem MLCC die "
                "effektive Kapazität unter Bias anhand typischer Derating- "
                "Kurven je Dielektrikum und Bauform (X5R/X7R; 0402 verliert "
                "mehr als 0805) — kennzeichne das EHRLICH als 'typisch', exakt "
                "geht nur mit der Hersteller-Kurve. Melde alle Fälle mit "
                "effektiv unter ~50 % nominal (Klassiker: 10 µF/6,3 V an 5 V ≈ "
                "4 µF) und schlage größere Bauform oder Spannungsklasse vor. "
                "Steht oben eine Auswahl, nur diese Kondensatoren. Keine "
                "Änderung, kein pcb_render.")
    ),
    SuperFeature(
        key="silk_cleanup",
        label="🔤 Silk-Aufräumen",
        name="Silkscreen aufräumen — Referenzen lesbar machen",
        status=SHIPPED,
        tooltip=("Rückt Reference-Designatoren so, dass sie lesbar sind: nicht "
                 "unter Bauteilen/Pads, konsistent orientiert, nah am richtigen "
                 "Teil — die mühsame Fleißarbeit am Ende jedes Layouts."),
        moat=("KiCad kann Text verschieben, aber nicht *Lesbarkeit* beurteilen."),
        prompt=(
                "Silk-Aufräumen: Lies EINMAL list_pcb_footprints (per Glob "
                "finden) und identifiziere Referenz-Beschriftungen, die "
                "unleserlich liegen: unter oder auf Bauteilkörpern, über Pads "
                "von Nachbarn, kollidierend oder uneinheitlich rotiert — "
                "bewerte das aus Positionen und Bauteilgrößen. Zeige den "
                "Aufräum-Plan (Ref → neue Textposition und Rotation, "
                "einheitliche Leserichtung) und setze ihn erst nach meinem Go "
                "um, gebündelt über die Live-Tools (ipc_move_items auf die "
                "Text-Elemente). EHRLICH: ohne Render prüfe ich Geometrie, "
                "nicht Optik — auf Wunsch ein pcb_render NACH Abschluss aller "
                "Änderungen. Steht oben eine Auswahl, nur deren Referenzen.")
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
