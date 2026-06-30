# kicad-mcp — Auftragsliste (zur Freigabe)

**Basis:** `main` @ `d49e3b2` (latest origin/main, inkl. `drc_triage`/`drc_select_group`, `center_item_clearance`, geschärfter Plugin-Prompt) · Stand 2026-06-30.
**Vorgehen:** Du hakst frei (✅ in „Freigabe"), dann implementiere ich die freigegebenen Tasks der Reihe nach, jeweils mit Test + Verifikation. Detailspezifikationen zu den Doctor-Tasks: siehe `MCP_DOCTOR_FINDINGS.md`.

## Übersicht
| ID | Titel | Kategorie | Prio | Aufwand | Freigabe |
|---|---|---|---|---|:--:|
| T1 | Bug 11: `set_footprint_property_visibility` korrumpiert Single-Line-Properties | Korrektheit | 🔴 Hoch | S–M | ☐ |
| T2 | `force`-Flag-Fix (B.Cu-Flip) committen + Test | Korrektheit | 🔴 Hoch | S | ☐ |
| T3 | `ctx` aus allen Tool-Schemas strippen (Schema-Token-Bloat) | Token | 🟠 P0 | S–M | ☐ |
| T4 | `list_schematic_components` entschlacken + Sparmodus | Token | 🟠 P0 | M | ☐ |
| T5 | 4× mehrdeutiger `value`-Parameter beschreiben | Beschreibung | 🟠 P0 | S | ☐ |
| T6 | Filter/`summary_only` für die schweren Read-Tools | Token | 🟡 P1 | M–L | ☐ |
| T7 | ~114 fehlende Parameter-Beschreibungen ergänzen | Beschreibung | 🟡 P1 | M | ☐ |
| T8 | Usage-Kontext für 20 Tools | Beschreibung | 🟡 P1 | M | ☐ |
| T9 | Bug: `audit_power_tree` meldet 0 power_nets am realen Board | Korrektheit | 🟡 P1 | M | ☐ |
| T10 | Bug: `generate_project`-DRC-Gate schwächer als echter ERC | Korrektheit | 🟡 P1 | S–M | ☐ |
| T11 | Repo-Hygiene: untracked Müll + `.gitignore` | Hygiene | ⚪ P2 | S | ☐ |
| — | UNCLEAR_PURPOSE-Rewrites / Jargon-Infos | — | ⚪ skip | — | bewusst nicht |

---

## Details

### T1 — Bug 11: Single-Line-Property-Korruption  🔴
**Problem:** `set_footprint_property_visibility(hide=True)` fügt bei einzeiligen `(property …)`-Blöcken eine alleinstehende `(hide yes)`-Zeile **vor** der Property (als Footprint-Sibling) ein → `.kicad_pcb` parst nicht mehr (GUI/pcbnew/MCP scheitern). Silent file corruption, High.
**Wo:** `kicad_mcp/tools/pcb_patch_tools.py` → `set_footprint_property_visibility_text` (~Z. 2796–2821); ggf. Spiegelung in `pcb_geometry_tools.py`.
**Fix:** `(hide yes)` **immer innerhalb** der Property-Parens vor deren schließendem `)` platzieren; Single-Line (`"\n" not in prop_block`) → inline `" (hide yes)"`, Multi-Line → eingerückte Zeile (bisheriges Verhalten).
**Akzeptanz:** neuer Test in `tests/test_pcb_patch_tools.py` (Single-Line + Multi-Line-Case), pcbnew-Load-Roundtrip besteht; bestehendes Multi-Line-Verhalten unverändert. Vollständige Spec in `Bug.md` Bug 11.

### T2 — `force`-Flag-Fix committen  🔴
**Status:** im Working-Tree fertig, **uncommitted** (`footprint_resync_tools.py` + `footprint_resync_worker.py`, 6× `force`). Löst den B.Cu-Footprint-Flip-Bug (Swaps trotz 1-µm-Drift-Gate erzwingbar; pcbnew-Engine flippt korrekt).
**Auftrag:** Test ergänzen (Swap mit `force=True` auf B.Cu → korrekte Pad-Geometrie, kein Drift bei `force=False`-Recheck), dann committen. **Server-Restart** nötig, damit aktiv.
**Akzeptanz:** Test grün; `replace_footprint_canonical(refs=[…], force=True)` swappt, `force=False`-Recheck zeigt 0 Drift.

### T3 — `ctx` aus Tool-Schemas strippen  🟠 P0
**Problem:** 48× `ctx: Context | None = None` landet samt ~150-Wort-Docstring in den `inputSchema`s → bläht die `tools/list`, die **jeder Agent vorab lädt** (Hebel über alle 173 Tools) + 33 Falsch-„MISSING_DESCRIPTION".
**Fix:** sicherstellen, dass FastMCP `Context`-Parameter strippt (Annotation/Version prüfen). Ziel: kein `ctx` in irgendeiner `inputSchema`.
**Akzeptanz:** `mcp-doctor … --check descriptions` → keine `parameter.ctx.*`-Treffer; `tools/list`-Größe sinkt messbar.

### T4 — `list_schematic_components` entschlacken + Sparmodus  🟠 P0
**Problem (board-unabhängig, ~116 Tok/Bauteil):** `properties{}` dupliziert `reference`/`value`/`footprint`; leere Felder werden gesendet; Pin-Encoding schräg (Nummer in `type`, `name`/`number` leer).
**Wo:** `kicad_mcp/tools/schematic_tools.py` (Tool `:148`, Datensatz-Bau ~`:56–94`).
**Fix:** Property-Duplikate raus, leere Felder weglassen, Pin → `{num,name}`; Parameter `include_pins=False`/`include_properties=False`/`fields=[]` (Default schlank).
**Akzeptanz:** Tok/Bauteil deutlich < 116; Default-Antwort ohne Pins/leere Felder; bestehende Felder bei `include_*=True` unverändert.

### T5 — 4× `value`-Parameter beschreiben  🟠 P0
`Field(description=…)` (Wert WOVON) für `value` in: `insert_footprint`, `bulk_set_property` (`pcb_patch_tools.py`), `update_symbol_property`, `create_library_symbol` (`sch_patch_tools.py`).
**Akzeptanz:** AMBIGUOUS_PARAMS = 0 im Doctor-Lauf.

### T6 — Filter/`summary_only` für schwere Read-Tools  🟡 P1
`compute_pad_world_positions` (refs/net-Filter), `extract_project_netlist`/`extract_schematic_netlist` (summary-Form), `list_pcb_footprints` (`fields=`/leere weg), `list_user_hotkeys` (Summary statt 21k-Volldump oder streichen).

### T7 — ~114 Parameter-Beschreibungen  🟡 P1
`Field(description=)` mit Einheit/Bezug für u.a. `x_mm`,`y_mm`,`center_x_mm`,`center_y_mm`,`dx_mm`,`dy_mm`,`width_mm`,`sch_path`,`group_id`. **Nach T3** (sonst zählt `ctx` mit).

### T8 — Usage-Kontext für 20 Tools  🟡 P1
„Wann nutzen"-Satz für die 20 MISSING_CONTEXT-Tools (Liste in `MCP_DOCTOR_FINDINGS.md` P1.3).

### T9 — Bug `audit_power_tree`  🟡 P1
Meldete `n_power_nets:0, rails:[]` am iFloat-Mainboard mit echten +5V/+3V3/+20V-Schienen → Erkennungslogik prüfen/fixen + Test gegen ein Board mit Power-Netzen.

### T10 — Bug `generate_project`-DRC-Gate  🟡 P1
Gate meldete `success`, eigenständiger `run_erc` fand 2 Errors (`wire_dangling` ×2 + `power_pin_not_driven`). Gate an echten ERC angleichen (gleiche Severity/Aggregation) oder Generator-Output-Artefakte (Draht-Stummel, fehlendes PWR_FLAG bei Connector-gespeisten Rails) beheben.

### T11 — Repo-Hygiene  ⚪ P2
Untracked entfernen/ignorieren: `kommt manchmal.txt`, `examples/test/`, `kicad_mcp/tools/pcb_patch_tools.py.bak_fpflipfix`; ggf. `.gitignore` ergänzen. (`MCP_DOCTOR_FINDINGS.md` + diese Liste committen.)

---

**Bewusst NICHT eingeplant:** die 117 UNCLEAR_PURPOSE (Heuristik-False-Positives, kosmetisch) und 38 TECHNICAL_JARGON-Infos (`json`/`uuid`/`api` im Domänenkontext legitim).

**Freigabe:** Markiere die ☐ → ✅ (oder nenn mir die IDs), dann setze ich genau diese um.
