# PLAN.md — kicad-mcp IPC-Erweiterung

Status: v4 EINGEDAMPFT — G1-G6 ALLE umgesetzt + live validiert (2026-06-11), s. §4.2
Repo: github.com/ChrisRudi/kicad-mcp (Fork von lamaalrajih/kicad-mcp)
Client: Claude Code (stdio)
Stand: 2026-06-11

## 1. Ziel

Interaktion mit einer laufenden KiCad-9-Instanz (pcbnew) ueber die
offizielle IPC-API:

- Nutzer selektiert Elemente im Editor -> LLM erhaelt Selektion als Kontext
- LLM markiert existierende Elemente sichtbar im Editor (Selektion)
- LLM zeichnet Vorschlagsmarker auf einen reservierten MCP-Layer
- LLM aendert das Board (undo-faehig ueber Commits)
- Gefuehrte Sessions: Menue aus offenen Punkten (DRC, Marker)

## 2. Festlegungen (entschieden)

- Marker-Typen v1: kreis, kreuz, label. Rechteck/Pfeil -> v2.
- Marker tragen IDs (M1, M2, ...) im Label; alle Marker-Tools
  adressieren per ID. (UC17, fest eingebaut)
- create_via: direkt UND als Zwei-Schritt (Marker -> Freigabe)
  erlaubt. Claude waehlt Zwei-Schritt bei Vorschlaegen, direkt bei
  expliziter Anweisung.
- Filter-Syntax select_items: benannte Parameter (netz=, typ=,
  layer=). Kein Query-String.
- get_selection bei leerer Selektion: leeres Ergebnis + Ein-Satz-
  Hinweis ("Nichts selektiert").
- Gefuehrte Session v1 als Chat-Menue (nummerierte Liste), MCP-
  Elicitation als v2 (braucht Claude Code >= 2.1.76).

## 3. Bedienkonzept (Nutzersicht)

### 3.1 Trigger-Modell

Das MCP beobachtet nichts. Die IPC-API ist reines Request/Response,
KiCad pusht keine Events. Ausloeser ist immer eine Chat-Nachricht in
Claude Code:

1. KiCad laeuft, Platine offen, IPC-Server aktiviert.
2. Claude Code startet im Repo, MCP-Server startet als stdio-Prozess
   mit. Verbindung zu KiCad lazy beim ersten Tool-Aufruf.
3. Nutzer arbeitet normal; MCP sieht nichts, kein Overhead.
4. Nutzer tippt -> erst jetzt liest/schreibt das MCP den Live-
   Editor-Zustand (kein Speichern/Reload noetig).

Bewusst nicht in v1: automatisches Mitlaufen (Polling), Viewport-
Steuerung (Zoom/Pan), Screenshots der Live-Ansicht.

### 3.2 Use Cases v1

- UC1 Selektion besprechen (Kern): Nutzer selektiert, fragt im
  Chat. Claude liest Live-Selektion (Typ, Netz, Layer, Position,
  Masse) und antwortet. Read-only.
- UC2 Element abfragen: per Referenz ("Was haengt an R12?") oder
  aus der Selektion. Detailinfo.
- UC3 Claude markiert: "Markiere alle GND-Vias" -> Claude setzt die
  UI-Selektion, natives Highlight. Nutzer scrollt selbst hin.
- UC4 Vorschlagsmarker: Claude zeichnet kreis/kreuz + Label mit ID
  auf den MCP-Layer. Nur Grafik, kein Kupfer. Layer ausblendbar.
- UC5 Vorschlag annehmen: "M1 und M3 uebernehmen" -> echte Vias als
  Commit (einzeln undo-bar), zugehoerige Marker werden entfernt.
- UC6 Direkte Aenderung: Tracks verbreitern, Bauteil schieben,
  Via direkt setzen. Live sichtbar, Undo wie eigene Arbeit.
- UC7 Aufraeumen: clear_mcp_layer loescht alle Marker; Check-Tool
  warnt vor Git-Commit, falls noch Marker auf dem Board.
- UC8 Datei-Tools (Upstream, unveraendert): DRC, BOM, Netzliste,
  Projektuebersicht. Ohne laufendes KiCad.
- UC9 Gefuehrte DRC-Session: DRC via kicad-cli, Claude setzt pro
  Verletzung Marker + Selektion, erklaert Ursache, schlaegt Fix
  vor, fuehrt ihn nach Freigabe aus, prueft per Re-DRC. "naechster"
  springt weiter.
- UC11 Regel-Audits: ad-hoc-Regeln in natuerlicher Sprache
  ("alle Tracks unter 0,3 mm an Power-Netzen") -> lesen, filtern,
  selektieren + Liste im Chat. Wiederkehrende Audits als Prompts
  in CLAUDE.md.
- UC21 Gefuehrte Session: session_status() aggregiert offene
  Punkte (DRC-Verletzungen nach Schwere, Marker nach Status/Region,
  Selektion). Claude rendert nummeriertes Menue im Chat, Nutzer
  antwortet mit Nummer, Workflow laeuft, zurueck ins Menue.
  Einstieg als Slash-Command (MCP-Prompt), z.B. /kicad:weiter.

### 3.3 v2-Backlog

- UC13 Routing-Vorschlag: Pfad als Polylinie auf MCP-Layer, nach
  Freigabe echte Tracks + Vias, danach Auto-DRC auf neue Elemente.
  Risiko mittel (Pad-Andocken, Layer-Wechsel, Clearance) ->
  Erwartung daempfen, kein Autorouter-Ersatz.
- Marker-Typen rechteck, pfeil.
- Elicitation-Menue: echter Dialog statt Chat-Liste (MCP
  elicitation/create, Claude Code >= 2.1.76). Achtung: blockiert
  bis Eingabe -> fuer automatisierte Laeufe ungeeignet.
- Beobachten, nicht planen: Konnektivitaet/Ratsnest (keine kipy-
  API, Feature-Request offen), Schaltplan-Interaktion (IPC-API in
  KiCad 9 fast nur pcbnew), Laengen-Matching nur als Report.

## 4. Ist-Zustand (eingedampft 2026-06-11)

> Reconciliation gegen die **bereits vorhandene** IPC-Schicht. Der
> urspruengliche Phasenplan (0-5) ist zu ~60% schon im Repo erfuellt;
> dieser Abschnitt streicht Erledigtes und behaelt nur die echten
> Luecken (G1-G6). Verifiziert: kipy 0.7.1 (KiCad 10) bietet die ganze
> Selektions-/Edit-/Marker-API; `ipc_check_status` zeigt
> `kipy_installed=true, kicad_reachable=true`. KiCad ist live und
> ueber `ipc_open_kicad`/`ipc_close_kicad` MCP-start-/stoppbar →
> **das manuelle Phase 0 entfaellt**.

### 4.1 Schon vorhanden (NICHT neu bauen)

| Plan-Element | Erledigt durch (vorhandenes Tool/Modul) |
|---|---|
| Phase 0 Precondition + Version/Hint | `ipc_check_status`, `ipc_get_open_documents` |
| Phase 0 KiCad starten/stoppen | `ipc_open_kicad`, `ipc_close_kicad` |
| Phase 1 IPC-Client (lazy connect, Reconnect, Fehlertexte) | `tools/ipc_tools.py::_connect_kicad`/`_require_editor`/`_kipy_available` |
| Commit-API (ein Commit/Aufruf, undo-bar) | `begin_commit`/`push_commit`/`drop_commit` in `ipc_tools.py` (genutzt) |
| Save/Revert/Export | `ipc_save`, `ipc_save_all`, `ipc_revert`, `ipc_export_schematic` |
| DRC/ERC ausfuehren | `ipc_run_drc`, `ipc_run_erc` |
| Routing-/Zonen-Primitive | `ipc_route_pin_to_pin`, `ipc_add_zone_pour`, `ipc_route_power_ring`, `ipc_set_footprint_pose`, `ipc_reload_and_fill_zones` |
| Pad-Welt-Koordinaten (flip-aware) | `ipc_get_pad_world_pos` |
| Live-Diff / „was hat der User geaendert" / Move | `live_get_state`, `live_diff_since_last`, `live_summarize_user_changes`, `live_move_footprint`, `live_session_status` |
| kipy-Selbstinstallation | `ipc_install_kipy` |

### 4.2 Echte Luecken (zu bauen) — gegen kipy 0.7.1 verifiziert

| ID | Luecke | UC | kipy-Methoden (bestaetigt vorhanden) |
|---|---|---|---|
| G1 | Selektion **lesen** | UC1, UC2 | ✅ `ipc_get_selection`, `ipc_inspect_item` (2026-06-11, Unit grün) |
| G2 | Selektion **setzen** | UC3 | ✅ `ipc_select_items`, `ipc_clear_selection` (2026-06-11, Unit grün) |
| G3 | **Marker** (kreis/kreuz/label, IDs) | UC4, UC7, UC17 | ✅ `ipc_draw_markers`/`ipc_list_markers`/`ipc_clear_markers`/`ipc_check_markers_before_save` (2026-06-11, **live validiert** an KiCad 10.0.1; Layer auto-enable+visible) |
| G4 | **Generische Edits per uuid** (via/track-width/move/remove) | UC5, UC6 | ✅ `ipc_create_via`/`ipc_accept_markers`/`ipc_set_track_width`/`ipc_move_items`/`ipc_remove_items` (2026-06-11, live validiert) |
| G5 | **DRC-Session-Schleife** | UC9 | ✅ `ipc_drc_session_start` (save→kicad-cli-DRC→Marker, gecappt; live validiert) |
| G6 | **Session-Menue** | UC21 | ✅ `ipc_session_status` (Marker + Selektion Roll-up) |

> **Stand 2026-06-11:** G1+G2 implementiert in `tools/ipc_interact_tools.py`
> (4 Tools, Unit-Tests gemockt grün, Tool-Count 149→153). **Live-Smoke
> offen** — neue Tools erst nach MCP-Server-Restart im laufenden Editor
> aufrufbar; dann an offener Platine: selektieren → `ipc_get_selection`,
> `ipc_select_items net=GND`. G3-G6 als Nächstes (G3/G4 wollen Live-Check).

## 5. Architektur-Entscheidungen (aktualisiert)

- kipy 0.7.1 ist **bereits** Dependency — keine neue Dep. Selektions-,
  Edit-, Marker-API headless verifiziert (s. 4.2). Die alte Sorge
  „IPC-API jung / `clear_selection`-Bug" ist fuer KiCad 10 erledigt:
  `clear_selection`/`add_to_selection`/`remove_from_selection` sind
  First-Class-Methoden.
- Neue G-Tools liegen in **einem** neuen Modul
  `tools/ipc_interact_tools.py` (Selektion + Marker + Edits + Session),
  registriert ueber `tool_registry.py` wie alle anderen Familien.
  Wiederverwendung von `_connect_kicad`/`_require_editor` aus
  `ipc_tools.py` — kein zweiter Client.
- Alle Schreiboperationen ueber die Commit-API (ein Commit/Aufruf,
  einzeln undo-bar) — Muster aus `ipc_route_pin_to_pin` uebernehmen.
- MCP-Zeichenlayer: User-Layer (Default `User.9`, `.env`
  `KICAD_MCP_LAYER`), exklusiv fuer Marker.
- Marker-IDs: fortlaufend pro Session, im `BoardText`-Label kodiert
  (`M1`, `M2`, …); `ipc_list_markers` liest ID → Position/Typ/Status
  durch Scan der Shapes auf dem MCP-Layer zurueck.

## 6. Phasen (eingedampft auf G1-G6)

### G1 — Selektion lesen (ipc_interact_tools.py) [UC1, UC2]
- `ipc_get_selection()` → kompaktes JSON: typ, referenz, uuid, netz,
  layer, position_mm, bbox_mm; Anreicherung Netzname + Footprint-Wert.
  Leere Selektion: `{success, items: [], note: "Nichts selektiert"}`.
- `ipc_inspect_item(ref_oder_uuid)` → Detail inkl. `get_connected_items`.
- Test: Unit mit gemocktem Board; Live-Smoke gegen offene Platine
  (Selektion durch User), da get_selection nur Vorhandenes liest.

### G2 — Selektion setzen (gleiches Modul) [UC3]
- `ipc_select_items(refs=, uuids=, netz=, typ=, layer=)` — benannte
  Parameter, kombinierbar; intern `get_items_by_*` + `add_to_selection`.
- `ipc_clear_selection()`.
- Test: Unit gemockt; Sichtbarkeit live manuell.

### G3 — Marker (gleiches Modul) [UC4, UC7, UC17]
- `ipc_draw_markers(liste)`: position_mm, typ (kreis|kreuz|label),
  label_text, groesse_mm → vergibt IDs, zeichnet `BoardCircle` /
  zwei gekreuzte `BoardSegment` / `BoardText` auf den MCP-Layer (Commit).
- `ipc_list_markers()` → ID, Position, Typ, Label (Scan MCP-Layer).
- `ipc_clear_markers(ids=None)` → einzelne oder alle (Commit).
- `ipc_check_markers_before_save()`: Warnung, falls Marker auf Board.
- Test: Unit gemockt; visuell live manuell.

### G4 — Generische Edits per uuid (gleiches Modul) [UC5, UC6]
- `ipc_create_via(position_mm, netz, groesse_mm, bohrung_mm)` — direkt
  ODER aus Marker (`ipc_accept_markers(ids)` → Vias + Marker weg).
- `ipc_set_track_width(uuids, breite_mm)` (via `update_items`).
- `ipc_move_items(uuids, delta_mm | ziel_mm)` (generisch; ergaenzt das
  footprint-spezifische `live_move_footprint`).
- `ipc_remove_items(uuids)` (via `remove_items_by_id`).
- Test: Unit gemockt; Integration live mit Undo-Pruefung.

### G5 — DRC-Session (gleiches Modul) [UC9]
- `ipc_drc_session_start()`: `ipc_run_drc` → Verletzungen als Marker
  (G3) mit IDs; Koordinaten-Mapping kicad-cli ↔ IPC.
- `ipc_drc_session_next()`: Selektion (G2) auf beteiligte Elemente,
  Kontext zum Erklaeren; Fix ueber G4; Re-DRC zur Pruefung.
- Test: Unit gemockt (DRC-JSON-Fixtures); Ablauf live manuell.

### G6 — Session-Menue [UC21]
- `live_session_status` erweitern: DRC offen / Marker offen /
  Selektion, sortiert nach Schwere. Kein neues Tool noetig, falls
  Erweiterung reicht.
- README: `.mcp.json`-Beispiel; CLAUDE.md: IPC-Konventionen
  (Layer, Marker-IDs, Commits, Aufraeumen vor Git-Commit).

## 7. Teststrategie

- Jede Datei unmittelbar nach dem Speichern testen (pytest, Unit,
  kipy gemockt) — Muster aus bestehenden ipc-Tests.
- Live-Integration gegen das laufende KiCad: Platine per
  `ipc_open_kicad` oeffnen, Smoke je G-Gruppe abhaken. Selektion-Lesen
  (G1) und Marker-Sichtbarkeit (G3) brauchen einen kurzen visuellen
  Check; der Rest ist headless gegen die offene Platine pruefbar.

## 8. Risiken (reduziert)

- `GetOpenDocuments`-Handler war im aktuellen Live-KiCad „no handler
  available", solange **kein** .kicad_pcb im PCB-Editor offen ist →
  immer erst Platine oeffnen (`ipc_open_kicad`), dann IPC-Tools.
- Kein Viewport-Zoom/-Pan, keine Live-Screenshots → Nutzer scrollt
  selbst zur Markierung.
- Netz-Highlight (Taste ~) nicht stabil exponiert; Ersatz: Selektion
  aller Netz-Items (G2 via `get_items_by_net`).
- Marker-IDs im `BoardText`-Label sind Konvention, kein Schema — bei
  manuellem Editieren bricht die Zuordnung (`ipc_list_markers` prueft).
- Schwere iFloat-Boards auf OneDrive: kalter Live-Open teuer → fuer
  G-Tests ein kleines Test-PCB oeffnen, nicht das Mainboard.

---

## Anhang A — Session-entdeckte Verbesserungen an bestehenden Tools (2026-06-11)

Beim iFloat-589-Workflow (langer Realeinsatz) aufgetretene Bugs/Luecken an
bestehenden Datei-Tools. Unabhaengig von der IPC-Erweiterung, parallel umsetzbar.
Backlog-Details siehe `Bug.md`.

| # | Tool | Befund | Typ | Status |
|---|------|--------|-----|--------|
| S1 | `compute_pin_world_positions_sch` | Kein `refs`-Filter -> 60-KB-Output sprengt Token-Limit bei jedem Aufruf | Feature | ✅ erledigt |
| S2 | `bulk_swap_symbol` | Refresht den gecachten `lib_symbol` nicht -> alte Pin-Namen, `lib_symbol_mismatch` | Bug | ✅ erledigt (drop+re-embed, project-local `${KIPRJMOD}`-Resolver) |
| S3 | `delete_schematic_items` | Loescht beim Label-Entfernen den von `add_schematic_label` mitgelegten Stub-Wire nicht -> Dangling Wire | Bug | ❌ verworfen — Fehlbefund: `add_schematic_label` legt nur das Label, keinen Wire (kein Bug) |
| S4 | `add_power_symbols` | Pin-on-Pin (Power-Symbol direkt auf Bauteil-Pin) verbindet nicht -> braucht Wire-Stub | Bug | ✅ erledigt — **echte Ursache:** Zwangs-Grid-Snap (1.27mm) schob das Power-Symbol ~0.6mm vom Off-Grid-IC-Pin (0.65/0.5mm-Pitch) weg → `pin_not_connected`. Pin-on-Pin selbst verbindet. Fix: `snap`-Flag (Tool + per-Anchor, Default True) |
| S5 | (fehlt) | Kein `add_no_connect(sch, x, y)`-Tool | Feature | ✅ erledigt (Tool-Count 147→148) |
| S6 | (fehlt) | Kein Symbol-Authoring-Tool (`create_library_symbol` aus Pin-Spec) | Feature | ✅ erledigt — neues Tool + `generators/symbol_author.py`; Rechteck-IC aus Pin-Spec, optional project-local registriert (→ S2-Resolver), `kicad-cli sym upgrade`-validiert. Tool-Count 148→149 |
