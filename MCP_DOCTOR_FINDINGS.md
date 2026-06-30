# kicad-mcp — MCP-Doctor-Audit: Befunde & Handlungsanweisung

**Stand:** 2026-06-30 · Werkzeug: [destilabs/mcp-doctor](https://github.com/destilabs/mcp-doctor) (`analyze`, descriptions) + manuelle, **board-unabhängige** Token-Messung.
**Server:** 173 Tools, headless über Console-Script `kicad-mcp` (= `kicad_mcp.server:main`) auditierbar.

## Methodik (wichtig)
Absolute Token-Zahlen einer Tool-Antwort **skalieren mit der Platinengröße** und sind daher **keine** MCP-Bewertung. Maßgeblich ist die **größenunabhängige** Effizienz: **Tokens pro Element**, **Fix-Overhead** und **Redundanz pro Datensatz**. Gemessen wurde dieselbe Tool-Menge auf einer winzigen Platine (`mcp_probe`, 7 Symbole) und dem realen iFloat-Mainboard (245 Symbol-Instanzen / 168 Footprints); die Pro-Element-Kosten ergeben sich aus dem 2-Punkt-Fit `tokens ≈ overhead + perItem·N`.

## Gesamturteil
- **descriptions:** 🟢 *Good foundation* — **0 Errors**, 23 Tools sauber. Kein Tool ohne eigene Beschreibung.
- **token_efficiency:** 🟡 Tools sind roh-vollständig **ohne Sparmodus**; Pro-Element-Redundanz + fehlende Filter/Paginierung. Schema-Bloat durch `ctx`.

---

# P0 — Hoher Hebel, kleiner Aufwand

## P0.1 — `ctx`-Parameter aus allen Tool-Schemas entfernen
**Problem:** 48× `ctx: Context | None = None`. Der Context-Parameter (rein injiziertes Framework-Plumbing) erscheint in der `inputSchema` **inklusive seiner ~150-Wort-Docstring** und wird so für jedes betroffene Tool mitübertragen. Das (a) erzeugt 33 falsche „MISSING_DESCRIPTION"-Treffer und (b) bläht die `tools/list`, die **jeder Agent vorab lädt** — board-unabhängiger Dauer-Overhead über die gesamte Tool-Menge.
**Wo:** alle `kicad_mcp/tools/*.py` (Signaturen `ctx: Context | None = None`).
**Wie:** sicherstellen, dass FastMCP `Context`-Parameter aus dem öffentlichen Schema strippt (FastMCP-Version/Annotation prüfen — z. B. exakt `ctx: Context` ohne Union, oder FastMCP-Update). Ziel: kein `ctx` in irgendeiner `inputSchema`.
**Nutzen:** −33 Doctor-Issues + spürbar kleinere `tools/list` für **alle** Clients.
**Verify:** `mcp-doctor analyze --target … --check descriptions` → keine `parameter.ctx.description`-Treffer mehr.

## P0.2 — `list_schematic_components`: Pro-Datensatz entschlacken + Sparmodus
**Problem (board-unabhängig, ~116 Tok/Bauteil):** jeder Datensatz enthält
1. **`properties{}` dupliziert** die Top-Level-Felder `reference`/`value`/`footprint`,
2. **leere Felder** werden mitgesendet (`footprint:""`, `Datasheet:""`, `Description:""`, Pin `name:""`,`number:""`),
3. **schräge Pin-Kodierung**: die Pin-Nummer steht in `type`, `name`+`number` sind leer.
```json
{"reference":"#PWR0006","value":"PWR_FLAG","footprint":"","properties":{"Reference":"#PWR0006","Value":"PWR_FLAG","Footprint":"","Datasheet":"","Description":""},"pins":[{"type":"1","name":"","number":""}]}
```
**Wo:** `kicad_mcp/tools/schematic_tools.py` — Tool ab `:148`; Datensatz-Bau ~`:56–94` (`"properties": props`, `"pins": pins`).
**Wie:**
- Aus `properties` die bereits oben vorhandenen Schlüssel (`Reference`/`Value`/`Footprint`) entfernen; nur Zusatz-Properties belassen.
- Leere String-Werte weglassen (`v or omit`).
- Pin-Encoding korrigieren: `{"num": "...", "name": "..."}` statt Nummer in `type` + zwei Leerfelder.
- **Sparmodus-Parameter** ergänzen: `fields: list[str] = []` (Whitelist) und/oder `include_pins: bool = False`, `include_properties: bool = False` (Default schlank).
**Nutzen:** ~116 → ~70 Tok/Bauteil auf **jeder** Boardgröße; mit `include_pins=False`-Default nochmals deutlich weniger.

## P0.3 — 4 mehrdeutige `value`-Parameter klären (AMBIGUOUS_PARAMS)
**Problem:** Parameter `value` ist ohne Beschreibung mehrdeutig in:
- `insert_footprint` — `kicad_mcp/tools/pcb_patch_tools.py`
- `bulk_set_property` — `kicad_mcp/tools/pcb_patch_tools.py`
- `update_symbol_property` — `kicad_mcp/tools/sch_patch_tools.py`
- `create_library_symbol` — `kicad_mcp/tools/sch_patch_tools.py`
**Wie:** je `Field(description="…")` ergänzen, der sagt **Wert WOVON** (z. B. „neuer Wert der zu setzenden Property", „Value-Feld des Bauteils"). Umbenennen wäre API-breaking → Beschreibung bevorzugen.

---

# P1 — Substanzieller Nutzen, mehr Fleißarbeit

## P1.1 — Filter/`summary_only`/Paginierung für die schweren Read-Tools
Gleiches Muster wie P0.2 auf die übrigen roh-vollständigen Tools:
| Tool | Kosten | Maßnahme |
|---|--:|---|
| `compute_pad_world_positions` | ~23k bei 168 FP | optionaler `refs=`/`net=`-Filter statt aller Pads |
| `extract_project_netlist` / `extract_schematic_netlist` | ~16k | kompakte `summary_only`-Form (Netz-Namen + Pin-Zahl) |
| `list_pcb_footprints` | ~41 Tok/FP | `fields=` + leere Felder weglassen |
| `list_user_hotkeys` | ~21k | **hohe Last, geringer Agenten-Nutzen** → Summary statt Volldump, oder Tool streichen |

## P1.2 — Fehlende Parameter-Beschreibungen ergänzen (~114, ohne `ctx`)
Häufigste: `x_mm`, `y_mm`, `center_x_mm`, `center_y_mm`, `dx_mm`, `dy_mm`, `width_mm`, `sch_path`, `group_id`. Je `Field(description=…)` mit Einheit/Bezug. (Erst nach P0.1, damit `ctx` nicht mitzählt.)

## P1.3 — Usage-Kontext für 20 Tools (MISSING_CONTEXT)
„*Wann nutzen*"-Satz ergänzen bei: `add_schematic_symbols`, `add_zone_pour_to_pcb`, `bulk_set_property`, `compute_pad_world_positions`, `convert_ltspice_to_kicad`, `esphome_to_kicad`, `extract_circuit_from_pdf`, `find_footprint_by_specs`, `generate_from_netlist`, `generate_project`, `ipc_add_zone_pour`, `ipc_export_schematic`, `ipc_route_power_ring`, `ipc_set_footprint_pose`, `patch_pcb_nets_from_netlist`, `rotate_pcb`, `rotate_schematic_group`, `search_footprints`, `benchmark_loop`, `benchmark_schematic`.

---

# P2 — Optional / niedrige Priorität

- **117× UNCLEAR_PURPOSE** = Heuristik, **false-positive-anfällig** (flaggt auch klare „Insert a standalone via…"-Texte). Nur kosmetisch (verb-first-Rewrites); **kein** inhaltlicher Mehrwert → niedrig priorisieren.
- **38× TECHNICAL_JARGON** (Info): flaggt `json`/`uuid`/`api` — im KiCad/MCP-Kontext legitim → **ignorieren**.

# Separate Bugs (kein Token-Thema, beim Audit aufgefallen)
- **`audit_power_tree`** meldete `n_power_nets:0, rails:[]` am Mainboard mit echten +5V/+3V3/+20V-Schienen → **Erkennungslücke** prüfen.
- **`generate_project`**-DRC-Gate ist **schwächer als ein echter ERC**: meldete `success`, ein eigenständiger `run_erc` fand 2 Errors (`wire_dangling` ×2 + `power_pin_not_driven`, fehlendes PWR_FLAG). Gate verschärfen oder ERC-Severity angleichen.

---

# Gut so (als Referenz/Vorbild)
Board-**unabhängige** Summary-Tools machen es richtig — kompakt, skalieren kaum:
`get_project_structure` (135→136 Tok, flach), `get_schematic_info` (53→316), `get_board_stats` (331→958). Diese Form ist das Ziel für die Sparmodi oben.

---

# Re-Verifikation
```bash
# descriptions (statisch, headless):
mcp-doctor analyze --target "$HOME/.local/bin/kicad-mcp" --check descriptions --output-format json > desc.json
# board-unabhängige Token-Messung (klein vs. groß):
python3 scratchpad/tokscale.py     # tok/Element + Fix-Overhead je Tool
```
**Erfolgskriterien:** keine `parameter.ctx.*`-Treffer · AMBIGUOUS_PARAMS = 0 · `list_schematic_components` Tok/Bauteil deutlich < 116 · Default-Antworten ohne Pins/leere Felder.
