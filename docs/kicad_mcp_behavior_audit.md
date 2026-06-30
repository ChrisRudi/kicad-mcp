# KiCad MCP Behavior Audit – Vollständige Analyse aller Verhaltensabweichungen

Version: 0.2

## Ziel

Vollständige Erfassung aller Verhaltensunterschiede zwischen dem KiCad MCP und einer unveränderten KiCad-Installation (10.0, Standardbibliotheken). Es wird **keine** endgültige Bewertung vorgenommen; jede Abweichung wird dokumentiert und gegen die zehn Audit-Fragen geprüft. Die Spalte **Klassifizierung** ist ein *Vorschlag* (K0–K3) — die finale Zuordnung trifft der Projekt-Owner.

Belegstellen beziehen sich auf den Quellbaum unter `kicad_mcp/`. Der gespiegelte Baum `plugin/mcp/kicad_mcp/` verhält sich identisch und wird nicht separat zitiert.

---

## Methodik

Untersucht wurden die Schaltplan-Pfade in zwei Modalitäten, die sich grundlegend unterscheiden:

* **Inkrementelles Patchen** (`add_schematic_symbols`, `add_schematic_wire`, `add_schematic_label`, `add_power_symbols`, `connect_pins`, `convert_global_labels_to_power`) — chirurgische Text-Patches auf eine bestehende `.kicad_sch`.
* **Vollgenerierung** (`build_schematic` / `generate_project`) — erzeugt ein komplettes Blatt aus einer Netzliste.

Geprüfte Module: `tools/sch_patch_tools.py`, `generators/schematic_patcher.py`, `generators/schematic/builder.py`, `generators/symbol_author.py`, `generators/circuit_block/_block_to_patch.py`, `utils/sch_geometry.py`, `generators/sexpr.py`, `tools/erc_tools.py`, `tools/drc_tools.py`.

Pro Eintrag werden die zehn Audit-Fragen kompakt beantwortet:
**(F1)** identisch zu KiCad? · **(F2)** Grund der Änderung · **(F3)** technisch notwendig? · **(F4)** historisch entstanden? · **(F5)** nur Workaround? · **(F6)** entfernbar? · **(F7)** sollte optional werden? · **(F8)** Seiteneffekte · **(F9)** Tests, die bei Entfernung brechen · **(F10)** im Code dokumentiert?

Klassifizierung: **K0** identisch zu KiCad · **K1** technisch notwendig · **K2** sinnvolle Verbesserung (behalten/optional) · **K3** historisch/unnötig (Kandidat zur Entfernung).

---

# 1. Symbolplatzierung

## AUD-101 — Unbedingtes 1.27-mm-Raster-Snapping
Bereich: 1 · Objekt: Symbol-/Wire-/Label-/Power-Anker
Code: `utils/sch_geometry.py::snap_to_grid` (Grid `SCH_PLACE_GRID_MM = 1.27`); aufgerufen u. a. in `add_schematic_symbols` (sch_patch_tools.py), `render_wire`, `render_label`, `_build_power_symbol_snippet`.

- F1: Nein. KiCad snappt auf das aktive GUI-Raster und erlaubt feinere Raster; der MCP rundet jede Eingabe hart auf 1.27 mm.
- F2: Verhindert `endpoint_off_grid`-ERC-Warnungen und Sub-Grid-Drift in Text-Patches.
- F3: Teilweise — ohne GUI ist defensives Snapping sinnvoll, aber nicht zwingend.
- F4: Nein, gezielt eingeführt.
- F5: Teilweise Workaround für fehlende GUI-Raster-Logik.
- F6: Ja, technisch entfernbar; dann Off-Grid-Drift möglich.
- F7: Ja — sinnvoller Kandidat für eine globale `snap`-Option (siehe AUD-1201 zur uneinheitlichen Exposition).
- F8: Effektive Position weicht bis ±0.635 mm von der Eingabe ab.
- F9: Kein Test deckt `snap_to_grid` direkt ab (keiner bricht).
- F10: Ja, Docstring von `snap_to_grid`.
- **Klassifizierung: K2** (sinnvoll, sollte aber konsistent optional werden).

## AUD-102 — Halbpitch-Offset für Passive
Bereich: 1 · Objekt: `Device:C/R/L/CP/D/LED` (+ `_Small`/`_US`)
Code: `utils/sch_geometry.py::snap_for_pin_grid` / `needs_half_grid_offset` / `HALF_GRID_OFFSET_LIBS`; angewandt in `add_schematic_symbols`.

- F1: Nein. KiCad verschiebt den Symbol-Mittelpunkt nicht; der MCP versetzt ihn um 1.27 mm senkrecht zur Pin-Achse.
- F2: Pins dieser Familien (Pitch ungerades Vielfaches von 1.27) sollen auf dem 2.54-Raster landen.
- F3: Nein — Korrektheit ließe sich auch durch korrekte Eingabe erreichen.
- F4: Als „Bug 8"-Fix entstanden (Kommentar im Code).
- F5: Ja, Komfort-Workaround gegen Off-Grid-Pins bei naiver 2.54-Eingabe.
- F6: Ja; Rückgabe-Feld `snapped` macht den Effekt transparent.
- F7: Ja — als optionale Auto-Korrektur denkbar.
- F8: Anker eines Passivs kann 1.27 mm von der Eingabe abweichen.
- F9: `test_sch_geometry.py::TestSnapForPinGrid` (7 Tests) brechen.
- F10: Ja, ausführlicher Kommentar + Docstring.
- **Klassifizierung: K2**.

## AUD-103 — BBox-Überlappungssperre
Bereich: 1 · Objekt: neu eingefügtes Symbol vs. bestehende
Code: `add_schematic_symbols` (Helfer `_bbox_for_part`/`_overlap`, Padding 0.5 mm).

- F1: Nein. KiCad erlaubt überlappende Platzierung.
- F2: Verhindert versehentliches Stapeln in skriptgesteuerten Platzierungen.
- F3: Nein.
- F4: Nein, bewusste Heuristik.
- F5: Nein, Schutzfunktion.
- F6: Ja.
- F7: Ja — könnte als `allow_overlap`-Flag konfigurierbar werden.
- F8: Platzierung schlägt fehl (`success=False`), statt zu überlappen; kann valide Dichtplatzierung blockieren.
- F9: `test_sch_patch_tools.py::TestRoundTrip::test_validate_collision_after_add`.
- F10: Teilweise (Fehlertext, kein Rationale-Kommentar).
- **Klassifizierung: K2** (Schutz sinnvoll, aber abschaltbar machen).

---

# 2. Symbolorientierung

## AUD-201 — Keine Auto-Rotation beim inkrementellen Platzieren
Bereich: 2 · Objekt: reguläre Symbole
Code: `add_schematic_symbols` — `rot = int(round(p.get("rotation_deg", 0)))`.

- F1: Ja (Default 0 = Bibliotheksorientierung).
- F2–F10: entfällt — Standardverhalten.
- **Klassifizierung: K0**.

## AUD-202 — Mirror-vor-Rotation + Pin-Y-Flip
Bereich: 2/10 · Objekt: Pin-Welt-Koordinaten
Code: `utils/sch_geometry.py::pin_world_xy` (Y-Flip `ly=-pin_y`, dann Mirror, dann Rotation).

- F1: Ja — bildet exakt KiCads Transformationsreihenfolge nach.
- F3: Ja, zwingend für korrekte Pin-Koordinaten.
- F10: Ja (Kommentar + CLAUDE.md §5).
- **Klassifizierung: K0/K1** (Nachbildung des Standards, technisch notwendig).

## AUD-203 — Power-Rotation: Generator widerspricht Patch-Pfad (INKONSISTENZ)
Bereich: 2/12 · Objekt: positive Rails (+3V3/+5V/VBUS/VCC …)
Code: `generators/circuit_block/_block_to_patch.py` (Zeilen 255/338/395): `"rotation_deg": 0 if net.upper().startswith("GND") else 180` — vs. `schematic_patcher.py::default_power_rotation` (**immer 0**, mit Kommentar „0 ist kanonisch für jede Power-Familie") und `add_power_symbols`-Default 0.

- F1: Nein für den Generator-Pfad; ja für die Patch-Pfade.
- F2: Vermutlich Annahme „positive Rail = Pin nach unten = 180" — widerspricht aber dem dokumentierten Prinzip, dass die Glyph-Orientierung im Lib-Symbol gebacken ist.
- F3: Nein.
- F4: Wahrscheinlich historisch/uneinheitlich gewachsen (zwei Code-Pfade).
- F5: Ja, faktisch ein Bug-artiger Widerspruch.
- F6: Ja — `else 180` auf `0` setzen vereinheitlicht beide Pfade.
- F7: Nein; sollte vereinheitlicht, nicht optionalisiert werden.
- F8: Dieselbe Rail erscheint je nach Erzeugungsweg 0° oder 180° gedreht.
- F9: Kein Test fixiert die circuit_block-Rotation; `test_convert_global_labels_to_power_replaces_power_nets` fixiert dagegen 0° für GND und +3V3 — eine Vereinheitlichung auf 0 bricht **keinen** bestehenden Test.
- F10: Ja, aber widersprüchlich (Generator-Code vs. `default_power_rotation`-Docstring).
- **Klassifizierung: K3** (echte Inkonsistenz, Vereinheitlichung auf 0 empfohlen).
- **Status: BEHOBEN** — die drei Anker-Stellen in `_block_to_patch.py` rufen jetzt `default_power_rotation(net)` (Single Source of Truth) statt `else 180`. Regressionstest `test_power_anchors_rotation_zero_for_all_rails`. Siehe CHANGELOG [Unreleased] → Fixed.

---

# 3. Symbolinstanzen

## AUD-301 — Deterministische UUID-5 statt Zufalls-UUID
Bereich: 3 · Objekt: Symbol-/Pin-/Wire-/Label-/no_connect-UUIDs
Code: `schematic_patcher.py::stable_uuid` (`uuid5(KICAD_MCP_NS, seed)`, Namespace in `generators/sexpr.py`). Seeds: `"{proj}|{ref}|sym"`, `"{proj}|{ref}|pin|{n}"`, `"{proj}|wire|…"`, `"{proj}|{kind}_label|…"`.

- F1: Nein. KiCad vergibt zufällige UUID-4.
- F2: Idempotenz — wiederholte Patches erzeugen byte-identische UUIDs → saubere Diffs.
- F3: Nein technisch zwingend, aber zentral für die Patch-Idempotenz-Strategie.
- F4: Nein, Design-Entscheidung.
- F5: Nein.
- F6: Ja, aber bricht die Idempotenz (jeder Re-Run würde diff-rauschen).
- F7: Nein — Kernverhalten der Patch-Engine.
- F8: Theoretisch Kollisionsrisiko bei identischem Seed über Projekte (durch project_uuid abgesichert).
- F9: Kein Test prüft `stable_uuid` direkt; viele Round-Trip-Tests setzen aber stabile Ausgaben voraus.
- F10: Ja (Docstring).
- **Klassifizierung: K2** (bewusste, sinnvolle Abweichung; behalten).

## AUD-302 — Erzwungene leere, versteckte Datasheet/Description
Bereich: 3/4 · Objekt: jede Symbol-Instanz
Code: `render_symbol_instance` (Zeilen 827–834).

- F1: Nein. KiCad emittiert diese leeren Felder nicht von sich aus.
- F2: Unterdrückt den Lib-Default-Boilerplate („Power symbol creates a global label …") im GUI-Render.
- F3: Nein.
- F4: Nein.
- F5: Ja, Workaround gegen KiCads Render-Fallback auf Lib-Defaults.
- F6: Ja; dann erscheint Boilerplate-Text auf Power-Symbolen.
- F7: Ja — könnte auf Power-Symbole beschränkt statt für alle Symbole emittiert werden.
- F8: Zusätzliche (versteckte) Properties in jeder Instanz; „Update Symbols from Library" füllt sie bei Bedarf.
- F9: Kein dedizierter Test gefunden.
- F10: Ja (ausführlicher Kommentar).
- **Klassifizierung: K2** (Verbesserung, aber Scope auf Power-Symbole eingrenzbar).

## AUD-303 — MCP-eigene Hidden-Property `kicad-mcp.group`
Bereich: 3 · Objekt: gruppierte Symbole
Code: `GROUP_PROP_NAME = "kicad-mcp.group"` (schematic_patcher.py:35); emittiert in `render_symbol_instance`, wenn `group_id` gesetzt.

- F1: Nein. Kein KiCad-Konzept.
- F2: Trägt Gruppen-Zugehörigkeit für `*_schematic_group`-Tools (move/rotate/list) im File.
- F3: Nein (nur für MCP-Gruppen-Feature).
- F4: Nein.
- F5: Nein.
- F6: Ja — nur bei aktiver Gruppen-Nutzung emittiert (konditional), also bereits opt-in über `group_id`.
- F7: Bereits faktisch optional (nur bei `group_id`).
- F8: Fremd-Property im File; KiCad ignoriert sie, Round-Trip-sicher.
- F9: Gruppen-Tool-Tests (sofern vorhanden) hingen daran; kein direkter Treffer im Mapping.
- F10: Ja (Kommentar zu Gruppen-Tracking).
- **Klassifizierung: K2** (MCP-Feature; nur bei Gruppennutzung sichtbar).

## AUD-304 — Hartkodierte Instanz-Flags + Per-Pin-UUIDs + `(instances)`
Bereich: 3/9 · Objekt: jede Symbol-Instanz
Code: `render_symbol_instance` — `(in_bom yes) (on_board yes) (dnp no)`, Per-Pin-`(pin … (uuid …))`, `(instances (project … (path … (reference …) (unit …))))`.

- F1: Teilweise. KiCad erzeugt diese Strukturen ebenfalls (in der GUI); ein roher Text-Patch ohne sie würde aber in KiCad-10 jeden Pin als `pin_not_connected` melden.
- F2: KiCad-10-Konnektivitätserkennung verlangt Per-Pin-UUIDs + `instances`.
- F3: Ja — ohne diese Blöcke ist die erzeugte Datei nicht korrekt konnektiv.
- F4: Nein.
- F5: Nein, Format-Anforderung.
- F6: Nein (Pin-UUIDs/instances); Flags wären defaultisierbar, aber `in_bom/on_board/dnp` entsprechen KiCads Standard-Defaults.
- F7: Nein.
- F8: `dnp`/`in_bom` sind fix; abweichende Wünsche erfordern Nachbearbeitung.
- F9: Round-Trip-/Konnektivitätstests hängen daran.
- F10: Ja (Docstring zu KiCad-10-Anforderung).
- **Klassifizierung: K1** (technisch notwendig).

## AUD-305 — Referenzvergabe: regulär manuell, Power automatisch
Bereich: 3/7 · Objekt: Referenzen
Code: reguläre Symbole brauchen `ref` vom Aufrufer (Auto-Annotation separat via `annotate_schematic`); Power über `_alloc_pwr_ref` → `#PWR{n:04d}`.

- F1: Teilweise. KiCad annotiert über GUI/ERC; `#PWRnnnn` ist KiCad-Konvention, das 4-stellige Format ebenfalls.
- F2: Kollisionsfreie, deterministische Power-Refs ohne GUI.
- F3: Ja für Power (sonst doppelte/leere Refs).
- F6: Format-Detail (4-stellig) entfernbar, aber konventionskonform.
- F9: `test_annotate_schematic`, `test_convert_global_labels_to_power_replaces_power_nets`.
- F10: Ja (Docstrings).
- **Klassifizierung: K1** (Power-Allokation notwendig) / Auto-Annotation regulär = K2.

---

# 4. Text

## AUD-401 — Feste Feld-Offsets
Bereich: 4 · Objekt: Reference/Value/Footprint/Datasheet/Description
Code: `render_symbol_instance` — Reference `y−5.08`, Value `y+5.08`, Footprint `y+7.62` (hidden), Datasheet `y+10.16`, Description `y+12.7`, extra `y+15.24`.

- F1: Nein. KiCad übernimmt Feldpositionen aus der Lib-Symbol-Definition (pro Symbol kuratiert).
- F2: Einheitliche, deterministische Textlage ohne Lib-Parsing der Feldpositionen.
- F3: Nein.
- F4: Nein.
- F5: Teilweise (vermeidet Auslesen der Lib-Feldgeometrie).
- F6: Ja — könnte die Lib-Feldpositionen übernehmen.
- F7: Ja.
- F8: Textlage weicht von der je Symbol optimierten KiCad-Lage ab; bei großen Symbolen kann Text im Body landen.
- F9: Kein dedizierter Test (Round-Trip-Tests tolerant).
- F10: Nein (nur Code, kein Rationale-Kommentar).
- **Klassifizierung: K3-Kandidat** (uniforme Offsets statt Lib-Positionen — prüfen, ob Lib-Übernahme besser).

## AUD-402 — Property-Winkel immer 0
Bereich: 4 · Objekt: Feldrotation
Code: `render_symbol_instance` übergibt Winkel `0` an jedes `render_property`, auch bei gedrehtem Symbol.

- F1: Teilweise. KiCad lässt Felder horizontal, kann sie aber mit dem Symbol mitführen.
- F2: Reference/Value bleiben horizontal lesbar.
- F6: Ja.
- F7: Ja.
- F8: Felder rotieren nicht mit dem Symbol; bei 90°-Symbolen evtl. unerwartete Lage.
- F9: Kein Test.
- F10: Nein.
- **Klassifizierung: K2** (meist gewünscht, aber undokumentiert).

## AUD-403 — Footprint/extra/group stets versteckt; Property-Reihenfolge
Bereich: 4 · Objekt: Sichtbarkeit & Reihenfolge
Code: Reihenfolge Reference→Value→Footprint→Datasheet→Description→extra→group→Pins→instances; Footprint/Datasheet/Description/extra/group `hide=yes`.

- F1: Reihenfolge entspricht KiCads nativer Property-Reihenfolge; Footprint/Datasheet hidden = KiCad-Standard.
- F8: extra_props sind immer hidden — keine sichtbaren Benutzerfelder möglich ohne Nachbearbeitung.
- F10: Teilweise.
- **Klassifizierung: K0/K2** (Reihenfolge K0; „extra immer hidden" = kleines K2).

---

# 5. Leitungen

## AUD-501 — 1-Ellbogen-Manhattan (horizontal-first)
Bereich: 5 · Objekt: `connect_pins` mode `wire`
Code: kollinear → ein Segment; sonst Knie bei `(p2.x, p1.y)`.

- F1: Nein. KiCad routet manuell; keine implizite Manhattan-Erzeugung aus reiner Pin-Auswahl.
- F2: Komfort: Pin-zu-Pin in einem Call.
- F3: Nein.
- F7: Sinnvolles Feature; Routing-Variante könnte konfigurierbar sein (immer horizontal-first).
- F8: Komplexe Pfade brauchen `add_schematic_wire`; horizontal-first kann ungünstige Knie erzeugen.
- F9: `test_add_wire` (segments_added).
- F10: Ja (Docstring).
- **Klassifizierung: K2**.

## AUD-502 — Wire-Endpunkt-Snap (mit `snap`-Flag)
Bereich: 5 · Objekt: `add_schematic_wire`/`render_wire`
Code: Endpunkte via `snap_to_grid` (Default True), `snap=False` möglich.

- F1: Nein (siehe AUD-101), aber `snap`-Flag mildert.
- F7: Bereits optional.
- F9: keiner direkt.
- F10: Ja.
- **Klassifizierung: K2** (bereits opt-out).

## AUD-503 — Keine automatischen Junctions
Bereich: 5/8 · Objekt: Junctions
Code: `connect_pins` meldet `junctions_added` stets 0; kein `render_junction`, kein `add_junction`-Tool.

- F1: Ja — KiCad setzt Junctions ebenfalls nur bei interaktivem Zeichnen; reine Text-Patches setzen keine.
- F8: Mehrfach-Abzweige können ohne explizite Junction unverbunden bleiben (Footgun, aber KiCad-konform für Patches).
- F10: Ja (Docstring).
- **Klassifizierung: K0** (kein Auto-Verhalten; entspricht Patch-Realität).

---

# 6. Netlabels

## AUD-601 — Ablehnung von Power-Netznamen als Global-Label
Bereich: 6 · Objekt: `add_schematic_label`
Code: bei `kind="global"` + `power_lib_id_for(text)` → `success=False`, `suggested_lib_id`.

- F1: Nein. KiCad lässt ein Global-Label „GND" zu.
- F2: Erzwingt die KiCad-Power-Symbol-Konvention (ERC `power_pin_not_driven`, einheitliche Glyphe, Netzklasse).
- F3: Nein.
- F5: Nein, bewusste Leitplanke.
- F6: Ja.
- F7: Ja — könnte als Warnung statt hartem Fehler ausgelegt werden.
- F8: Ein bewusst gewünschtes Power-Global-Label ist ohne `add_power_symbols` nicht setzbar.
- F9: `test_add_label_rejects_power_net_text`.
- F10: Ja (Kommentar/Docstring).
- **Klassifizierung: K2** (sinnvolle Leitplanke; ggf. als Warnung optional).

## AUD-602 — Auto-Justierung aus Winkel
Bereich: 6 · Objekt: `justify_for_angle`
Code: leer → `left` für 0/90, `right` für 180/270.

- F1: Nein. KiCad wählt nicht automatisch winkelabhängig.
- F2: Labels lesen „auswärts" vom Chip.
- F6/F7: Ja, optional/überschreibbar (`justify`-Param überschreibt bereits).
- F9: kein Test.
- F10: Ja (Docstring).
- **Klassifizierung: K2**.

## AUD-603 — `connect_pins` label-mode: vollautomatische Label-Geometrie
Bereich: 6/4 · Objekt: Auto-Winkel/Stub/Justify/Kollisions-Push/Typ/Name
Code: `pin_outward_angle` + `LABEL_OUTWARD_TABLE` (Stub 3.81 mm), Kollisions-Push 2.54 mm (≤12 Iter.), immer `global`, Default-Name `f"{ref}_{pin}"`.

- F1: Nein. KiCad positioniert/benennt Labels manuell.
- F2: Sheet-übergreifende Netze in einem Call.
- F7: Ja — Auto-Name/Typ könnten konfigurierbar sein.
- F8: Default-Typ `global` weicht von `add_schematic_label`-Default `local` ab (siehe AUD-1202).
- F9: kein direkter Test im Mapping.
- F10: Teilweise (Docstring).
- **Klassifizierung: K2**.

---

# 7. Versorgungssymbole

## AUD-701 — `#PWRnnnn`-Auto-Allokation
Siehe AUD-305. **K1**.

## AUD-702 — Rail-Suffix-Kanonisierung (`+5V_SYS` → Value `+5V`)
Bereich: 7 · Objekt: `power_lib_id_for`/`_POWER_LIB_IDS`
Code: u. a. `+5V_SYS → (power:+5V, "+5V")`, `+3.3V → (power:+3V3, "+3V3")`, `VBUS_SYS → (power:VBUS, "VBUS")`.

- F1: Nein. KiCad benennt nicht automatisch um; Netz-Join erfolgt über exakt gleichen Value.
- F2: Verhindert ein isoliertes `+5V_SYS`-Inselnetz; alle Verbraucher landen auf einer Rail.
- F3: Nein.
- F5: Teilweise.
- F6: Ja.
- F7: Ja — der Suffix-Verlust kann unerwünscht sein (Information geht verloren).
- F8: Eingegebener Suffix-Name verschwindet im Value; betrifft nur Power-Value, nicht Signalnetze.
- F9: `test_convert_global_labels_to_power_canonical_value`.
- F10: Ja (ausführlicher Kommentar).
- **Klassifizierung: K2** (sinnvoll, aber Datenverlust — optional machen).

## AUD-703 — `convert_global_labels_to_power` Auto-Konvertierung
Bereich: 7 · Objekt: bestehende Power-Global-Labels
Code: ersetzt erkannte Power-Global-Labels durch `power:`-Symbole am selben Anker, Rotation aus `default_power_rotation` (=0), löscht Original.

- F1: Nein. Explizite KiCad-fremde Sammeloperation.
- F2: Bringt mit Global-Labels verdrahtete Versorgung auf die Power-Symbol-Konvention.
- F3: Nein.
- F6: Ja (eigenes Tool, kein implizites Verhalten).
- F7: Bereits opt-in (eigener Tool-Call, `dry_run` vorhanden).
- F8: Lokale/hierarchische Labels unangetastet; kollidierende Anker werden übersprungen.
- F9: `test_convert_global_labels_to_power_replaces_power_nets` (+ Rotation 0 für GND/+3V3), `…_hides_pwr_reference`.
- F10: Ja (ausführlicher Docstring).
- **Klassifizierung: K2** (explizites Hilfs-Tool).

## AUD-704 — Reference von Power-Symbolen erzwungen versteckt
Bereich: 7/4 · Objekt: `#PWR`-Reference
Code: `_build_power_symbol_snippet(… hide_reference=True)`.

- F1: Ja im Effekt — KiCad versteckt `#PWR`-Refs ebenfalls; der MCP setzt es zusätzlich hart.
- F8: keine.
- F9: `test_convert_global_labels_to_power_hides_pwr_reference`.
- F10: Ja (Kommentar).
- **Klassifizierung: K0/K1** (entspricht KiCad-Erscheinung).

---

# 8. ERC-bezogene Automatik

## AUD-801 — Auto-`PWR_FLAG` bei Vollgenerierung
Bereich: 8 · Objekt: `build_schematic` (nur Generierung)
Code: `generators/schematic/builder.py::_emit_pwr_flags` (unbedingt aus `build_schematic` aufgerufen); platziert `power:PWR_FLAG` an erstem Pin jedes Power-Netzes, `(in_bom no) (on_board no)`, Ref `#FLGnn`.

- F1: Nein. KiCad verlangt manuelles Setzen von `PWR_FLAG`.
- F2: Unterdrückt `power_pin_not_driven` in generierten Projekten.
- F3: Nein (für die Generierungs-Bequemlichkeit).
- F4: Nein.
- F5: Ja — ERC-Workaround.
- F6: Ja.
- F7: Ja — als generator-option (`add_pwr_flags=True/False`).
- F8: Verändert die ERC-Semantik automatisch; ein Netz ohne echte Quelle wird als „getrieben" markiert (kann reale Fehler maskieren). Nur im Generierungspfad, nicht beim Patchen.
- F9: kein Test im Mapping nachgewiesen (Generator-Pfad).
- F10: Teilweise (Inline-Kommentar).
- **Klassifizierung: K2/K3** (Bequemlichkeit vs. ERC-Maskierung — optional machen).

## AUD-802 — Auto-`no_connect` auf allen unbenutzten Pins (Vollgenerierung)
Bereich: 8 · Objekt: `build_schematic`
Code: `builder.py::_emit_no_connects` (unbedingt); setzt no-connect auf jeden Pin ohne Netz, unit-gefiltert.

- F1: Nein. KiCad verlangt manuelles Setzen.
- F2: Verhindert `pin_not_connected`-ERC-Fehler.
- F5: Ja — ERC-Workaround.
- F7: Ja (Generator-Option).
- F8: Maskiert evtl. real vergessene Verbindungen als „bewusst offen". Nur Generierungspfad.
- F9: kein Test im Mapping.
- F10: Ja (Docstring).
- **Klassifizierung: K2/K3** (optional machen).

## AUD-803 — `run_erc`/`run_drc` read-only (kein Auto-Fix)
Bereich: 8 · Code: `tools/erc_tools.py`, `tools/drc_tools.py` (kicad-cli, nur Lesen).

- F1: Ja — identisch zu KiCad (Report-only).
- **Klassifizierung: K0**.

---

# 9. Konnektivität

## AUD-901 — Multi-Unit-Pin-Filterung
Bereich: 9 · Objekt: Mehr-Unit-Bauteile
Code: `schematic_patcher.py::get_lib_symbol_pins(unit=…)`; `builder.py::_detect_units`/`_find_user_unit`.

- F1: Ja im Ergebnis — korrektes Unit-Verhalten; `add_schematic_symbols` verlangt expliziten `unit`-Parameter.
- F2: Ohne Filterung bekäme Unit 2 die Pin-UUIDs von Unit 1 → Konnektivität korrupt.
- F3: Ja, zwingend für korrekte Multi-Unit-Konnektivität.
- F10: Ja (Docstring).
- **Klassifizierung: K1**.

## AUD-902 — Per-Pin-UUID/`instances`
Siehe AUD-304. **K1**.

---

# 10. Geometrie

## AUD-1001 — `_fmt` 4-Nachkommastellen + Snap-Rundung
Bereich: 10 · Code: `_fmt` (in `sch_patch_tools.py` und `schematic_patcher.py`), `snap_to_grid` rundet auf 4 Dezimalstellen.

- F1: Ja — entspricht KiCads 100-nm-Auflösung und Normalisierung beim Save.
- F3: Ja, vermeidet Float-Diff-Rauschen.
- F9: kein dedizierter Test.
- F10: Ja (Docstring).
- **Klassifizierung: K0/K1**.

## AUD-1002 — Transform-/Spiegel-/Rotationsreihenfolge konsistent
Siehe AUD-202. Einheitlich über alle Tools (`pin_world_xy`). **K0/K1**.

---

# 11. Generatoren

## AUD-1101 — Symbol-Generator-Defaults (`symbol_author.py`)
Bereich: 11 · Objekt: neu erzeugte Lib-Symbole (`render_library_symbol`)
Code: Pitch `2.54`, Pin-Länge `2.54`, Body-Breite aus längstem Pin-Namen (≈1 mm/Zeichen) oder Override, Body-Höhe `max(Pin-Span+Pitch, Pitch)`, Pin-Auto-Split (erste Hälfte links, Rest rechts), `(exclude_from_sim no) (in_bom yes) (on_board yes)`, Stroke `0.254`, Fill `background`, Reference/Value an festen Offsets.

- F1: Nein direkt vergleichbar — KiCad-Symbol-Editor erzeugt Symbole interaktiv. Die Defaults sind konventionskonform (2.54-Raster).
- F2: Deterministisches, raster-konformes Auto-Layout für generierte Symbole.
- F3: Teilweise (ein Layout muss gewählt werden).
- F7: Ja — Layout-Parameter könnten exponiert werden.
- F8: Pin-Auto-Split kann unergonomische Pinouts erzeugen; bewusst „minimum viable".
- F9: kein Test im Mapping nachgewiesen.
- F10: Teilweise (Konstanten kommentiert).
- **Klassifizierung: K2**.

## AUD-1102 — Circuit-Block radiale Ring-Anordnung
Bereich: 11/1 · Objekt: generierte Blöcke
Code: `circuit_block/_block_to_patch.py::_ring_position` — Startradius `_RING_RADIUS=12.7`, Schritt `_RING_STEP=5.08`, 8 feste Richtungen, Umbruch alle 8 Slots; alle Peripherie `rotation_deg:0` außer Power (siehe AUD-203).

- F1: Nein. KiCad ordnet beim Einfügen nicht an.
- F2: Deterministisches, kollisionsarmes Startlayout („minimum viable").
- F3: Nein.
- F4: Nein.
- F5: Teilweise (Platzhalter-Layout).
- F6: Ja.
- F7: Ja.
- F8: Kein vollwertiger Auto-Placer; Nachjustierung via `move/rotate_schematic_group` vorgesehen.
- F9: kein Test im Mapping (Konstanten 12.7/5.08 ungetestet).
- F10: Ja (Kommentar).
- **Klassifizierung: K2**.

## AUD-1103 — Generator-Determinismus
Bereich: 11 · Objekt: alle Generatoren
Code: keine `random`/`uuid4`/`time`-Nutzung in Schaltplan-Tools; alle UUIDs `uuid5`-seedbasiert.

- F1: Abweichung von KiCads Zufalls-UUIDs, aber gewünschte Eigenschaft.
- Gleiche Eingabe → byte-gleiche Ausgabe.
- **Klassifizierung: K2** (siehe AUD-301).

---

# 12. Toolübergreifende Konsistenz

## AUD-1201 — `snap`-Flag uneinheitlich exponiert (INKONSISTENZ)
Bereich: 12 · Objekt: Snap-Opt-out
Code: exponieren `snap`: `add_schematic_wire`, `add_power_symbols` (+ per-anchor), `connect_pins`. Exponieren **nicht**: `add_schematic_symbols`, `add_schematic_label`, `convert_global_labels_to_power` (hart `snap=True`).

- F1: n/a (interne Konsistenzfrage).
- F2: Historisch nachgerüstet — `snap` wurde nur dort ergänzt, wo Feinpitch-Footgun auftrat.
- F5: Ja, asymmetrisch gewachsen.
- F6/F7: Ja — `snap`-Flag einheitlich auf allen Platzierungs-Tools anbieten.
- F8: Symbole/Labels lassen sich nicht exakt auf Off-Grid-Feinpitch-Pins setzen (kein Opt-out).
- F9: kein Test fixiert das Fehlen.
- F10: Nein (Asymmetrie nirgends dokumentiert).
- **Klassifizierung: K3** (Inkonsistenz; `snap` vereinheitlichen).

## AUD-1202 — Label-Kind-Default uneinheitlich
Bereich: 12 · Objekt: Default-Labeltyp
Code: `add_schematic_label` Default `local`; `connect_pins` label-mode hart `global`.

- F1: n/a.
- F2: Sinnvoll (sheet-übergreifend = global), aber in den Docstrings nicht erklärt.
- F7: Doku angleichen; ggf. Typ in `connect_pins` konfigurierbar.
- F8: Überraschungseffekt für Nutzer.
- F10: Nein.
- **Klassifizierung: K2** (intentional, aber undokumentiert → dokumentieren).

## AUD-1203 — Power-Rotation Generator vs. Patch (Querverweis)
Siehe AUD-203. **K3**.

---

# Inventur & vorgeschlagene Klassifizierung

| ID | Bereich | Kurzbeschreibung | KiCad-identisch | Test-Abdeckung | Vorschlag |
|----|---------|------------------|-----------------|----------------|-----------|
| AUD-101 | Platzierung | 1.27-mm-Snap (unbedingt in Teilen) | Nein | nein | K2 |
| AUD-102 | Platzierung | Halbpitch-Offset Passive | Nein | ja (7) | K2 |
| AUD-103 | Platzierung | BBox-Überlappungssperre | Nein | ja | K2 |
| AUD-201 | Orientierung | keine Auto-Rotation (Patch) | Ja | – | K0 |
| AUD-202 | Orientierung/Geom | Mirror-vor-Rotation, Pin-Y-Flip | Ja | indirekt | K0/K1 |
| AUD-203 | Orientierung/Konsistenz | Power-Rot 180 (Generator) vs 0 | Nein (Gen.) | ja (neu) | **K3 — behoben** |
| AUD-301 | Instanzen | deterministische UUID-5 | Nein | indirekt | K2 |
| AUD-302 | Instanzen/Text | leere hidden Datasheet/Description | Nein | nein | K2 |
| AUD-303 | Instanzen | `kicad-mcp.group` Property | Nein | – | K2 |
| AUD-304 | Instanzen/Konn. | Flags + Pin-UUID + instances | Teilw. | indirekt | K1 |
| AUD-305 | Instanzen/Power | Ref-Vergabe (Power auto) | Teilw. | ja | K1/K2 |
| AUD-401 | Text | feste Feld-Offsets | Nein | nein | K3-Kandidat |
| AUD-402 | Text | Feldwinkel immer 0 | Teilw. | nein | K2 |
| AUD-403 | Text | hidden-Felder + Reihenfolge | Teilw. | indirekt | K0/K2 |
| AUD-501 | Leitungen | 1-Ellbogen-Manhattan | Nein | ja | K2 |
| AUD-502 | Leitungen | Wire-Snap (opt-out) | Nein | – | K2 |
| AUD-503 | Leitungen | keine Auto-Junction | Ja | – | K0 |
| AUD-601 | Netlabels | Power-Netz-Ablehnung | Nein | ja | K2 |
| AUD-602 | Netlabels | Auto-Justify | Nein | nein | K2 |
| AUD-603 | Netlabels | label-mode Auto-Geometrie | Nein | teils | K2 |
| AUD-701 | Power | `#PWRnnnn`-Allokation | Teilw. | ja | K1 |
| AUD-702 | Power | Rail-Suffix-Kanonisierung | Nein | ja | K2 |
| AUD-703 | Power | global→power Konvertierung | Nein | ja | K2 |
| AUD-704 | Power/Text | `#PWR`-Ref hidden | Ja (Effekt) | ja | K0/K1 |
| AUD-801 | ERC | Auto-PWR_FLAG (Generierung) | Nein | nein | K2/K3 |
| AUD-802 | ERC | Auto-no_connect (Generierung) | Nein | nein | K2/K3 |
| AUD-803 | ERC | run_erc/run_drc read-only | Ja | – | K0 |
| AUD-901 | Konnektivität | Multi-Unit-Pin-Filter | Ja | indirekt | K1 |
| AUD-1001 | Geometrie | `_fmt`/Rundung 4 Dezimal | Ja | nein | K0/K1 |
| AUD-1101 | Generator | Symbol-Generator-Defaults | n/a | nein | K2 |
| AUD-1102 | Generator | Ring-Anordnung 12.7/5.08 | Nein | nein | K2 |
| AUD-1103 | Generator | Determinismus (uuid5) | Nein | indirekt | K2 |
| AUD-1201 | Konsistenz | `snap`-Flag asymmetrisch | n/a | nein | **K3** |
| AUD-1202 | Konsistenz | Label-Kind-Default lokal/global | n/a | nein | K2 |

---

# Empfehlungen für die anschließende Bereinigung

Die folgenden Punkte sind die klarsten Bereinigungs-Kandidaten (rein dokumentierend, keine Aktion in diesem Audit):

1. **AUD-203 / AUD-1203 (K3) — ERLEDIGT:** Power-Rotation vereinheitlicht — `circuit_block` ruft jetzt `default_power_rotation(net)` statt `else 180`; alle Pfade folgen derselben Quelle. Regressionstest ergänzt.
2. **AUD-1201 (K3):** `snap`-Flag auf `add_schematic_symbols`, `add_schematic_label`, `convert_global_labels_to_power` nachrüsten (Default `True`), für einheitliches Off-Grid-Opt-out.
3. **AUD-401 (K3-Kandidat):** Prüfen, ob feste Feld-Offsets durch Übernahme der Lib-Symbol-Feldpositionen ersetzt werden sollten.
4. **AUD-801/802 (K2/K3):** Auto-`PWR_FLAG` und Auto-`no_connect` der Vollgenerierung als Generator-Optionen exponieren — sie können reale ERC-Fehler maskieren.
5. **AUD-302 (K2):** Erwägen, die erzwungenen leeren Datasheet/Description nur für `power:`-Symbole zu emittieren statt für alle Instanzen.
6. **AUD-702 (K2):** Rail-Suffix-Kanonisierung optional machen (Informationsverlust des Suffix-Namens).
7. **Doku-Lücken (F10 = Nein):** AUD-401, AUD-402, AUD-1201, AUD-1202 sind im Code nicht begründet — Rationale ergänzen.
8. **Test-Lücken (F9 = kein Test):** AUD-101 (snap_to_grid), AUD-602 (justify_for_angle), AUD-1001 (`_fmt`), AUD-301/1103 (stable_uuid), AUD-1102 (Ring-Konstanten) sind ungetestet — vor jeder Änderung Regressionstests ergänzen.

---

# Zielzustand

Nach Abschluss bleibt ein MCP, dessen Standardverhalten dem nativen KiCad möglichst entspricht und der nur wenige, klar dokumentierte, technisch begründete Erweiterungen besitzt. Die obige Inventur ist die Grundlage für die Klassifizierungs-Entscheidung (K0–K3) und die anschließende Bereinigung.
