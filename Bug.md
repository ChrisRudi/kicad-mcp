# kicad-mcp — Known Bugs

Discovered 2026-04-26/27 during V8.2.0-Schematic-Tandem-Workflow. To be addressed in future maintenance.

---

## ✅ Bug 1 — `mcp__kicad__run_erc` parses wrong JSON path [RESOLVED 2026-04-27]

> Status-Marker am Top synchron mit dem Resolution-Eintrag weiter unten in
> dieser Datei. Details siehe ✅-Block 2026-04-27.

**Severity:** High (silent data loss — reports "0 errors" when there are real errors)

**File:** `kicad_mcp/tools/erc_tools.py`

**Symptom:** Tool returns `total_violations: 0, errors: 0, warnings: 0, violations: []` even when `kicad-cli sch erc` produces a JSON report containing many violations.

**Root cause:** The parser reads `report.get("violations", [])` at the top level. But KiCad-10 ERC JSON nests violations under `sheets[N].violations` (one per sheet). Top-level `violations` doesn't exist → always empty.

**Reproduction:**
```bash
kicad-cli sch erc --format json --output report.json some.kicad_sch
# report.json has structure:
#   { "sheets": [ { "path": "/", "violations": [...32 entries...] } ] }
# but mcp__kicad__run_erc reports 0/0
```

**Suggested fix** in `_run_erc_cli`:
```python
all_violations = []
for sheet in report.get("sheets", []):
    all_violations.extend(sheet.get("violations", []))
# also keep root-level "violations" for legacy schematics
all_violations.extend(report.get("violations", []))
report["violations"] = all_violations  # backwards compat
```

---

## ✅ Bug 2 — `mcp__kicad__extract_schematic_netlist` is label-only [RESOLVED 2026-04-29]

**Severity:** Medium (documented in tool response as `partial: true`, but easy to miss)

**File:** `kicad_mcp/utils/netlist_parser.py`

**Symptom:** Tool returns nets with empty pin-lists (`[]`) and `total_pin_connections: 0`. The `partial_reason` field says: *"Wire-based connectivity tracing is not yet implemented. Nets are derived from labels and power symbols only; pin-level connections are incomplete."*

**Root cause:** No S-expression wire-tracing — only label-based net assignment.

**Workaround:** Use `kicad-cli sch export netlist --format kicadsexpr` directly (correctly handles wire tracing).

**Suggested fix:** either implement proper wire-tracing, or replace the tool with a thin wrapper around `kicad-cli sch export netlist` (parse the kicadsexpr output for nets+pins).

**Important:** the netlist file's `(node ...)` entries can have extra fields beyond `(ref "X")(pin "N")` — they include `(pinfunction "...")` and `(pintype "...")`. A naïve regex that requires `(node (ref X) (pin N))` followed by `)` will MISS most pins. Use balanced-paren extraction or include optional trailing fields.

**Resolution 2026-04-29:** `extract_netlist()` versucht jetzt zuerst
`kicad-cli sch export netlist --format kicadsexpr` und parst das Ergebnis
mit dem bestehenden balanced-paren `parse_sexpr()` — das deckt alle
optionalen `(pinfunction ...)` / `(pintype ...)` Trailing-Fields korrekt
ab. Bei Erfolg returnt das Tool `partial: False, source: "kicad-cli"`
mit voller Pin-Connectivity. Fallback auf den Label-only Parser nur
wenn kicad-cli nicht verfügbar oder Export fehlschlägt — dann weiterhin
`partial: True`. Tests in `tests/test_netlist_parser.py` (3 Cases inkl.
Mock-CLI für CI ohne KiCad-Install).

---

## ✅ Bug 3 — No annotation tool exposed [RESOLVED 2026-04-29]

**Severity:** Medium (blocks netlist export via `kicad-cli` when annotation is incomplete)

**Symptom:** When the schematic has unannotated symbols (e.g., custom power-symbol references like `#PWR_CPU_3V3_U1` that don't follow `#PWR0001` convention), `kicad-cli sch export netlist` prints *"Schaltplan hat Annotationsfehler"* and silently produces an incomplete netlist (often only 1 pin per net).

**Root cause:** kicad-cli has no `annotate` sub-command. Annotation only via Eeschema GUI (`Tools → Annotate Schematic`).

**Workaround:** User must open the schematic in Eeschema and run Annotate. After save, the netlist export works.

**Suggested fix:** add `mcp__kicad__annotate_schematic` tool that:
- Either invokes Eeschema headlessly (if KiCad ever exposes it),
- Or programmatically rewrites the schematic to assign sequential `#PWR0xxx` refs to power symbols, and `R1`/`C1`/etc. to passives based on prefix counts.

A pure-Python annotator is feasible: parse `(symbol ... (property "Reference" "PREFIX"))` instances, count per-prefix, write back as `PREFIXn`.

**Resolution 2026-04-29:** `mcp__kicad__annotate_schematic` Tool implementiert
in `sch_patch_tools.py`. Parst alle Top-Level `(symbol ...)`-Blöcke, klassifiziert
Refs (annotated wenn `\d+`-Suffix oder `#PWR0001`-Pattern; unannotated wenn `?`-
Suffix oder `#PWR_xxx`/`#FLG_xxx` non-conforming). Vergibt pro Prefix die
nächste freie Nummer (fills gaps), updated sowohl `(property "Reference" ...)`
als auch nested `(reference "X")` in der `(instances …)`-Section. Mode
`force_renumber=True` re-annotiert von 1 weg. Tests in
`tests/test_sch_patch_tools.py::TestAnnotateSchematic` (4 Cases).

---

## ✅ Bug 4 — `add_schematic_label` does not accept `justify` parameter [RESOLVED]

> Status-Marker am Top synchron mit dem Resolution-Eintrag weiter unten in
> dieser Datei. Details siehe ✅-Block.

**Severity:** Low (workaround exists)

**File:** `kicad_mcp/tools/sch_patch_tools.py`

**Symptom:** Global labels emitted by the tool always render with KiCad's default justify behavior (left-aligned text relative to anchor for `rotation 0`). When user wants `(justify right)` for right-side labels with `rotation_deg=180`, must post-edit the file with regex.

**Suggested fix:** add optional `justify: str = ""` parameter (`"left"` / `"right"`) and emit `(justify <value>)` inside the label's `(effects)` block.

---

## ✅ Bug 5 — `add_schematic_symbols` doesn't dedupe lib_symbols on second call [RESOLVED — not reproducible 2026-04-29]

**Severity:** Low (cosmetic — duplicate `(symbol ...)` entries inside `lib_symbols` if a tool call adds the same lib_id twice)

**Symptom:** When calling `add_schematic_symbols` multiple times in one session with the same `lib_id` (e.g., `Device:C_Small`), the second call appends another copy of the symbol library definition into `(lib_symbols ...)`. KiCad accepts but the file grows unnecessarily.

**Suggested fix:** before inserting into `lib_symbols`, check whether `(symbol "lib_id_here" ...)` already exists; skip if so.

**Resolution 2026-04-29:** `SchematicDoc.ensure_lib_symbol()`
(schematic_patcher.py:509-530) macht bereits den Existenz-Check via
`find_lib_symbol(lib_id)` vor jedem Insert. Repro mit zwei
sequentiellen `add_schematic_symbols`-Calls ergibt genau einen
`(symbol "Device:R" ...)`-Eintrag. Bug-Report war veraltet —
vermutlich aus einer Pre-Phase-S-Version.

---

## ✅ Bug 6 — Custom power-symbol references trigger annotation errors [RESOLVED 2026-04-29]

**Severity:** Medium (related to Bug 3)

**Symptom:** When `add_schematic_symbols` is called with a `#PWR_xxx`-style ref (e.g., `#PWR_CPU_GND_BULK`) that doesn't match KiCad's expected `#PWR0001`, `#PWR0002`, ... pattern, the schematic loads but cannot be netlist-exported until re-annotated.

**Root cause:** KiCad's annotator considers the `#` prefix as "auto-numbered power symbol family". Custom names like `#PWR_CPU_GND_BULK` are treated as unannotated.

**Suggested fix:** in `add_schematic_symbols`, when ref starts with `#PWR` or `#FLG` and doesn't match `#PWR\d{4}` / `#FLG\d{4}`, automatically convert to next available number. Document this behavior.

Alternative: accept any ref but warn the user that the schematic must be re-annotated before netlist export.

**Resolution 2026-04-29:** Wird durch Bug 3's `annotate_schematic` Tool
abgedeckt. Workflow: User callt `add_schematic_symbols` mit beliebigen
`#PWR_*`-Refs, dann `annotate_schematic` einmal vor `kicad-cli sch export
netlist`. Auto-Renumber im add-Pfad bewusst nicht eingebaut, weil der User
manchmal nicht-konforme Refs *bewusst* verwenden will (z.B. für ein
Multi-Sheet-Layout, in dem der Annotator Sheet-spezifische Nummern
vergeben soll).

---

## ✅ Bug 7 — `delete_schematic_items` only handles symbols, not labels/wires [RESOLVED 2026-04-29]

**Severity:** Low (mentioned in CLAUDE.md but worth a tracking entry)

**Symptom:** `delete_schematic_items(group_id=...)` removes only `(symbol ...)` instances tagged with the given `kicad-mcp.group` property. Wires and labels are not tagged (S-expression doesn't allow comments officially), so they cannot be group-deleted.

**Workaround:** use direct file-edit + paren-balanced extraction to remove specific labels/wires.

**Suggested fix:** allow deletion by element-type + position-coords: `delete_schematic_items(types=["label", "wire"], region={x,y,w,h})`.

**Resolution 2026-04-29:** `delete_schematic_items` akzeptiert jetzt
`types: list[str]` (`symbol`/`wire`/`label`/`global_label`/`hierarchical_label`/
`junction`/`no_connect`) und `region: {x, y, w, h}` (mm). Spatial-Test:
für Wires reicht ein Endpoint im Box, für Labels/Symbols der Anchor.
Selektoren kombinieren — `group_id="X" + types=["wire"] + region=...`
löscht beides. Dedup via Start-Offset-Set verhindert Doppel-Delete bei
überlappenden Selektoren. Tests in `TestAnnotateSchematic` (jetzt
gemischt, sollte umbenannt werden in einer Folge-PR).

---

## ✅ Bug 8 — Small-pitch passive symbols (C_Small / R_Small / L_Small) need half-grid offset [RESOLVED 2026-04-29]

**Severity:** High (silent wiring failures — pins land off-grid, wires won't connect, ERC silent)

**Discovered:** 2026-04-27 during V8.2.0 power section

**Symptom:** When `add_schematic_symbols` places a `Device:C_Small` (or `R_Small`, `L_Small`) at a center coordinate that is a multiple of 2.54 mm (the KiCad schematic grid), the symbol's two pins land at **center ± 1.27 mm**, which is OFF the standard grid. Wires drawn to those pins from grid-aligned points won't form electrical connections — the schematic LOOKS wired but the pins are silently floating.

**Root cause:** `_Small` variants of passives have pin pitch 2.54 mm with pins symmetrically at ±1.27 from the symbol center. For both pins to be on the 2.54-grid, the center must be at `(N + 0.5) × 2.54` (e.g., 1.27, 3.81, 6.35, 8.89, …). The full `Device:C` / `Device:R` symbols use pin pitch 5.08 / 7.62 — also problematic depending on choice.

**Pin pitch by symbol:**

| lib_id | Pin pitch | Center on grid → pins on grid? |
|---|---|---|
| `Device:C` | 7.62 mm (3 grid units) | ✅ pins at ±3.81 = ±1.5 grid → OFF grid (need same trick) |
| `Device:C_Small` | 2.54 mm (1 grid unit) | ❌ — center must be at `(N+0.5)*2.54` |
| `Device:R` | 7.62 mm | ❌ same as `Device:C` |
| `Device:R_Small` | 2.54 mm | ❌ — center must be at `(N+0.5)*2.54` |
| `Device:L_Small` | 2.54 mm | ❌ — center must be at `(N+0.5)*2.54` |
| `Switch:SW_Push` | 5.08 mm (2 grid units) | ✅ pins at ±2.54 = ±1 grid → ON grid ✓ |

**Suggested fix in `add_schematic_symbols`:**

When `lib_id` ends with `_Small` (or matches a known small-pitch list), automatically adjust the user-supplied `x_mm` / `y_mm` to land the pins on the 2.54-grid:

```python
SMALL_PITCH_LIBS = {"Device:C_Small", "Device:R_Small", "Device:L_Small", "Device:CP_Small"}

def _snap_for_pin_grid(x, y, lib_id, rotation):
    if lib_id not in SMALL_PITCH_LIBS:
        return x, y
    # Pin pitch 2.54 → center must be at (N+0.5)*2.54 to place pins on grid
    # Vertical orientation (rotation 0/180): adjust Y; horizontal (90/270): adjust X
    if rotation in (0, 180):
        y = round((y - 1.27) / 2.54) * 2.54 + 1.27
    else:
        x = round((x - 1.27) / 2.54) * 2.54 + 1.27
    return x, y
```

**Workaround until fixed:** when manually computing coordinates for `_Small` parts, use centers like `21.59, 24.13, 26.67, 29.21, 31.75, 34.29, …` (i.e., `n*2.54 + 1.27`). A Python helper:

```python
def small_pitch_center(n: int) -> float:
    """Center coordinate for a Device:*_Small part so both pins land on 2.54-grid."""
    return n * 2.54 + 1.27
```

**LLM-relevant rule:** when writing a placement spec for `Device:C_Small` / `R_Small` / `L_Small` etc., NEVER use a center at `n*2.54` exactly. Always offset by 1.27 in the axis perpendicular to the pin orientation.

**Resolution 2026-04-29:** `add_schematic_symbols` jetzt auto-snapped. Helper
`snap_for_pin_grid()` in `kicad_mcp/utils/sch_geometry.py` mit Lookup-Set
`HALF_GRID_OFFSET_LIBS` (deckt `Device:C/R/L/CP/D/LED` plus alle `_Small`-
Varianten ab). Tool-Antwort enthält neues Feld `snapped: [{ref, lib_id,
from, to}, …]` — Caller kann den Move im Diff sehen. Tests in
`tests/test_sch_geometry.py::TestSnapForPinGrid` (8 Cases inkl. Rotation
0/90/180/270, negative Rotation, Already-on-grid no-op).

---

## ✅ Bug 10 — B.Cu pad world-coords doppelt gespiegelt [RESOLVED 2026-05-26]

**Severity:** High (silent wrong-pad selection — Routing-Tools verlinken Vias und Tracks auf den Pad mit der falschen Nummer, kann SMT-Pins kurzschließen)

**Files:**
- `kicad_mcp/tools/pcb_geometry_tools.py` — `_transform_pad_world`
- `kicad_mcp/tools/pcb_patch_tools.py` — `place_at_pivot_text` (pad/bbox-Pivot-Pfad)

**Symptom:** `compute_pad_world_positions` und `place_at_pivot(pivot_kind="pad", layer="B.Cu", …)` liefern für B.Cu-Footprints Pad-Welt-Positionen die mit DRC und pcbnew's Pad-Hover-Anzeige nicht übereinstimmen — die X-Komponente ist relativ zur Footprint-Origin gespiegelt. Für eine SOIC-16 mit Pin 1 und Pin 16 auf gleicher Y-Linie aber gegenüberliegender X-Seite landet die berechnete Pin-1-Position EXAKT dort wo Pin 16 sitzt (Pad-Vertauschung).

**Root cause:** KiCad's `FOOTPRINT::Flip` mirrors `PAD::m_pos.X` in-place beim Flip auf B.Cu — die Datei speichert anschließend die *post-flip* Pad-Local-Coords. `_transform_pad_world` rief `pcb_local_to_world(…, flipped=(fp_layer=="B.Cu"))` auf, das die X-Achse noch ein zweites Mal spiegelt. Resultat: Doppel-Flip, X-Komponente endet beim falschen Pad. Gleicher Pattern in `place_at_pivot_text`: die `rotated_pivot`-Berechnung passte `flipped=True` an, obwohl der Funktion nicht den Pad-Coord-Inhalt mirror (`_patch_fp_pose` lässt Pad-Positionen unangetastet — nur Layer-Tag, fp.rot und Pad-Local-Rotation werden umgeschrieben).

**Reproducer (real-world):** reference-Mainboard `reference_Mainboard.kicad_pcb`, U_597 (74HC597, SOIC-16) auf B.Cu mit fp=(129.102, 96.525) rot=−113.6°. File-Pad-1 lokal (−2.475, −4.445). pcbnew zeigt Pin 1 bei Welt (134.166, 96.037); DRC-Violation-Report bestätigt diese Position. `compute_pad_world_positions` gab vor Fix (132.184, 100.573) zurück — Position von Pad 16 (+3V3), nicht Pad 1 (nFAULT_DRV1).

**Resolution 2026-05-26:**
- `_transform_pad_world`: `flipped=False` hardgecoded, mit Kommentar zur Begründung (Datei-Coords sind bereits post-flip).
- `place_at_pivot_text`: gleicher Schritt in der `rotated_pivot`-Berechnung.
- Bestehende Tests `test_bcu_flip_mirrors_x` / `test_bcu_with_rotation` umbenannt zu `test_bcu_pad_coords_are_post_flip` und mit den korrekten Erwartungswerten geupdated. Fixture-Pad-Position für U2 (B.Cu) in `TestComputePadPositions.test_extracts_world_pads` bzw. `TestAddTrack.test_adds_segment_with_via_when_layers_differ` von (1, 0) → erwartet (−11, −5) auf erwartet (−9, −5) korrigiert (= echte post-flip Realität).
- Neuer Test `test_bcu_realistic_soic_pin1` (reference-Repro) + `test_bcu_pad_pivot_no_double_flip` (place_at_pivot Pad-Pivot auf B.Cu).
- Footgun #2 in `CLAUDE.md` (B.Cu-X-Mirror vergessen) bleibt gültig für *placement* (Library → World mit `flipped=True`); für *readback* (File → World) jetzt explizit `flipped=False`.

**LLM-relevant rule für die Zukunft:** wenn DRC-Output und MCP-Pad-Position für einen B.Cu-Pad widersprechen, IMMER DRC trauen (operiert auf KiCad's internem Pad-Zustand, nicht auf einer rechnerischen Local→World-Transformation). MCP `find_tracks_by_net` ist ebenfalls von KiCad's pcbnew-Bindings gefüttert und stimmt mit DRC — verwende beide als Ground Truth bei Pad-Positions-Streitfällen.

---

## ✅ Bug 1 — `mcp__kicad__run_erc` parses wrong JSON path [RESOLVED 2026-04-27]

Aggregation jetzt über `report.get("sheets", [])` mit Fallback auf
top-level `violations` (siehe `erc_tools.py::_run_erc_cli` + Zeile 131-138
in `run_erc`). Auch `unconnected_items` sheet-aggregiert.

---

## ✅ Bug 4 — `add_schematic_label` does not accept `justify` [RESOLVED]

Tool-Signatur hat jetzt `justify: str = ""` (`"left"`/`"right"`); Default
delegiert an `justify_for_angle()`. Siehe `sch_patch_tools.py:540-595`,
Renderer in `schematic_patcher.py:766-799`.

---

## 📚 LLM Guidance — Electronics Conventions

**These are user-facing rules for LLMs interacting with MCP-generated schematics. NOT bugs in the code, but instructions to prevent the LLM from giving incorrect electrical analysis.**

### Non-polar parts have interchangeable pins

For the following part types, **Pin 1 and Pin 2 are electrically identical** — the symbol numbering is arbitrary and carries NO polarity information:

- **Unpolarized capacitors** (ceramic MLCC, NP0, X5R, X7R, …): `Device:C`, `Device:C_Small`, `Capacitor_SMD:C_*`
- **Resistors** (all types): `Device:R`, `Device:R_Small`, `Resistor_SMD:R_*`
- **Inductors** (without polarity-marking arrow): `Device:L`, `Device:L_Small`
- **Switches** (SPST momentary): `Switch:SW_Push`

When analyzing a netlist or a wiring spec, **NEVER report errors of the form "Cap.Pin1 is on net X but should be Pin2 instead"**. The cap simply bridges two nets; which physical terminal sits on which net is irrelevant.

**Correct error report style** for a misrouted bridge component:
> ❌ `C_TPS_BOOT` bridges `+5V_SYS ↔ BOOT` but should bridge `BOOT ↔ SW`. One terminal needs to be re-routed from `+5V_SYS` to `SW`.

**Wrong** (electrically confused):
> ❌ `C_TPS_BOOT.1` is on `+5V_SYS` instead of `BOOT`. Rotate the cap 180° to swap pins.

### Polar parts where pin orientation DOES matter

For these, `Pin 1` vs `Pin 2` carries real meaning — preserve it in analysis:

- **Polarized capacitors** (electrolytic, tantalum): `Device:CP`, `CP_Small`, `Polarized_Capacitor_SMD:*` — Pin 1 = `+`, Pin 2 = `−`
- **Diodes / LEDs**: Pin 1 = anode (`A`), Pin 2 = cathode (`K`) — directional
- **Transistors**: discrete pin functions (G/D/S, B/C/E)
- **ICs**: every pin is unique

### Implications for tool design

`add_schematic_symbols` could optionally tag each part's "polarity" in the JSON spec so downstream verification can decide whether to flag pin-orientation errors. For now: when LLMs receive netlist data, they must check the part type and adjust analysis accordingly.

---

## Notes for ScanAllX-Driven Validation

When verifying a schematic patched by these tools, **do not rely on**:
- `mcp__kicad__run_erc` (returns 0/0 — Bug 1)
- `mcp__kicad__extract_schematic_netlist` (returns empty pin-lists — Bug 2)

**Use instead:**
```bash
kicad-cli sch erc --format json --output report.json file.kicad_sch
kicad-cli sch export netlist --format kicadsexpr --output nl.net file.kicad_sch
```

Parse with balanced-paren extraction (regex alone is not enough due to nested fields like `(pinfunction "...") (pintype "...")` inside `(node ...)`).
