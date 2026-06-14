# CLAUDE.md — kicad-mcp

MCP-Server für KiCad-EDA (Schaltplan/PCB), **165 Tools**. Läuft unter **KiCads
gebündeltem Python** (kipy 0.7.1 + pcbnew, KiCad 10.0). Start: `start_mcp.bat`
(Windows) bzw. `start_mcp_wsl.sh` (WSL/Linux/macOS). Tests: `pytest tests/` unter
dem KiCad-Python (CI: pylint 0/0 + pytest, siehe `.github/workflows/ci.yml`).

Dies ist ein GPL-3.0-or-later-Fork des MIT-Projekts
[lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) — Begründung in
`NOTICE`, Original-MIT-Notice in `LICENSE.MIT`.

## Agent-Verhalten — gegen Toolcall-Explosion (Pflicht beim PCB-Editieren)

Grundproblem: Single-Op-Tools + Verify-nach-jeder-Mutation = I/O in Schleife. Eine
24-Via-Platzierung darf nicht 40+ Calls erzeugen. Ursache ist nicht das Platzieren, sondern
Render + State-Rücklesen nach **jeder** Einzelmutation.

**Render:** `pcb_render` ist der teuerste Call — **nie** nach einer Einzelmutation. Nur am
Abschluss aller Mutationen, an gesetzten Meilensteinen, oder auf direkte Aufforderung. Beim
iterativen Platzieren (Vias/Bauteile/Tracks) zwischendrin kein Render; erst wenn die ganze
Tranche steht.

**Verify:** Korrektheit = Connectivity/Zählwert prüfen, **nicht** rendern (`check_connectivity`
ist billig, `pcb_render` nicht). Mutations-Tools, die ihren Effekt selbst zurückgeben (neue
Cluster-/Netz-/DRC-Zahl), brauchen **kein** separates `check_connectivity` danach — Result
lesen statt nachfragen. Kein Re-`Read` des Board-/Netlist-States nach einer Mutation; der
State ist im Adapter-Cache.

**Batch vor Einzeln:** Mehrere gleichartige Mutationen (N Vias, N Moves) → Batch-Tool wenn
vorhanden (`add_vias_to_pcb` statt N× `add_via_to_pcb`), sonst Einzel-Calls bündeln, dann
*einmal* füllen, *einmal* verifizieren. Verify-Granularität: pro Tranche (6–8) oder am Ende,
nicht pro Element.

**Loop-Vorlage Via-Platzierung:** (1) alle Vias der Tranche setzen, (2) einmal füllen,
(3) einmal `check_connectivity` (Cluster-Zahl gegen Erwartung), (4) Render erst am Schluss
aller Tranchen.

### Tool-Design-Regeln (nur beim Arbeiten am MCP-Code, nicht beim PCB-Editieren)

- **Effekt-Echo in Mutations-Results:** Jedes Mutations-Tool gibt den relevanten Effekt direkt
  zurück (was geändert, neuer Zählwert: Cluster/Netze/DRC). Ziel: kein Rücklesen nötig → killt
  die Read-back-Hälfte des Loops. Kein Input-Signatur-Wechsel — nur das Result erweitern.
- **Batch-Varianten:** Operationen, die selten allein vorkommen, brauchen ein Plural-Tool
  (`add_vias_to_pcb`, `move_components`, `set_properties`). Das Plural-Tool erzwingt Batch
  strukturell (es gibt keinen Per-Element-Call). Nur die 3–4 nachweislich oft geschleiften
  Operationen batchen, nicht alle Tools.
- **Tool-Descriptions steuern Verhalten:** In Mutations-Tools rein: „Rendert nicht. Für
  visuelle Kontrolle `pcb_render` separat nach Abschluss aller Mutationen." Descriptions
  wirken stärker als dieser File (sie werden im Moment der Tool-Wahl gelesen) — Regel und
  Description müssen konsistent sein.

## Architektur — Schichten

| Schicht | Modul | Funktion |
|---|---|---|
| Text-Patcher (PCB) | `tools/pcb_patch_tools.py` | F8-Äquivalent ohne GUI: Netze/Footprints/Rotation als chirurgischer Text-Patch |
| Geometrie/Routing | `tools/pcb_geometry_tools.py` | Welt-Koords, Track-/Zone-Insertion (flip-aware) |
| Schaltplan-Patcher | `tools/sch_patch_tools.py` | Inkrementelles `.kicad_sch`-Editieren (Symbole, Wires, Labels, Gruppen, Power-Symbole) |
| IPC-Bridge | `tools/ipc_tools.py` | Live-KiCad-GUI über kipy: Track/Via/Zone in den laufenden Editor |
| IPC-Live-Diff | `tools/ipc_live_tools.py` + `ipc_live_diff.py` | Pull-only Diff des *lebenden* Editors, agent-vs-user-Attribution, `agent:`-Commits |
| Circuit-Blocks | `tools/circuit_block_tools.py` + `generators/circuit_block/` | Datasheet-Spec → Schaltplan-Block (komponiert über den Schaltplan-Patcher) |
| Datasheet-Review | `tools/review_tools.py` + `generators/review/` | Review-Material je IC + System-Interconnect |
| Generatoren | `generators/` | Projekt/Schaltplan/PCB aus Specs/Netlist; ESPHome- & LTspice-Konverter |
| Warm-Board-Daemons | `tools/_warm_daemon.py` | `pcb_eval`, `check_connectivity`, `via_promote` halten geladene+gefüllte `BOARD`s im Speicher (Cache by path+mtime) |
| File-Cache | `cache/file_cache.py` | Text-Cache für `.kicad_pcb`/`.kicad_sch`, stat-revalidiert (mtime_ns+size) |
| Cross-Environment | `utils/path_env.py` | Pfad-Konversion WSL↔Windows, KiCad-Install-Discovery |

## Konventionen (Pflicht für neue Tools)

1. **Pfade normalisieren:** erste Body-Zeile jedes Tools `path = to_local_path(path)`
   (WSL `/mnt/c/...` ↔ Windows `C:\...` transparent), direkt danach Existenz-Check.
   `test_all_tools_dynamic.py` / `test_tool_audit.py` erzwingen das.
2. **Argumente** primitiv oder JSON-String (`json.loads` im Tool). **Return** ein Dict mit
   mindestens `{success: bool}`; Fehler → `{"success": False, "error": "<klartext>"}`.
3. **Registrierung** zentral in `tool_registry.py::TOOL_REGISTRARS` (Single Source of
   Truth; `test_tool_audit.py::test_tool_count_locked` ist der Drift-Wächter). Pro
   Familie ein `register_*(mcp)` mit `@mcp.tool()` — nie nackte Funktionen.
4. **File-Edit-Tools** (mutieren `.kicad_pcb`/`.kicad_sch`): eine reine Companion
   `<tool>_text(text, **args) -> tuple[str, dict]` (keine I/O), via `@_register_text_fn`
   in `PCB_PATCH_TEXT_FNS`/`PCB_GEOMETRY_TEXT_FNS` registriert → automatisch über
   `pcb_batch` (N Edits, eine Open/Write-Runde). Plus `dry_run: bool = False`.
5. **Docstring** LLM-tauglich: was/wann (Abgrenzung zu Nachbar-Tools) + `Args:`/`Returns:`
   + mind. ein Usage-Cue („Use this when …"). Plain-Text, keine Markdown-Headings.
6. **Tests:** Happy-Path + Edge/Idempotenz + Error-Path in `tests/test_<kategorie>_tools.py`
   (Pfade als WSL-Pfade übergeben). Tool-Count in `test_tool_audit.py` mitbumpen,
   CHANGELOG-Eintrag, pylint 0/0.

## Tool wählen statt selbst parsen

Für jede KiCad-Operation **erst ein MCP-Tool suchen**, nicht Read/Bash/Regex nachbauen —
der Server rechnet Flip/Rotation/Net-Auflösung korrekt. Faustregeln:

- PCB/Schaltplan lesen → `list_pcb_footprints` / `analyze_pcb_nets` / `list_schematic_components` (nicht Datei + Regex)
- Pad-/Pin-Welt-Koords → `compute_pad_world_positions` / `ipc_get_pad_world_pos` (B.Cu-flip-aware; nie selbst rechnen)
- Konnektivität / „ist dieses Via load-bearing?" → `check_connectivity` (warm, headless Ratsnest)
- Ad-hoc-Geometrie/What-if gegen ein warmes Board → `pcb_eval`
- Layout *sehen* → `pcb_render` (Region-PNG statt aus Koordinaten im Kopf rechnen)
- Mehrere Mutationen → `pcb_batch` (eine Open/Write-Runde)
- Blind/Buried→Through-Optimierung → `via_promote`
- Runde/Polar-Boards → `polar_grid`
- Headless ERC/DRC → `run_erc` / `run_drc_check` (kicad-cli), nicht von Hand
- Power-/GND-Pins verdrahten → `add_power_symbols` (nicht `add_schematic_label`)
- **Offenes Board mutieren → IPC, nicht Text-Patcher.** Ist die `.kicad_pcb` in der KiCad-GUI
  offen, blockiert `write_text` (in `cache/file_cache.py`) Direkt-Patches hart
  (`BoardOpenError`, via `utils/board_open_guard.py`) — sie kollidieren mit dem
  Editor-Speichern. Nutze die Live-Tools (`ipc_*` / `live_*`), die KiCads In-Memory-Modell
  ändern (beide Seiten speichern kohärent). Override: `KICAD_MCP_ALLOW_DISK_WRITE_WHILE_OPEN=1`.
  Schaltpläne sind ausgenommen (Eeschema hat in KiCad 10 keinen IPC-Save).

## KiCad-Koordinaten — Footguns (hart erkämpft)

Einheiten: PCB/Footprint mm @ 1 nm intern (6 Nachkommast.); Schaltplan mm @ 100 nm
(4 Nachkommast.); kipy/IPC nm-int64. **Alle Subsysteme Y-down.**

1. **Rotation ist KiCad-CW, nicht Math-CCW** — immer `pcb_local_to_world()` aus
   `utils/pcb_geometry.py`, nie selbst rechnen (sonst ~0,4 mm Fehler @ 90° auf 0402).
2. **B.Cu spiegelt X, nicht Y** (`lx → -lx`); die Footprint-Orientierung wird negiert.
3. **Pad-Shape-Rotation** dreht NICHT mit, wenn man nur den Footprint-`(at)` editiert →
   `place_at_pivot` nutzen (sonst Pad-Kurzschlüsse zu Nachbarn).
4. **Long-Way-Arc:** naives `mid = midpoint(start,end)` zeichnet die *lange*
   Bogenhälfte → `short_arc_mid_xy()` / `add_arc_to_pcb`.
5. **Schaltplan: erst Mirror, dann Rotation**; Lib-Symbol-Pin-Y ist Y-up und wird beim
   Instanzieren geflippt.
6. **Via-Typ kommt aus dem Token** `(via blind/buried/micro` — ohne Token = Through
   (KiCad normalisiert beim Save still auf F.Cu/B.Cu → Shorts). Net-Tag-Format
   (String- vs Index-Form) vor dem Emittieren erkennen (`utils/pcb_net_format.py`).
7. **kipy:** nm-int; `Vector2.from_xy_mm(...)` ist der einzige mm-Konstruktor, lesen
   mit `pos.x / 1_000_000`.

Code-Belege: `utils/pcb_geometry.py`, `utils/sch_geometry.py`, `utils/pcb_net_format.py`,
`tools/pcb_geometry_tools.py`.

## Bekannte Grenze (KiCad 10.0)

Eeschema-IPC kann **kein** Save/Revert/RunAction (Tracking: KiCad #2077) → Live-ERC läuft
über `kicad-cli` (`run_erc`), nicht `ipc_run_erc` (Stub). PCB-IPC ist vollständig
(Save/Revert/Tracks/Vias/Zonen). SWIG `pcbnew` ist deprecated → IPC/Text-Patcher sind die
strategische Antwort.

**Über IPC nicht möglich (kein Workaround, nicht implementieren):**
- **Live-Mausposition / Statusleisten-X/Y** wird nicht exponiert; kein Push/Event für Maus
  oder Selektion (alles Polling). Ersatz für „worauf zeige ich" = **GUI-Selektion**
  (`ipc_get_selection`).
- **3D-Viewer** hat keine API (keine Selektion/Steuerung).
- **Keine Schematic-API** in KiCad 10 (nur PCB-Editor).

## Performance

Auf gesynchten Disks (OneDrive/Dropbox) dominiert der **I/O** — der kalte Erst-Read
hydratisiert die Multi-MB-Datei aus der Cloud (~80 s), nicht pcbnew (Load+Fill ~1 s lokal).
→ aktives Projekt auf **lokale Disk**, nur Outputs syncen. Die Warm-Board-Daemons
(`_warm_daemon.py`) amortisieren Load+Fill über wiederholte Queries; `pcb_batch` bündelt
Writes (jeder Write = ein Sync-Upload); `file_cache` killt redundante Reads.

**Live-IPC:** Der zentrale Session-Layer `utils/ipc_session.py` hält **einen** wieder-
verwendeten kipy-Client (`get_client()`, von `_connect_kicad` genutzt) statt pro Tool-Call
neu zu verbinden — der größte Latenz-Hebel. Timeout konfigurierbar via
`KICAD_MCP_IPC_TIMEOUT_MS` (Default 15000 ms, statt kipys 2000 ms); `call_with_retry`
fängt „KiCad is busy" mit Backoff ab und reconnectet bei abgerissener Verbindung. File-Log
neben dem Board (`kicad_mcp_ipc.log`, Fallback Temp-Dir), da stdout beim Plugin-Launch
unsichtbar ist. Wait-/Restart-Loops nutzen `new_client()` (frisch, gleicher Timeout).
