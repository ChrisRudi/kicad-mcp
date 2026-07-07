# KiCad-11-Vorbereitung — Features vorab ausarbeiten

Arbeitsdokument: was mit KiCad 11 (erwartet ~Februar 2027, Nightlies = 10.99)
für das MCP **neu machbar** wird, vorab durchgeplant, damit wir am Release-Tag
liefern statt anfangen. Gepflegt wie `docs/superfeatures.md`; jede Ausarbeitung
hier wird bei Umsetzung in einen normalen Release-Zyklus überführt (Konventionen
aus CLAUDE.md gelten unverändert: Tool-Registry, `{success: bool}`,
Batch-Varianten, Effekt-Echo, Tests + CHANGELOG + Version).

## 1. Faktenlage (Stand Juli 2026)

| Baustein | Konfidenz | Quelle |
|---|---|---|
| Headless-Betrieb der IPC-API über `kicad-cli` | **bestätigt** („added for KiCad 11") | kipy-Doku/dev-docs |
| Plot/Export über die API („KiCad 11 and up") | **bestätigt** | kipy-Doku |
| SWIG-`pcbnew`-Bindings werden in 11.0 **entfernt** | **angekündigt** | devlist |
| Schematic-/Library-Editor-API | **in Arbeit, nicht zugesagt** — war schon für 10 geplant und rutschte | devlist, KiCad #2077 |
| Protobuf-Kompatibilität: bestehende Messages ändern ihre Bedeutung nicht; Deprecation ≥ 1 Major | **Policy** | dev-docs |
| Release-Takt: Major bis 31. Januar (FOSDEM), 10.0 rutschte auf März | Policy + Empirie | dev-docs |

Konsequenz aus der Protobuf-Policy: unsere kipy-0.7.x-Aufrufe bleiben
semantisch gültig; **aber** der Handshake (`GetVersion`) und neue Messages
verlangen vermutlich ein kipy-Update. Die Version wird zur Laufzeit erkannt,
nicht zur Importzeit — Feature-Gates im Code (Abschnitt 6), kein zweiter Tree.

## 2. Frühwarnsystem: Nightly-CI-Job ✅ UMGESETZT (0.31.0)

Der **optionale** CI-Job `kicad-nightly` ist da (`.github/workflows/ci.yml`):
wöchentlich (cron `17 4 * * 1`) + `workflow_dispatch`, `continue-on-error:
true`, gegated per `if: schedule||workflow_dispatch` (läuft NICHT bei Push/PR).
Er installiert die Nightly-PPA (best effort), fährt `kicad_mcp.selftest` und
druckt `scripts/kicad_capability_probe.py` in die Job-Summary. Baseline
KiCad 10.0.4: kicad-cli-Subkommandos fp/jobset/pcb/sch/sym/version (kein
api/ipc/serve), kipy Board+Project (KEINE Schematic-API), 85 Proto-Commands.
Der Wochen-Diff dieser Zahlen ist das Trigger-Signal für §3–5.

Original-Plan (Referenz):

- Gleiches Muster wie der `live-ipc`-Job, nur PPA `ppa:kicad/kicad-dev-nightly`
  statt stable; danach `python -m kicad_mcp.selftest`, pcbnew-Suite,
  Live-IPC-Harness.
- Zusätzlich ein **Fähigkeits-Report** (neues Skript `scripts/kicad_capability_probe.py`):
  fragt ab und druckt als Job-Summary, was die installierte Version kann —
  `kicad-cli`-Subkommandos (taucht ein API-/Serve-Modus auf?), kipy-Handshake,
  `ping()` gegen headless gestarteten Prozess, vorhandene Proto-Messages.
  Der Report ist das Trigger-Signal für die Abschnitte 3–5.
- Erwartung managen: der Job wird zeitweise rot sein (Nightlies sind instabil);
  sein Wert ist der Diff von Woche zu Woche.

Aufwand: klein (CI-YAML + Probe-Skript). Kein Produktcode betroffen.

## 3. Headless IPC — die SWIG-Ablösung

**Was neu geht:** ein KiCad ohne GUI/Xvfb beantwortet IPC-Aufrufe. Damit
verschwindet die künstliche Trennung „Datei-Maschinerie (SWIG/Text) vs.
Mitarbeiter-Schicht (IPC, nur mit offener GUI)".

**Betroffene Bestände (Rückbau-Liste — Code-Diät!):**

| Heute | Mit v11 | Gewinn |
|---|---|---|
| `tools/_warm_daemon.py` + Worker (`pcb_eval`, `check_connectivity`, `via_promote`) auf SWIG-`pcbnew` | derselbe Daemon-Rahmen, aber Worker sprechen IPC gegen einen headless KiCad-Prozess | SWIG-Abhängigkeit weg; ein Codepfad für „Board offen" und „Board zu" |
| `tests/live_ipc_harness.py` (Xvfb + xdotool + Welcome-Dialog wegklicken) | headless `kicad-cli`-Start, kein Display | CI-Job schrumpft massiv, Flakiness-Quelle weg |
| `utils/board_open_guard.py` / `BoardOpenError` (Text-Patch vs. offenes Board) | bleibt für die GUI, aber der Standard-Mutationspfad kann IMMER IPC sein | weniger Sonderfälle in Tool-Docstrings |
| `kicad_mcp/selftest.py` pcbnew-Pfade | IPC-Pfade | ein Selftest für beide Welten |

**Vorarbeit heute (ohne v11 sinnvoll):**
1. **Worker-Interface schmal ziehen:** die drei Warm-Worker greifen `BOARD`
   direkt an. Eine dünne Adapter-Schicht (`utils/board_backend.py`: `load()`,
   `fill_zones()`, `connectivity()`, `eval(expr)`) mit SWIG-Implementierung
   heute und IPC-Implementierung später macht die Migration zum Austausch
   EINER Klasse. (Synergie-Regel: Adapter auch für `ipc_tools` nutzen.)
2. **kipy-Nutzung zentral halten:** läuft bereits über `utils/ipc_session.py`
   — jede neue kipy-Fläche NUR dort andocken.

**Gates:** bestehende pcbnew-Suite muss unverändert unter dem IPC-Backend
bestehen (das ist die Messlatte, nicht neue Tests).

## 4. Plot/Export über die API — Render des LEBENDEN Boards

**Was neu geht:** Screenshot/Plot direkt aus dem laufenden (auch ungespeicherten)
Editor-Zustand, ohne Umweg über Datei + `kicad-cli export svg`.

**Feature-Ausarbeitung `pcb_render` v2:**
- Neue Quelle `source="live"` (Default bleibt `file`): rendert, was der Nutzer
  JETZT sieht — inklusive ungespeicherter Agent-/Nutzer-Änderungen. Damit wird
  der Live-Diff (`ipc_live_tools`) endlich **zeigbar**: „das habe ich geändert"
  als Vorher/Nachher-Bild.
- Region-Rendering (bbox um Selektion aus `ipc_get_selection`) bleibt — die
  Agent-Verhaltensregeln (Render nur am Abschluss!) gelten unverändert und
  werden in die Tool-Description übernommen.
- 3D-PDF-Export (seit 10.0) und API-Plot zusammen ergeben ein neues Tool
  `export_board_view(kind="png|svg|pdf3d", region=...)` — EIN Tool statt
  Format-Zoo, Registrierung wie üblich über `tool_registry`.

**Vorarbeit heute:** `pcb_render`-Aufrufer auf eine interne
`render_backend(path|live, region)`-Signatur umstellen, sodass v2 nur ein
Backend hinzufügt.

## 5. Schematic-API (FALLS sie in 11 landet — Konfidenz niedrig)

Das größte Loch schließt sich: Eeschema kann heute weder Save noch Selektion
noch Mutation über IPC (KiCad #2077); unsere Schaltplan-Schiene ist deshalb
reine Text-Patcherei.

**Dann machbar, vorab ausgearbeitet:**
- **`ipc_sch_get_selection`** — der (a)-Vertrag der Super-Features („wirkt auf
  aktuelle Selektion") endlich auch im Schaltplan: semantischer ERC, Datasheet-
  Review, Circuit-Block-Einfügung an der Cursor-Stelle.
- **Live-Schaltplan-Mutation** (`ipc_sch_add_wire/label/symbol`): der
  Schaltplan-Patcher (`sch_patch_tools`) bekommt den gleichen Zwilling wie die
  PCB-Seite (`pcb_patch_tools` ↔ `ipc_tools`). Der Generator bleibt Text
  (Batch-Erzeugung ist auf Datei schneller); Live ist für inkrementelle Edits
  am offenen Plan.
- **Eeschema-`BoardOpenError`-Ausnahme fällt:** heute dürfen wir Schaltpläne
  trotz offener GUI patchen (kein IPC-Save = keine Kollisionsvermeidung
  möglich). Mit Schematic-API drehen wir das auf das PCB-Modell: offen ⇒ IPC.
- **Live-ERC** (`ipc_run_erc` entstubben), Cross-Probe Schaltplan↔Board in
  beide Richtungen.

**Vorarbeit heute:** keine — zu unsicher. Nur beobachten (Probe-Skript meldet,
sobald Eeschema-Messages im Proto auftauchen).

## 6. Versions-Koexistenz (10 und 11 parallel bedienen)

Nutzer werden gemischt auf 10.x und 11.x sitzen; das Plugin shipped für beide.

- **Ein Feature-Gate, zentral:** `utils/ipc_session.get_capabilities()` →
  gecachtes Dict (`headless`, `api_plot`, `sch_api`, kipy-/KiCad-Version),
  gespeist aus dem Handshake. Tools fragen Fähigkeiten ab, nie Versionsnummern
  (Muster wie `transport_mode()`).
- **kipy-Doppelversion vermeiden:** erst auf das kipy-Release gehen, das 10
  UND 11 spricht (Protobuf-Policy macht das wahrscheinlich); sonst Fähigkeits-
  Degradation statt Import-Fehler.
- **Dateiformat:** 11er-Dateien nicht mit 10er-Tools „reparieren" — Parser
  müssen unbekannte Tokens weiterhin unangetastet durchreichen (Kontrakt der
  Text-Patcher; Wächter-Test mit einem 10.99-Beispielboard, sobald der
  Nightly-Job läuft).
- **SWIG-Wegfall in 11:** `import pcbnew` schlägt dort fehl → alle
  SWIG-Verbraucher müssen VOR dem Umstieg hinter dem Backend-Adapter
  (Abschnitt 3) liegen, sonst verlieren 11er-Nutzer `pcb_eval` & Co. ersatzlos.

## 7. Reihenfolge & Trigger

| # | Schritt | Trigger | Aufwand |
|---|---|---|---|
| 1 | Nightly-CI-Job + Capability-Probe | sofort | S |
| 2 | `board_backend`-Adapter unter die Warm-Worker | sofort (reine Refaktur, Code-Diät) | M |
| 3 | `render_backend`-Signatur unter `pcb_render` | sofort | S |
| 4 | kipy-Update + `get_capabilities()` | kipy mit 11-Support erscheint | S |
| 5 | IPC-Backend für Warm-Worker + headless Test-Harness | Probe meldet headless-IPC in 10.99 | L |
| 6 | `pcb_render source="live"` + `export_board_view` | Probe meldet API-Plot | M |
| 7 | Schematic-Schiene (Abschnitt 5) | Probe meldet Eeschema-API | L |
| 8 | Rückbau: Xvfb-Harness, SWIG-Pfade, Doppel-Sonderfälle | 11.0 stable + Nutzerbasis migriert | M |

Schritte 1–3 sind reguläre Arbeit ab jetzt; 4–7 sind fertig durchdacht und
warten auf ihren Trigger; 8 ist die Belohnung (weniger Code, ein Codepfad).

## 8. Risiken

- **Terminrutsch** (10.0 kam 2 Monate zu spät) → nichts an v11-Verfügbarkeit
  koppeln, was Nutzer heute brauchen.
- **Nightly-Instabilität**: der Nightly-Job ist Frühwarnung, nie Gate.
- **Schematic-API rutscht erneut** → Abschnitt 5 bewusst ohne Vorarbeit.
- **kipy-Versionssprung** bricht 10er-Installationen → Capability-Gate statt
  Versions-Pinning, Plugin-Updater erst umstellen, wenn beide bedient sind.
