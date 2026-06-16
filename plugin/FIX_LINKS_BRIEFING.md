# Einweisung: Klickbare Board-Links im Chat-Plugin reparieren (Live-KiCad nötig)

**An:** lokales Claude Code mit echtem Zugriff auf laufendes KiCad 10.0 + kipy 0.7.1 +
ein offenes `.kicad_pcb`.
**Ziel:** klären, warum die klickbaren Links im Chat-Panel beim Nutzer **gar nicht
erscheinen** (ab Plugin v0.2.26) und warum **Bauteil-Pins** (`U1B.33`) **nie**
funktioniert haben — dann gezielt fixen. Das ist ein reines **Live-Problem**: die
Unit-Tests (gegen Mocks) sind grün, der Fehler tritt nur gegen echtes kipy auf.

> Warum nicht headless lösbar: in der CI/Sandbox ist weder `pcbnew` noch `kipy`
> importierbar. Alle kipy-Pfade sind nur gegen **Fakes** getestet — wenn die echte
> kipy-0.7.1-API anders aussieht als die Fakes annehmen, ist genau das die Lücke.

---

## 0. Symptome (Original-Feedback des Nutzers)

- „**0.2.26 — Links wieder zuverlässig stimmt nicht: kein Link mehr in der Anzeige.**"
  → Im Chat erscheint *kein einziger* orange-unterstrichener Link, auch nicht für
  Refs/Netze, die in der Antwort klar vorkommen.
- „**0.2.23 — Bauteil-Pins klickbar: das hat nie funktioniert.**" → `U1B.33` & Co.
- „**Alle klickbaren Dinge bis 0.2.21 haben funktioniert.**" → Refs/Netze/Koordinaten
  liefen mal; irgendwann zwischen 0.2.21 und 0.2.26 brach es.

---

## 1. So funktioniert die Link-Funktion (Architektur)

Zwei Schichten, beide in `plugin/board_links.py`:

### (a) PURE — `tokenize()` (headless, voll getestet)
Zerlegt eine Claude-Antwort in `(chunk, target)`-Segmente. `target` ist `None` (Klartext)
oder einer von: `("ref", "R12")`, `("net", "GND")`, `("layer", "F.Cu")`,
`("pin", (ref, pin))`, `("coord", (x_mm, y_mm))`. Verlinkt **nur Tokens, die wirklich
auf dem Board existieren** (gegen `known_refs/known_nets/known_layers`). Diese Schicht
ist nicht das Problem — sie ist rein und getestet.

### (b) kipy — nur in KiCad lauffähig
- `connect()` → `KiCad(timeout_ms=15000)` + `client.get_board()`; `call()` wiederholt
  „KiCad is busy" mit Backoff (5×, exponentiell ab 0,2 s).
- `board_targets(board)` → `(refs, nets, layers)` vom **lebenden** Board. **Das ist die
  Quelle für `known_refs/...`.** Liefert sie leer, kann `tokenize` nichts verlinken →
  „kein Link in der Anzeige". **Primärer Verdächtiger.**
- `select()/select_pin()/select_coord()/set_active_layer()` → markieren + zoomen im
  Editor (Klick-Handler).

### Anzeige/Flow — `plugin/chat_dialog.py`
- `_worker()` (nach jedem Turn): `connect()` → `board_targets()` → schreibt
  `result["_refs"/"_nets"/"_layers"]` **und** `result["_link_counts"]`; ein Fehler landet
  in `result["_link_error"]` (wird **nicht** verschluckt).
- `_on_reply()`: speichert die Mengen in `self._refs/...` und zeigt seit **v0.2.27** eine
  sichtbare Diagnosezeile:
  - `ⓘ Links aus: <Fehler>` (connect/board_targets warf), oder
  - `ⓘ Links: 0 Refs/Netze/Layer vom Board gelesen` (Mengen leer).
- `_append_claude()`: tokenisiert die Antwort mit `self._refs/...`, schreibt Link-Spans
  orange + unterstrichen (`_write(..., underline=True)`) und merkt sich Char-Range +
  Target in `self._links`.
- `_on_output_click()` → `HitTestPos` → `_select_worker()` ruft die passende
  `board_links.select*`-Funktion.

---

## 2. Konkrete API-Annahmen, die gegen echtes kipy 0.7.1 zu PRÜFEN sind

Das ist der Kern von Weg B. Jede Zeile unten ist eine **Annahme im Code**; verifiziere
sie gegen das laufende KiCad und notiere Abweichungen.

| Stelle in `board_links.py` | Angenommene kipy-API | Prüfen |
|---|---|---|
| `_ref_of()` | `fp.reference_field.text.value` (str) | Gibt das echte Footprint-Objekt das so her? |
| `board_targets` refs | `board.get_footprints()` iterierbar | Methodenname korrekt? |
| `board_targets` nets | `board.get_nets()`, `n.name` | Heißt das Attribut wirklich `name`? |
| `board_targets` layers | `board.get_enabled_layers()` → **ints** | Gibt es die Methode? Liefert sie ints oder Enum-Objekte? |
| `_enum_to_canonical` | `BoardLayer.Name(int)` → `"BL_F_Cu"` | Enum-Pfad `kipy.proto.board.board_types_pb2.BoardLayer` korrekt? |
| `select` ref | `board.add_to_selection([fp])` | Nimmt es Footprint-Objekte? |
| `select` net | `board.get_items_by_net(net)` | Existiert die Methode? Signatur (Net-Objekt vs Name/Code)? |
| **`select_pin`** | `fp.definition.pads`, `pad.number`, `board.add_to_selection([pad])` | **Hauptverdacht „Pins nie".** Heißt der Pad-Zugriff wirklich `definition.pads`? Hat das Pad eine Board-ID, sodass `add_to_selection` es markiert? Ist `.number` ein str? |
| `select_coord` | `board.get_footprints/get_vias/get_pads`, `item.position.x/.y` (nm) | Gibt es `get_vias`/`get_pads`? `.position` in nm? |
| `set_active_layer` | `board.set_active_layer(int)`, `board.get_layer_name(int)` | Existieren beide? |
| zoom | `client.run_action("common.Control.zoomFitSelection")` dann `"pcbnew.Control.zoomFitObjects"` | Heißen die Actions in 10.0 so? |

> Maßgeblich ist die **installierte kipy-0.7.1-Quelle** (nicht das Gedächtnis). Finde sie
> z. B. so und lies die echten Klassen/Methoden:
> ```bash
> "<KiCad>/bin/python.exe" -c "import kipy, os; print(os.path.dirname(kipy.__file__))"
> ```
> Dann in `board/`, `proto/board/board_types_pb2.py` etc. die echten Namen abgleichen.

---

## 3. Live-Diagnose-Snippet (zuerst ausführen)

Unter **KiCads gebündeltem Python**, mit dem Board **offen** in KiCad. Das deckt in einem
Rutsch auf, welche Annahme bricht. Pfad anpassen.

```python
# probe_links.py — unter "<KiCad>/bin/python.exe" mit offenem Board ausführen
from kipy import KiCad
k = KiCad(timeout_ms=15000)
b = k.get_board()

fps = list(b.get_footprints())
print("Footprints:", len(fps))
fp = fps[0]
print("  reference_field:", repr(getattr(getattr(getattr(fp, "reference_field", None),
      "text", None), "value", "??")))
# Pad-Zugriff (Hauptverdacht "Pins nie funktioniert"):
try:
    pads = list(fp.definition.pads)
    print("  pads via definition.pads:", len(pads), "| number0:",
          repr(getattr(pads[0], "number", "??")))
except Exception as e:
    print("  definition.pads FEHLT:", e)

nets = list(b.get_nets())
print("Nets:", len(nets), "| name0:", repr(getattr(nets[0], "name", "??")))
try:
    items = list(b.get_items_by_net(nets[0]))
    print("  get_items_by_net OK:", len(items))
except Exception as e:
    print("  get_items_by_net BRICHT:", e)

try:
    layers = list(b.get_enabled_layers())
    print("Layers:", layers[:5], "| typ:", type(layers[0]).__name__)
except Exception as e:
    print("get_enabled_layers BRICHT:", e)

for meth in ("get_vias", "get_pads", "set_active_layer", "get_layer_name",
             "add_to_selection", "clear_selection"):
    print("hat", meth, ":", hasattr(b, meth))
```

Parallel: im Plugin (v0.2.27+) eine Frage stellen, die Refs/Netze nennt, und die graue
`ⓘ Links …`-Zeile unter der Antwort lesen — sie sagt direkt, ob `connect()`/`board_targets`
wirft („Links aus: …") oder 0 Elemente liefert.

---

## 4. Erwartete Fixpfade (je nach Befund)

- **`board_targets` liefert leer, aber kein Fehler** → eine der drei Getter-Methoden
  (`get_footprints/get_nets/get_enabled_layers`) heißt anders oder das Attribut
  (`name`/`reference_field`) stimmt nicht → in `board_links.py` an die echte API angleichen.
- **`connect()`/`get_board()` wirft „busy"/Timeout** → MCP-Server hält die Single-Thread-API
  belegt, während das Panel pollt. Optionen: `board_targets` direkt im **MCP-Turn** mitliefern
  (statt zweiter kipy-Verbindung), oder die Refs/Netze über ein **MCP-Tool** holen statt über
  eine zweite kipy-Session. (Architektur-Entscheidung — vorher mit dem Nutzer abklären.)
- **Pins:** wenn `fp.definition.pads`/`pad.number`/Pad-Board-ID nicht stimmen, `select_pin`
  + `_pads_of` an die echte Pad-Repräsentation anpassen (kipy 0.7.1 hat ggf.
  `fp.pads`/`pad.number()` o. Ä.). Akzeptanz: `U1B.33` markiert genau dieses Pad + zoomt.

---

## 5. Pflicht: Mocks an die Realität nachziehen + Tests

Sobald die echte API feststeht, **die Fakes in `tests/test_plugin_board_links.py` an die
reale Form angleichen** (sonst „grün, aber live kaputt" — genau der jetzige Zustand).
Danach:

```bash
pytest tests/test_plugin_board_links.py -q
```

Akzeptanzkriterien (live, manuell im offenen Board):
1. Antwort, die `R…`/`GND`/`F.Cu`/`U1B.33`/`(x, y)` nennt → alle erscheinen orange +
   unterstrichen.
2. Klick auf Ref/Netz → markiert + zoomt im Editor.
3. Klick auf `U1B.33` → genau dieses Pad markiert.
4. Klick auf Layer → aktiver Layer wechselt.
5. Klick auf Koordinate → nächstes Element markiert.
6. Die `ⓘ Links …`-Diagnosezeile zeigt sinnvolle Zahlen (>0) statt eines Fehlers.

---

## 6. Projekt-Konventionen (aus `CLAUDE.md`, beim Arbeiten am MCP-/Plugin-Code beachten)

- Neue/angepasste Tools: Pfad-Normalisierung `to_local_path(path)` als erste Body-Zeile;
  Return-Dict mit `{success: bool}`; Registrierung zentral.
- Tests: Happy-Path + Edge + Error-Path; pylint im CI-Scope (`--disable=C,R` auf
  `kicad_mcp tests`) sauber; CHANGELOG-Eintrag; `plugin/version.py` bumpen.
- **Offenes Board nur über IPC mutieren**, nie Text-Patch (Board-offen-Guard).
- kipy: nm-int; lesen mit `pos.x / 1_000_000`; B.Cu spiegelt X. Hier nur **lesen +
  selektieren**, keine Geometrie schreiben — also unkritisch, aber Einheiten beachten.

---

## 7. Branch & Abschluss

```bash
git checkout -b fix/board-links-live
# board_links.py an echte kipy-API angleichen; Mocks + Tests nachziehen;
# version.py bumpen; CHANGELOG ergänzen
pytest tests/test_plugin_board_links.py -q
git add -A && git commit
```

**Nicht raten — verifizieren.** Erst das Probe-Snippet (Abschnitt 3) bzw. die kipy-Quelle
(Abschnitt 2) lesen, dann ändern. Der ganze Bug existiert, weil bisher gegen Annahmen statt
gegen die echte kipy-API getestet wurde.
