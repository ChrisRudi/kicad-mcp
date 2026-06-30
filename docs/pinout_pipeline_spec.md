<!-- Design-Spezifikation. STATUS: IMPLEMENTIERT in v0.4.0 (Commit edba3e1, 2026-06-18). Historisches Designdokument. -->
<!-- Erstellt 2026-06-18 in der Analyse-Phase (TASK_pinout_validator.md). -->

# Pinout-Pipeline: Symbol-Suche → Datenblatt-Validator (Spezifikation)

**Status:** freigegeben, **IMPLEMENTIERT** in v0.4.0 (Commit `edba3e1`,
2026-06-18; verifiziert 2026-06-30). Die Module (`generators/pinout/`:
`symbol_pins.py`, `datasheet_pins.py`, `type_map.py`, `diff.py`, `search.py`)
und die Tools `search_symbol` / `validate_pinout` / `match_symbol_to_datasheet`
(`tools/pinout_tools.py`, registriert in `tool_registry.py`) existieren, samt
Tests `tests/test_pinout_*.py`. Dokument bleibt als historische Design-Vorlage
erhalten. Self-contained — keine Chat-Historie nötig.

## 0. Ziel

Ein Validator, der das Pinout eines KiCad-Symbols **unabhängig gegen das
Datenblatt** prüft (Pin-Nummer, Pin-Name, electrical_type) — den teuren
Fehlerklassen „Pin vertauscht / falsche Package-Variante / EP-Nummer falsch".
KiCad selbst kann das nicht (es vertraut der Eingabe blind). Kombiniert mit
einer aufgebohrten lokalen Symbol-Suche zu **einer Pipeline**: Suche liefert
rangierte Kandidaten, der Datenblatt-Diff verifiziert/disambiguiert sie.

Festgelegtes Design: Vergleichsseite `.kicad_sym`; Scope Standard-ICs mit
Text-Pinout-Tabellen; Extraktion hybrid (pdfplumber zuerst, LLM-Fallback nur
bei Versagen); Diff strikt auf allen drei Feldern; LLM-Fallback als
austauschbarer Hook `extract(pdf_path, pages) -> dict`, Default `None`.

## 1. Ist-Stand im Repo (Analyse-Befunde, mit Belegen)

Kanonischer Baum `kicad_mcp/` (Kopie unter `plugin/mcp/kicad_mcp/` ignorieren).

| Baustein | Status | Beleg |
|---|---|---|
| `review_ic_against_datasheet` | implementiert, **kein Diff** — Pins aus `.kicad_sch`, PDF nur zu PNG gerastert, Urteil per LLM auf Bildern | `tools/review_tools.py:380`, `:446`, `:537`, `:583` |
| Symbol↔Footprint-Check (Nummern-Set) | vorhanden (Referenz fürs Diff-Muster) | `generators/review/_pin_check.py:49,102` |
| Erzeuger-Gegenstück | `apply_circuit_block` (nicht `sch_apply_block_from_json`); JSON-Schema, kein Pydantic | `tools/circuit_block_tools.py:196`; `generators/circuit_block/schema_v1_1.json:45` |
| S-Expr-Reader (stdlib) | vorhanden | `utils/sexpr_parser.py:11/84/102` |
| Pin-Lesen inkl. electrical_type | vorhanden, aber aus `.kicad_sch` | `tools/pin_tools.py:80-88` (Typ = `pin[1]`) |
| Standalone `.kicad_sym`-Auflösung + **extends-Inlining** + sym-lib-table | vorhanden (gibt Symbol-Text zurück) | `generators/symbol_cache.py:213/265/330/353` |
| Deterministische PDF-Extraktion (pdfplumber) | vorhanden, passende Signatur | `generators/circuit_block/_pdf_extract.py:36/87` |
| KiCad-electrical_type Zielvokabular | vorhanden | `generators/symbol_author.py:18`; `generators/validator.py:37-39` |
| Lokaler Symbol-Index `name→lib_id` | vorhanden, **nur Einzeltreffer**, Install-Dir-only, hardcodiertes Suffix-Strippen, kein Score, kein MCP-Tool | `generators/kicad_library_index.py:141`, genutzt nur intern `generators/symbol_lib.py:172` |
| Footprint-Suche mit Ranking/Confidence | vorhanden (Muster zum Portieren) | `tools/footprint_search_tools.py:299/335/492` |
| LLM-Hook / API-Key-Infra | **fehlt komplett** (nur Docstring-Erwähnungen) | — |
| Typ-Mapping Datenblatt→KiCad | **fehlt** (nur Zielset existiert) | — |

**Designentscheidung:** Option A — neues, eigenständiges deterministisches
Modul, **nicht** an `review_ic_against_datasheet` andocken (Mechanik disjunkt:
review = `.kicad_sch`+Bild+LLM; hier = `.kicad_sym`+Tabelle+Python-Diff).
Internet-Symbol-Suche **verworfen** als primär (ungeprüfte Fremd-Symbole =
negativer Vertrauensgewinn, Secrets/Netz/ToS, bricht Offline-Determinismus);
falls je online, dann Datenblatt-Fetch, nicht Symbol-Fetch — und nur optional,
gated, default aus, hinter derselben Kandidaten-Schnittstelle.

## 2. Modul-Layout

Reine Logik (keine I/O, headless unit-testbar) in `kicad_mcp/generators/pinout/`:

| Datei | Inhalt | Reuse |
|---|---|---|
| `symbol_pins.py` | `.kicad_sym` + Symbolname → Pin-Liste | `symbol_cache`, `sexpr_parser` |
| `datasheet_pins.py` | PDF-Tabellen → Pinout + Normalisierung | `circuit_block/_pdf_extract` |
| `type_map.py` | Datenblatt-Typbegriff → KiCad-Typ | `symbol_author.VALID_PIN_TYPES` |
| `diff.py` | strikter 3-Felder-Diff | `_pin_check` (Muster) |
| `search.py` | rangierte Kandidatensuche (alle lokalen Libs) | `kicad_library_index`, `footprint_search_tools._score_name_match` |
| `__main__.py` | CLI | alle obigen |

MCP-Wrapper `kicad_mcp/tools/pinout_tools.py` mit **3 Tools**
(`search_symbol`, `validate_pinout`, `match_symbol_to_datasheet`), registriert
in `tool_registry.py::TOOL_REGISTRARS`; Tool-Count in `test_tool_audit.py` +3.

## 3. Schnittstellen (reine Kernfunktionen)

```
# symbol_pins.py
extract_symbol_pins(sym_path, symbol_name) ->
    {success, symbol, pins:[{num:str, name:str, type:str}], pin_count, extends?:str, error?}

# datasheet_pins.py
extract_datasheet_pins(pdf_path, pages:list[int]|None=None,
                       llm_extract:Callable[[str,list[int]],dict]|None=None) ->
    {success, source:"pdfplumber"|"llm", pins:[{num,name,type,type_raw}],
     unclassifiable:[{num,name,type_raw}], fallback_used:bool, error?}

# type_map.py
map_datasheet_type(raw:str) -> str|None        # None = nicht klassifizierbar
normalize_pin_name(raw:str) -> str             # Aktiv-Low/Overbar/Separatoren vereinheitlicht

# diff.py
diff_pinout(symbol_pins, datasheet_pins, strict=True) ->
    {match:bool, rows:[{num, status, sym:{name,type}, ds:{name,type}}],
     summary:{matched, name_mismatch, type_mismatch, missing_in_symbol,
              missing_in_datasheet, unclassifiable}}

# search.py
search_symbol_candidates(query, expected_pin_count=0, limit=10) ->
    [{lib_id, score, pin_count, source, footprint_hint?}]
```

## 4. Typ-Mapping-Tabelle (zentrales neues Artefakt)

`map_datasheet_type` normalisiert `raw` (uppercase, Satzzeichen strippen), Lookup:

| KiCad-Typ | Datenblatt-Begriffe |
|---|---|
| `input` | I, IN, INPUT, DI |
| `output` | O, OUT, OUTPUT, DO |
| `bidirectional` | I/O, IO, B, BIDIR, DIO |
| `power_in` | P, PWR, POWER, SUPPLY, VCC, VDD, VS, VM, VIN; **GND-Klasse:** G, GND, GROUND, VSS, RTN, EP, PAD, POWERPAD |
| `power_out` | PO, VREF_OUT, LDO_OUT |
| `open_collector` | OC, OD, OPEN-DRAIN, OPEN-COLLECTOR |
| `passive` | PAS, PASSIVE |
| `no_connect` | NC, N/C, DNC |
| `tri_state` / `open_emitter` / `free` / `unspecified` | seltene Direktbegriffe |

Unbekannter Begriff → `None` → Diff-Status `unclassifiable` → bei `strict`
Fehler (kein stiller Pass). EP/PowerPAD → `power_in` **und** gesonderter
EP-Pin-Nummer-Abgleich (oft „29"/„EP").

## 5. Normalisierungsregeln

- **Pin-Name** (`normalize_pin_name`): uppercase; Whitespace weg; Aktiv-Low
  vereinheitlichen — KiCad `~{X}`/Overbar, Datenblatt `nX`, `/X`, `X#`, `X_N`,
  `X̄` → kanonischer Token `~X`; Separatoren `-`/`_`/`.` einheitlich;
  Funktions-Suffixe nicht strippen (Name-Treue).
- **Pin-Nummer**: als String vergleichen (BGA „A1", EP „EP"/„29"); keine
  Int-Coercion.

## 6. Fallback-Trigger (deterministisch → LLM)

In `extract_datasheet_pins`: LLM nur wenn (a) Pin-Count Tabelle ≠
`expected`/Symbol-Count, **oder** (b) Pin-Nummern nicht lückenlos `1..N` bzw.
dupliziert. Dann `llm_extract(pdf_path, [betroffene_seiten])`. Ist
`llm_extract is None` → `fallback_used=False`, best-effort + Flag (kein harter
Abbruch).

## 7. Diff-Semantik (strict)

Join über Pin-Nummer; je Pin `(name_norm, type_mapped)`. Status:
`match | name_mismatch | type_mismatch | unclassifiable | missing_in_symbol |
missing_in_datasheet`. `match=True` nur wenn **alle** Pins `match`. Output =
strukturierte Zeilenliste mit beiden Seiten.

## 8. Pipeline-Tool `match_symbol_to_datasheet`

`search_symbol_candidates(query)` → je Kandidat `extract_symbol_pins` +
`diff_pinout` gegen `extract_datasheet_pins(pdf)` → Kandidaten **nach
Diff-Treffer** rangieren (0 Abweichungen = richtige Variante). Löst
Variante/EP, das reine Namenssuche nicht kann.

## 9. Such-Erweiterung (Delta zu `kicad_library_index.py`)

1. **Kandidatenliste statt Einzeltreffer** — kein Suffix-Kollabieren (`:89-93`),
   alle Treffer behalten.
2. **Pin-Anreicherung** je Kandidat via `symbol_cache` (extends-inlined) +
   `sexpr_parser` → `pin_count` + Pin-Liste.
3. **Quelle erweitern** — zusätzlich sym-lib-table (`symbol_cache._load_user_sym_libs`, `:213`).
4. **Ranking** — `footprint_search_tools._score_name_match` (`:299`) portieren +
   Pin-Count-Faktor → Score `[0,1]`.
5. **MCP-Tool** `search_symbol` exponieren (read-only).

Harte Grenze: Suche rankt nach Name + Pinzahl, **verifiziert nichts** —
Variante/EP entscheidet erst der Datenblatt-Diff.

## 10. MCP-Konventionen (Pflicht je Tool, siehe CLAUDE.md)

`to_local_path()` + Existenz-Check als erste Body-Zeilen (#1); Args primitiv/
JSON, Return `{success,…}` / Fehler `{success:False,error}` (#2); LLM-tauglicher
Docstring mit „Use this when …" + Abgrenzung zu `review_ic_against_datasheet`
(#5). `search_symbol` read-only; `validate_pinout`/`match` rendern nichts.

## 11. CLI

```
python -m kicad_mcp.generators.pinout validate \
    --sym X.kicad_sym --symbol DRV8313 --pdf d.pdf [--pages 5,6] [--json]
python -m kicad_mcp.generators.pinout search DRV8313 [--pins 28] [--json]
```
Ruft dieselben reinen Funktionen.

## 12. Tests (`tests/test_pinout_*.py`)

- `symbol_pins`: Happy (Nummer/Name/Typ), **extends**-Symbol, Multi-Unit,
  fehlende Datei (Error-Path).
- `type_map`: jede Mapping-Zeile + unbekannt → `None`; Name-Normalisierung
  (alle Aktiv-Low-Formen → ein Token).
- `datasheet_pins`: pdfplumber-Tabelle (Fixture-PDF oder gemockte Rows),
  Fallback-Trigger feuert/feuert-nicht, `llm_extract`-Stub.
- `diff`: match, je ein name/type/unclassifiable/missing-Fall; `strict` an/aus.
- `search`: Kandidaten-Ranking, Pin-Count-Filter, leerer Index → `[]`.
- `match_symbol_to_datasheet`: Variante-Disambiguierung (zwei Kandidaten gleicher
  Pinzahl, einer matcht).
- `test_tool_audit`: Count +3, Path-Audit, Docstring-Floor.

## 13. Offene Punkte / Defaults für die Implementierung

- **Fehlende `type`-Spalte im Datenblatt:** Default **strikt** — Typ als
  `unclassifiable` führen (kein stiller Pass). Name-basierte Power-Heuristik
  nur als optionaler, markierter Sekundärpfad (nicht im ersten Wurf).
- **Tabellen-Spaltenerkennung** (PIN/NO./NAME/TYPE-Header) ist die fragilste
  Stelle; pdfplumber liefert manche Pin-Tabellen als Fließtext → LLM-Fallback
  federt ab, deterministische Trefferquote ist datenblattabhängig.
- **20-kB-Limit** (externer Standard `github.com/ChrisRudi/tools`, hier nicht
  verifizierbar): Aufteilung in die 6 Dateien hält jede klein.
- **Symbol-Eingang:** Phase 1 nur direkter `.kicad_sym`-Pfad + Symbolname
  (extends via `symbol_cache` intern). lib_id/Projekt-Auflösung später optional.
