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

## Stand 0.7.5 — alle 33 Features aktiv (v1)

Seit 0.7.5 dispatcht **jeder** Button einen kanonischen, geführten Auftrag
(Registry-Feld `prompt`). „Aktiv" heißt v1, nicht Endausbau — die Ehrlichkeits-
Verträge gelten überall: Mutationen nur nach ausdrücklichem **Go**, Annahmen
werden offengelegt, bei Unsicherheit wird **gefragt statt geraten**, und jeder
Prompt benennt seine Grenze. Die wichtigsten v1-Grenzen im Überblick:

| Feature | v1-Umfang | Ehrliche Grenze |
|---|---|---|
| 🔀 Pin-Tausch | Kreuzungs-Analyse + Swap-Vorschläge; Umsetzung Go-gated (Schaltplan nur bei geschlossenem Eeschema) | Pinmux-Wissen aus dem Modell, nicht aus einer Mux-Datenbank |
| 👁️ Mitdenken | Ein Klick = ein Review der Handänderungen (`live_summarize_user_changes`) | kein Dauer-Beobachten — IPC liefert keine Events |
| 📈 Simulation | echtes ngspice über `run_spice_sim` — nutzt **KiCads mitgeliefertes libngspice** (Eeschemas Simulator-Kern) oder ein ngspice-Binary; analytischer Fallback | Eeschemas GUI-Simulator selbst hat keine API — es läuft dieselbe Engine, nur headless |
| 🧬 SPICE-Modelle | Modell-Suche per WebSearch + fertige Sim.*-Eintragungen | Einträge schreiben nur nach Go, Eeschema zu |
| 🛒 Sourcing | WebSearch-Verfügbarkeit/Preise + pin-kompatible Alternativen | Momentaufnahme, kein Live-Katalog |
| 📷 Foto→Schaltung | Bild per Read analysieren, Netz-Hypothesen mit Konfidenz | verdeckte Lagen bleiben unsichtbar |
| 〰️ Impedanz | IPC-2141-/Microstrip-Näherungen aus dem Stackup (`pcb_eval`) | Näherung, kein Feldlöser — Fab-Rechner gegenprüfen |
| ⚡ Sicherheitsabstände / 🔌 Schutzklassen | Domänen-Erkennung + Messung gegen die IEC-60664-Snapshot-Werte aus `get_safety_spacing` | Ingenieurs-Vorprüfung, keine Zertifizierung; Produktnormen können abweichen |
| 🏭 DFM / 💰 Kosten | Prüfung/Schätzung gegen Fab-Regeln bzw. Kostentreiber aus `get_board_stats`/`analyze_bom` | Regelstand = Modellwissen mit Datum; Kosten = Größenordnung |
| 🌡️ Thermik/Betriebstemp., 📐 Slew, ⌚ Load-Caps, 📉 MLCC | Physik-Formeln mit offengelegten Annahmen; Datenblatt-Werte aus `docs/` oder per Nachfrage | typische Werte ehrlich als „typisch" markiert |
| 🔤 Silk, 📐 Anordnen | Plan → Go → EIN gebündelter Live-Zug | Geometrie-, nicht Optik-Urteil (Render nur am Ende auf Wunsch) |

Die GUI-Button-Leiste liest diese Liste aus `plugin/superfeatures.py`. Jedes
neue Feature wird dort *ergänzt statt verstreut* — so wächst die Roadmap
kontrolliert, und die Kopplung „Doc ↔ GUI ↔ Code" bleibt an einer Stelle.

## Querschnitts-Prinzip: alles ist selektions-fähig

**Jedes** Super-Feature arbeitet nicht nur board-weit, sondern auch **auf die
aktuelle KiCad-Selektion** — der Nutzer markiert Teile im Editor, das Feature
wirkt genau darauf (`ipc_get_selection`). „Stromtragfähigkeit der *markierten*
Bahnen", „Datenblatt-Abgleich des *markierten* ICs", „simuliere den *markierten*
Teilschaltkreis", „source die *markierten* Bauteile". In der Registry ist das der
Vertrag `selection_aware = True` (Default). Kein Feature ohne diese Fähigkeit.

**Die Regel ist global und automatisch (seit 0.7.2):** ohne Selektion wirkt der
Button aufs **ganze Board**; ist etwas markiert, zeigt das Panel beim Klick an,
*worauf* der Zug wirkt („🎯 Wirkt auf deine Auswahl: R1, C3, U2"), und das
Feature beschränkt sich exakt darauf. Deshalb gibt es **keine separaten
„Auswahl …"-Features** — das Scoping ist Grundverhalten jedes Buttons, kein
eigener Roadmap-Eintrag.

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

### 🧶 Entwirren — Ratsnest-Entkreuzung fürs Routing  · ✅
**Sorte:** Bedeutung ableiten + Freiheitsgrade nutzen.

KiCad kippt beim „Update PCB from Schematic" alle Footprints als überlappenden
Haufen hin und hat **keinen** Erstplatzierer. „Entwirren" füllt genau diese
Lücke: es ordnet den Haufen so an, dass sich die **Luftlinien (Ratsnest)
möglichst wenig kreuzen** — der saubere, routbare Startpunkt für die
Handplatzierung.

- **Ablauf:** einmal lesen (Netze, Pad-Positionen, Footprint-Größen) → **im Kopf
  lösen** (der Agent entwirrt durch Reasoning, geprüft an einem *nicht-mutierenden*
  `evaluate_layout`-Scorer: Kreuzungen + Überlappung) → **Plan mit Score
  vorher → nachher** → auf „Go" **ein** Batch-Move.
  - **Gebaut ✅ (v1, 0.7.2):** der GUI-Button orchestriert genau diesen Ablauf —
    einmal lesen, im Kopf entwirren, Kandidat gegen den `evaluate_layout`-Scorer
    (Tool #177, `utils/placement_eval.py`: Signalnetz-Kreuzungen, Überlappung,
    Wirelength) prüfen, Plan als Text-Vorschau zeigen, erst nach ausdrücklichem
    Go EIN gebündelter Live-Move. Mit Selektion wirkt es nur auf die markierten
    Bauteile (Rest = fixer Anker). **Seit 0.7.6 mit Geister-Vorschau:** die
    Zielpositionen erscheinen vor dem Go als Kreuz-Marker mit Ref-Label auf dem
    Skizzen-Layer (MCP.Skizze) und werden nach Umsetzung oder Ablehnung
    weggeräumt. **Ehrliche Grenze:** Trigger-Erkennung („frisch
    synchronisierter Haufen") ist offen. Das Board
    wird während des Denkens **null mal** angefasst — genau das
    Anti-Toolcall-Explosion-Prinzip.
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

*(„Auswahl entwirren" ist kein eigenes Feature mehr — das Selektions-Scoping ist
seit 0.7.2 Grundverhalten **jedes** Buttons, siehe Querschnitts-Prinzip oben.)*

### 🚌 Bus-Radar — Bus-Teilnehmer finden  · ✅
**Sorte:** Bedeutung ableiten. Das Fundament-Feature, das viele andere speist.

KiCad kennt Einzelnetze (`SDA`, `SCL`), aber nicht, dass `SDA+SCL+INT+ADDR` = *der
I²C-Bus zum Sensor* ist. „Bus-Radar" leitet die Bus-Zugehörigkeit aus Netznamen,
Konnektivität und Bauteilrollen ab und **listet + markiert alle Teilnehmer samt
Pins**. Damit werden „platziere den ganzen Bus", „route Bus X" und „match diese
Leitungen" erst möglich.

- **Warum KiCad das nicht kann:** es sieht Netze, keine Busse als
  Bedeutungseinheit (Schaltplan-Bus-Aliase ausgenommen — die enden am PCB).
- **Erste Stufe gebaut ✅:** `list_bus_members` (Tool #179, `utils/bus_infer.py`)
  erkennt Protokoll-Busse (I²C/SPI/UART/USB/CAN/SWD/JTAG), nummerierte Busse und
  Diff-Paare aus den Netznamen und listet Netze + Pins je Bus — headless getestet.
  Offen: Gruppen-Platzierung/-Routing darauf aufsetzen.

### 📄 Datenblatt-Abgleich  · ✅
**Sorte:** externes Wissen. Das sichtbare Flaggschiff.

Zieht das Datenblatt eines ICs und **vergleicht deine Beschaltung mit der
Referenz**: „passt die Entkopplung?", „ist EN korrekt beschaltet?", „stimmen die
Quarz-Load-Caps?", „fehlt ein externes Bauteil aus der Applikationsschaltung?".

- **Fundament da:** `tools/review_tools.py` + `generators/review/`,
  `circuit_block` (Datenblatt-Spec → Schaltungsblock), PDF-Tabellen-Extraktion.
- **Ehrliche Grenze:** der harte Teil ist *das richtige PDF finden* und
  *Pin-Tabellen robust extrahieren* — der Vergleich selbst ist LLM-Stärke.
- **Warum KiCad das nicht kann:** es weiß nichts von Datenblättern.
- **Gebaut ✅ (v1, 0.7.3):** der GUI-Button reviewt das *markierte* IC über
  `review_ic_against_datasheet` (Pin-Tabelle + Schaltplan-Ausschnitt +
  Datenblatt-Seite → Abgleich durch den Agenten); ohne Auswahl inventarisiert
  `list_missing_datasheets` erst, welche PDFs unter `docs/<Value>.pdf`
  liegen/fehlen. **Grenze:** das PDF muss lokal vorliegen (kein Auto-Download);
  mehrseitige Datenblätter brauchen ggf. die richtige Seite.

### 🛡️ Design-Wächter — semantischer ERC  · ✅
**Sorte:** externes Wissen + Bedeutung.

Prüfungen jenseits des syntaktischen ERC: fehlende **Pull-ups** am I²C, fehlende
**Abblock-Cs** nah am IC, unpassende **Quarz-Load-Caps**, ein Power-Netz ohne
Stützung, ein Reset-Pin ohne Pull-up … Regeln, die *Absicht* verstehen.

- **Warum KiCad das nicht kann:** KiCads ERC prüft Netz-Syntax, nicht die Absicht
  der Schaltung.
- **Gebaut ✅ — als persistente Regel-Registry:** `audit_design` (Tool #180) fährt
  alle in `utils/design_rules.RULES` registrierten Regeln gegen einen *einmal*
  geparsten `BoardContext`. Neue Regel = ein Registry-Eintrag. Bisher:
  **I²C ohne Pull-ups** + **Quarz ohne Load-Caps**. Reine Komposition
  (`bus_infer` + `pcb_board_parse` + `is_power_net`), headless getestet. Nächste
  Regeln (SPI-CS-/Reset-Pull-up, Entkopplung aus `audit_power_tree` einhängen,
  Load-Cap-*Werte* gegen CL) sind je eine Registry-Zeile.

### 🔎 Test-Punkt-Wächter — probe-bar für Bring-up & Serientest?  · ✅
**Sorte:** externes Fertigungs-/Bring-up-Wissen.

KiCad kennt Netze, aber nicht, welche einen **Prüfpunkt-Zugang** *verdienen*. Bei
Flying-Probe/Nadeladapter und beim Bring-up willst du an die wichtigen Netze ran:
Versorgungs-Rails, Reset, Clock, Bus. Ein kritisches Netz ohne Test-Punkt und ohne
Stecker ist **blind** — das merkst du erst mit dem Board in der Hand. Das Feature
rankt Netze nach Test-Wichtigkeit und meldet Abdeckung in % plus die blinden
kritischen Netze.

- **Warum KiCad das nicht kann:** ERC/DRC kennen keine Netz-*Wichtigkeit* — das
  ist Test-/Fertigungs-Wissen, keine Geometrie.
- **Gebaut ✅:** `audit_test_points` (Tool #183). Rankt über dieselben Signale wie
  der Design-Wächter (`is_power_net`, Reset-Regex, `bus_infer`) auf dem *einmal*
  geparsten `BoardContext`; Zugang = `TP*`/`TestPoint`-Footprint oder Stecker.
  Selektions-fähig (`refs`). Nächste Stufe: Vorschlag, *wo* ein Test-Punkt hin
  soll (nächste freie Stelle am Netz).

### 🔀 Pin-Tausch — GPIO ans Routing anpassen  · ✅
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

### 💡 Board erklären  · ✅
Rekonstruiert aus Netzliste + Bauteilen, **was das Board tut**: Funktionsblöcke,
Schnittstellen, Stromversorgung, Signalfluss. Reverse-Engineering der Absicht —
nützlich für fremde/alte Boards. **Warum KiCad das nicht kann:** es hat ein Modell
der Verbindungen, keines der Funktion.

- **Gebaut ✅ (0.7.3):** der GUI-Button liest einmal `list_pcb_footprints` +
  `analyze_pcb_nets` und erklärt Blöcke/Fluss mit klickbaren Ref-/Netznamen;
  mit Selektion gezielt den markierten Teilschaltkreis.

### 🧭 Netz-Navigator — Fragen in normaler Sprache  · ✅
„Welcher Pin treibt Motor-Enable?", „was liegt sonst auf U1.7?", „wo geht 3V3
überall hin?" — **semantische** Netz-/Pin-Navigation, die direkt ins Board
markiert. **Warum KiCad das nicht kann:** es zeigt Netze an, sucht aber nicht nach
Bedeutung.

### 📐 Ausrichten & Anordnen  · ✅
Markierte Bauteile per Satz ordnen: **bündig ausrichten, im Raster verteilen,
spiegeln, als Array anordnen, um U1 gruppieren** — mit der korrekten Rotations-
(KiCad-CW) und B.Cu-Spiegel-Mathematik, an der man von Hand fummelt. **Warum KiCad
das nicht kann:** kein sprachgesteuertes, absicht-basiertes Anordnen.

### ✏️ Skizzen-Dirigent — gezeichnete Absicht → Kupfer  · ✅
Zeichne grob deine Absicht auf einen Markup-Layer — eine Linie (= Track), ein
Rechteck (= Platzierungs-Ziel/Keepout), ein mit `GND` beschrifteter Pfeil
(= Fläche + Stitching), eine Zahl neben der Linie (= Track-Breite). Der Agent
**interpretiert die Skizze und gießt Kupfer / platziert entsprechend**. Baut auf
`ipc_markup_to_tracks` auf. **Warum KiCad das nicht kann:** es interpretiert keine
gezeichnete Absicht.

- **Gebaut ✅ (erste Stufe):** Linien und Bögen auf `User.9` werden per
  GUI-Button (0.7.1) zu Kupferbahnen auf `F.Cu` — erst `dry_run`-Zählung,
  dann EIN Umsetzungs-Call = ein Undo-Schritt; leerer Layer wird ehrlich
  gemeldet statt geraten. **Ehrliche Grenze:** die *Interpretation* darüber
  hinaus (Rechteck = Keepout, `GND`-Pfeil = Fläche + Stitching, Zahl =
  Breite, geschlossene Polygone/Kreise = Zonen) ist noch offen.

### 👁️ Mitdenken-Modus — Live-Assistenz beim Routen  · ✅
Während *du* von Hand routest, kommentiert Claude **live** über den Live-Diff:
Clearance-Unterschreitung, gerade fragmentierte Netze, DRC-Risiken —
Copilot-Stil, ohne Prompt. **Warum KiCad das nicht kann:** es hat kein
mitlaufendes, verstehendes Assistenz-Auge.

### ⊙ Polar-Board — Radial-Layout für runde Boards  · ✅
**Gebaut ✅ (v1, 0.7.3):** der GUI-Button zeigt die Grid-Konfiguration
(`polar_grid op=check_grid_config`) und den Radial-Workflow; platziert/geroutet
wird erst auf ausdrückliches Go (mit Selektion: Vorschlag, wie die markierten
Teile auf einen Ring kämen). **Fundament:** das `polar_grid`-Tool existiert
vollständig (place_on_ring/spoke, polare Bögen/Segmente/Vias, route). Platzieren und Routen in **Polarkoordinaten (Radius +
Winkel)** statt X/Y: LEDs gleichmäßig auf einem Kreis, Stecker rund um den Rand,
radiale und konzentrische Leiterbahnen, Bohrbild auf Teilkreis. **Warum KiCad das
nicht kann:** es rechnet nur kartesisch; runde Boards zwingen sonst zur
Handrechnung von Winkel und Radius (und der Rotations-Footgun schlägt dabei
doppelt zu).

### 🖊️ Skizzen-Layer — gemeinsamer Notiz-/Hilfslayer  · ✅
Ein **verwalteter Hilfslayer als gemeinsames Skizzenblatt**: du zeichnest
Absichten hin, der Agent zeichnet **Vorschläge, Marker und Geister-Vorschauen** —
ein Klick zum Ein-/Ausblenden und Leeren. Das ist das *Medium*, auf dem
„Skizzen-Dirigent", die Geister-Vorschau von „Entwirren" und die Marker
zusammenlaufen. **Warum KiCad das nicht kann:** es hat keinen dedizierten, von
Mensch *und* Agent gemeinsam genutzten Skizzen-/Vorschau-Kanal.

- **Gebaut ✅ (v1, 0.7.3):** der GUI-Button zeigt den Layer-Inhalt
  (`ipc_list_markers` auf User.9) und bietet Legende zeichnen
  (`ipc_draw_sketch_legend`) bzw. Leeren (`ipc_clear_markers`) an — beides erst
  nach Go. **Grenze:** Ein-/Ausblenden der Layer-Sichtbarkeit exponiert die
  IPC-API nicht; Geister-Vorschauen zeichnen ist offen.

## Elektrik & Fertigung (DFM)

Die Sorte, die am deutlichsten zeigt, warum ein rein geometrisches Tool hier
blind ist: **Strom, Wärme, Impedanz, Kosten und Fertigbarkeit stehen nicht im
Layout** — sie kommen aus Design-Absicht + externem Wissen (Normen, Fab-Regeln,
Kostenmodelle).

### 🔥 Stromtragfähigkeit — Leiterbahn-Breite vs. Strom  · ✅
Prüft jede **Leiterbahn-Breite gegen den Strom, den ihr Netz trägt** (IPC-2221),
markiert unterdimensionierte Bahnen und schlägt Breiten vor. Den Strom leitet der
Agent aus Bauteilrollen ab (Power-Netz, Motortreiber, LED) oder fragt nach.
**Warum KiCad das nicht kann:** es kennt keine Ströme — wie viel ein Netz führt,
ist Design-Absicht, nicht Geometrie.

- **Gebaut ✅ (0.7.4):** `check_ampacity` (Tool #184, `utils/ampacity.py`) —
  IPC-2221 in beide Richtungen (nötige Breite ↔ tragbarer Strom), Innenlagen
  mit dem strengeren internen Chart, Parameter Temperaturanstieg + Kupferdicke.
  Ohne Ströme liefert es das Breiten-Inventar je Netz (damit der Agent weiß,
  wo Ströme zuzuweisen sind); mit `currents` die Verstöße samt nötiger Breite,
  schlimmste zuerst. Der GUI-Button orchestriert: Inventar → Strom-Annahmen
  offenlegen → EIN Prüf-Call. **Grenze:** IPC-2221 ist das generische
  Chart (konservativ); Zonen/Polygone werden (noch) nicht bewertet, nur
  Track-Segmente.

### ⌚ Quarz-Load-Caps — richtige Lastkapazität berechnen  · ✅
Berechnet die **Load-Kondensatoren** für einen Quarz aus dessen Datenblatt-`CL`
und der geschätzten Streukapazität (`C = 2·(CL − Cstray)`) und prüft, ob die
verbauten Werte passen — ein extrem häufiger, stiller Fehler. **Warum KiCad das
nicht kann:** es kennt weder den `CL`-Wert eines Quarzes noch die Load-Cap-Formel.

### 🔩 Via-Optimierung — Anzahl & Kosten senken  · ✅
**Fundament da:** `via_promote` (Blind/Buried→Through) existiert. Erweitert:
findet **überflüssige Vias**, senkt die Via-Anzahl (Kosten + Zuverlässigkeit) und
schlägt via-ärmeres Routing vor. **Warum KiCad das nicht kann:** es zählt Vias,
bewertet aber weder ihre Fertigungskosten noch ihre Notwendigkeit.

- **Gebaut ✅ (erste Stufe):** der GUI-Button (0.7.1) liefert den
  `via_promote`-Report (`dry_run`): welche teuren Blind/Buried-Vias sich
  kollisionsfrei zu Through wandeln ließen; umgesetzt wird erst auf
  ausdrückliches Go. **Ehrliche Grenze:** „überflüssige Vias finden" und
  via-ärmere Routing-Vorschläge sind noch offen.

### 🌡️ Thermik — Verlustleistungs-Hotspots  · ✅
Findet **Hotspots** (Regler, MOSFETs, Shunts) und schlägt Kühl-Kupfer,
Thermal-Vias und Abstände vor. **Warum KiCad das nicht kann:** kein
Verlustleistungs-/Wärmemodell.

### 🌡️ Betriebstemperatur — Junction-Temp & Derating-Reserve  · ✅
Schätzt die reale **Betriebs-/Sperrschichttemperatur** je Bauteil
(`Tj = Ta + P·θ`) aus Verlustleistung, Umgebungstemperatur und Wärmewiderstand —
und wie viel **Derating-Reserve** bleibt. Ergänzt „Thermik" (Hotspots) um die
harte Zahl. **Warum KiCad das nicht kann:** kein Modell für Wärmewiderstand,
Umgebung oder Verlustleistung.

### 📐 Slew-Rate — schafft der Verstärker/Treiber das Signal?  · ✅
Rechnet, ob ein **OpAmp/Treiber die geforderte Signalflanke schafft**
(Slew-Rate-Limit) bzw. die **Flankensteilheit** digitaler Signale — relevant für
Verzerrung, Timing und EMV. **Warum KiCad das nicht kann:** es rechnet kein
dynamisches Signalverhalten aus Bauteil-Specs.

### 〰️ Impedanz — controlled impedance aus dem Stackup  · ✅
Berechnet **Breite und Abstand für eine Ziel-Impedanz** (USB, Ethernet, RF) aus
dem Lagenaufbau. **Warum KiCad das nicht kann:** es rechnet keine Impedanz aus
Stackup + Geometrie.

### 🏭 DFM-Check — Fertigbarkeit gegen echte Fab-Regeln  · ✅
Prüft gegen die Regeln eines **konkreten Fertigers** (min. Track/Space, Annular
Ring, Acid Traps, Silk-über-Pad) — nicht nur generisches DRC, sondern „ist das für
JLCPCB 2-Lagen zu aggressiv?". **Warum KiCad das nicht kann:** sein DRC kennt
keine fertiger-spezifischen DFM-Regeln oder deren Begründung.

### 💰 Kosten-Schätzer — was macht das Board teuer  · ✅
Grobe **Fertigungskosten** aus Boardfläche, Lagenzahl, Via-Anzahl und BOM — plus
was die Kosten treibt. **Warum KiCad das nicht kann:** kein Kostenmodell.

## Simulation & Beschaffung

### 📈 Simulation — Verhalten & Bandbreite verstehen  · ✅
**Fundament da:** LTspice↔KiCad-Konverter (`generators/ltspice2kicad`). Simuliert
Schaltungsverhalten (Verstärker-**Bandbreite**, Frequenzgang, Arbeitspunkt) über
SPICE und **erklärt das Ergebnis in Klartext** statt nur Kurven auszuspucken.
**Warum KiCad das nicht kann:** es kann ngspice *starten*, aber weder die Frage
noch das Ergebnis interpretieren.

- **Gebaut ✅ (0.7.6, Backend 2 in 0.8.1):** `run_spice_sim` (Tool #185)
  führt ein komplettes SPICE-Deck aus — über ein ngspice-Binary
  (`KICAD_MCP_NGSPICE` → PATH → KiCad-bin) **oder über KiCads
  mitgeliefertes `libngspice`** (dieselbe Engine, mit der Eeschemas
  Simulator läuft; per ctypes in einem isolierten Kindprozess, damit ein
  Konvergenz-Absturz nie den Warm-Server reißt). Auf einer normalen
  KiCad-Installation ist damit KEINE Extra-Software nötig. Warum nicht
  Eeschemas Simulator direkt: Eeschema hat in KiCad 10 keine IPC-API und
  kicad-cli kein sim-Kommando. Ohne beide Backends fällt der Button ehrlich
  auf analytische Analyse + Deck zum Kopieren zurück. **Grenze:** das Deck
  muss selbstständig sein — Hersteller-Modelle besorgt der 🧬-Button.

### 🧬 Simulationsmodelle ergänzen  · ✅
Findet und **hängt das passende SPICE-Modell je Bauteil an**, damit die Simulation
überhaupt läuft — der lästige manuelle Schritt vor jeder Simulation. **Warum KiCad
das nicht kann:** es verlangt manuelle Modell-Zuordnung und weiß nicht, welches
Modell zu welchem Bauteil passt.

### 💰 BOM-Konsolidierung — E-Reihe standardisieren, Feeder sparen  · ✅
Jeder eigene R/C-Wert ist eine eigene BOM-Zeile, Rolle und Bestückungs-Feeder.
Boards sammeln über die Zeit **fast-gleiche Werte** an — 10k neben 10,2k neben
9,1k — die denselben Job tun. Dieses Feature **snappt** jeden Wert auf den
nächsten **E-Reihen-Wert** (E6/E12/E24 …) und zeigt, welche Zeilen sich
**zusammenlegen** lassen: weniger Feeder beim Bestücker (Rüstkosten), größere
Stückzahlen (besserer Preis) — und zwar **ohne** ein Bauteil über eine sichere
Toleranz (Default 5 %) zu verschieben. Werte, deren nächster Standardwert weiter
weg liegt, werden ehrlich als *nicht konsolidierbar* ausgewiesen statt still
verbogen. Schlägt vor, ändert nichts. Selektions-fähig: nur die markierten
Bauteile werten. **Warum KiCad das nicht kann:** KiCad kennt weder E-Reihen noch
Feeder oder Bestellmengen — das ist Fertigungs-Wissen über der Netzliste.
*Gebaut:* `consolidate_bom` (headless) über den geteilten `pcb_board_parse` +
`utils/bom_consolidate` (kanonischer SI-Parser inkl. `4k7`/`4n7`-Infix-Notation).

### 🏭 Fab-Standardteile — No-Load-Fee-Teile bevorzugen  · ✅
Große Bestücker halten eine **Hausbibliothek** und verlangen für jeden
Bauteiltyp **außerhalb** davon eine **Feeder-Ladegebühr** (JLCPCB **Basic** vs
**Extended** ~3 $/Typ, Seeed **OPL**, Aisler **Push-Parts** …). Bei 15 Extended-
Typen sind das schnell ~45 $ nur an Ladegebühren, die mit Vorzugsteilen
**wegfallen**. Dieses Feature mappt jeden R/C-Wert+Bauform auf das Vorzugsteil des
Fertigers und schätzt die gesparte Gebühr. **Fab-agnostisch** gebaut: je Fertiger
ein **datierter Snapshot** (`resources/data/fab_parts_<provider>.json`) + ein
Eintrag in der Provider-Registry — neuer Fertiger = JSON + eine Zeile, kein Tool-
Umbau. Läuft ideal **nach** der BOM-Konsolidierung (erst Werte zusammenlegen,
dann aufs Vorzugsteil mappen). Selektions-fähig. **Warum KiCad das nicht kann:**
kein Wissen über Fab-Kataloge, Lagerbestand oder Ladegebühren. *Gebaut:*
`suggest_preferred_parts` (headless, `provider=jlcpcb`) über den geteilten
`pcb_board_parse` (Footprint-ID → Bauform) + `utils/fab_parts`. Der Snapshot ist
kuratierte Seed-Abdeckung mit Datum + Disclaimer, nicht der Live-Katalog — vor
Bestellung Lager prüfen.

### 🛒 Bauteil-Sourcing — Verfügbarkeit, Preis & Alternativen  · ✅
Prüft **live Verfügbarkeit und Preis** gegen Distributoren und findet
**pin-kompatible Alternativen** für abgekündigte oder nicht-lagernde Teile — der
**Live-Netz**-Teil über die offline Fab-Standardteil-Prüfung hinaus. **Warum KiCad
das nicht kann:** kein Wissen über Distributoren, Lagerbestand oder Preise.

## Kreativ / grenzüberschreitend

Die Sorte, bei der ein Layout-Tool endgültig aussteigt — weil sie Wahrnehmung,
externes Wissen oder die Brücke in andere Welten (Firmware, Fertigung, Physik)
braucht.

### 📷 Foto → Schaltung  · ✅
Zieh ein **Foto einer echten Platine** rein — Bauteile, Beschriftungen,
Leiterbahnen werden erkannt und Netzliste/Schaltplan rekonstruiert. **Warum KiCad
das nicht kann:** keine Bild-Wahrnehmung. Reine Multimodal-Arbeit.

### 📄 Datenblatt → Applikationsschaltung  · ✅
**Fundament da:** `circuit_block` (Datenblatt-Spec → Schaltungsblock). Aus dem
Datenblatt die **typische Applikationsschaltung** generieren. **Warum KiCad das
nicht kann:** es liest keine Datenblätter.

### 🔌 Schutzklassen — Isolationskonzept nach IEC 61140/60664  · ✅
Bestimmt die **Schutzklasse** des Geräts (I: geerdet + Basisisolierung, II:
doppelte/verstärkte Isolierung, III: SELV/PELV) und prüft das Isolationskonzept:
je Spannungsgrenze holt der Agent die **geforderten Kriech-/Luftstrecken** aus
`get_safety_spacing` (Tool #186) und stellt sie gegen die gemessenen
Ist-Abstände. **Warum KiCad das nicht kann:** Schutzklassen und Normtabellen
sind reines Norm-Wissen außerhalb der Geometrie.

- **Gebaut ✅ (0.7.7):** `get_safety_spacing` — die IEC-60664-1-Tabellen
  (F.1 Stoßspannung je OVC, F.2 Luftstrecke inkl. PD-Minima, F.4 Kriechstrecke
  je PD/Materialgruppe) als **kuratierter, datierter Snapshot**
  (`resources/data/safety_spacing_iec60664.json`, Werte gegen publizierte
  Normauszüge quergeprüft), verstärkte Isolierung = Kriechweg ×2 +
  Stoßspannungs-Stufe höher, F.7-Regel (Kriechweg ≥ Luftstrecke) eingebaut.
  **Grenze:** Richtwerte für die Ingenieurs-Vorprüfung — Produktnormen
  (62368-1/60335-1/60601-1) können abweichen; keine Zertifizierung.

### ⚡ Sicherheitsabstände — Creepage & Clearance  · ✅
Prüft **Kriech- und Luftstrecken** zwischen Netzspannung und Kleinspannung gegen
Sicherheitsnormen (IEC 62368) — der Bereich, in dem ein Fehler *gefährlich* ist.
**Warum KiCad das nicht kann:** kein Isolations-/Spannungsmodell, keine Normen.

### 💾 Firmware-Pinmap — Pinbelegung als Code  · ✅
Exportiert die MCU-Pinbelegung als **Firmware-Header/Config** (C, DeviceTree,
ESPHome) — die Brücke Hardware ↔ Software, konsistent in beide Richtungen. Paart
sich stark mit **Pin-Tausch**. **Warum KiCad das nicht kann:** es hat kein Modell
der Firmware-Seite.

### 📉 MLCC-Derating — echte Kapazität unter DC-Bias  · ✅
Rechnet die *effektive* Kapazität eines Keramik-Cs unter **DC-Bias und
Temperatur**: ein 10 µF/6,3 V an 5 V ist real oft nur ~4 µF — ein berüchtigter,
stiller Fehler. **Warum KiCad das nicht kann:** es kennt nur den Nennwert, nicht
das reale Bauteilverhalten.

### 🔤 Silkscreen aufräumen — Referenzen lesbar machen  · ✅
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
