<!-- Lebende Roadmap. Single Source of Truth für die Feature-Metadaten ist
     plugin/superfeatures.py (die GUI rendert daraus die Buttons); dieses
     Dokument ist die ausformulierte Erzählung dazu. Beim Ausbau eines Features:
     hier die Beschreibung schärfen UND in plugin/superfeatures.py den status
     von SOON auf SHIPPED setzen. -->

# Super-Features — Roadmap

Dinge, die **KiCad prinzipiell nicht kann** — und dieses Plugin schon (oder
bald). Der Grund ist präzise:

> **KiCad versteht Geometrie und Konnektivität — das „Was".** Es hat *kein*
> Modell von **Bedeutung, Funktion und externem Wissen** — das „Warum" und das
> „Sollte". Genau diese semantische Schicht bringt der LLM-über-MCP-Layer. Das
> ist der eigentliche Mehrwert, nicht der Chat.

Die Super-Features gruppieren sich in drei Sorten:

1. **Externes Wissen hereinholen** — Datenblätter, Referenzdesigns, Bauteil-Specs
   → vergleichen und validieren.
2. **Bedeutung aus dem Design ableiten** — Busse, Funktionsblöcke, Pin-Rollen
   → navigieren, gruppieren, handeln.
3. **Freiheitsgrade ausnutzen, die KiCad nicht sehen kann** — Pin-Tausch,
   funktionale Gleichwertigkeit → optimieren.

## Status-Legende

- ✅ **fertig** (`SHIPPED`) — im GUI aktiv nutzbar.
- 🔜 **kommt bald** (`SOON`) — entworfen und beschrieben, noch nicht gebaut. Der
  Button ist schon da: Hover zeigt die Kurzbeschreibung, ein Klick den Pitch.

Die GUI-Button-Leiste liest diese Liste aus `plugin/superfeatures.py`. Jedes
neue Feature wird dort *ergänzt statt verstreut* — so wächst die Roadmap
kontrolliert, und die Kopplung „Doc ↔ GUI ↔ Code" bleibt an einer Stelle.

## Querschnitts-Prinzip: alles ist selektions-fähig

**Jedes** Super-Feature arbeitet nicht nur board-weit, sondern auch **auf die
aktuelle KiCad-Selektion** — der Nutzer markiert Teile im Editor, das Feature
wirkt genau darauf (`ipc_get_selection`). „Stromtragfähigkeit der *markierten*
Bahnen", „Datenblatt-Abgleich des *markierten* ICs", „simuliere den *markierten*
Teilschaltkreis", „source die *markierten* Bauteile". In der Registry ist das der
Vertrag `selection_aware = True` (Default), im GUI landet es später als Umschalter
„ganzes Board ↔ nur Auswahl". Kein Feature ohne diese Fähigkeit.

---

## Fundament (bereits gebaut)

Diese Interaktions-Bausteine sind live und tragen die Super-Features:

- **Verlässliche Board-Links** — jede unterstrichene Ref/Netz/Pin/Koordinate im
  Chat springt garantiert ins Board (Pins gegen echte Pads verifiziert,
  Koordinaten-Anker robust).
- **Glaskasten-Zug** — der Agent-Zug spricht Board-Sprache statt Tool-Slugs, mit
  Änderungs-Quittung „✎ geändert: … · [📍 zeigen]".
- **Undo sichtbar** — „↶ zurück" pro Quittung + Footer-Button, löst KiCads
  natives Undo aus.

---

## Die Features

### 🧶 Entwirren — Ratsnest-Entkreuzung fürs Routing  · 🔜
**Sorte:** Bedeutung ableiten + Freiheitsgrade nutzen.

KiCad kippt beim „Update PCB from Schematic" alle Footprints als überlappenden
Haufen hin und hat **keinen** Erstplatzierer. „Entwirren" füllt genau diese
Lücke: es ordnet den Haufen so an, dass sich die **Luftlinien (Ratsnest)
möglichst wenig kreuzen** — der saubere, routbare Startpunkt für die
Handplatzierung.

- **Ablauf:** einmal lesen (Netze, Pad-Positionen, Footprint-Größen) → **im Kopf
  lösen** (der Agent entwirrt durch Reasoning, geprüft an einem *nicht-mutierenden*
  `evaluate_layout`-Scorer: Kreuzungen + Überlappung) → **Geister-Vorschau der
  ganzen Lösung** → auf „übernehmen" **ein** Batch-Move.
  - **Fundament gebaut ✅:** der `evaluate_layout`-Scorer (Tool #177,
    `utils/placement_eval.py`) steht headless + getestet — Signalnetz-Kreuzungen,
    Überlappung, Wirelength. Offen: Trigger-Erkennung, Geister-Vorschau, das
    finale Anordnen. Das Board wird während
  des Denkens **null mal** angefasst — genau das Anti-Toolcall-Explosion-Prinzip.
- **Ehrliche Grenze:** Reale Netz-Graphen sind meist **nicht-planar** — „null
  Kreuzungen" ist dann mathematisch unmöglich (dafür gibt es Layer und Vias). Der
  Optimierer läuft bis **Plateau** („kein Durchgang verbessert mehr"), nicht bis
  einer festen Zahl, und meldet ehrlich, ob der Rest layer-bedingt ist — fast eine
  Layer-Bedarfs-Schätzung gratis.
- **Nur Signalnetze zählen:** GND/VCC werden als Federn ignoriert (sie werden eine
  Kupferfläche, keine geroutete Luftlinie), sonst kollabiert alles auf einen Punkt.
- **Trigger:** kein IPC-Event für „synchronisiert" verfügbar → wir erkennen den
  *Zustand* (viele Teile, ~0 Tracks, überlappender Haufen, hoher Signal-Ratsnest)
  beim Board-Summary und bieten es als Banner an — nie automatisch. Selbst-löschend,
  sobald platziert.
- **Warum KiCad das nicht kann:** kein Erstplatzierer; Kreuzungs-Minimierung
  erfordert Reasoning über die ganze Topologie.

### 🧶 Auswahl entwirren  · 🔜
Wie „Entwirren", aber **nur auf die markierten Bauteile**; der Rest des Boards
bleibt als fixer Anker stehen. Für gezieltes Aufräumen einer Baugruppe, ohne die
schon platzierten Teile anzufassen.

### 🚌 Bus-Radar — Bus-Teilnehmer finden  · 🔜
**Sorte:** Bedeutung ableiten. Das Fundament-Feature, das viele andere speist.

KiCad kennt Einzelnetze (`SDA`, `SCL`), aber nicht, dass `SDA+SCL+INT+ADDR` = *der
I²C-Bus zum Sensor* ist. „Bus-Radar" leitet die Bus-Zugehörigkeit aus Netznamen,
Konnektivität und Bauteilrollen ab und **listet + markiert alle Teilnehmer samt
Pins**. Damit werden „platziere den ganzen Bus", „route Bus X" und „match diese
Leitungen" erst möglich.

- **Warum KiCad das nicht kann:** es sieht Netze, keine Busse als
  Bedeutungseinheit (Schaltplan-Bus-Aliase ausgenommen — die enden am PCB).

### 📄 Datenblatt-Abgleich  · 🔜
**Sorte:** externes Wissen. Das sichtbare Flaggschiff.

Zieht das Datenblatt eines ICs und **vergleicht deine Beschaltung mit der
Referenz**: „passt die Entkopplung?", „ist EN korrekt beschaltet?", „stimmen die
Quarz-Load-Caps?", „fehlt ein externes Bauteil aus der Applikationsschaltung?".

- **Fundament da:** `tools/review_tools.py` + `generators/review/`,
  `circuit_block` (Datenblatt-Spec → Schaltungsblock), PDF-Tabellen-Extraktion.
- **Ehrliche Grenze:** der harte Teil ist *das richtige PDF finden* und
  *Pin-Tabellen robust extrahieren* — der Vergleich selbst ist LLM-Stärke.
- **Warum KiCad das nicht kann:** es weiß nichts von Datenblättern.

### 🛡️ Design-Wächter — semantischer ERC  · 🔜
**Sorte:** externes Wissen + Bedeutung.

Prüfungen jenseits des syntaktischen ERC: fehlende **Pull-ups** am I²C, fehlende
**Abblock-Cs** nah am IC, unpassende **Quarz-Load-Caps**, ein Power-Netz ohne
Stützung, ein Reset-Pin ohne Pull-up … Regeln, die *Absicht* verstehen.

- **Warum KiCad das nicht kann:** KiCads ERC prüft Netz-Syntax, nicht die Absicht
  der Schaltung.

### 🔀 Pin-Tausch — GPIO ans Routing anpassen  · 🔜
**Sorte:** Freiheitsgrade nutzen. Der „das kann KiCad *niemals*"-Leuchtturm.

Viele MCU-Pins sind **funktional austauschbar** (jeder GPIO kann die LED sein,
SPI liegt auf mehreren Pins). Erzwingt Pin 12 eine Kreuzung, während der ebenfalls
freie Pin 9 direkt daneben liegt → **Netz auf Pin 9 umlegen** und Schaltplan *und*
PCB kohärent nachziehen. Routing wird trivial, ohne dass du eine Leitung ziehst.

- **Ehrliche Grenze:** braucht Pin-Funktions-/Mux-Wissen (nicht jeder Pin kann
  alles) und ändert den **Schaltplan** — in KiCad 10 dateibasiert
  (`sch_patch_tools`, Board zu), da Eeschema kein IPC-Save hat. Hart, aber
  spektakulär → bewusst ein späteres Ziel.
- **Warum KiCad das nicht kann:** es hat kein Konzept funktional
  austauschbarer Pins.

### 💡 Board erklären  · 🔜
Rekonstruiert aus Netzliste + Bauteilen, **was das Board tut**: Funktionsblöcke,
Schnittstellen, Stromversorgung, Signalfluss. Reverse-Engineering der Absicht —
nützlich für fremde/alte Boards. **Warum KiCad das nicht kann:** es hat ein Modell
der Verbindungen, keines der Funktion.

### 🧭 Netz-Navigator — Fragen in normaler Sprache  · 🔜
„Welcher Pin treibt Motor-Enable?", „was liegt sonst auf U1.7?", „wo geht 3V3
überall hin?" — **semantische** Netz-/Pin-Navigation, die direkt ins Board
markiert. **Warum KiCad das nicht kann:** es zeigt Netze an, sucht aber nicht nach
Bedeutung.

### 📐 Ausrichten & Anordnen  · 🔜
Markierte Bauteile per Satz ordnen: **bündig ausrichten, im Raster verteilen,
spiegeln, als Array anordnen, um U1 gruppieren** — mit der korrekten Rotations-
(KiCad-CW) und B.Cu-Spiegel-Mathematik, an der man von Hand fummelt. **Warum KiCad
das nicht kann:** kein sprachgesteuertes, absicht-basiertes Anordnen.

### ✏️ Skizzen-Dirigent — gezeichnete Absicht → Kupfer  · 🔜
Zeichne grob deine Absicht auf einen Markup-Layer — eine Linie (= Track), ein
Rechteck (= Platzierungs-Ziel/Keepout), ein mit `GND` beschrifteter Pfeil
(= Fläche + Stitching), eine Zahl neben der Linie (= Track-Breite). Der Agent
**interpretiert die Skizze und gießt Kupfer / platziert entsprechend**. Baut auf
`ipc_markup_to_tracks` auf. **Warum KiCad das nicht kann:** es interpretiert keine
gezeichnete Absicht.

### 👁️ Mitdenken-Modus — Live-Assistenz beim Routen  · 🔜
Während *du* von Hand routest, kommentiert Claude **live** über den Live-Diff:
Clearance-Unterschreitung, gerade fragmentierte Netze, DRC-Risiken —
Copilot-Stil, ohne Prompt. **Warum KiCad das nicht kann:** es hat kein
mitlaufendes, verstehendes Assistenz-Auge.

### ⊙ Polar-Board — Radial-Layout für runde Boards  · 🔜
**Fundament da:** das `polar_grid`-Tool existiert bereits — dieses Feature ist am
nächsten dran am „fertig". Platzieren und Routen in **Polarkoordinaten (Radius +
Winkel)** statt X/Y: LEDs gleichmäßig auf einem Kreis, Stecker rund um den Rand,
radiale und konzentrische Leiterbahnen, Bohrbild auf Teilkreis. **Warum KiCad das
nicht kann:** es rechnet nur kartesisch; runde Boards zwingen sonst zur
Handrechnung von Winkel und Radius (und der Rotations-Footgun schlägt dabei
doppelt zu).

### 🖊️ Skizzen-Layer — gemeinsamer Notiz-/Hilfslayer  · 🔜
Ein **verwalteter Hilfslayer als gemeinsames Skizzenblatt**: du zeichnest
Absichten hin, der Agent zeichnet **Vorschläge, Marker und Geister-Vorschauen** —
ein Klick zum Ein-/Ausblenden und Leeren. Das ist das *Medium*, auf dem
„Skizzen-Dirigent", die Geister-Vorschau von „Entwirren" und die Marker
zusammenlaufen. **Warum KiCad das nicht kann:** es hat keinen dedizierten, von
Mensch *und* Agent gemeinsam genutzten Skizzen-/Vorschau-Kanal.

## Elektrik & Fertigung (DFM)

Die Sorte, die am deutlichsten zeigt, warum ein rein geometrisches Tool hier
blind ist: **Strom, Wärme, Impedanz, Kosten und Fertigbarkeit stehen nicht im
Layout** — sie kommen aus Design-Absicht + externem Wissen (Normen, Fab-Regeln,
Kostenmodelle).

### 🔥 Stromtragfähigkeit — Leiterbahn-Breite vs. Strom  · 🔜
Prüft jede **Leiterbahn-Breite gegen den Strom, den ihr Netz trägt** (IPC-2221),
markiert unterdimensionierte Bahnen und schlägt Breiten vor. Den Strom leitet der
Agent aus Bauteilrollen ab (Power-Netz, Motortreiber, LED) oder fragt nach.
**Warum KiCad das nicht kann:** es kennt keine Ströme — wie viel ein Netz führt,
ist Design-Absicht, nicht Geometrie.

### ⌚ Quarz-Load-Caps — richtige Lastkapazität berechnen  · 🔜
Berechnet die **Load-Kondensatoren** für einen Quarz aus dessen Datenblatt-`CL`
und der geschätzten Streukapazität (`C = 2·(CL − Cstray)`) und prüft, ob die
verbauten Werte passen — ein extrem häufiger, stiller Fehler. **Warum KiCad das
nicht kann:** es kennt weder den `CL`-Wert eines Quarzes noch die Load-Cap-Formel.

### 🔩 Via-Optimierung — Anzahl & Kosten senken  · 🔜
**Fundament da:** `via_promote` (Blind/Buried→Through) existiert. Erweitert:
findet **überflüssige Vias**, senkt die Via-Anzahl (Kosten + Zuverlässigkeit) und
schlägt via-ärmeres Routing vor. **Warum KiCad das nicht kann:** es zählt Vias,
bewertet aber weder ihre Fertigungskosten noch ihre Notwendigkeit.

### 🌡️ Thermik — Verlustleistungs-Hotspots  · 🔜
Findet **Hotspots** (Regler, MOSFETs, Shunts) und schlägt Kühl-Kupfer,
Thermal-Vias und Abstände vor. **Warum KiCad das nicht kann:** kein
Verlustleistungs-/Wärmemodell.

### 🌡️ Betriebstemperatur — Junction-Temp & Derating-Reserve  · 🔜
Schätzt die reale **Betriebs-/Sperrschichttemperatur** je Bauteil
(`Tj = Ta + P·θ`) aus Verlustleistung, Umgebungstemperatur und Wärmewiderstand —
und wie viel **Derating-Reserve** bleibt. Ergänzt „Thermik" (Hotspots) um die
harte Zahl. **Warum KiCad das nicht kann:** kein Modell für Wärmewiderstand,
Umgebung oder Verlustleistung.

### 📐 Slew-Rate — schafft der Verstärker/Treiber das Signal?  · 🔜
Rechnet, ob ein **OpAmp/Treiber die geforderte Signalflanke schafft**
(Slew-Rate-Limit) bzw. die **Flankensteilheit** digitaler Signale — relevant für
Verzerrung, Timing und EMV. **Warum KiCad das nicht kann:** es rechnet kein
dynamisches Signalverhalten aus Bauteil-Specs.

### 〰️ Impedanz — controlled impedance aus dem Stackup  · 🔜
Berechnet **Breite und Abstand für eine Ziel-Impedanz** (USB, Ethernet, RF) aus
dem Lagenaufbau. **Warum KiCad das nicht kann:** es rechnet keine Impedanz aus
Stackup + Geometrie.

### 🏭 DFM-Check — Fertigbarkeit gegen echte Fab-Regeln  · 🔜
Prüft gegen die Regeln eines **konkreten Fertigers** (min. Track/Space, Annular
Ring, Acid Traps, Silk-über-Pad) — nicht nur generisches DRC, sondern „ist das für
JLCPCB 2-Lagen zu aggressiv?". **Warum KiCad das nicht kann:** sein DRC kennt
keine fertiger-spezifischen DFM-Regeln oder deren Begründung.

### 💰 Kosten-Schätzer — was macht das Board teuer  · 🔜
Grobe **Fertigungskosten** aus Boardfläche, Lagenzahl, Via-Anzahl und BOM — plus
was die Kosten treibt. **Warum KiCad das nicht kann:** kein Kostenmodell.

## Simulation & Beschaffung

### 📈 Simulation — Verhalten & Bandbreite verstehen  · 🔜
**Fundament da:** LTspice↔KiCad-Konverter (`generators/ltspice2kicad`). Simuliert
Schaltungsverhalten (Verstärker-**Bandbreite**, Frequenzgang, Arbeitspunkt) über
SPICE und **erklärt das Ergebnis in Klartext** statt nur Kurven auszuspucken.
**Warum KiCad das nicht kann:** es kann ngspice *starten*, aber weder die Frage
noch das Ergebnis interpretieren.

### 🧬 Simulationsmodelle ergänzen  · 🔜
Findet und **hängt das passende SPICE-Modell je Bauteil an**, damit die Simulation
überhaupt läuft — der lästige manuelle Schritt vor jeder Simulation. **Warum KiCad
das nicht kann:** es verlangt manuelle Modell-Zuordnung und weiß nicht, welches
Modell zu welchem Bauteil passt.

### 🛒 Bauteil-Optimierung gegen JLCPCB / Mouser  · 🔜
Prüft **Verfügbarkeit und Preis** gegen JLCPCB/Mouser, bevorzugt
**JLCPCB-Basic-Teile** (günstigere Bestückung) und findet **pin-kompatible
Alternativen** für abgekündigte oder nicht-lagernde Teile. **Warum KiCad das nicht
kann:** kein Wissen über Distributoren, Lagerbestand oder Preise.

## Kreativ / grenzüberschreitend

Die Sorte, bei der ein Layout-Tool endgültig aussteigt — weil sie Wahrnehmung,
externes Wissen oder die Brücke in andere Welten (Firmware, Fertigung, Physik)
braucht.

### 📷 Foto → Schaltung  · 🔜
Zieh ein **Foto einer echten Platine** rein — Bauteile, Beschriftungen,
Leiterbahnen werden erkannt und Netzliste/Schaltplan rekonstruiert. **Warum KiCad
das nicht kann:** keine Bild-Wahrnehmung. Reine Multimodal-Arbeit.

### 📄 Datenblatt → Applikationsschaltung  · 🔜
**Fundament da:** `circuit_block` (Datenblatt-Spec → Schaltungsblock). Aus dem
Datenblatt die **typische Applikationsschaltung** generieren. **Warum KiCad das
nicht kann:** es liest keine Datenblätter.

### ⚡ Sicherheitsabstände — Creepage & Clearance  · 🔜
Prüft **Kriech- und Luftstrecken** zwischen Netzspannung und Kleinspannung gegen
Sicherheitsnormen (IEC 62368) — der Bereich, in dem ein Fehler *gefährlich* ist.
**Warum KiCad das nicht kann:** kein Isolations-/Spannungsmodell, keine Normen.

### 💾 Firmware-Pinmap — Pinbelegung als Code  · 🔜
Exportiert die MCU-Pinbelegung als **Firmware-Header/Config** (C, DeviceTree,
ESPHome) — die Brücke Hardware ↔ Software, konsistent in beide Richtungen. Paart
sich stark mit **Pin-Tausch**. **Warum KiCad das nicht kann:** es hat kein Modell
der Firmware-Seite.

### 📉 MLCC-Derating — echte Kapazität unter DC-Bias  · 🔜
Rechnet die *effektive* Kapazität eines Keramik-Cs unter **DC-Bias und
Temperatur**: ein 10 µF/6,3 V an 5 V ist real oft nur ~4 µF — ein berüchtigter,
stiller Fehler. **Warum KiCad das nicht kann:** es kennt nur den Nennwert, nicht
das reale Bauteilverhalten.

### 🔤 Silkscreen aufräumen — Referenzen lesbar machen  · 🔜
Rückt Reference-Designatoren so, dass sie **lesbar** sind: nicht unter
Bauteilen/Pads, konsistent orientiert, nah am richtigen Teil. **Warum KiCad das
nicht kann:** es kann Text verschieben, aber nicht Lesbarkeit *beurteilen*.

---

## Weitere Kandidaten (noch nicht in der Liste)

Ideen im selben Geist, die auf Zuruf in die Registry wandern:

- **Längen-/Delay-Matching** (Busse, Diff-Paare) · **EMV-Review** (Schleifen-
  fläche, Rückstrompfad, Guard-Ring am Quarz) · **Stackup-Berater** ·
  **Testpunkt-Platzierung** · **Panelisierung** · **Bestückbarkeit/DFA**.
- **Netznamen auto-benennen** aus Funktion (`Net-(U1-Pad3)` → `SPI_MOSI`) ·
  **RF-/Antennen-Keepout** aus dem Modul-Datenblatt · **Return-Pfad/Ground-Split-
  Analyse** · **Design-Reuse** (Block aus anderem Projekt einsetzen) ·
  **Varianten-Management** (Bestückungs-Optionen/DNP-Matrix) ·
  **Auto-Dokumentation** (Theory-of-Operation aus der Netzliste) ·
  **Boardview für Reparatur** (Netz→Pad-Karte, Testpunkte).

---

## Wie wir das ausbauen

Jedes Mal, wenn wir eines dieser Features (oder ein Stück davon) bauen:

1. In `plugin/superfeatures.py` den `status` von `SOON` → `SHIPPED` setzen und den
   `key` im Panel-Handler live verdrahten.
2. Hier den Abschnitt von „🔜" auf „✅" ziehen und die Beschreibung um das
   tatsächlich Gebaute schärfen (inkl. ehrlicher Grenzen).
3. `CHANGELOG.md`-Eintrag + Tests (die reinen Kern-Funktionen headless).

So bleibt die Roadmap ein *lebendes* Dokument, und der GUI-Roadmap-Streifen
spiegelt jederzeit den echten Stand.
