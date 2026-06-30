# KiCad MCP Behavior Delta
Version: 0.2

## Zweck

Dieses Dokument beschreibt ausschließlich das tatsächlich beobachtete Verhalten des KiCad MCP gegenüber dem unveränderten Standardverhalten von KiCad.

Es werden keine Empfehlungen ausgesprochen und keine Regeln definiert. Jeder Eintrag beschreibt eine reproduzierbare automatische Verhaltensweise des MCP.

Dieses Dokument dient als Grundlage für spätere Verhaltensregeln und Regressionstests.

Jeder Eintrag nennt den Code-Beleg (Datei + Funktion), aus dem das Verhalten reproduzierbar folgt. Belegstellen beziehen sich auf den Quellbaum unter `kicad_mcp/` (das gespiegelte `plugin/mcp/kicad_mcp/` verhält sich identisch).

---

## Referenz

Referenz ist das Verhalten einer unveränderten KiCad-Installation (10.0) mit den Standardbibliotheken und den Standardorientierungen der Symbole. Die betrachteten MCP-Pfade sind die Schaltplan-Patch-Tools (`tools/sch_patch_tools.py`), der Schaltplan-Patcher (`generators/schematic_patcher.py`), die Schaltplan-Geometrie (`utils/sch_geometry.py`) und der Circuit-Block-Generator (`generators/circuit_block/_block_to_patch.py`).

---

# Beobachtete Abweichungen

## Symbolplatzierung

### BD-001
Objekt:
Symbol-Anker (alle über `add_schematic_symbols` platzierten Symbole)

Status:
Reproduzierbar

Beschreibung:
Jede vom Aufrufer übergebene Koordinate wird vor dem Einfügen auf das 1.27-mm-Platzierungsraster gerundet.

KiCad Standard:
Das Symbol wird an der angegebenen Position eingefügt. Off-Grid-Platzierung ist (bei kleinerem Raster) erlaubt.

MCP Verhalten:
`add_schematic_symbols` ruft unbedingt `snap_to_grid(raw_x, raw_y)` (`SCH_PLACE_GRID_MM = 1.27`) auf, bevor die Position verarbeitet wird (`tools/sch_patch_tools.py::add_schematic_symbols`, `utils/sch_geometry.py::snap_to_grid`). Es gibt keinen Schalter, um dies bei `add_schematic_symbols` abzuschalten.

Auswirkung:
Die effektive Platzierungs-Koordinate kann um bis zu ±0.635 mm von der Eingabe abweichen. Pin-Sockets landen verlässlich auf dem Raster (kein `endpoint_off_grid`).

---

### BD-002
Objekt:
Neu eingefügtes Symbol relativ zu bestehenden Symbolen

Status:
Reproduzierbar

Beschreibung:
Das Einfügen wird verweigert, wenn die approximierte Welt-BBox des neuen Symbols die BBox eines bereits platzierten Symbols überlappt.

KiCad Standard:
Symbole dürfen frei übereinander/überlappend platziert werden; KiCad blockiert das nicht.

MCP Verhalten:
`add_schematic_symbols` berechnet pro Teil eine Pin-basierte BBox und prüft sie gegen `doc.iter_symbol_world_bboxes()`. Bei Überlappung (Padding 0.5 mm) wird das Teil **nicht** eingefügt, sondern mit `"… BBox overlaps existing symbol … refused to insert."` als Fehler gemeldet (`tools/sch_patch_tools.py::add_schematic_symbols`, Hilfsfunktionen `_bbox_for_part`/`_overlap`).

Auswirkung:
Kollidierende Platzierungen schlagen fehl statt zu überlappen; der Aufrufer muss umpositionieren. `success` wird false, sobald ein Teil verworfen wurde.

---

### BD-003
Objekt:
Zweipolige Halbpitch-Passive (`Device:C`, `Device:R`, `Device:L`, `Device:CP`, `Device:D`, `Device:LED` und deren `_Small`/`_US`-Varianten)

Status:
Reproduzierbar

Beschreibung:
Der Symbol-Mittelpunkt dieser Familien wird nach dem Grid-Snap zusätzlich um 1.27 mm in der zur Pin-Achse senkrechten Richtung verschoben.

KiCad Standard:
Das Symbol wird genau an der angegebenen Position eingefügt; eine Mittelpunkt-Korrektur findet nicht statt.

MCP Verhalten:
Für `lib_id in HALF_GRID_OFFSET_LIBS` ruft `add_schematic_symbols` `snap_for_pin_grid(x, y, lib_id, rot)` auf. Bei Rotation 0/180 wird Y, bei 90/270 X auf `(N + 0.5) × 2.54` gesnappt, damit beide Pins (Pitch ungerades Vielfaches von 1.27 mm) auf dem 2.54-mm-Raster landen (`utils/sch_geometry.py::snap_for_pin_grid`, `needs_half_grid_offset`). Die Verschiebung wird im Result-Feld `snapped` zurückgegeben.

Auswirkung:
Die tatsächliche Anker-Position eines Passivs kann um 1.27 mm von der Eingabe abweichen. Beide Pins liegen danach auf dem Standard-Raster.

---

### BD-004
Objekt:
Symbol-Instanz-Metadaten (Flags, UUIDs)

Status:
Absichtlich

Beschreibung:
Jede Symbol-Instanz erhält feste Status-Flags und deterministisch abgeleitete UUIDs.

KiCad Standard:
KiCad vergibt zufällige UUIDs und übernimmt `in_bom`/`on_board`/`dnp` aus der GUI/Bibliothek; pro Platzierung neu.

MCP Verhalten:
`render_symbol_instance` emittiert hart `(in_bom yes) (on_board yes) (dnp no)`. Die Symbol-UUID ist `stable_uuid(f"{project_uuid}|{ref}|sym")`, jede Pin-UUID `stable_uuid(f"{project_uuid}|{ref}|pin|{n}")` (deterministisch, UUID-5). Zusätzlich wird ein `(instances …)`-Block mit Projektname und Root-Sheet-Pfad erzeugt (`generators/schematic_patcher.py::render_symbol_instance`).

Auswirkung:
Wiederholtes Platzieren desselben `ref` erzeugt byte-identische UUIDs (idempotente Patches, saubere Diffs). Die KiCad-10-Konnektivitätserkennung greift, weil Per-Pin-UUIDs + `instances` immer vorhanden sind.

---

## Symbolorientierung

### BD-101
Objekt:
Reguläres Symbol über `add_schematic_symbols`

Status:
Reproduzierbar

Beschreibung:
Es wird keine automatische Rotation angewandt; das Symbol bleibt in Bibliotheksorientierung, solange der Aufrufer keine Rotation übergibt.

KiCad Standard:
Symbol wird in Bibliotheksorientierung (0°) eingefügt.

MCP Verhalten:
`rot = int(round(float(p.get("rotation_deg", 0))))` — Default 0, durchgereicht. Keine Heuristik dreht das Symbol (`tools/sch_patch_tools.py::add_schematic_symbols`). (Abweichend davon dreht der Circuit-Block-Generator Versorgungssymbole automatisch — siehe BD-103/BD-701.)

Auswirkung:
Für die Patch-Tools entspricht die Orientierung dem KiCad-Standard; eine Drehung erfolgt nur auf expliziten Wunsch.

---

### BD-102
Objekt:
Pin-Welt-Koordinaten gespiegelter/gedrehter Symbole

Status:
Absichtlich

Beschreibung:
Bei der Welt-Koordinaten-Auflösung wird zuerst gespiegelt, dann gedreht; die Lib-Symbol-Pin-Y-Achse wird beim Instanzieren invertiert.

KiCad Standard:
Identische Transformations-Konvention (Mirror vor Rotation, Y-Flip beim Instanzieren) — dies ist die Referenz, die der MCP nachbildet.

MCP Verhalten:
`pin_world_xy` flippt zunächst `ly = -pin_local_y` (Lib-Symbole sind Y-up), wendet dann Mirror (`y` → `lx = -lx`, `x` → `ly = -ly`) und erst danach die diskrete Rotation 0/90/180/270 an (`utils/sch_geometry.py::pin_world_xy`). Die Reihenfolge ist in CLAUDE.md (§Koordinaten #5) als hart erkämpfter Footgun dokumentiert.

Auswirkung:
Pin-Positionen gespiegelter Symbole stimmen mit KiCads interner Berechnung überein; eine vertauschte Reihenfolge würde Pins fehlplatzieren.

---

### BD-103
Objekt:
Versorgungssymbol-Rotation bei generierten Blöcken vs. Patch-Tools

Status:
Beobachtet

Beschreibung:
Die Default-Rotation eines Versorgungssymbols hängt vom Code-Pfad ab: Patch-Tools/Konvertierung verwenden 0° für alle Familien, der Circuit-Block-Generator verwendet 180° für positive Rails.

KiCad Standard:
Das Power-Symbol wird in Bibliotheksorientierung eingefügt (Glyph-Orientierung ist im Lib-Symbol gebacken).

MCP Verhalten:
`default_power_rotation(value)` gibt **immer 0** zurück (genutzt von `convert_global_labels_to_power`); `add_power_symbols` defaultet `rotation_deg` ebenfalls auf 0 (`generators/schematic_patcher.py::default_power_rotation`, `tools/sch_patch_tools.py::add_power_symbols`). Der Circuit-Block-Generator hingegen setzt `"rotation_deg": 0 if net.upper().startswith("GND") else 180` (`generators/circuit_block/_block_to_patch.py`, mehrfach). Das widerspricht dem Kommentar in `schematic_patcher.py`, der 0° als kanonisch für alle Familien beschreibt.

Auswirkung:
Dieselbe Rail (z. B. `+3V3`) erscheint je nach Erzeugungsweg unterschiedlich orientiert: 0° via `add_power_symbols`/Konvertierung, 180° via generiertem Block.

---

## Textposition

### BD-201
Objekt:
Property-Felder (Reference, Value, Footprint, Datasheet, Description) jeder Symbol-Instanz

Status:
Reproduzierbar

Beschreibung:
Die Feldpositionen werden mit festen Offsets relativ zum Symbol-Ursprung gesetzt, unabhängig von den Feldpositionen der Bibliothek.

KiCad Standard:
Die Feldpositionen stammen aus der Lib-Symbol-Definition (pro Symbol unterschiedlich) und werden bei der Platzierung übernommen.

MCP Verhalten:
`render_symbol_instance` setzt feste Offsets vom Anker `(x, y)`: Reference `y − 5.08`, Value `y + 5.08`, Footprint `y + 7.62` (hidden), Datasheet `y + 10.16` (hidden), Description `y + 12.7` (hidden); jeder optionale `extra_prop` bei `y + 15.24` (`generators/schematic_patcher.py::render_symbol_instance` + `render_property`).

Auswirkung:
Die Textanordnung ist über alle Symbole hinweg uniform, weicht aber von der je nach Symbol kuratierten KiCad-Standardlage ab. Reference steht stets oberhalb, Value unterhalb des Symbols.

---

### BD-202
Objekt:
Datasheet- und Description-Property jeder Symbol-Instanz

Status:
Absichtlich

Beschreibung:
Datasheet und Description werden als leere, versteckte Instanz-Properties erzwungen statt aus dem Lib-Symbol übernommen.

KiCad Standard:
Eine Instanz erbt Datasheet/Description aus dem Lib-Symbol (bei Power-Symbolen z. B. der Boilerplate-Text „Power symbol creates a global label …").

MCP Verhalten:
Sofern nicht via `extra_props` überschrieben, hängt `render_symbol_instance` `(property "Datasheet" "" … (hide yes))` und `(property "Description" "" … (hide yes))` an, gezielt um den GUI-Render-Fallback auf die Lib-Defaults zu unterdrücken (`generators/schematic_patcher.py::render_symbol_instance`, Kommentar dort).

Auswirkung:
Power-Symbol-Boilerplate erscheint nicht als störender Text auf dem Blatt; „Update Symbols from Library" kann die Felder bei Bedarf neu befüllen.

---

## Textrotation

### BD-301
Objekt:
Rotation der Property-Felder

Status:
Reproduzierbar

Beschreibung:
Alle Property-Felder werden mit Winkel 0 emittiert, unabhängig von der Rotation des Symbols.

KiCad Standard:
Feldwinkel können der Symbolrotation folgen bzw. aus der Lib-Definition stammen.

MCP Verhalten:
`render_symbol_instance` übergibt an jedes `render_property` den Winkel `0` (dritter Positionsparameter), auch wenn die Symbol-Instanz mit `rot != 0` platziert wird (`generators/schematic_patcher.py::render_symbol_instance`/`render_property`).

Auswirkung:
Reference/Value bleiben horizontal lesbar, auch wenn das Symbol gedreht ist; die Feldlage rotiert nicht mit dem Symbol mit.

---

## Verdrahtung

### BD-401
Objekt:
Pin-zu-Pin-Verbindung über `connect_pins` (mode `wire`)

Status:
Reproduzierbar

Beschreibung:
Die Verbindung wird als 1-Ellbogen-Manhattan-Route (erst horizontal, dann vertikal) erzeugt; kollineare Pins ergeben ein einzelnes Segment.

KiCad Standard:
Wires werden manuell gezogen; es gibt keine automatische Manhattan-Erzeugung zwischen zwei Pins durch bloße Auswahl.

MCP Verhalten:
`connect_pins` liest beide Pin-Welt-Koordinaten und emittiert bei `|Δx|<0.001` oder `|Δy|<0.001` ein einzelnes `render_wire`, sonst zwei Segmente mit Knie bei `(p2.x, p1.y)` (horizontal-first) (`tools/sch_patch_tools.py::connect_pins`).

Auswirkung:
Routen sind deterministisch und immer horizontal-first; komplexere Pfade müssen über `add_schematic_wire` von Hand gelegt werden.

---

### BD-402
Objekt:
Wire-Endpunkte (`add_schematic_wire`, `connect_pins`)

Status:
Reproduzierbar

Beschreibung:
Wire-Endpunkte werden standardmäßig auf das 1.27-mm-Raster gesnappt.

KiCad Standard:
Wire-Endpunkte liegen auf dem aktiven GUI-Raster; off-grid ist mit feinerem Raster möglich.

MCP Verhalten:
`add_schematic_wire`/`render_wire` snappen beide Endpunkte via `snap_to_grid`, sofern `snap` (Default True) nicht abgeschaltet wird; bei `snap=False` nur Rundung auf 0.0001 mm (`tools/sch_patch_tools.py::add_schematic_wire`, `generators/schematic_patcher.py::render_wire`). `snap=False` ist für Feinpitch-IC-Pins vorgesehen.

Auswirkung:
Wires landen verlässlich auf Raster-Vertices; ohne `snap=False` würde ein Endpunkt auf einem Off-Grid-Pad weggezogen und das Netz brechen.

---

### BD-403
Objekt:
Junction-Punkte

Status:
Reproduzierbar

Beschreibung:
`connect_pins` fügt nie automatisch Junctions ein.

KiCad Standard:
KiCad setzt beim interaktiven Zeichnen an T-Abzweigungen automatisch Junction-Dots.

MCP Verhalten:
`connect_pins` meldet `junctions_added` stets als 0; eine Pin-zu-Pin-Verbindung erzeugt keinen Junction-Knoten. Tippt ein dritter Draht denselben Punkt an, muss die Junction explizit gesetzt werden (`tools/sch_patch_tools.py::connect_pins`, Docstring + Zählwert).

Auswirkung:
Mehrfach-Abzweigungen können ohne explizite Junction elektrisch unverbunden bleiben.

---

## Netlabels

### BD-501
Objekt:
`add_schematic_label` mit Power-Netznamen als `global`

Status:
Absichtlich

Beschreibung:
Ein als global angefragtes Label, dessen Text ein erkannter Power-Netzname ist, wird abgelehnt.

KiCad Standard:
Ein Global-Label mit Text „GND"/„+3V3" lässt sich problemlos platzieren.

MCP Verhalten:
`add_schematic_label` prüft bei `kind == "global"` via `power_lib_id_for(text)`; ist der Name ein Power-Netz, gibt das Tool `success: False` plus `suggested_lib_id` zurück und verweist auf `add_power_symbols` (`tools/sch_patch_tools.py::add_schematic_label`). Erkannte Netze: GND/GNDA/GNDD/GNDPWR, +3V3, +5V, +12V, -12V, +15V, -15V, VBUS, VCC, VDD, VEE, VSS, +BATT, -BATT, EARTH u. a. (`generators/schematic_patcher.py::_POWER_LIB_IDS`).

Auswirkung:
Power-Rails lassen sich nicht versehentlich als Global-Label verdrahten; der ERC-`power_pin_not_driven`-Vertrag und die einheitliche Glyph-Konvention bleiben gewahrt.

---

### BD-502
Objekt:
Text-Justierung von Labels

Status:
Reproduzierbar

Beschreibung:
Bei leerem `justify` wird die Justierung automatisch aus dem Label-Winkel abgeleitet.

KiCad Standard:
Die Justierung wird beim Setzen des Labels nicht winkelabhängig automatisch gewählt.

MCP Verhalten:
`add_schematic_label` setzt `eff_justify = justify or justify_for_angle(rotation_deg)`; `justify_for_angle` liefert `left` für 0/90 und `right` für 180/270 (`tools/sch_patch_tools.py::add_schematic_label`, `generators/schematic_patcher.py::justify_for_angle`).

Auswirkung:
Labels lesen standardmäßig „auswärts" vom Chip-Körper; eine explizite `justify`-Angabe überschreibt dies.

---

### BD-503
Objekt:
`connect_pins` (mode `label`)

Status:
Reproduzierbar

Beschreibung:
Im Label-Modus werden Winkel, Position, Justierung, Typ und Name des Labels automatisch bestimmt.

KiCad Standard:
Labels werden manuell positioniert, gedreht und benannt.

MCP Verhalten:
Pro Pin berechnet `connect_pins` die Auswärtsrichtung via `pin_outward_angle` und nimmt aus `LABEL_OUTWARD_TABLE` Offset + Winkel; der Stub ist `LABEL_STUB_LEN_MM = 3.81 mm`. Bei BBox-/Label-Kollision wird in 2.54-mm-Schritten (bis zu 12 Iterationen) auswärts geschoben. Das Label wird immer als `global` mit `justify_for_angle(lbl_angle)` emittiert; der Name ist `c["label"]` oder per Default `f"{ref1}_{pin1}"` (`tools/sch_patch_tools.py::connect_pins`, `generators/schematic_patcher.py::pin_outward_angle`/`LABEL_OUTWARD_TABLE`).

Auswirkung:
Sheet-übergreifende Netze entstehen mit konsistenter, vom Chip wegzeigender Label-Geometrie und automatisch vergebenem Netznamen.

---

## Versorgungssymbole

### BD-601
Objekt:
Reference-Feld von Power-Symbolen

Status:
Absichtlich

Beschreibung:
Die `#PWRnnnn`-Reference wird bei Power-Symbolen erzwungen versteckt.

KiCad Standard:
Power-Symbole tragen ihre Reference standardmäßig ebenfalls unsichtbar; die MCP setzt dies jedoch unbedingt selbst.

MCP Verhalten:
`_build_power_symbol_snippet` ruft `render_symbol_instance(..., hide_reference=True)`; das Reference-Property wird mit `(hide yes)` emittiert, das Value-Property (Netzname) bleibt sichtbar (`tools/sch_patch_tools.py::_build_power_symbol_snippet`).

Auswirkung:
Auf dem Blatt erscheint nur der Netz-Glyph mit Netzname, nicht der redundante `#PWR`-Designator.

---

### BD-602
Objekt:
Reference-Vergabe für Power-Symbole

Status:
Reproduzierbar

Beschreibung:
Fehlt eine explizite Reference, wird deterministisch das nächste freie `#PWRnnnn` (vierstellig) vergeben.

KiCad Standard:
Power-Symbol-Referenzen (`#PWRnn`) werden von KiCads Annotation vergeben.

MCP Verhalten:
`add_power_symbols` sammelt belegte `#PWR`-Nummern und vergibt über `_alloc_pwr_ref` die kleinste freie als `f"#PWR{n:04d}"` (`tools/sch_patch_tools.py::add_power_symbols`/`_alloc_pwr_ref`).

Auswirkung:
Referenzen sind kollisionsfrei und deterministisch; das Format ist immer vierstellig (`#PWR0001`).

---

### BD-603
Objekt:
Wert (Netzname) projektsuffigierter Rails

Status:
Absichtlich

Beschreibung:
Suffigierte Rail-Namen werden auf den kanonischen KiCad-Rail-Namen als Value-Feld zurückgeführt.

KiCad Standard:
Ein Power-Symbol trägt exakt den eingegebenen Wert; KiCad verbindet Power-Netze über den angezeigten Value.

MCP Verhalten:
`power_lib_id_for` mappt z. B. `+5V_SYS → (power:+5V, "+5V")`, `+3V3_SYS → (power:+3V3, "+3V3")`, `VBUS_SYS → (power:VBUS, "VBUS")`, `+3.3V → (power:+3V3, "+3V3")` (`generators/schematic_patcher.py::_POWER_LIB_IDS`/`power_lib_id_for`).

Auswirkung:
Alle Verbraucher derselben Rail landen auf einem Netz, statt ein separates `+5V_SYS`-Insel-Netz zu bilden. Der eingegebene Suffix-Name geht im Value verloren.

---

### BD-604
Objekt:
Vorhandene Power-Netz-Global-Labels (`convert_global_labels_to_power`)

Status:
Absichtlich

Beschreibung:
Global-Labels mit Power-Netznamen werden in `power:`-Symbol-Instanzen am selben Anker umgewandelt und die Original-Labels gelöscht.

KiCad Standard:
Ein Global-Label bleibt ein Global-Label, bis es manuell ersetzt wird.

MCP Verhalten:
`convert_global_labels_to_power` ersetzt jedes top-level `(global_label …)` mit erkanntem Power-Netz durch ein Power-Symbol am identischen `(at x y)`, mit frischem `#PWRnnnn` und Rotation aus `default_power_rotation` (=0), und löscht den Original-Block. Lokale/hierarchische Labels und mit anderen Labels kollidierende Anker bleiben unangetastet (`tools/sch_patch_tools.py::convert_global_labels_to_power`).

Auswirkung:
Eine zuvor mit Global-Labels verdrahtete Versorgung wird auf die KiCad-Power-Symbol-Konvention gebracht; bestehende Wires bleiben verbunden, da der Pin am selben Punkt sitzt.

---

## Masse

### BD-701
Objekt:
GND-Symbol-Orientierung

Status:
Beobachtet

Beschreibung:
GND-Symbole werden mit Rotation 0 erzeugt — sowohl über die Patch-Tools/Konvertierung als auch im generierten Block.

KiCad Standard:
`power:GND` wird in Bibliotheksorientierung eingefügt (Balken unter dem Pin, Pin nach oben), also faktisch Rotation 0.

MCP Verhalten:
`default_power_rotation` gibt für GND (wie für alle Familien) 0 zurück; `add_power_symbols` defaultet 0. Der Circuit-Block-Generator wählt explizit `0 if net.upper().startswith("GND") else 180`, d. h. GND ist 0, positive Rails 180 (`generators/schematic_patcher.py::default_power_rotation`, `generators/circuit_block/_block_to_patch.py`). Die GND-Orientierung entspricht damit über alle Pfade dem Standard; der Unterschied betrifft nur positive Rails (siehe BD-103).

Auswirkung:
GND-Glyphen erscheinen einheitlich in Standardlage (Pin oben, Balken unten).

---

## Bauteilanordnung

### BD-801
Objekt:
Mehrere Bauteile eines generierten Circuit-Blocks

Status:
Reproduzierbar

Beschreibung:
Periphere Bauteile werden deterministisch auf konzentrischen Ringen um den zentralen Chip angeordnet.

KiCad Standard:
KiCad ordnet beim Einfügen nicht automatisch an; Bauteile liegen dort, wo platziert.

MCP Verhalten:
Der Chip sitzt am Platzierungs-Ursprung; jedes periphere Bauteil bekommt via `_ring_position(index, cx, cy)` einen Ring-Slot: Startradius `_RING_RADIUS = 12.7 mm`, Ringabstand/Slotabstand `_RING_STEP = 5.08 mm`, 8 feste Richtungen (E, SE, S, SW, W, NW, N, NE), Umbruch alle 8 Slots auf den nächsten Ring (`generators/circuit_block/_block_to_patch.py::_ring_position`). Es ist explizit „minimum viable" — Nachjustierung via `move_schematic_group`/`rotate_schematic_group` vorgesehen.

Auswirkung:
Generierte Blöcke haben ein deterministisches, kollisionsarmes Startlayout; es ist bewusst kein vollwertiger Auto-Placer.

---

## Sonstige

### BD-901
Objekt:
Numerische Koordinaten-Ausgabe aller emittierten S-Expressions

Status:
Absichtlich

Beschreibung:
Alle Koordinaten werden auf 4 Nachkommastellen gerundet und um nachlaufende Nullen bereinigt ausgegeben.

KiCad Standard:
KiCad schreibt Schaltplan-Koordinaten in 100-nm-Auflösung (4 Nachkommastellen mm) und normalisiert beim Speichern.

MCP Verhalten:
`_fmt(v)` formatiert als `f"{v:.4f}".rstrip("0").rstrip(".")` (leer → `"0"`) (`generators/schematic_patcher.py::_fmt`).

Auswirkung:
Die Ausgabe matcht KiCads eigene Normalisierung; ein anschließendes KiCad-Save erzeugt keinen Diff-Rauschen durch zusätzliche Nachkommastellen.

---

### BD-902
Objekt:
Konnektivitäts-Metadaten jeder Symbol-Instanz

Status:
Absichtlich

Beschreibung:
Jede platzierte Symbol-Instanz erhält Per-Pin-UUIDs und einen `(instances …)`-Block.

KiCad Standard:
KiCad erzeugt diese Strukturen selbst beim GUI-Platzieren; ein roher Text-Patch ohne sie würde Pins als unverbunden melden.

MCP Verhalten:
`render_symbol_instance` emittiert für jede Pin-Nummer `(pin "n" (uuid …))` und einen `(instances (project … (path … (reference …) (unit …))))`-Block, sofern `pin_numbers`/`project_name` vorliegen — ohne sie meldet KiCad-10 jeden Pin als `pin_not_connected` (`generators/schematic_patcher.py::render_symbol_instance`).

Auswirkung:
Per-Tool-Patch erzeugte Symbole sind in KiCad-10 sofort korrekt konnektiv; Multi-Unit-Teile erhalten nur die Pins ihrer Unit (`unit`-gefiltert).

---

# Status

Statuswerte

- Beobachtet
- Reproduzierbar
- Absichtlich
- Unbekannt
- Geändert
- Entfernt

---

# Beispiel

## BD-101

Objekt:
Power Symbol

Status:
Beobachtet

Beschreibung:
Nach dem Platzieren wird das Symbol automatisch gedreht.

KiCad Standard:
Symbol wird in Bibliotheksorientierung eingefügt.

MCP Verhalten:
Symbol wird unmittelbar nach dem Platzieren um 180° gedreht.

Auswirkung:
Die Symbolorientierung weicht vom Standard ab und beeinflusst das Erscheinungsbild des Schaltplans.
