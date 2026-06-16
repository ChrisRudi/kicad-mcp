# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once
the first tag ships.

## [Unreleased]

### Added
- **v0.2.33: Live-Kollaboration — Compare-and-Swap gegen Clobber von
  User-Edits.** Bei offenem Board ist KiCads In-Memory-Modell die einzige
  Wahrheit (Disk-Patches sind geblockt → nur KiCad schreibt die Datei, kein
  Zwei-Prozess-Race). Offen blieb der Modell-Race: Agent-IPC-Move vs. paralleler
  User-Drag am selben Footprint (per-Item last-write-wins). `live_move_footprint`
  bekommt jetzt optimistic concurrency: `dry_run` liefert die `sig` des Ziels;
  beim realen Write wird gegen diese Baseline (Param `expect_sig`, sonst der
  letzte Live-Snapshot) re-geprüft — hat der User das Footprint seit dem Plan
  bewegt (und ist es kein Agent-Self-Write), wird der Write VERWEIGERT
  (`{success: False, conflict: True, who: "user", baseline_sig, current_sig}`)
  statt zu überschreiben. Reine, getestete Entscheidungsfunktion
  `ipc_live_diff.cas_conflict` (+ `_sig_eq`, JSON-int/float-tolerant);
  `agent:`-Commits bleiben als Undo-Netz, der User besitzt Ctrl+S. Neuer Test
  `TestCasConflict` (kein/unverändert/User-bewegt/Self-Write/JSON-Drift).

### Fixed
- **Plugin v0.2.32: Chat-Links — die echte „kein Link"-Ursache ist eine
  KiCad-MEHRFACHINSTANZ, nicht board_links.** Gegen das laufende KiCad 10.0.1 +
  kipy 0.7.1 verifiziert: `board_links.py` ist korrekt — `board_targets`
  liefert refs/nets/layers voll, `select_pin U1B.33` selektiert (definition-pads
  tragen echte Board-KIIDs), `tokenize` linkt alle Typen, und parallele
  kipy-Clients (MCP + Panel) stören sich nicht. Der reproduzierte Ausfall:
  laufen ZWEI KiCad-Instanzen auf einem IPC-Socket, ist `GetOpenDocuments`
  ohne Handler → `connect()` warf einen kryptischen `ApiError` → „ⓘ Links aus:
  …" ohne Handlungsanweisung → gar kein Link. Fix: `connect()` erkennt diesen
  Zustand und wirft `BoardUnavailable` mit klarer Anweisung („zusätzliche
  KiCad-Fenster schließen, genau EIN Board offen"), die der Chat verbatim
  anzeigt. Die Unit-Mocks decken sich nachweislich mit der realen kipy-API
  (deshalb waren sie „grün"); neuer Test `TestConnectDiagnostics` sichert die
  Diagnose ab.
- **Plugin v0.2.31: Deps-Installation läuft jetzt ganz ohne cmd/Batch (direkter
  Subprozess) — der robusteste Umlaut-Fix.** Der Env-Variablen-Weg aus v0.2.29
  funktioniert, hängt aber weiter an cmd.exe. Sauberer: `_install_deps` ruft pip nun
  als argv-**Liste** direkt über `subprocess.Popen` (kein Shell-String, kein `.bat`)
  — Windows reicht den Unicode-Pfad über `CreateProcessW` unverfälscht durch, sodass
  ein `ü` strukturell nicht mehr gefaltet werden kann. Die Ausgabe streamt live in
  einen Plugin-Dialog (`CREATE_NO_WINDOW`, kein blitzendes Konsolenfenster), inkl.
  abschließender Import-Verifikation. Neue Helfer `deps.pip_install_argv` /
  `deps.verify_import_argv` (headless getestet). Der terminal-basierte Pfad
  (`pip_install_commands` + `pip_install_env`, `%KICAD_MCP_DEPS%`) bleibt als Legacy
  bestehen.
- **Plugin v0.2.30: Installierte MCP-Abhängigkeiten wurden nach erfolgreicher Installation
  als „fehlt" gemeldet (Endlos-Neuinstallation).** Symptom: `_deps` voll befüllt, Server-Probe
  `OK (167 Tools)`, aber die Checkliste blieb rot → der Nutzer installierte immer wieder neu.
  Ursache: `deps.check_deps` legte den `_deps`-Ordner nur über die Env-Variable `PYTHONPATH`
  auf den Suchpfad — KiCads gebündeltes Python **ignoriert PYTHONPATH** (isolierter
  `._pth`-Build). Der `find_spec`-Probe lief also ohne `_deps` auf `sys.path` und meldete alle
  Module als fehlend. Der Rest des Codes (Server-Start, Install-Verifikation, `start_mcp.bat`)
  injiziert `sys.path` längst **in-process**; nur die Check-Probe tat es nicht. Fix:
  `build_check_cmd(kicad_py, deps_dir)` injiziert `sys.path[:0]=[deps_dir]` im `-c`-Code —
  identisch zu `mcp_config.server_bootstrap_code`, sodass der Check mit dem realen Server-Start
  übereinstimmt. Headless getestet (`test_plugin_deps.py`).
- **Plugin v0.2.29: Umlaut-Pfad-Fix endgültig (Benutzername „üser") — Pfad reist jetzt
  über die Environment-Variable, nicht über den Batch-Text.** Trotz v0.2.28 (UTF-8-Batch +
  `chcp 65001`) brach die Deps-Installation weiter mit `C:\Users\Sch?ler\…` →
  `WinError 123` ab: `chcp 65001` macht cmd.exe **nicht** zuverlässig dazu, ein im
  Batch-Text stehendes `ü` korrekt an den Kindprozess (pip) durchzureichen — es wird beim
  Parsen über die Konsolen-Codepage zu `?` gefaltet. Robuster Fix: Der (möglicherweise
  nicht-ASCII-)Zielpfad steht **nicht mehr als Literal im `.bat`**, sondern wird über die
  Umgebungsvariable `%KICAD_MCP_DEPS%` getragen (Windows übergibt den Environment-Block als
  UTF-16 → codepage-immun) und im Batch nur referenziert; das Arbeitsverzeichnis ebenso über
  `%KICAD_MCP_CWD%`. Der Batch-Text bleibt reines ASCII. POSIX nutzt unverändert den
  Literal-Pfad (UTF-8-Shell, keine Verstümmelung). Headless getestet
  (`test_plugin_terminal.py`, `test_plugin_deps.py`).
- **Plugin v0.2.28: Deps-Installation scheitert bei Umlaut im Windows-Benutzernamen.** Bei
  einem Benutzer wie „üser" wurde der `_deps`-Zielpfad `C:\Users\üser\…` zu
  `C:\Users\Sch?ler\…` verstümmelt (`?` = ungültiges Windows-Pfadzeichen) → pip-`makedirs`
  bricht mit `WinError 123` ab. Erster Anlauf: Batch als **UTF-8 (ohne BOM)** schreiben statt
  `ascii`/`errors="replace"` — verbesserte das Schreiben, der cmd-Round-Trip mangelte den
  Pfad aber weiterhin (siehe v0.2.29 für die endgültige Lösung).

### Added
- **KiCad-PCM-Paket: „Aus Datei installieren" möglich (`make_pcm_zip.py`).** GitHubs
  automatische Repo-ZIP ist KEIN gültiges KiCad-Add-on (sie verpackt das ganze Repo in
  einen `<repo>-<branch>/`-Ordner). Das neue Skript baut die **PCM-konforme** ZIP
  (`metadata.json` an der Wurzel + `plugins/` mit dem Plugin **inkl. gebündeltem
  mcp/-Server** + `resources/icon.png`), die KiCads Plugin and Content Manager über „Aus
  Datei installieren…" akzeptiert. Version automatisch aus `plugin/version.py`; eine
  GitHub-Action (`.github/workflows/pcm-zip.yml`) baut die ZIP bei jedem Release und hängt
  sie als Asset an. README um den PCM-Weg ergänzt.
- **Footprint-Resync-Tools (3 neue MCP-Tools, headless GUI-F8-Äquivalent, Branch
  `feat/footprint-resync`).** Behebt Footprint-Defekte ohne die SWIG-Flip-Bugs:
  `normalize_footprint_libid` (bare lib_id `"NAME"`→`"Lib:NAME"` aus dem Schaltplan,
  reiner Text-Patch, idempotent + Namens-Guard), `refresh_pinfunctions` (stale Pad-
  `(pinfunction …)` aus den Symbol-Pinnamen, Text-Patch, beide Net-Token-Formen, keine
  Geometrie/Netze) und `replace_footprint_canonical` (Footprint-Ersatz flip-/placement-
  korrekt über echte pcbnew-Engine im Subprozess; Pad-Drift-Gate <1 µm vor dem Commit,
  `SaveBoard`=Voll-Rewrite → dry_run-Default + Board-offen-Guard + fp-lib-table-Auflösung).
  Gemeinsamer `utils/sch_inspect.py`-Parser (ref→Footprint, ref→Pin-Namen). Tool-Count
  167 → 170. Headless getestet (`tests/test_footprint_resync.py`); pcbnew-Swap ist
  KiCad-only.

### Changed
- **Plugin v0.2.27: Link-Fehler werden sichtbar (Diagnose der „keine Links"-Regression).**
  Das Holen von Refs/Netzen/Layern fürs Linkifizieren wurde bei Fehler von einem
  `except: pass` **stillschweigend verschluckt** — „keine Links" war so nicht
  diagnostizierbar. Jetzt zeigt das Panel den echten Grund als dezente Zeile
  („ⓘ Links aus: <Fehler>") bzw. „0 Refs/Netze/Layer gelesen", wenn die Verbindung klappt
  aber nichts zurückkommt. Render-/Klick-Logik ist seit 0.2.21 unverändert, Link-Logik
  getestet — der Fehler liegt in der Laufzeit-Verbindung zu KiCad und war bisher unsichtbar.

### Fixed
- **Plugin v0.2.26: Chat-Links (Refs/Netze/Pins/Layer/Koordinaten) wieder funktionsfähig —
  Nebeneffekt des MCP-Fixes behoben.** Die Links waren nie im Code kaputt, aber
  `board_links.connect()` verband sich mit kipys **2-s-Default-Timeout und ohne Retry**.
  Solange der MCP „failed" war, hatte das Panel KiCads IPC für sich allein → Links gingen.
  Seit der MCP korrekt verbunden ist, belegt der Server die IPC-Leitung, und die zweite
  Verbindung des Panels lief in „KiCad is busy"/Timeout → stillschweigend verschluckt →
  keine Links. Fix: `connect()` nutzt jetzt **15 s Timeout**, und alle Live-kipy-Aufrufe
  (`board_targets` + alle `select_*`/`set_active_layer`) laufen über einen neuen
  **`call()`-Busy-Retry** (exponentieller Backoff) — genau wie der Server-Session-Layer aus
  Task A, nur plugin-seitig. Headless getestet (Busy-then-success).
- **Plugin v0.2.25: „MCP nicht verbunden (failed)" — Ursache gefunden + behoben.** Die
  Diagnose bewies: der Server startet sauber (initialize + tools/list mit 167 Tools in ~2 s
  warm). Der Fehler ist ein **Kaltstart-Timeout-Rennen**: Claudes MCP-Start-Timeout ist
  default nur 30 s, und der allererste Start auf Windows (pandas/numpy/pywin32 + 167 Tools
  aus dem frisch geschriebenen `_deps`, jede `.pyd` von Windows Defender gescannt) kann das
  überschreiten → der Server wird still als „failed" verworfen. Fix: Timeout großzügig auf
  **300000 ms** angehoben — auf BEIDEN Wegen (`MCP_TIMEOUT`-Env in `claude_bridge` UND das
  per-Server-`timeout`-Feld in der MCP-Config), plus `PYTHONUNBUFFERED=1`. Die Server-Probe
  testet jetzt auch **tools/list** (lief im selben Timeout-Fenster, wurde bisher nicht
  geprüft → Probe war zu nachsichtig) und **misst die Zeit**; die Diagnose zeigt sie an und
  weist bei langem Kaltstart auf den Defender-Ausschluss von `_deps`/`mcp` hin. Headless
  getestet.
- **Plugin v0.2.24 (neu durchdacht): die entgleiste Session an der Wurzel gefixt.** Vier
  Ursachen konsolidiert behoben: (1) **Tool-Sperre war wirkungslos** — `--disallowedTools`
  bekam einen komma-verketteten String, der **kein** Tool matcht; daher liefen
  `Write`/`PowerShell` trotzdem. Jetzt **ein Tool-Name pro argv-Wert**, plus `PowerShell`
  (Windows-Shell ohne Git-for-Windows). Deny wirkt auch unter
  `--dangerously-skip-permissions`. (2) **Agent-Regeln erreichten den Agenten nie** —
  `claude -p` lädt CLAUDE.md aus dem cwd (Board-Ordner), nicht aus dem Repo; Kernregeln
  jetzt per `--append-system-prompt` pro Turn, inkl. der entscheidenden Regel „fehlen die
  MCP-Tools: in einem Satz sagen und aufhören — nicht raten/per Shell behelfen". (3)
  **Runaway-Bremse** `--max-turns` (Default 80, `KICAD_MCP_MAX_TURNS`, 0 = aus). (4) **Limit
  graziös** — wird das Schritt-Limit erreicht, kommt eine klare Meldung („Schritt-Limit (80)
  erreicht …") statt eines kryptischen Fehlers. (Ersetzt den vorherigen, hastigen v0.2.24,
  der zuvor per Revert zurückgenommen wurde.) Headless getestet.

### Added
- **Batch-Tool `add_vias_to_pcb` (gegen Toolcall-Explosion, Prio 3).** Setzt N Vias in EINEM
  Read+Write statt N Einzel-Calls — der dokumentierte 24-Via-Fall. Atomar (ungültige Spec →
  nichts geschrieben, `failed_index` gemeldet), nimmt Liste oder JSON-String, `dry_run`.
  **Effekt-Echo** im Result (`count` + Per-Via-Liste), damit kein Rücklesen nötig ist; die
  Description sagt explizit „Rendert nicht — `pcb_render` separat nach Abschluss". `add_via_to_pcb`
  verweist jetzt auf die Batch-Variante und trägt denselben Render-Hinweis. „set_properties" ist
  bereits durch `bulk_set_property` abgedeckt, Moves laufen über `pcb_batch` — daher keine
  redundanten Plural-Tools. Tool-Count 166 → 167. Headless getestet.
- **Plugin v0.2.23: Bauteil-Pins im Chat klickbar (`U1B.33`).** Die Klick-Mechanik
  (Refs/Netze/Layer/Koordinaten) deckt jetzt auch **Pins** ab: nennt Claude `U1B.33`
  (Footprint U1B, Pin 33), wird das ein Link; ein Klick **selektiert + zoomt den Pad** im
  Editor (Auswahl über die Pad-Board-ID via `fp.definition.pads`, Position egal). Verlinkt
  nur, wenn die Referenz wirklich am Board existiert; der `<ref>.<pin>`-Span hat Vorrang vor
  dem bloßen Ref-Link (kein „U1B" + „.33"-Zerfall). Auch alphanumerische Pin-Namen (`J3.A1`).
  Reine Tokenizer-/Select-Logik in `plugin/board_links.py` (`_pin_matches`, `select_pin`),
  headless getestet.
- **Plugin v0.2.22: Stopp-Knopf, Claude-Optionen, Tool-Calls im Chat.** Drei Chat-UX-
  Lücken geschlossen: (1) **Stopp** — während Claude denkt (Eingabe gesperrt) erscheint statt
  „Senden" ein roter „Stopp"-Knopf, der den laufenden Turn samt MCP-Kindprozess sofort killt
  (`claude_bridge.stop`); Ergebnis „⏹ Abgebrochen". (2) **Claude-Code-Switches** — ein
  Optionen-Feld („⚑ …, z. B. `--model sonnet`") wird shlex-geparst und an jeden Turn-Befehl
  angehängt (`build_command(extra_args=…)`). (3) **Tool-Calls sichtbar** — jeder gestreamte
  Tool-Aufruf erscheint live als gedimmte `⚙ <name>`-Zeile im Verlauf (neue `tool_names()`
  + `on_tool`-Callback), nicht mehr nur in der Statuszeile. `ask()` reicht zusätzlich den
  Live-Prozess via `on_proc` an das Panel (für Stopp). Headless getestet
  (`tests/test_plugin_bridge.py`).
- **Plugin v0.2.21: Layer-Namen im Chat sind klickbar (Task D).** Erwähnt Claude einen
  Layer (`F.Cu`, `In1.Cu`, `User.9`, …), wird er im Panel zum Link; ein Klick setzt den
  **aktiven Layer** im PCB-Editor (`board.set_active_layer`, verifiziert in kipy 10). Client/
  Renderer ist das wx-Chat-Panel des Plugins (kein Markdown/HTML), daher dieselbe Mechanik
  wie Refs/Netze/Koordinaten: verlinkt werden nur Layer, die wirklich am Board **aktiviert**
  sind (`get_enabled_layers` → kanonischer Name via `BoardLayer`-Enum), kein Fehltreffer.
  Reine Tokenizer-/Resolver-Logik in `plugin/board_links.py` (`set_active_layer`,
  `_enum_to_canonical`/`_canonical_to_enum`), headless getestet. `board_targets` liefert nun
  zusätzlich die Layer-Menge (3-Tupel).

### Changed
- **`ipc_get_selection` fängt den „KiCad is busy"-Bug ab (Task C).** Die GUI-Selektion auf
  Sprachtrigger („aktuelle Auswahl", „was ist hier selektiert") wird schon mit
  Refdes/Typ/Layer/Position (mm) und „Nichts selektiert"-Note zurückgegeben — neu ist, dass
  der bekannte kipy-Bug (Einzelselektion mancher Primitive → „KiCad is busy and cannot
  respond") jetzt über den zentralen Retry/Backoff aus Task A (`ipc_session.call_with_retry`)
  abgefangen statt als Fehler durchgereicht wird; nach erschöpften Versuchen klare Meldung.
  Signatur unverändert. Headless getestet.

### Added
- **Markup→Kupfer-Tool `ipc_markup_to_tracks` (Task B).** Der User skizziert Routing als
  einfache Grafik-Linien/Arcs auf einem Markup-Layer (Default `User.9`); das Tool liest die
  Geometrie live über IPC und legt äquivalente Kupfer-**Tracks** (Track/ArcTrack) auf einen
  Ziel-Kupferlayer. Quell-/Ziellayer + Breite (mm) sind Parameter (nichts hardcoded außer
  dem `User.9`-Default), die erzeugten Tracks sind **netlos**. Geschlossene Polygone/Kreise
  werden bewusst übersprungen (Zonen = separater Schritt). Alles in einem
  `begin_commit`/`push_commit` → **ein** Undo-Schritt; Koordinaten bleiben durchgängig nm
  (int), einzige Konversion ist `width_mm` an der Eingabe-Grenze; `dry_run` zählt nur.
  Tool-Count 165 → 166. Headless getestet (`tests/test_ipc_markup_tools.py`).
- **Zentraler IPC-Session-Layer (`utils/ipc_session.py`) — Connection-Robustheit + Speed
  (Task A).** Behebt „MCP nicht verbunden (failed)" auf großen Boards und die Per-Call-
  Reconnect-Latenz. (1) **Wiederverwendeter Client:** `get_client()` hält prozessweit eine
  IPC-Verbindung, die `_connect_kicad()` (Hot-Path fast aller Read/Edit-Tools) jetzt nutzt
  statt pro Tool-Call neu zu verbinden — größter Speed-Hebel; Reconnect-on-stale inklusive.
  (2) **Konfigurierbarer Timeout:** `KICAD_MCP_IPC_TIMEOUT_MS` (Default **15000 ms** statt
  kipys 2000 ms); alle 12 Inline-`KiCad()`-Stellen in `ipc_tools.py` bekommen den zentralen
  Timeout. (3) **Busy-Retry:** `call_with_retry` fängt „KiCad is busy and cannot respond"
  mit exponentiellem Backoff ab und reconnectet einmal bei abgerissener Verbindung.
  (4) **File-Logging** neben dem offenen `.kicad_pcb` (`kicad_mcp_ipc.log`, Fallback
  `tempfile.gettempdir()`): Connect/Reconnect, Timeouts, Busy-Retries, Call-Dauer — da
  stdout/stderr beim Plugin-Launch unsichtbar sind. (5) **Klare Fehlermeldungen** an den
  MCP-Client statt nur „failed". Wait-/Restart-Loops nutzen `new_client()` (frisch, gleicher
  Timeout — kein stale-Cache). Headless getestet (`tests/test_ipc_session.py`), kipy lazy.

### Fixed
- **Plugin v0.2.20: kein verwaister Claude/MCP-Prozess mehr, wenn KiCad geschlossen wird.**
  `claude -p` (+ sein MCP-Kindprozess) wird aus KiCad heraus gestartet; unter Windows
  beendet das Schließen von KiCad die Kindprozesse **nicht** automatisch — bei einem
  KiCad-Schluss *während* einer laufenden Anfrage konnten sie verwaisen. Jetzt wird jeder
  laufende Turn registriert (`claude_bridge._register`) und beim Schließen des Chat-Panels
  sowie via `atexit` beim KiCad-Beenden **inklusive Kindprozessen** abgeräumt
  (`terminate_all` → `_kill_tree`: Windows `taskkill /F /T`, POSIX `killpg` dank
  `start_new_session`). Zwischen den Anfragen war ohnehin nichts offen — `claude -p` ist ein
  Einmal-Aufruf, der seinen MCP-Server beim Beenden mitnimmt.

### Added
- **Disk-Write-Guard fürs gemeinsame Arbeiten (Plugin v0.2.19).** Beim gleichzeitigen
  Arbeiten (du in KiCad, der Agent über MCP) blockiert der Server jetzt Direkt-Patches auf
  eine `.kicad_pcb`, die in der KiCad-GUI **offen** ist (`utils/board_open_guard.py` →
  `BoardOpenError`). Grund: Ein Platten-Patch ist für den laufenden Editor unsichtbar, das
  nächste Strg+S überschreibt ihn (oder umgekehrt) — ein echtes Zwei-Seiten-Datei-Locking
  gibt es nicht. Stattdessen ist der **IPC-Live-Pfad** der Locking-Mechanismus: `ipc_*` /
  `live_*` ändern KiCads In-Memory-Modell (eine Wahrheit), **alle Fenster bleiben offen und
  beide Seiten speichern kohärent**. Zentraler Chokepoint: neue `cache/file_cache.write_text`
  (Guard + Schreiben + Cache) ersetzt die 23 `open()+put_text`-Paare im PCB-Text-Patcher.
  Headless (KiCad zu / kein `KICAD_API_SOCKET`) unverändert; Erkennung nur bei erreichbarer
  GUI, Client negativ-gecacht (kurze Zugriffszeit). Override:
  `KICAD_MCP_ALLOW_DISK_WRITE_WHILE_OPEN=1`. **Schaltpläne sind ausgenommen** — Eeschema hat
  in KiCad 10 keinen IPC-Save, daher bleibt der Text-Patcher dort der Weg.
- **Plugin v0.2.18: auch Koordinaten im Chat sind anklickbar.** Gibt Claude eine Stelle als
  Koordinatenpaar an (`(120.5, 84.0)`, auch mit `mm` / negativ), wird das im Panel zum Link;
  ein Klick **selektiert das nächstgelegene Board-Element (Footprint/Via/Pad) an dieser
  Stelle und zoomt darauf** (KiCad hat keine „Ansicht auf Punkt zentrieren"-API, deshalb
  dient das nächste Element als Anker; Treffer nur innerhalb 8 mm, sonst Statusmeldung
  „kein Element in der Nähe"). Nur Paare in Klammern werden verlinkt (kein Fehltreffer bei
  Kommas im Fließtext). Erkennung + Anker-Suche rein in `plugin/board_links.py`
  (`select_coord`), headless getestet.
- **Plugin v0.2.17: anklickbare Board-Elemente im Chat (Cross-Probe).** Footprint-
  Referenzen (`R12`, `U8`) und Netznamen (`GND`), die Claude in einer Antwort nennt,
  werden im Panel als orange unterstrichene Links dargestellt; ein Klick **selektiert das
  Element im laufenden PCB-Editor und zoomt darauf** (native Auswahl + best-effort
  `zoomFitSelection`) — löst das „auf einer großen, viellagigen Platine finde ich das
  Teil nicht"-Problem. Verlinkt werden nur Tokens, die wirklich auf dem Board existieren
  (Refs/Netze werden je Antwort frisch über IPC geholt), daher keine toten Links und keine
  Substring-Fehltreffer (`R1` matcht nicht in `R12`/`R1_OUT`). Reine Tokenizer-/Select-
  Logik in `plugin/board_links.py` (headless getestet), das wx-Panel hängt nur Klick +
  Styling dran. Auswahl/Zoom laufen direkt über kipy aus dem Panel — kein Claude-Turn nötig.

### Changed
- **Plugin v0.2.16: Streaming statt 300-s-Fallbeil.** Der Chat konsumiert `claude -p`
  jetzt als `stream-json` (mit `--verbose`): Die Statuszeile zeigt **live**, was gerade
  passiert („✻ Claude denkt nach … (45s) · Tool list_pcb_footprints …"), und abgebrochen
  wird nur noch bei **Inaktivität** (180 s ohne Stream-Event; Sicherheitsdeckel 30 min)
  statt nach starren 300 s Gesamtzeit — ehrliche lange Board-Arbeit (OneDrive-Kaltreads
  ~80 s/Datei) überlebt damit. Bonus: Das Init-Event verrät den **MCP-Verbindungsstatus**
  pro Turn; ist der Server nicht verbunden, zeigt das Panel das jetzt als rote Zeile statt
  stillschweigend ohne Board-Tools zu antworten. Idle-Abbrüche nennen die häufigsten
  Ursachen (Projekt-Trust, `claude login`).

### Fixed
- **Plugin v0.2.15 — ROOT CAUSE „MCP läuft nicht": KiCads Python ignoriert `PYTHONPATH`.**
  Experimentell bestätigt auf der betroffenen Maschine: `set PYTHONPATH=…` +
  `python -m kicad_mcp.server` → „No module named 'kicad_mcp'" trotz korrektem Pfad
  (isolierter `._pth`-Build). Deshalb fand Claudes MCP-Start den Server nie, während
  Installation (pip) und Verifikation (in-process `sys.path.insert`) funktionierten.
  Der Server wird jetzt überall per `-c`-Bootstrap gestartet, der `sys.path` **im
  Prozess** setzt (`mcp_config.server_bootstrap_code`): MCP-Config (`args: ["-c", …]`),
  Server-Probe und das Diagnose-Rezept. `PYTHONPATH` bleibt nur noch als Hosenträger
  für Pythons, die ihn beachten. Damit ist die gesamte Fehlerklasse
  „env-var-abhängiger Start" beseitigt.

### Added
- **Plugin v0.2.14: Diagnose-Button.** Nach mehreren Debug-Runden über abgetippte
  Einzelzeilen sammelt ein Klick im Einrichtungs-Panel jetzt ALLES in einen kopierbaren
  Report (`plugin/diagnose.py`, headless getestet): Plugin-/Projekt-/`mcp_root`-/`_deps`-
  Pfade samt Ordnerinhalt, KiCad-Python + Version, Claude + Version, Env-Overrides
  (`KICAD_MCP_ROOT`/`KICAD_PYTHON_PATH`), das Ergebnis der echten Server-Probe mit
  **vollem** Stderr-Traceback (`probe_server` liefert jetzt auch `stderr` ungekürzt)
  und ein Copy-Paste-Rezept, um den Serverstart manuell in `cmd.exe` nachzustellen.
  Der Report wird zusätzlich als `kicad_claude_diagnose.txt` ins Temp-Verzeichnis
  geschrieben; „Alles kopieren"-Knopf inklusive.

### Fixed
- **Plugin v0.2.13:** Das Deps-Install-Terminal zeigt jetzt auch den `_deps`-Zielordner
  („Ziel-Ordner (_deps): …") — damit sind alle an der Diagnose beteiligten Pfade direkt
  im Terminal-Output ablesbar.
- **Plugin v0.2.12: „Error while finding module" präzise diagnostiziert.** Diese Meldung
  heißt: das `kicad_mcp`-Paket selbst fehlt unter dem `mcp_root` (unvollständige
  Plugin-Installation) — nicht fehlende Abhängigkeiten. Die Server-Probe prüft das jetzt
  vor dem Start und meldet den konkreten fehlenden Pfad samt Abhilfe („Update prüfen"
  lädt den `mcp/`-Ordner neu); jeder andere Probe-Fehler zeigt zusätzlich den verwendeten
  `PYTHONPATH` in der roten Zeile. `_mcp_root()` fällt außerdem nicht mehr auf einen
  nicht existierenden Dev-Pfad zurück, sondern auf den gebündelten `mcp/`-Pfad — damit
  zeigen Fehlermeldungen immer auf das erwartete Verzeichnis.
- **Plugin v0.2.11: Claude darf im Board-Chat keine Dateien mehr schreiben.** Ohne
  verbundenen MCP hat Claude Fragen „hilfsbereit" beantwortet, indem es Projektdateien
  (`.kicad_pcb`/`.kicad_sch`/`.kicad_pro`) mit seinen eingebauten Tools direkt editierte —
  KiCad sah externe Änderungen an offenen Dokumenten und meldete beim Öffnen/Schließen
  dauerhaft „ungespeicherte Änderungen". Jeder `claude -p`-Aufruf läuft jetzt mit
  `--disallowedTools Bash,Edit,Write,MultiEdit,NotebookEdit`: Mutationen gehen
  ausschließlich über die MCP-Tools (die Flip/Rotation/Netz korrekt rechnen), Lesen
  (Read/Grep/Glob) bleibt erlaubt.
- **Plugin v0.2.11: Server-Probe ist jetzt eine echte Generalprobe (MCP-Handshake).**
  Die Import-Probe reichte im Feld nicht („alles installiert, MCP läuft trotzdem nicht"):
  Module können importierbar sein und der Server trotzdem beim Start sterben. Die Probe
  startet den Server jetzt exakt wie Claude (`python -m kicad_mcp.server`, gleiche
  `PYTHONPATH`) und verlangt die Antwort auf ein echtes MCP-`initialize` über stdio —
  antwortet er der Probe, antwortet er auch Claude. Bei Fehlschlag zeigt die rote
  Preflight-Zeile den echten Stderr-Traceback (Timeout 120 s für den Kaltstart).
- **Plugin v0.2.10: Deps-Installation ist jetzt selbst-diagnostizierend.** Das
  Install-Terminal zeigt, welches Python läuft (`<KiCad>\bin\python.exe` + Version),
  bootstrappt pip per `ensurepip --user`, falls das KiCad-Bundle ohne pip ausgeliefert
  wurde (häufige Ursache für „er versucht die Installation, aber nichts passiert"), und
  **verifiziert nach der Installation per Test-Import** aus dem `_deps`-Ordner, dass alle
  sechs Module wirklich importierbar sind („OK - alle MCP-Module importierbar") —
  Installation und Server-Start können nicht mehr still auseinanderlaufen.
- **Kein „ungespeicherte Änderungen" mehr durch bloßes Reden mit dem MCP.** Der
  Presence-Beacon (erster IPC-Kontakt) hat die MCP.Skizze-Ebene im Board-Setup aktiviert
  und die How-to-Legende aufs Board gestempelt — beides markiert das Board als geändert,
  und da jeder Chat-Turn ein frischer Server-Prozess ist, stand der Dialog nach jedem
  KiCad-Neustart wieder da. Der Beacon ist jetzt strikt nicht-mutierend: Er schaltet die
  Skizzen-Ebene nur noch *sichtbar* (View-Einstellung), und nur wenn sie bereits aktiviert
  ist. Ebene aktivieren + Legende stempeln passiert erst, wenn der Agent wirklich zeichnet
  (Marker-Tools / `ipc_draw_sketch_legend`) — also wenn eine Board-Änderung der Zweck ist.
- **Plugin v0.2.9: MCP-Abhängigkeiten landen jetzt in einem plugin-eigenen Ordner**
  (`<plugin>/_deps`, `pip install --target`) statt per `pip --user` in der User-Site —
  die ist mit anderen CPython-Installationen geteilt (Versionskonflikte) und unter
  KiCads gebündeltem Python nicht zuverlässig auf `sys.path` („Installation klappt,
  Server startet trotzdem nicht"). Der `_deps`-Ordner wird überall konsistent auf den
  `PYTHONPATH` gesetzt: MCP-Config (`build_mcp_config`), Deps-Check (`deps.check_deps`)
  und Server-Start-Probe (`server_probe`). Frühere `--user`-Installationen funktionieren
  weiter (Site-Verzeichnisse bleiben Fallback); der Deps-Check läuft zudem ohne
  aufblitzendes Konsolenfenster.
- **Plugin: „Claude antwortet, hat aber keinen MCP" wird jetzt erkannt und blockiert
  (Plugin v0.2.8).** `claude -p` verwirft einen nicht startenden MCP-Server *stillschweigend*
  — der Chat lief dann ohne Board-Tools weiter. Drei Gegenmaßnahmen: (1) Neue
  Server-Start-Probe (`plugin/server_probe.py`): KiCads Python importiert
  `kicad_mcp.server` mit derselben `PYTHONPATH` wie die MCP-Config; schlägt das fehl,
  zeigt der Preflight die echte Traceback-Zeile als FAIL-Zeile („MCP-Server startet
  nicht") mit Ein-Klick-Fix bei fehlenden Modulen. (2) Fehlende MCP-Abhängigkeiten
  (fastmcp/mcp/…) sind jetzt FAIL statt WARN — der Chat startet nicht mehr, solange der
  Server gar nicht starten kann. (3) `MCP_TIMEOUT=120000` als Default beim
  `claude`-Aufruf, damit ein kalter KiCad-Python-Start (165 Tools, gesyncte Disks) nicht
  am Standard-Startup-Timeout scheitert und still wegfällt.

### Changed
- **Plugin-Chat dockt jetzt in KiCad an (Plugin v0.2.7).** Das Chat-Panel wird als
  natives AUI-Pane in den PCB-Editor eingehängt (neues `plugin/dock.py`, über
  `wx.aui.AuiManager.GetManager` am `PcbFrame`) — es snapt an die Fensterränder, lässt
  sich wie Darstellung/Suche abreißen, verschieben, in der Größe ziehen und wieder
  andocken; KiCad merkt sich die Position in der Perspective. Das UI lebt dafür jetzt in
  `ClaudeChatPanel` (wx.Panel); der bisherige schwebende `ClaudeChatDialog` bleibt als
  automatischer Fallback, wenn das Andocken auf einem System nicht möglich ist.
  Erneuter Toolbar-Klick zeigt das vorhandene Pane wieder (mit aufgefrischtem RunPlan)
  statt ein zweites Fenster zu öffnen. Pure Anteile (Frame-Erkennung inkl. deutscher
  Titel, Pane-Spec) headless getestet (`tests/test_plugin_dock.py`).
- **Plugin-Chat im Claude-Code-Look (Plugin v0.2.6).** Das Chat-Panel sieht jetzt aus wie
  das Claude-Code-Terminal: dunkler Hintergrund, Monospace-Schrift (Cascadia/Consolas/…),
  Claude-Orange für Antwort-Bullets (`●`) und Eingabe-Chevron (`❯`), eigene Eingaben
  gedimmt, Fehler rot, plus pulsierender CLI-Spinner mit Sekundenzähler
  („✻ Claude denkt nach … (12s)") statt statischem Statustext. Farben/Rollen/Spinner leben
  als reine Logik in `plugin/chat_theme.py` (headless getestet,
  `tests/test_plugin_chat_theme.py`); `chat_dialog.py` wendet sie nur an.

### Fixed
- **Plugin-Chat: kein schwarzes Konsolenfenster mehr pro Frage.** Der headless
  `claude -p`-Kindprozess (auch der `wsl claude`-Fallback) wird unter Windows jetzt mit
  `CREATE_NO_WINDOW` gestartet (`claude_bridge.hidden_console_kwargs`), statt für jede
  Chat-Runde ein cmd-Fenster aufblitzen zu lassen. Die Antwort floss schon immer per Pipe
  ins Chat-Panel — das Fenster war ein reiner Windows-Nebeneffekt (GUI-Prozess spawnt
  Konsolen-Kind) ohne Funktion.

### Added
- **KiCad Action Plugin (`plugin/`, Stufe 1)** — a "Claude" toolbar button in the PCB editor
  that opens a chat panel wired to the open board. Each message runs one headless **Claude
  Code** turn (`claude -p … --mcp-config … --strict-mcp-config --resume … --output-format
  json`) against the bundled kicad-mcp server — the user's subscription, **no API key/cost**.
  The session id from the first reply is reused so the turns form one conversation; the panel
  is non-modal so the board updates live. Pure-logic layers (`claude_bridge`, `mcp_config`)
  are unit-tested headless (`tests/test_plugin_bridge.py`); the wx/pcbnew layers are
  KiCad-only. One-time user setup (install Claude Code + `claude login`, trust the project
  dir) is unavoidable and documented in `plugin/README.md`. Backend choice (Codex/…) +
  bundling + onboarding are Stufe 2/3.
- Live PCB-editor **selection** tools over IPC (PLAN.md §4.2 gaps G1+G2), new module
  `tools/ipc_interact_tools.py`: `ipc_get_selection` (read what the user has highlighted —
  type/ref/uuid/net/layer/position/bbox, empty = note not error), `ipc_inspect_item`
  (by ref or uuid, with `get_connected_items`), `ipc_select_items` (set selection by
  refs/uuids/net/item_type/layer — native highlight) and `ipc_clear_selection`. Reuses the
  existing `ipc_tools` connection helpers (one client). The PLAN.md Block-B draft was
  condensed (v4) against the already-present IPC/`live_*` layer — Phase 0/1 + save/DRC/
  routing were already covered; only the selection/marker/edit/DRC-session gaps remain.
  kipy 0.7.1 selection API verified headless. Tool count 149 → 153.
- **Presence beacon**: on the MCP's **first contact with an open board** (the first time any
  `ipc_*` tool connects), the MCP.Skizze layer is auto-enabled + made visible and the how-to
  legend is stamped (if missing) — so the user can *see* in KiCad that the MCP server is
  active on this board. Runs once per server process, best-effort (never breaks a tool), and
  is disablable with `KICAD_MCP_SKETCH_PRESENCE=0` (or false/off/no). (Note: the IPC API
  cannot *rename* the layer, so its display stays "User.9" until renamed once in Board Setup
  → "MCP.Skizze".)
- The MCP marker layer is now framed as the **"MCP.Skizze" sketch / proposal layer** (the
  agent draws marker proposals + DRC findings there; the user accepts or clears them). New
  `ipc_draw_sketch_legend` tool stamps a short German how-to legend onto the layer so it's
  self-documenting in KiCad; `ipc_clear_markers` was made legend-safe (it now removes only
  `M<n>` markers + their shapes, never the legend). The layer is still `User.9` internally
  (rename its display to "MCP.Skizze" once in Board Setup; the tools address it by enum and
  keep working). Tool count 164 → 165. Tool docstrings/`session_status` hints updated to the
  sketch-layer terminology.
- Live PCB-editor **markers** over IPC (PLAN.md §4.2 gap G3): `ipc_draw_markers`
  (circle/cross/label on a dedicated MCP user layer, sequential `M<n>` IDs encoded in each
  marker's text), `ipc_list_markers`, `ipc_clear_markers` (all or by ID) and
  `ipc_check_markers_before_save` (warn before a git commit). Graphics only, undoable. The
  marker layer (default `User.9`) is auto-**enabled and made visible** — KiCad silently drops
  `create_items` onto a disabled layer, and a hidden layer shows nothing. The whole
  create→commit→scan→remove pipeline + the layer enable/visible handling were validated live
  against a running KiCad 10.0.1 (the `set_enabled_layers(copper_count, layers)` signature and
  the BoardText/BoardCircle/BoardSegment construction were confirmed on the real board, not
  just mocks). Tool count 153 → 157.
- Live PCB-editor **edits + DRC session + status** over IPC (PLAN.md §4.2 gaps G4/G5/G6),
  completing the condensed Block B. G4: `ipc_create_via` (custom diameter/drill via the kipy
  `Via.diameter`/`drill_diameter` setters), `ipc_accept_markers` (turn G3 markers into real
  vias + clear them), `ipc_set_track_width`, `ipc_move_items`, `ipc_remove_items` (by uuid).
  G5: `ipc_drc_session_start` — saves the live board (`board.save()`), runs headless
  `kicad-cli` DRC, drops a capped set of cross markers at the violations and returns
  counts + item uuids so you can select→fix→re-check. G6: `ipc_session_status` — read-only
  roll-up of open markers + current selection. All edit primitives (via create, width,
  move, remove, `board.save()`, DRC parse) were validated live against KiCad 10.0.1 on a
  real board (scrap items created and removed). Source-confirmed via the local kipy package
  (`create_items` takes a list; `update_items`/`remove_items_by_id`; `Via.diameter`).
  Tool count 157 → 164.
- `compute_pin_world_positions_sch` now accepts an optional `refs` list to restrict
  the output to specific symbols (e.g. `refs=["U1B"]`). Without it the full-board pin
  dump routinely exceeded the MCP token limit on real boards; the filter returns only
  the requested symbols and reports unknown refs in `not_found`. No new tool, fully
  backward-compatible (omitting `refs` returns every symbol). (PLAN.md Anhang A — S1)
- New `add_no_connect(sch_path, x_mm, y_mm)` tool — places a no-connect (×) flag at a
  pin so ERC stops raising `pin_not_connected` for an intentionally unused/reserved pin.
  Deterministic UUID + grid-snap (new `render_no_connect` renderer); removable via
  `delete_schematic_items` `types=["no_connect"]`. Tool count 147 → 148. (PLAN.md Anhang A — S5)
- `bulk_swap_symbol` can now resolve the target symbol from a **project-local**
  (`${KIPRJMOD}`) `sym-lib-table`, not just stock + global libraries — via the new
  `get_project_symbol()` resolver and an optional `project_dir` arg on
  `SchematicDoc.ensure_lib_symbol`. (PLAN.md Anhang A — S2)
- New `create_library_symbol` tool — authors a complete KiCad library symbol
  (`.kicad_sym` entry) from a pin spec: a rectangular-IC body with pins evenly pitched
  and centred on the requested sides (left/right/top/bottom, auto-split when omitted).
  Creates/extends the `.kicad_sym` (replace existing only with `overwrite=true`) and can
  register the lib project-locally (`register_in_project`) so the S2 resolver picks it up.
  Lets an agent create custom parts via MCP instead of hand-editing `.kicad_sym` (which has
  corrupted symbols before). Output validated by `kicad-cli sym upgrade`. New generator
  module `generators/symbol_author.py`. Tool count 148 → 149. (PLAN.md Anhang A — S6)

- `add_power_symbols` gained a `snap` flag (tool-wide, default `True`) plus a per-anchor
  `"snap"` override, and `render_symbol_instance` / `_build_power_symbol_snippet` gained a
  matching `snap` parameter (default `True`, all other callers unchanged). (PLAN.md Anhang A — S4)

### Fixed
- `_patch_loaded_footprint` (used by `update_pcb_from_schematic` add_new,
  `resolve_pcb_footprints`, `_swap_fp_library`) wrote the board position onto the
  **Reference property's local `(at)`** instead of inserting a footprint-header `(at)` — a
  raw `.kicad_mod` has no header `(at)`, so "the first `(at)`" is the ref label's offset.
  Result: added footprints stacked at one spot and their ref designators flew off by the
  staging coordinate (confirmed on the V16_06 board). It now always inserts a real header
  `(at)` and leaves every property's local `(at)` untouched.
- `_ensure_index_net` gave the **first** net on a bootstrap index-format board index **0** —
  KiCad's "no net" sentinel — so that net read as unconnected. Real nets now start at 1 and a
  `(net 0 "")` sentinel is emitted.
- **Multi-unit symbols** were placed wrong: `render_symbol_instance` hardcoded `(unit 1)` and
  `get_lib_symbol_pins` returned the **union of all units'** pins — so placing unit 2 of a
  multi-unit part (op-amp, 74xx gate) emitted unit 1's pin UUIDs and corrupted connectivity.
  `add_schematic_symbols` now takes a per-part `unit` field; `get_lib_symbol_pins(node, unit=N)`
  filters to that unit's pins (+ the shared unit-0 pins), and `(unit N)` is emitted in both the
  header and the instances block.
- `connect_pins` / `add_schematic_wire` / `render_wire` force-snapped wire endpoints to the
  1.27 mm grid, pulling a wire **off a fine-pitch IC pin** (off-grid pad) and breaking the net
  — the same footgun fixed earlier for `add_power_symbols`. They now take a `snap` flag
  (default True); pass `snap=false` to land exactly on a pin endpoint from
  `compute_pin_world_positions_sch`.
- Symbol extraction (`symbol_cache`) used **string-literal-unaware** paren counting, so a
  stray `)` inside a property string (e.g. `Description "smiley :)"`) or a `(`/`)` in a
  sym-lib-table URI/descr **truncated** the extracted symbol/lib block — KiCad then rejects
  or mis-renders it. `_extract_top_level_symbol` and `_iter_sym_lib_blocks` are now
  string-aware (new `_balanced_block_end`/`_paren_depth_before` helpers).
- `(extends …)` inlining discarded the **derived** symbol's own properties: the inlined
  symbol carried the *base's* Description/keywords/Footprint instead of the derived ones.
  It now overlays the derived symbol's properties onto the base geometry (verified against
  stock `Filter_EMI_CommonMode`).
- `ipc_route_pin_to_pin` created its layer-change via at **zero size** (same default-`Via()`
  bug) — now uses the board default via size (`_board_default_via_nm`, shared with
  `ipc_create_via`).
- `ipc_route_power_ring` **silently created unconnected copper** when the net name wasn't
  found: it built the ring tracks with no net but reported `success`. It now fails loudly
  (mirrors `ipc_add_zone_pour`).
- `ipc_close_kicad` / `_close_editor_silent` called `client._client.send(cmd)` **without the
  required response type**, raising a `TypeError` that was swallowed — so the graceful
  Save/CloseDocument before the force-`taskkill` never actually ran (risking a lost save).
  Now pass `Empty`, matching the working call sites.
- `ipc_create_via` / `ipc_accept_markers` created **zero-size vias** when `size_mm`/`drill_mm`
  were left at 0: a default kipy `Via()` has diameter/drill 0 and KiCad keeps it at 0 (a
  degenerate via). They now fall back to the board's Default net-class via size (new
  `_board_default_via_nm` helper; 0.4/0.2 mm fallback). Verified live (size 0 → 0.4/0.2 mm).
- `ipc_draw_markers` / `ipc_drc_session_start` drew **degenerate circle markers**: kipy's
  `Circle` has no `radius` setter (it's a derived method), so `c.radius = …` was a silent
  no-op that left `radius_point` at the origin → a circle from the marker centre to (0,0)
  instead of a small ring. Now sets `radius_point = centre + (radius, 0)`. (Found by the
  source-vs-impl audit; confirmed against the kipy `Circle` source.)
- `via_promote` silently did nothing: it rewrote a blind/buried via's `(layers …)` to
  `"F.Cu" "B.Cu"` but left the `(via blind`/`(via buried` **type token**, which KiCad treats
  as authoritative over the layer pair — so the via stayed blind/buried at fab and the
  reported tier savings were fictional. It now also strips the type token (verified against
  pcbnew's `GetViaType()`). (Found by the geometry audit.)
- `ipc_inspect_item` / `ipc_get_selection` / `ipc_select_items` read footprint references
  wrong against **live kipy**: `Field.text` is a `BoardText` (string in `.value`), not a bare
  string, so `_field_text` returned the object and every footprint ref/value lookup missed
  (found via a live smoke — the mocks used the flat shape). Fixed `_field_text` to unwrap
  `.value`; the unit mocks now mirror the real nested shape so this can't regress.
- `ipc_inspect_item` now answers footprint connectivity via the **pad→net map**
  (`pads` + distinct `nets`) instead of `get_connected_items`, which KiCad rejects for a
  footprint argument. Verified live on `U_589` (the 74HC589: pins 1–6 = `nFAULT_DRV1..6`).
- `ipc_open_kicad` could launch a **standalone** pcbnew/eeschema while a KiCad **project
  manager** was already running — two IPC API servers then fought over one socket and
  `GetOpenDocuments` stopped resolving (`no handler`), which silently broke *every* `ipc_*`
  tool. It now detects a running manager (new `_kicad_manager_running()` helper) and refuses
  to double-launch, returning `manager_running: True` with guidance to open the editor from
  the manager (or close KiCad for a clean cold start). The readiness poll also now
  distinguishes the unrecoverable `no handler for GetOpenDocuments` state (returns
  `api_handler_missing: True` immediately) from a slow editor launch, instead of burning the
  whole timeout on a misleading "enable the API" message.
- `add_power_symbols` force-snapped every anchor to the 1.27 mm grid, which silently moved a
  power symbol up to ~0.6 mm **off** a fine-pitch IC pin (pads at 0.65 / 0.5 mm pitch are
  off-grid) — the connection point no longer coincided with the pad and ERC raised
  `pin_not_connected`. (This was the real cause behind the "power-symbol-on-pin doesn't
  connect" symptom; pin-on-pin itself connects fine.) Pass `snap=false` (or `"snap": false`
  on the anchor) to land the connection point exactly on the pin endpoint from
  `compute_pin_world_positions_sch`. (PLAN.md Anhang A — S4)
- `bulk_swap_symbol` embedded the **wrong geometry** for the new symbol: it renamed the
  old cached `lib_symbol` block in place, keeping the source symbol's graphics and pin map
  under the target's name. Whenever the two symbols differed (the entire point of a swap)
  the schematic showed/used the old body. It now **drops** the stale block (new
  `SchematicDoc.drop_lib_symbol`) and **re-embeds** the target's real definition fresh from
  the library — its true graphics and per-unit children (correctly bare-named) land in
  `lib_symbols`. An unresolvable target now fails cleanly without writing a half-applied
  swap, and the result reports `old_lib_symbol_dropped`. (PLAN.md Anhang A — S2)

## [1.0.0] — 2026-06-09 — First public release (GPL-3.0-or-later)

First tagged, publicly released version. Headline changes vs. the
MIT-licensed upstream ([lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp)):

- **147 MCP tools** for KiCad EDA (schematic/PCB patching, IPC live layer,
  geometry, BOM/netlist/DRC/ERC, generators, review) — runs under KiCad's
  bundled Python.
- **Relicensed to GPL-3.0-or-later** (in-process `pcbnew` is GPL); original
  MIT notice preserved in `LICENSE.MIT`, rationale in `NOTICE`.
- **FreeRouting/autoroute integration removed** entirely.
- **Warm pcbnew daemons** for `pcb_eval`, `check_connectivity` and
  `via_promote` (board cached by path+mtime; first load/fill paid once),
  plus scoped/optional zone fill for connectivity.
- Dead-code, temp-file and personal-data cleanup; hardened `.gitignore`.

The dated sections below are the development history that culminates in 1.0.0.

## 2026-06-09 — perf: warm via_promote daemon

### Changed

- **`via_promote` now runs against a warm in-memory board** instead of
  spawning a cold pcbnew process per call. `via_promote_worker` became a
  daemon that caches loaded + zone-filled boards by path+mtime (LRU 5) and
  reuses the shared `WarmDaemon` client. The analysis is read-only, so the
  cached board is reused as-is — the typical `dry_run` (report) →
  `dry_run=False` (apply) flow loads + fills once instead of twice; the apply
  rewrites the file, so the next analysis sees a new mtime and reloads. No
  scoped fill here (the clearance check is whole-board by nature). Measured
  ~31× on the small fixture (46.8 ms → 1.5 ms); on a dense poured board the
  cold load+fill was the ~240 s case, now paid once.
- Tests: +3 in `tests/test_via_promote.py` (warm cache hit, apply→mtime
  invalidation→reload, status op). Suite 1573 → 1576.

## 2026-06-09 — perf: warm connectivity daemon + scoped/optional zone fill

### Changed

- **`check_connectivity` now runs against a warm in-memory board** instead of
  spawning a cold pcbnew process per call. The new `connectivity_worker`
  daemon caches loaded boards by path+mtime (LRU 5), so the first query on a
  dense poured board pays `LoadBoard` + fill once and every later query on the
  unchanged file is a cache hit. Measured ~19× on the small test fixture
  (186 ms → 9.6 ms); on a large fully-poured mainboard the cold case was the
  ~240 s wall-clock, now paid once.
- **Scoped / optional zone fill (`fill` param on `check_connectivity`).** Zone
  fill dominates on poured boards. `overview` accepts `fill=False` for a fast
  pour-blind ratsnest pass; `pad` / `whatif` fill only the relevant net's
  zones (a net's cluster depends only on its own copper), cached per net.
- Extracted the proven warm-worker client into `kicad_mcp/tools/_warm_daemon.py`
  (`WarmDaemon`); `pcb_session_tools` and `connectivity_tools` now share it
  (spawn / pipe / broken-pipe retry / mutated+SwigPyObject+load-cap recycle).
  `whatif` reports `mutated` so the daemon recycles and the next call reloads a
  pristine board — read-only on disk as before.
- Tests: +6 in `tests/test_connectivity_tools.py` (warm cache hit, fill=True/False,
  scoped-fill pad reuse, whatif cache-drop, status op). Suite 1567 → 1573.

## 2026-06-09 — chore!: relicense to GPL-3.0-or-later

### Changed

- **License: MIT → GPL-3.0-or-later.** This software loads KiCad's `pcbnew`
  Python module in-process (PCB geometry / connectivity / via analysis);
  `pcbnew` is GPL-3.0, so the combined work must be GPL-3.0-or-later.
  - `LICENSE` now contains the full GNU GPL v3 text.
  - `LICENSE.MIT` preserves the original MIT notice (© 2025 Lama Al Rajih) for
    the upstream-derived portions — MIT is GPL-3.0-compatible, so attribution
    is retained as that license requires.
  - Added `NOTICE` documenting the relicense rationale, the derivation from
    `lamaalrajih/kicad-mcp`, and the licenses of third-party components.
  - `pyproject.toml` `license` + classifier updated; README/README.de License
    sections updated.
  - Added `# SPDX-License-Identifier: GPL-3.0-or-later` headers to all 188
    first-party Python files (shebang-aware, idempotent).

## 2026-06-09 — feat!: remove FreeRouting / autoroute integration

### Removed

- **FreeRouting/autoroute integration, entirely.** Deleted
  `kicad_mcp/tools/autoroute_tools.py` and its five tools — `install_autorouter`,
  `autoroute_pcb`, `check_autorouter_status`, `export_pcb_dsn`, `import_pcb_ses`
  — plus the suites `tests/test_autoroute_e2e.py` and
  `tests/test_autoroute_install.py`. Dropped `register_autoroute_tools` from
  `tool_registry.py` and the Java/`freerouting_jar` probe from
  `kicad_mcp_doctor` (Java was only there for FreeRouting). Tool count
  152 → 147; `EXPECTED_TOOL_COUNT` and the autoroute entries in the audit
  allowlists updated accordingly.
- **Not affected:** the simple built-in trace generator used by `generate_pcb`
  (`generators/pcb/route.py` + `builder.py`) — that is an independent feature,
  not the FreeRouting integration, and stays.

## 2026-06-09 — chore: dead-code & temp-file cleanup, test-lock catch-up

### Removed

- **Dead code (1445 lines across 18 files)** — verified-unreferenced (0 call
  sites, 0 test references, byte-compile clean): the orphan module
  `generators/schematic/optimize.py`; 20 refactor-leftover helpers from the
  `auto_place.py`/`pcb_generator.py` split (`defrag_place`, `drc_reroute`,
  `_fd_refine`, `_routability_check`, `_place_analog_signal_core`,
  `collect_pad_positions`, `_segment_outside_board`, `_map_bypass_caps_to_ics`,
  `_build_net_members`, `_ref_signal_nets`, `_auto_layout_factor`,
  `_simplify_path`, `_is_number`, `render_junction`, `_compute_component_scale`,
  `symbol_scale_vectors`, `enrich_parts_with_spice`, `_resolve_svg_output_path`,
  `_label_text`); and two dead `server.py` stubs (`setup_signal_handlers`,
  `cleanup_handler`). Stale provenance comments referencing the removed
  functions cleaned up. No tool or public API affected.
- Temp/build cruft: 202 `.pyc`, all `__pycache__`, 6 empty tool dirs
  (`build/dist/out/target/.next/node_modules`), three lint/test caches and
  ~17 MB coverage artifacts; stale `CLAUDE.md.bak`.

### Fixed

- **Test-lock drift** — `EXPECTED_TOOL_COUNT` 145 → 152 (the seven newest
  tools `via_retype`, `via_resize` and the five `live_*` were added without
  bumping it). Added the missing `pcb_path = to_local_path(pcb_path)` first
  line to the `via_retype`/`via_resize` wrappers (matching `via_promote`), and
  gave `live_get_state`/`live_move_footprint`/`live_session_status` proper
  "Use this …" usage cues. Broadened `test_route_when_kipy_missing` to accept
  the "no kicad project active" failure path (KiCad open without a project).
  Suite: 1606 passed / 0 failed.

## 2026-06-08 — feat: `via_resize` (board-wide via size/drill standardisation)

### Added

- **`via_resize(pcb_path, size, drill, uuids, dry_run)`** — surgical patch of
  each via's `(size …)`/`(drill …)` tokens, board-wide (`uuids=None`) or by
  UUID; layers/type/net/position untouched. Collapses a mix of via sizes to a
  single standard (e.g. all → 0.4 mm / 0.2 mm: one drill tool, more copper
  clearance). Idempotent. Tests: `tests/test_via_promote.py::TestResize` (4).
  Tool count 151 → 152.

## 2026-06-08 — feat: `via_retype` (surgical via-type token patch)

### Added

- **`via_retype(pcb_path, uuids, new_type, dry_run)`** — companion to
  `via_promote`: changes the via-*type* word right after `(via`
  (`through`/`blind`/`buried`/`micro`) for specific vias by UUID, leaving
  layers/size/drill/net and every other via byte-for-byte intact (same
  surgical text-patch mechanism as the promote apply). Primary use: drop a
  needless manufacturing tier — a mechanically-drillable via mis-tagged
  `micro` forces an HDI/laser process; retyping it to `blind` keeps the span
  but removes the laser tier with no routing change.
- Motivated by the reference V16_04 via cost analysis: 5 vias tagged `micro` but
  all 0.2 mm drill (mechanical, not laser) → de-micro removes the whole HDI
  tier. (`via_promote` itself found 0 promotable on that board — outer GND
  flood blocks every blind/buried→through.)
- Tests: `tests/test_via_promote.py::TestRetype` (5 pure-text cases). Tool
  count 150 → 151.

## 2026-06-07 — feat: IPC live layer (pull live editor state, diff user edits, masked writes)

### Added

- **Live IPC layer** over a running KiCad 10 PCB editor (kipy), 5 new tools,
  built only after `verify_kicad_ipc.py` passed all mandatory checks against a
  live 10.0.1 instance (runtime-discovered field names, no guessing):
  - **`live_get_state`** — reads footprints+tracks+vias straight from the
    *living* editor (uncached, never the file read-cache) and baselines the
    diff snapshot.
  - **`live_diff_since_last`** — diffs live state vs snapshot; each change
    attributed **agent vs user**. Agent self-writes are masked, so a manual
    user edit is the only thing flagged. Re-baselines each call.
  - **`live_summarize_user_changes`** — plain-language hand-off ("User moved 3
    footprints on F.Cu in the upper-left quadrant; re-routed 2 tracks …").
  - **`live_move_footprint`** — visible move; `dry_run` default (reports
    old→new + affected nets), retry-with-backoff (KiCad single-thread busy),
    `agent:`-tagged commit (individually undoable in Local History), and
    self-write masking so it never reads back as a user edit.
  - **`live_session_status`** — health ping + reconnect, board-change
    detection (invalidates snapshot), persist cadence for watch-then-pull
    (KiCad 10 uses Local-History debounce, not `autosave_interval`), read-only
    state.
- **Read-only flag** `KICAD_MCP_LIVE_READONLY=1` disables all live writes.
- Pure diff engine `kicad_mcp/tools/ipc_live_diff.py` (stdlib only) with
  signature builders, diff, agent/user attribution and the summary renderer —
  unit-tested without a running KiCad: `tests/test_ipc_live_diff.py` (12).
- Live end-to-end smoke test confirmed self-write masking (no false user
  alarm) and real user-edit detection against the open board.
- **Reads are retry-wrapped too**, not just writes: KiCad reports "busy" on
  read calls (get_board / snapshot build / footprint lookup) during board load
  and zone refill, found end-to-end against the live 51 MB board after the
  server restart.

## 2026-06-07 — feat: `via_promote` via-in-pad (POFV) detection + tier report

### Added

- **`via_promote` classifies each promotion three ways** (was go / no-go) and
  now flags vias that would land in a pad — previously unanalysed:
  - **`needs_pofv`** — candidate becomes through but sits inside an *own-net
    SMD pad*; a bare through via there wicks solder, so it is promotable only
    as a filled+capped via-in-pad (POFV — free at JLC on 6–20 layers). The
    offending pad(s) are listed per via in `in_pads`.
  - **`pad_shorts`** — other-net pads on F.Cu/B.Cu a through via would short
    (reported on the `blocked` record alongside `blocked_on`).
  - Pad overlap is tested on **both** outer layers regardless of the via's
    current span, so a pad on a layer the blind via already occupied is no
    longer missed.
- **Manufacturing-tier summary**: `tier_before`, `tier_after_promotable`,
  `tier_after_with_pofv`, each `{spans, blind_buried_types,
  blind_buried_vias}` — quantifies how many distinct blind/buried span
  classes (the real cost driver) remain in each scenario.
- **`pofv_ok` parameter** (default True): the apply step also promotes the
  `needs_pofv` vias (accepting POFV); set False to promote only the clean set.
- Tests: `tests/test_via_promote.py::TestPofvAndTier` (4 cases). 11 passed.

## 2026-06-05 — fix: `add_via_to_pcb` emits via-type token

### Fixed

- **`add_via_to_pcb` / `_via_block` (`pcb_geometry_tools.py`) now write the
  KiCad via-type token** (`buried` / `blind`) after `(via`. KiCad reads a
  via's type from this token, **not** from the `(layers …)` pair — so a
  buried/blind via emitted as a plain `(via` (the previous behaviour) loaded
  in KiCad as a plain **through** via, silently discarding the intended
  inner-layer span. `_via_block` now derives the token from `layer_pair`
  (outer = F.Cu/B.Cu: two outer → through/no token; exactly one outer →
  `blind`; none/inner-inner → `buried`). Through vias are unchanged. Also
  fixes the same defect for any buried/blind via created via `pcb_batch`
  (which dispatches `add_via_to_pcb`'s `_text` companion). 8 new tests
  (`tests/test_buried_vias.py::TestViaTypeToken`) assert the emitted token
  for through/blind/buried at both the `_via_block` and MCP-tool level —
  the prior tests checked only `(layers …)` and so missed this. No tool
  count change (bug fix).

## 2026-06-01 — polar routing: arc tolerance, `route`, `via_promote`

### Added

- **`via_promote(pcb_path, clearance_mm=0.2, dry_run=True)`**
  (`via_promote_tools.py` + `via_promote_worker.py`) — universal
  board-wide pass that promotes blind/buried vias to plain through
  (F.Cu↔B.Cu) vias wherever it is safe (through vias are JLC-standard and
  cheaper). Analysis runs in a subprocess-isolated `pcbnew` worker (twin
  of `connectivity_worker`) that fills zones first, then tests each
  candidate's pad circle against other-net copper (track/pad/via/filled
  zone) on the layers a through via would newly occupy. Apply is a
  surgical text-patch of the promotable vias' `(layers …)` lines only.
  Report mode answers "where can I free a through via?"; the remaining
  blind/buried count is the manufacturing-tier indicator. Tool count
  144 → 145. 7 tests (`tests/test_via_promote.py`).
- **`polar_grid op="route"`** — pin-to-pin polar router.
  `route(connections=[{from,to,ring|r_mm}, …])` (or single
  `from_ref_pad`/`to_ref_pad`/`ring`) lays a tangential arc on `arc_layer`
  + radial stubs on `radial_layer` + vias only where a pad does not
  already reach the layer (THT `*.Cu` pads need none). Net is taken
  automatically from the pins (refuses mixed nets). One read/write for the
  whole list; `dry_run` previews; intra-batch ring-overlap warning.
  9 tests (`tests/test_polar_route.py`).

### Changed

- **`add_arc_to_pcb` center mode** now accepts up to ±50 µm radius
  mismatch between start/end (was 1 µm) and places the arc on the *mean*
  radius — lets an arc span two real pads/vias that are never perfectly
  equidistant. `short_arc_mid_xy` gained an optional `radius` arg. 3 new
  tests.

### Fixed

- **`_op_route` persists to disk.** Discovered that `put_text()` only
  updates the in-memory cache (it does not write the file); the existing
  polar edit-ops (`add_polar_arc`/`_radial_segment`/`_via`/`place_on_*`)
  now all persist via a shared `_persist()` helper. 4 persistence tests.
- **`bulk_swap_symbol` — two crashes/corruptions fixed.** (1) It called a
  non-existent `doc._reparse()` after editing the text → every swap raised
  `'SchematicDoc' object has no attribute '_reparse'`; replaced with the
  lazy-tree `doc._invalidate()`. (2) It renamed the parent `lib_symbol`
  but **not** the per-unit child symbols (`<bare>_<u>_<s>`), so the parent
  and its units diverged and KiCad refused to load the schematic; now the
  child units are renamed in lockstep. The tool had **no tests** (which is
  why both shipped) — added `TestBulkSwapSymbol` (4 cases incl. the
  multi-unit rename).

## 2026-05-30 — `pcb_render` cropped-region PNG (see the layout)

### Added

- **`pcb_render(pcb_path, center_x_mm, center_y_mm, window_mm=10, …)`**
  (`pcb_render_tools.py`) — renders a cropped square region of a PCB to a
  PNG the agent can actually *view* (then read with the image tool),
  instead of reasoning blind from coordinates like it had to before.
  Pipeline: `kicad-cli pcb export svg` (vector, whole board, cached by
  file+mtime+layers) → set the SVG `viewBox` to the requested region →
  rasterise only that crop at high DPI with cairosvg. Edge.Cuts geometry
  bbox (parsed from the file, no pcbnew) gives the board→SVG offset.
  cairosvg's native cairo DLLs are resolved by putting KiCad's bin dir on
  PATH. ~9 s cold (SVG export), ~3.5 s warm (SVG cached). Tests in
  `test_pcb_render.py` (skip without cairo/kicad-cli); pylint 10/10.
  Motivation: a layouter solves "rotate this stub to a right angle" in
  ~10 s by *seeing* it — this gives the agent the same eyes.

## 2026-05-30 — warm-board `pcb_eval` session (100× on repeated analysis)

### Added

- **`pcb_eval(pcb_path, code, …)`** + `pcb_session_status` / `pcb_session_reset`
  (`pcb_session_tools.py` + standalone daemon `pcb_session_worker.py`).
  A persistent pcbnew daemon keeps loaded + zone-filled `BOARD` objects in
  memory (cached by path + mtime); arbitrary analysis code runs against the
  warm board in **~ms** after a one-time ~1 s load. Measured on the reference
  board: first eval ~1.4 s, warm evals **0–80 ms (~100×+)**; a 14-step
  real-analysis battery dropped from ~30 s (cold per-script) to **4.7 s**.
  - **Why:** the agent always wrote ad-hoc pcbnew scripts (clearance/
    collision/what-touches/cluster checks) that no fixed tool covers, each
    paying a cold pcbnew load. `pcb_eval` is the fast scripting substrate —
    same capability, warm. Pre-bound helpers (flip/arc-accurate):
    `world_pos`, `fp_pads`, `pads_on_net`, `cluster_of`, `what_touches`,
    `nearest_copper`, `rt`/`xy`/`ring_radius`, `fill`, `unconnected`,
    `nets`, plus `board`/`pcbnew`/`ctx` (persists across calls). A
    `helpers()` call returns the always-current full reference (name →
    signature → return shape) so the agent never guesses or falls back to
    raw pcbnew — self-documenting on demand.
  - **Read/what-if model:** code may mutate the board in memory (what-if),
    but it is NEVER written to disk — real edits stay with the text-patch
    tools. A mutation is auto-detected (item-count signature) and the
    daemon is **recycled** (a what-if poisons the pcbnew interpreter so
    even the next `LoadBoard` returns un-typed `SwigPyObject`s — only a
    fresh process resets it). Client owns recycling (race-free), respawns
    on next request; falls back / reloads on mtime change.
  - Edge cases covered (17 tests, `test_pcb_session.py`): cold→warm reuse,
    mtime invalidation, mutation→read recovery in one session, what-if not
    touching disk, timeout→recycle→recover, result truncation, stdout
    capture, ctx persistence, error/empty/missing-file. pylint 10/10.

## 2026-05-29 — `check_connectivity` subprocess isolation + speed

### Changed

- **`check_connectivity` now runs pcbnew in a fresh, lean *standalone
  worker* subprocess per call** (`connectivity_worker.py`, launched by
  file path) instead of in the long-running server process. Two wins:
  - *Reliability:* fixes a real failure observed in a long session —
    after many `LoadBoard` calls in one interpreter KiCad's SWIG bindings
    degrade and return un-typed `SwigPyObject` instances
    (`'SwigPyObject' object has no attribute 'BuildConnectivity'`). A
    fresh process does exactly one load → never degrades.
  - *Speed:* the worker imports **nothing** from `kicad_mcp` / `mcp`.
    An earlier `-m kicad_mcp.tools.connectivity_tools` variant dragged in
    the package `__init__` (→ `server` → all ~30 tools, ~3 s) + FastMCP
    (~1.3 s) on every call; the real pcbnew work is ~1 s. Running the
    lean worker file directly cut a call from **~5.9 s → ~1.5 s** (3.5×)
    and the test suite from 32 s → 9 s.
  - Logic lives in `connectivity_worker.py` (`run()` + helpers, stdlib +
    pcbnew only); `connectivity_tools.py` is the thin MCP wrapper
    (validate-cheap → spawn worker by path → parse). Result framed by
    `<<<CONN_JSON>>>…<<<CONN_END>>>` markers so pcbnew stdout chatter
    can't corrupt the parse. 8 tests green; pylint 10/10.

## 2026-05-29 — `check_connectivity` ratsnest tool

### Added

- **`check_connectivity` tool** (`kicad_mcp/tools/connectivity_tools.py`)
  — closes the long-standing gap that headless `kicad-cli pcb drc` runs
  no "unconnected items" check. Uses the `pcbnew` Python API (KiCad's own
  engine, no GUI) and fills zones first so pour-connected pads are not
  falsely reported. Three modes via `mode`: `overview` (global
  unconnected count + nets that split into >1 cluster), `pad` (the
  electrical cluster of one `REF.PAD`), and **`whatif x_mm y_mm`** —
  removes the nearest via/track in memory, recomputes, and reports which
  pads would be orphaned (`load_bearing` flag). Read-only: `whatif`
  mutates only the in-memory board. Core logic in module-level
  `check_connectivity_impl` for unit testing; 8 tests in
  `tests/test_connectivity_tools.py` (skipped without `pcbnew`).
- Fixed `polar_grid` to call `to_local_path` in the tool body itself
  (it only normalised inside the per-op helpers), so it passes the
  dynamic path-normalisation test.

## 2026-05-29 — `polar_grid` tool for circular PCBs

### Added

- **`polar_grid` umbrella tool** (`kicad_mcp/tools/polar_grid_tools.py`)
  with 12 operations under an `op` parameter dispatcher:
  `polar_to_xy`, `xy_to_polar`, `ring_radius`, `align_rotation`,
  `place_on_ring`, `place_on_spoke`, `align_outer_components`,
  `add_polar_arc`, `add_radial_segment`, `add_polar_via`,
  `list_ring_occupants`, `check_grid_config`.
- Codifies the polar-coordinate workflow for circular PCBs (motor
  drives, coil boards, etc.): N concentric rings between
  `r_inner..r_outer`, M radial spokes, components rotated radially,
  arcs on `arc_layer` (typ. `In1.Cu`), straight radial stubs on
  `radial_layer` (typ. `In2.Cu`), vias at grid intersections.
- reference-Mainboard defaults out of the box: centre (148.5, 105), 31
  rings r=13.5..30 step 0.55, 18 spokes every 20°. Override any
  field for other boards.
- Footprint long-axis auto-detect for rotation: caps/Rs/diodes
  (long-X) vs SOIC/SOT-23/TO-252/Chilisin-inductors (long-Y, +90°
  offset).
- Eliminates the ad-hoc Python snippets that polar layouts otherwise
  require (theta math, ring lookup, snap-to-spoke, center-mode arc
  midpoint, bulk rotation of outer-ring components).

## 2026-05-26 — B.Cu pad double-flip bug closed

### Fixed

- **`compute_pad_world_positions` / `place_at_pivot` returned wrong-pad
  positions for B.Cu footprints** (Bug 10 in `Bug.md`). The transform
  applied an X-mirror to pad-local coords whenever the footprint sat on
  B.Cu — but KiCad's `FOOTPRINT::Flip` already mirrors `PAD::m_pos.X`
  in-place on flip, so the on-disk pad-rel value is post-flip. The
  redundant mirror swapped pad numbers across the footprint's X-axis
  (Pin 1 ↔ Pin 16 on a SOIC-16), with the result that downstream
  routing tools placed vias on `+3V3` thinking they were on `nFAULT_DRV1`
  (real reproducer: reference-Mainboard V14_07, U_597 SOIC-16 on B.Cu).
  Fix in `pcb_geometry_tools.py::_transform_pad_world` (hardcoded
  `flipped=False`) and parallel fix in `pcb_patch_tools.py::
  place_at_pivot_text` (rotated_pivot calc uses `flipped=False`).
  Both fixes have new tests in
  `tests/test_pcb_geometry_tools.py::TestPadWorldTransform::
  test_bcu_realistic_soic_pin1` and
  `tests/test_place_at_pivot.py::TestLayerSwap::
  test_bcu_pad_pivot_no_double_flip`. Existing fixtures
  `test_bcu_flip_mirrors_x` / `test_bcu_with_rotation` /
  `test_extracts_world_pads` updated to assert the correct post-flip
  behavior (B.Cu world-coord = `fp + rotate(pad_rel)` with no further
  mirror). Existing 1424 tests still pass.

## 2026-05-23 — leftover TODOs cleared

### Fixed

- **Multi-sheet `build_schematic` now emits hierarchical labels for
  cross-sheet signal nets** — closes the long-standing TODO in
  `generation_tools.py:159`. Pre-fix, `find_intersheet_nets()` returned
  the right set but the result was never threaded into `build_schematic`,
  so every sub-sheet wrote local `(label "SIG_X" …)` while the root's
  sheet-symbol exposed a hierarchical pin of the same name — KiCad's
  ERC then reported "no connection" for every cross-sheet net. The fix
  adds an `intersheet_nets` kwarg to `build_schematic` (forwarded to
  `_emit_wires_and_labels`) and a third mode `is_hierarchical=True` to
  `_place_label_with_stub`; precedence is `hierarchical > global > local`
  (a cross-sheet signal that *also* looks like a power rail still emits
  hierarchical because the root pin demands it). Power nets stay on the
  global-label / real-power-symbol path — `find_intersheet_nets()`
  excludes power-typed nets from its return set.
  Tests: `tests/test_intersheet_labels.py` — 8 cases covering
  `_place_label_with_stub` (label-kind precedence) and end-to-end
  `build_schematic` (hierarchical-vs-local routing per-net).

### Changed

- **`netlist_parser._build_netlist` TODO retired** as design intent,
  not pending work. The label-only fallback exists because the primary
  path in `extract_netlist()` delegates to
  `kicad-cli sch export netlist --format kicadsexpr` (since the
  2026-04-29 Bug 2 resolution), which already does full pin-level
  connectivity tracing via KiCad's own engine. Re-implementing
  wire-tracing in pure Python would duplicate substantial KiCad
  internals against an upstream source of truth. Replaced the
  misleading "TODO: implement netlist building algorithm" comment with
  an honest design note and tightened the fallback's `partial_reason`
  text to say *why* it's partial (kicad-cli unavailable, intentional
  fallback) rather than implying an unfinished method.

## 2026-05-23 — coord-system audit cleanup (round 3)

### Fixed

- **`flip_footprint_to_layer` X-mirrored the footprint anchor** on
  PCBs whose footprint header writes `(at …)` *before* `(uuid …)` —
  the order this server's own `generate_project` emits. The header-
  skip in the X-mirror pass was a regex requiring
  `(uuid …) (at …)`; on the at-first ordering the regex returned no
  match, `exclude_end` stayed at 0, and the subsequent
  `at_pat.sub(mirror_at, …)` ran over the anchor too. Result: every
  flip moved the footprint to `(−x, y)` instead of preserving its
  world position — pads landed off the board on round-2 generator
  output, even though the existing `MIN_PCB` fixture (uuid-first
  ordering) showed the tool as passing. Replaced the regex with a
  depth-walking helper `_find_footprint_header_at_end()` that
  identifies the first `(at …)` at depth 1 inside the footprint
  block regardless of sibling-tag order. Two new regression tests
  cover the at-first ordering: `test_anchor_preserved_when_at_precedes_uuid`
  (world-position contract) and `test_flip_idempotent_round_trip_at_first`
  (numeric idempotency). New fixture `MIN_PCB_AT_FIRST_HEADER`
  mirrors the generator's output shape.

## 2026-05-23 — coord-system audit cleanup (round 2)

### Fixed

- **`generators/schematic_patcher.py:_fmt` was `:.6f`** — but schematic
  files are written with 100 nm IU (4 decimal mm), so every
  `add_schematic_symbols` / `add_schematic_label` / `add_schematic_wire`
  / `connect_pins`-style patch produced text that KiCad's next save
  silently normalised. Diffs flagged spurious "changes" on every
  round-trip and pins could land 0.0007 mm off the 1.27 mm grid.
  Reduced to `:.4f` to match the file-format norm.
- **`generators/ltspice2kicad/builder.py:207-208` emitted `(mirror x)`**
  for an LTspice `mirror=true` symbol, but the matching origin-solver
  in `main.py:210-212` negated X (= `(mirror y)` semantics by KiCad).
  Result: every mirrored LTspice symbol was rendered in KiCad with
  the wrong mirror axis, so wires/labels routed to the pre-flip pin
  set landed on the wrong side of the symbol. Builder now emits
  `(mirror y)`; main.py's X-negation stays — semantics consistent.
- **`pcb_patch_tools.py:_render_footprint_block` `mirror_to_bcu`** and
  **`flip_footprint_to_layer` layer-pair table** missed
  `F.CrtYd↔B.CrtYd`, `F.Adhes↔B.Adhes`, and `F.Silkscreen↔B.Silkscreen`
  (KiCad 8+'s new name for `F.SilkS`). Modern LCSC footprints emit
  courtyard / adhesive / silkscreen on every part, so an
  `add_placeholder_footprint(layer="B.Cu")` call produced footprints
  with courtyards / adhesive lines on the **wrong** side → DRC
  "Courtyard on wrong side" + pick-and-place miscentroids. Both
  tables now cover every paired F.*/B.* layer KiCad knows.
- **`generators/schematic/route.py:_extract_pin_positions` ignored the
  `_mirror` property** of placed symbols — it read `_rotation` only,
  so any LTspice-imported `(mirror …)` symbol routed wires to pin
  positions on the wrong side. Now applies mirror **before** rotation
  per KiCad's `SCH_SYMBOL::SetOrientation` semantics
  (`(mirror x)`=Y-negation, `(mirror y)`=X-negation). The
  `_pin_pos_cache` key was also bumped from `lib_id` to
  `(lib_id, rotation, mirror)` — the flat cache returned stale
  oriented positions when two instances of the same lib_id had
  different orientations.

### Changed

- **`clone_routing._emit` (`pcb_patch_tools.py:1948-1956`) detects
  net-format board-wide** via `pcb_net_format(pcb_text)` instead of
  the prior per-source-block heuristic (`_NET_STR_RE.search(block)`).
  Same behaviour on homogeneous boards, but the rare case of cloning
  a legacy index-form block onto a string-form board is now correct.
- **`pcb_patch_tools.py:cluster_around` Y-convention aligned to KiCad
  Y-down** — companion-radial `ty = fy + radius·sin(phi)` was
  math-Y-up (north of parent landed *south* of it), while
  `cluster_block_outside_pcb` had Y-down. Fixed to `ty = fy − radius·sin(phi)`
  so user-specified angles match the rendered placement.

## 2026-05-23 — coord-system audit cleanup

### Fixed

- **`flip_footprint_to_layer` X-mirrors instead of Y**
  (`pcb_patch_tools.py:3942/3960`). Pre-fix the tool documented and
  implemented a Y-mirror for F↔B flips, contradicting KiCad's own
  `FOOTPRINT::Flip(FLIP_DIRECTION::LEFT_RIGHT)` semantics — pads
  ended up vertically gespiegelt on B.Cu. Renamed the `mirror_y`
  parameter to `mirror` for the same reason. Three new tests verify
  world-position preservation, idempotency on a F→B→F round-trip,
  and the no-op path. Tool was untested before this session.
- **Three further patcher tools were not format-aware** after the
  morning's string-form net-tag fix — they still wrote blind
  `(net N "name")` index-form pad tags and refused to find string-
  form nets:
  - `_patch_pad_with_net` + `_patch_pcb_nets` (= the engines behind
    `patch_pcb_nets_from_netlist` and `update_pcb_from_schematic`):
    now route through a new `ensure_pad_net_tag()` helper in
    `pcb_net_format.py` and emit the short form on string-form
    boards.
  - `patch_track_nets_from_pads_text`: now uses `ensure_net_tag()`
    for routing tags.
  - `delete_pcb_routing_text`: the `name_to_id` map was built only
    from `(net N "name")` table entries and was empty on string-
    form PCBs → `delete_pcb_routing(net_name=…)` failed with "Net
    not found" even when the net was tagged on every block. Now
    scans both `(net N "name")` table entries AND `(net "name")`
    short refs; `_block_matches` matches by name instead of by id.
  New helper `ensure_pad_net_tag()` covers the pad-specific case
  where index-form pads carry the full `(net N "name")` (both id
  AND name), not the routing-element-only `(net N)` short form.
  Tests added for all four (`TestStringFormPcb` classes in
  `test_pcb_patch_tools.py` and `test_delete_pcb_routing.py`).
- **`_patch_fp_pose` Pad-rot now additive** instead of overwriting.
  Footprint rotation `Δ` is applied as `new_pad_rot = lib_pad_rot +
  Δ` so library pads with a non-zero rotation (45°-rotated SMT
  pads, chamfered QFN corners) keep their orientation after a
  ``place_at_pivot`` / ``clone_layout_around_pivot`` move. Pre-fix
  every pad's rot was set to `Δ` unconditionally, destroying any
  non-zero lib rotation. Two new tests in
  `TestPatchFpPoseAdditiveRotation`.

### Changed

- **PCB-side decimal precision unified at `:.6f` (= 1 nm IU)**;
  schematic-side `_fmt` reduced from `:.6f` to `:.4f` (= 100 nm IU)
  to match the file format's own truncation behaviour. Mixed
  precisions across `pcb_geometry_tools.py` and `pcb_patch_tools.py`
  collapsed to one PCB norm. CHANGELOG of affected tests updated
  inline.
- **`generators/pcb/builder.py` now uses `pcb_local_to_world()`** for
  pad world coordinates instead of its own math-CCW rotation matrix
  (Footgun #1: produced 0.4 mm pad-position errors for footprints at
  90° rotation).
- **`export_gerbers` / `export_drill` / `export_pos` auto-detect a
  non-zero `aux_axis_origin`** and pass the matching origin flags
  (`--use-drill-file-origin`, `--drill-origin plot`) so fab-bound
  exports stay aligned to each other (Footgun #7). `export_pos`
  additionally defaults to mm units (KiCad-CLI's bare default is
  inch, which surprises most modern fabs). Each tool returns a new
  `origin: "aux" | "page"` field so the LLM can report which
  reference was used. Opt out via `use_drill_file_origin=False`.

## 2026-05-23 — string-form net-tag fix + coord-system cheat-sheet

### Documentation

- **CLAUDE.md gains a "KiCad-Koordinatensysteme" section**: cross-
  subsystem reference for units (PCB nm IU, SCH 100-nm IU, kipy nm-
  int64 wire), Y-axis (down everywhere except inside `lib_symbols`
  pin frame), rotation (KiCad's math-CW RotatePoint that *appears*
  CCW because of the screen-Y flip), B.Cu side-flip (X-mirror, not
  Y — `FLIP_DIRECTION::LEFT_RIGHT`), schematic `(mirror x/y)`
  conventions (about-axis, not the negated component), kicad-cli
  export origins (Page vs Aux vs User), and the 11 most common
  footguns. Each claim is cross-linked to either a KiCad master-
  branch source file on GitLab or to the matching helper in this
  repo (`pcb_geometry.py`, `sch_geometry.py`, `ipc_tools.py`).
  Replaces ad-hoc coord-system folklore scattered across individual
  tool docstrings.

### Fixed

- **Geometry emitters now respect the PCB's net-tag convention**
  (`kicad_mcp/utils/pcb_net_format.py` new; `pcb_geometry_tools.py` +
  `pcb_patch_tools.py` patched). KiCad accepts two equivalent ways to
  reference a net inside a `(segment)`/`(arc)`/`(via)`/`(zone)`: the
  indexed form `(net N)` plus a top-level `(net N "name")` table, or
  the short form `(net "name")` with no table. The SWIG `pcbnew`
  writer emits the indexed form on classic boards; KiCad 10 round-
  trips the string form, which some hand-curated PCBs (e.g. reference
  V13 mainboards, 1246 short-form refs / 0 table entries) use
  exclusively. The geometry emitters were hard-wired to the indexed
  form: their `_ensure_net` indexed-lookup found no table and
  synthesised one with index 0, so every inserted track/via/arc/zone
  silently landed on `(net 0)` (= no-connect) and the file grew a
  synthetic `(net 0 "name")` table entry at the top. The fix moves
  format detection + tag emission into a shared
  `kicad_mcp.utils.pcb_net_format` module (`pcb_net_format(text)` →
  `"string" | "index"`; `ensure_net_tag(text, name)` → ready-to-embed
  S-expression fragment), and all four emitters
  (`add_track_to_pcb`, `add_arc_to_pcb`, `add_via_to_pcb`,
  `add_zone_pour_to_pcb`) plus the patcher's `add_segment` now route
  through it. On a string-form PCB the tools emit `(net "name")` and
  never touch the (non-existent) net table; on an indexed PCB
  behaviour is byte-identical to before. Result-dict gains a
  `net_format: "string"|"index"` field and `net_id: None` signals
  the string-form case to LLM callers. Also fixes the geometry pad
  parser, which previously only recognised `(net N "name")` pad
  tags and reported `net_name=None` on string-form pads (so
  `add_track_to_pcb`'s net-fallback didn't pick up the source pad's
  net) — `_PAD_NET_STR_RE` now covers the short form. Tests:
  `tests/test_pcb_net_format.py` (new, 9 cases on the helper module)
  plus 5 new cases in `tests/test_pcb_geometry_tools.py` exercising
  via/track/arc/zone on a string-form fixture and a multi-edit-
  no-drift check. Verified end-to-end against the real reference
  V13_4 mainboard (`reference_Mainboard_V13_4.kicad_pcb`, 49 391 lines,
  pure string form): a synthetic `nFAULT_DRV4` via lands on the
  named net with no table pollution.

## 2026-05-22 — file-text cache (speed)

### Added

- **File-text cache** (`kicad_mcp/cache/file_cache.py`, new `cache/`
  package) — eliminates redundant disk reads of the same
  `.kicad_pcb` / `.kicad_sch` across MCP tool calls. The server is one
  long-lived process; on a OneDrive-synced disk a 1.7 MB read costs a
  fixed ~16 ms every time (sync filter + UTF-8 decode — the OS page
  cache does not hide it). `get_text(path)` revalidates via a cheap
  `os.stat` fingerprint (mtime_ns + size) and serves cached text on a
  match; `put_text(path, text)` keeps the cache warm after a tool
  writes. Measured: **~14.7 ms read → ~0.10 ms cache hit, ~142×** per
  redundant read (realpath memoized — it dominated the hit cost). The
  mtime fingerprint doubles as the staleness guard: a save from the
  KiCad GUI changes mtime → automatic cache miss → fresh read. LRU
  bounded to 5 entries; `invalidate()` / `cache_status()` for control
  and diagnostics.
- Cache wired into the two text-patcher tool modules:
  `pcb_patch_tools.py` (24 reads → `get_text`, 23 writes + `put_text`)
  and `pcb_geometry_tools.py` (5 reads, 4 writes) — minimal-invasive,
  only the thin I/O wrappers changed, `_text` companions untouched.
  `pcb_batch` benefits automatically.
- Tests: `tests/test_file_cache.py` — 10 new (hit/miss via
  fingerprint, mtime-change, `put_text`, realpath key normalization,
  LRU eviction, invalidate idempotency, `cache_status`, missing file).
  Full suite stays green.

## 2026-05-22 — clone_routing

### Added

- **`clone_routing`** (`kicad_mcp/tools/pcb_patch_tools.py`) — clones
  tracks/arcs/vias from one anchor's region onto N sibling anchors.
  Unlike `clone_layout_around_pivot` (footprint placement only, pure
  rotation), the source→target transform is *fitted* from >=3 shared
  pad positions via an orthogonal Procrustes solve, so it yields a
  rotation OR a reflection — whichever the actual pads demand. Mirrored
  / dihedral anchor groups are therefore cloned correctly (a plain
  `R(trot−srot)` rotation lands 5–7 mm off the target pads in that
  case). Per-target `net_map` substitutes the per-instance net names;
  `clear_target` wipes prior copper on the mapped nets in the target
  region first. Pure text-companion `clone_routing_text` registered in
  `PCB_PATCH_TEXT_FNS` (chainable via `pcb_batch`), `dry_run` supported.
  Tests: `tests/test_clone_routing.py` — 9 new (rotation, reflection,
  net-substitution, dry-run, clear_target idempotency, 4 error paths).
  Tool count 110 → 111.

## 2026-05-21 — cluster_block_outside_pcb

### Added

- **`cluster_block_outside_pcb`** (`kicad_mcp/tools/pcb_patch_tools.py`)
  — high-level placement helper for round-PCB initial layouts. Reads
  the `kicad-mcp.group` property from a `.kicad_sch`, finds all member
  refs in the corresponding `.kicad_pcb`, and places them in an
  N-column tangential grid at polar position `(cluster_phi_deg,
  cluster_r_mm)` relative to the PCB centre. Each footprint is rotated
  according to `align_mode` (radial_in / radial_out / tangential_cw /
  tangential_ccw). Internally loops `place_at_pivot_text` so
  pad-shape rotation propagation is preserved (avoids the
  pad-Rechteck-shorting-Bug). Universal-Callable compliant: pure
  `cluster_block_outside_pcb_text(pcb_text, refs, ...)` companion
  (refs pre-resolved by the MCP wrapper), `dry_run` parameter,
  registry entry via `@_register_text_fn`.
- **Tests** `tests/test_cluster_block_outside_pcb.py` — 12 cases:
  pure-text fn happy path / radial-in rotation / empty refs / invalid
  align_mode / invalid grid_cols / ghost ref / idempotency; MCP
  wrapper happy path / dry-run preservation / group filtering /
  block-not-found / missing-pcb / missing-sch.
- Decision-Matrix entry in `CLAUDE.md` linking the "Initial-Placement
  of a kicad-mcp.group as a tangential grid outside the PCB" workflow
  to this tool.
- Tool count bumped 109 → 110 in `CLAUDE.md` projektstatus block.

## 2026-05-21 — Curated action index extended (50 → 145)

### Added

- **`scripts/extend_kicad10_actions.py`** — idempotent patcher that
  appends 95 high-value KiCad-10 actions to
  `kicad_mcp/data/actions/kicad10.json`. New coverage: PCB-Setup
  dialogs (Stackup, Net Classes, Constraints, Layers, Solder Mask),
  Plot/Fabrication outputs (PDF, SVG, DXF, Position file), Length
  tuner (single / diff pair / skew), Diff-Pair routing, full Eeschema
  placement primitives (No-Connect, Bus-Entry, Hierarchical Label,
  Text, Image), Hierarchy navigation (Next/Prev sheet, Leave/Up
  hierarchy), Symbol/Footprint-Editor entry points, Grid cycling,
  3D-Viewer attribute toggles, common Edit operations
  (Move/Drag/Rotate/Mirror/Edit-Value/-Reference/-Footprint/Swap/Autoplace).
- `_meta.version` bumped to `1.1`, `_meta.total_actions` added (= 145).

### Why

`lookup_kicad_action` / `list_kicad_actions` are backed by this curated
index — the previous 50-action seed missed entire Setup-dialog
workflows ("Stackup", "Net Classes", "Constraints") that users
frequently ask about. ~145 entries cover the bulk of menu/dialog
look-ups while staying maintainable.

### Re-run safety

Script is idempotent: it matches by `id` and skips duplicates. Second
run reports `Added 0, skipped 95`.

## 2026-05-18 — Universal Callable convention + pcb_batch

### Added

- **`pcb_batch`** (`kicad_mcp/tools/pcb_patch_tools.py`) — chain N
  file-edit operations against a single ``.kicad_pcb`` in one
  open/write cycle. Dispatches via the new `PCB_PATCH_TEXT_FNS` /
  `PCB_GEOMETRY_TEXT_FNS` registries; supports `dry_run` and
  `halt_on_error`. Eliminates the N×open+parse+write penalty when a
  workflow needs many small mutations on a large PCB file (especially
  on synced drives like OneDrive / Dropbox).
- **Universal Callable convention** for file-edit tools:
  1. Pure `<tool>_text(pcb_text, **args) -> (new_text, result_dict)`
     companion to every MCP-decorated file-edit tool.
  2. Registry decorator `@_register_text_fn("<tool_name>")` populates
     `PCB_PATCH_TEXT_FNS` / `PCB_GEOMETRY_TEXT_FNS`. The generic
     `pcb_batch` tool dispatches through these registries.
  3. `dry_run: bool = False` keyword on every MCP wrapper.
  4. Idempotency documented + verified.
- **CLAUDE.md "Neues Tool hinzufügen" Pflicht-Checkliste** extended
  with section 8 "Universal Callable" enumerating the four
  requirements with a copy-paste-ready code skeleton. New tools that
  touch `.kicad_pcb` / `.kicad_sch` must conform.

### Changed

- **6 tools refactored** to the Universal Callable convention while
  preserving public-API backwards compatibility (existing callers
  still work; the wrappers just delegate to the new `_text`
  counterparts and accept an extra `dry_run`):
  - `place_at_pivot`
  - `clone_layout_around_pivot`
  - `delete_pcb_routing`
  - `add_arc_to_pcb`
  - `add_via_to_pcb`
  - `update_pcb_from_schematic`

### Tests

- `tests/test_universal_callable.py` — 15 dynamic tests over every
  registered `_text` function: signature shape, dry_run keyword on the
  MCP wrapper, idempotency for no-op invocations, plus an opt-out list
  for UUID-emitting tools.
- `tests/test_pcb_batch.py` — 10 cases covering operation chaining,
  dry-run preview, halt-on-error vs continue-on-error semantics,
  unknown-tool rejection, argument mismatch handling, empty list
  rejection, missing-PCB error.

Tool count: **107** (was 106). Full suite: 1115 passed, 3 pre-existing
non-related failures.

## 2026-05-18 — update_pcb_from_schematic (F8-headless)

### Added

- **`update_pcb_from_schematic`** (`kicad_mcp/tools/pcb_patch_tools.py`)
  — the headless equivalent of GUI's Tools → "Update PCB from
  Schematic" (F8). Diffs the schematic's component table against the
  PCB and applies, with per-operation switches:
  * **add_new** — load missing footprints from the bundled library
    and stage them at a configurable position outside the board.
  * **update_values** — rewrite the Value property when schematic ≠ PCB.
  * **update_footprints** — when a component's Footprint property
    changed in the schematic, reload the new `.kicad_mod` while
    preserving position / rotation / side / reference.
  * **remove_orphans** — delete footprints that no longer have a
    schematic counterpart (off by default — safer to keep until the
    user confirms).
  * **sync_nets** — chain `patch_pcb_nets_from_netlist`'s pad-net
    assignment so newly added pads get their nets in the same pass.
  Supports `dry_run=True` for a preview, and reports missing library
  entries so callers know which footprints to add manually.
- `tests/test_update_pcb_from_schematic.py` — 10 cases stubbing the
  kicad-cli netlist export so the test runs offline. Covers diff
  detection, each operation in isolation, orphan opt-in semantics,
  missing-library reporting, and error paths.

Tool count: **106** (was 105).

## 2026-05-18 — buried/blind via support

### Added

- **`add_via_to_pcb`** (`kicad_mcp/tools/pcb_geometry_tools.py`) — drop a
  standalone via at an arbitrary world coordinate on a chosen
  ``layer_pair`` (defaults to F.Cu / B.Cu through-via). Use case:
  inner-layer-switch vias that sit between layer-1 routing and
  layer-2 routing on a 4-layer board, placed at offsets from any
  pad so they clear neighbouring IC exposed pads.
- `tests/test_buried_vias.py` — 11 cases covering both the new tool
  and the `via_layers` extension to `add_track_to_pcb`.

### Changed

- **`add_track_to_pcb`** now accepts `via_layers`, `via_size_mm`,
  `via_drill_mm` parameters and forwards them to the via emitter. The
  default ``via_layers=None`` keeps the historical through-hole
  behaviour; pass e.g. ``["In1.Cu", "In2.Cu"]`` for a buried via.
  Returns ``via_layers`` in the response dict so the agent sees what
  was actually emitted.
- **`_via_block`** (internal) now takes a ``layer_pair`` parameter
  instead of hard-coding the F.Cu / B.Cu layer pair. All callers
  pass the pair through explicitly; backward-compatible default keeps
  through-vias the historical behaviour.

Tool count: **105** (was 104).

## 2026-05-18 — add_arc_to_pcb

### Added

- **`add_arc_to_pcb`** (`kicad_mcp/tools/pcb_geometry_tools.py`) — insert
  a circular arc segment into a `.kicad_pcb`. Two modes:
  * **Center mode** (preferred): pass `(center_x_mm, center_y_mm)` and
    the midpoint is computed automatically via `short_arc_mid_xy`,
    eliminating the long-way-around bug that plagues hand-rolled
    `(arc start mid end)` emissions.
  * **Explicit-mid mode**: pass `(mid_x_mm, mid_y_mm)` directly for
    cases that need the long-way arc deliberately.
  The new net is auto-added to the PCB net table on first use.
- `tests/test_add_arc_to_pcb.py` — 10 cases covering quarter-arc
  geometry, the V12 P0 short-mid wrap regression, explicit-mid mode,
  collinear sentinel, mode validation (both / neither / coincident
  endpoints), and net-handling (new and reused).

Tool count: **104** (was 103).

## 2026-05-18 — delete_pcb_routing

### Added

- **`delete_pcb_routing`** (`kicad_mcp/tools/pcb_patch_tools.py`) — delete
  top-level routing elements (`segment` / `arc` / `via`) from a
  `.kicad_pcb` filtered by net name, copper layer, kind subset, and / or
  bbox. Supports `dry_run=True` for a preview of the first 20 matches.
  Closes the "I need to wipe the prior routing for this net before I
  retry" workflow gap; today users hack regex sweeps which silently
  drop unrelated elements when nested parentheses confuse the pattern.
- `tests/test_delete_pcb_routing.py` — 14 cases covering net filter,
  layer filter (including via layer-pair semantics), bbox filter, kind
  subset, dry-run idempotency, second-call zero-deletions, top-level-
  only invariant (footprint contents never touched), and error paths.

Tool count: **103** (was 102).

## 2026-05-18 — clone_layout_around_pivot

### Added

- **`clone_layout_around_pivot`** (`kicad_mcp/tools/pcb_patch_tools.py`) —
  replicate a manually-placed peripheral group from one anchor onto N
  other anchors, preserving each peripheral's *relative* offset and
  rotation in the source's local frame. Eliminates the per-DRV /
  per-IC "place 6 caps and a resistor 6× by hand" boilerplate. Pad-
  shape rotation match is applied automatically (lock-step with the
  footprint rotation).
- `tests/test_clone_layout.py` — 11 cases covering 4-anchor cardinal
  layout, source-pose-unchanged invariant, pad lokal-rot propagation,
  and 7 error paths (missing PCB / source / target refs, length
  mismatch, empty lists).

Tool count: **102** (was 101).

## 2026-05-18 — compute_pad_world_positions CW math fix

### Fixed

- **`_transform_pad_world` rotation convention.** The helper used by
  `compute_pad_world_positions`, `add_track_to_pcb`, and any downstream
  routing tool applied a math-CCW rotation matrix to pad-local offsets.
  In KiCad's y-down screen coords this produced visually-CW results,
  disagreeing with KiCad's GUI and with the DRC engine by 0.4 mm for a
  0402 at 90° rotation (and larger errors at other rotations). Fixed by
  delegating to the canonical math-CW `pcb_local_to_world` helper in
  `kicad_mcp/utils/pcb_geometry.py`. **This is a behaviour change** —
  any caller that was compensating for the bug downstream will now over-
  correct. The three pre-existing tests in `test_pcb_geometry_tools.py`
  whose expected world coordinates were derived from the buggy math have
  been updated; the file format itself is untouched.

## 2026-05-18 — place_at_pivot + pcb_geometry math helpers

### Added

- **`place_at_pivot`** (`kicad_mcp/tools/pcb_patch_tools.py`) — single-footprint
  pose tool that drops a chosen pivot point (footprint anchor, named pad, or
  bbox centre) at a target world coordinate and propagates rotation to every
  pad shape. Pad `(at lx ly rot)` lokal-rot is updated in lock-step with the
  footprint header rotation so saved pad rectangles match what the GUI's
  right-click → "Rotate" produces — closes a known correctness gap when
  text-patching individual footprint rotations. Optional `auto_rotation` in
  `{radial_in, radial_out, tangential_ccw, tangential_cw}` computes the
  rotation against a supplied centre point — drop-in primitive for radial
  / circular layouts.
- **`kicad_mcp/utils/pcb_geometry.py`** — pure-math helpers used by the new
  tool and by future placement / routing work. Exposes `wrap_signed`,
  `phi_short`, `short_mid_phi`, `short_arc_mid_xy` (the wrap-aware arc-mid
  needed to avoid drawing the long way around when constructing KiCad
  `(arc start mid end)` blocks), `pcb_local_to_world` /
  `pcb_world_to_local` using the canonical CW-screen-convention transform,
  `align_radial_rotation`, and `compute_fp_bbox` (reads a `.kicad_mod` and
  returns the local-frame bbox over pads + F.Fab + F.CrtYd + F.SilkS).
- **`featureplan.md`** — collected backlog of placement / routing / sync /
  validation features the server is missing, with extend-vs-new mapping
  for each item.

### Tests

- `tests/test_pcb_geometry_helpers.py` — 28 cases covering angle wrap, the
  short-mid bug pattern (φ=2.4° → 351.4° must land at ~357°, not the
  diametrically-opposite 177°), CW-transform round-trip, radial alignment
  cardinal directions, and bbox extraction from `(pad …)` + `(fp_line …)`.
- `tests/test_place_at_pivot.py` — 11 cases covering anchor / pad / bbox
  pivots, auto-rotation, layer swap, error paths (missing PCB, unknown
  ref, unknown pad, missing mod path, invalid layer / mode), and
  idempotency (two identical calls → byte-identical PCB).

Tool count: **103** (was 102).

## 2026-05-15 — Layer S: hide-flag extension + DNP-read fix

### Added

- **`update_symbol_property` now accepts `hide_reference`,
  `hide_value`, `hide_footprint`, `hide_datasheet`,
  `hide_description`** (each `"yes"` / `"no"` / `""`).
  `add_schematic_symbols` emits new instances with all Property
  lines visible (no `(hide ...)` clause), which clashes with the
  common convention of hiding Reference + Footprint so only the
  Value text shows up in the schematic. Before this change the
  user had to open the GUI and toggle "Show" on each property
  individually because no MCP tool could rewrite the visibility
  flag (`update_symbol_property` only handled the four
  `(dnp/in_bom/on_board/in_pos_files)` flags + textual property
  values). New flags insert a fresh `(hide ...)` line right after
  the property's `(at x y rot)` clause when none exists, rewrite
  it in place when one does, and noop on idempotent re-runs.
  Tests added in `TestUpdateSymbolProperty`: insert-when-missing,
  toggle-existing, idempotency, invalid-value-errors. Tool count
  unchanged (extension of existing tool).

### Fixed

- **`list_schematic_components` returned `dnp: true` for every
  symbol.** The DNP check in
  `kicad_mcp/tools/schematic_tools.py:53` was
  `find_node(sym, "dnp") is not None` — but KiCad-10 always emits
  the `(dnp yes|no)` node, so "node exists" is not the same as
  "DNP is set". Now reads the node's value and compares to
  `"yes"` (case-insensitive). Same pattern as the existing
  `in_bom` reader below it. Visible to LLM workflows that
  filtered by DNP before deciding whether a part shipped to the
  BOM.


## 2026-05-15 — Layer S: surgical property edit

### Added

- **`update_symbol_property(sch_path, refs, value?, footprint?, datasheet?,
  description?, dnp?, in_bom?, on_board?, in_pos_files?, properties_json?)`**
  in `kicad_mcp/tools/sch_patch_tools.py`. Surgical property /
  flag edit for already-placed symbols. Closes the gap where the
  only previous workflow for "change R10 value from 1k to 22k" was
  `delete_schematic_items` + `add_schematic_symbols`, which triggers
  MCP-snap (position drifts by ±1.27 mm), BBox-conflict checks
  against adjacent pins, and wire-anchor invalidation. Properties
  are updated only when they already exist on the symbol (no
  auto-creation); flags update unconditionally because every
  instance carries the four `(dnp/in_bom/on_board/in_pos_files
  yes|no)` lines. Returns per-ref `{changed: {field: [old, new]}}`
  for traceability and is idempotent on re-run. Tests:
  `tests/test_sch_patch_tools.py::TestUpdateSymbolProperty` (6
  tests covering value+footprint, flag flip, idempotence, unknown
  ref → not_found, no-update error, invalid flag value error).
  Decision-Matrix entry under "Symbol-Property / DNP-Flag eines
  bereits platzierten Bauteils ändern".

  Tool count: **101 → 102**.


## 2026-05-15 — Layer R: datasheet-vs-implementation review

### Added

- **New tool category: Layer R — review tools.** Three MCP tools in
  `kicad_mcp/tools/review_tools.py` that assemble data for an
  LLM-driven schematic-vs-datasheet review. Tools prepare structured
  payloads + images; the reviewing model does the actual analysis.

  - `review_ic_against_datasheet(ic_reference, project_path,
    datasheet_pdf?, datasheet_page?, pin_range_start?, pin_range_end?,
    padding_mm?, output_dir?)` — per-IC. Produces
    `<project_dir>/review/<REF>/{review_payload.json, review_brief.md,
    schematic_region.png, datasheet_p<NN>.png}`. Pin-by-pin connectivity
    (net + connected refs with value/footprint), schematic region cropped
    to the IC + its periphery, rasterised datasheet page (300 dpi),
    filtered BOM-local, hard-wired review prompt with placeholders.
    Datasheet-path resolution chain: `datasheet_pdf` argument →
    `<project_dir>/docs/<value>.pdf` convention → symbol `Datasheet`
    property (if local). Pin-consistency check: symbol pins vs. PCB
    pads (best-effort, warnings if PCB present).

  - `review_system_interconnect(project_path, output_dir?)` — system-
    wide audit data. Power tree (each net + consumer count + source
    hint), pull-up / pull-down detection (R-components bridging signal
    nets and power/ground; flags duplicates on the same net), decoupling
    cap audit (per IC VCC pin: caps on the same net), bus peers
    (I2C / SPI / UART / USB / RESET / BOOT pattern match on net names).
    Output `<project_dir>/review/system/{system_payload.json,
    system_brief.md}`.

  - `list_missing_datasheets(project_path)` — read-only inventory tool.
    For every unique IC `Value` in the schematic, reports whether a PDF
    exists at `<project_dir>/docs/<value>.pdf` and surfaces the
    symbol's `Datasheet` property as a download hint. Used as the
    pre-review Phase 0 step so the LLM can ask the user up front which
    datasheets to fetch.

- **New generators submodule `kicad_mcp/generators/review/`:**
  - `_svg_crop.py` — re-write the `viewBox` of a `kicad-cli sch export
    svg` output to a schematic-mm bbox + padding, then cairosvg-render
    to PNG. Defensive: falls back to full-sheet render if the SVG
    header can't be parsed or the bbox lands outside the sheet extent.
  - `_pdf_raster.py` — `pdfplumber.page.to_image(resolution=300).save(...)`.
    Lazy `import pdfplumber` with friendly install hint, matching the
    `circuit_block/_pdf_extract.py` pattern.
  - `_pin_check.py` — cross-check symbol-pin numbers against
    `_parse_pcb_pads_per_ref` (reuses the helper from `pcb_patch_tools`).
    Returns warnings rather than hard errors.
  - `_brief.py` — Markdown rendering for both per-IC and system briefs;
    pin tables, BOM tables, embedded image references, hard-wired
    review prompt at the end of each brief.

- **`test_all_tools_dynamic.py` updated** to import + register the
  new module, add `datasheet_pdf` to `PATH_PARAM_NAMES`, and list the
  three new tools in `EXPECTED_EMPTY_CALL_FAILURES`.

- **`tests/test_review_tools.py`** — 12 tests across Happy / Edge /
  Error per tool plus an idempotency-hash test and a kicad-cli-gated
  end-to-end image-rendering test that self-skips when the CLI is
  missing.

### Workflow

`CLAUDE.md` gains a Workflow-Cookbook block "Datasheet-Review (Layer R)"
covering the three phases (Phase 0 inventory → Phase 1 per-IC →
Phase 2 system). Tool-Decision-Matrix carries three new rows.

## 2026-05-12 — symbol_cache: resolve user sym-lib-table libraries

### Fixed

- **`get_real_symbol` now consults the user's global `sym-lib-table`.**
  Previously, `kicad_mcp/generators/symbol_cache.py` only scanned the
  stock KiCad symbol directory (`C:\Program Files\KiCad\10.0\share\kicad\symbols`
  or the `KICAD_SYMBOL_DIR` env override). Custom / third-party libraries
  registered via *KiCad → Preferences → Manage Symbol Libraries* were
  invisible to `add_schematic_symbols` and `apply_circuit_block`, which
  failed with ``lib_symbol 'X' not found in KiCad libraries`` even
  when the library was properly installed.

  Resolution now tries the stock dir first, then falls back to libraries
  registered in the user's `sym-lib-table`. The table is located via
  (in order): the `KICAD_CONFIG_DIR` env override, `%APPDATA%/kicad/<ver>/`
  on Windows, `/mnt/c/Users/*/AppData/Roaming/kicad/<ver>/` on WSL,
  `~/.config/kicad/<ver>/` on Linux, and `~/Library/Preferences/kicad/<ver>/`
  on macOS. URIs containing `${KIPRJMOD}` (project-local) are skipped;
  other `${VAR}` placeholders are expanded via `os.path.expandvars`.

  New `tests/test_symbol_cache.py` covers six scenarios: happy-path
  resolution, missing config dir, broken URI entry, unresolvable
  variable URI, stock+user namespaces side-by-side, and unknown lib_id.
  All 608 existing tests still pass.

## 2026-05-10 — Layer T: spec-driven circuit-block composition

### Added

- **New tool category: Layer T — circuit-block composition.** Five MCP
  tools in `kicad_mcp/tools/circuit_block_tools.py` that turn a
  datasheet-defined IC + outer beschaltung (chip + decoupling +
  bootstrap + FB divider + …) from a JSON spec into Layer-S patcher
  calls. Tool count goes from **93 → 98**.

  - `validate_circuit_block(spec)` — Pre-flight a spec against
    `schema_v1_1.json`. Reports JSON-Schema-style errors and warnings
    on missing kicad_symbol cache hits without touching disk.
  - `apply_circuit_block(sch_path, spec, instance_id?, dry_run?)` —
    Compose the spec into ordered `add_schematic_symbols` +
    `add_power_symbols` + `connect_pins` calls. Power-pin convention
    enforced (every `power_in` pin gets a `power:` lib-symbol). Multi-
    instance via `instances[]` + `net_suffix`. `dry_run=True` returns
    the would-apply payload.
  - `apply_template_block(template_id, chip_meta, app_params, out_path?)`
    — Materialise one of the bundled templates
    (`smps_buck_converter`, `linear_voltage_regulator`, `h_bridge`)
    into a v1.1 spec. Merges chip-specific overrides and
    application parameters; sets `review_status="needs_review"`.
  - `extract_pdf_tables(pdf_path, pages?)` — pdfplumber-backed
    layout-aware table extraction for datasheet ingestion. Lazy
    import; freundliche Fehlermeldung wenn der optionale Dep fehlt.
  - `extract_circuit_from_pdf(pdf_path, target_chip, pages?)` —
    Bundles tables + per-page text + a v1.1 skeleton with
    `needs_review[]` so the orchestrating LLM can map raw PDF
    content to a draft block-spec without writing back to disk.

- **Schema v1.1** in `kicad_mcp/generators/circuit_block/schema_v1_1.json`.
  Datasheet-zentrisch: `pins[]` (typed),
  `peripherals[].between` als typed pin/net references,
  `instances[]` für Multi-Instance, `external_nets[]` mit
  `direction`/`type`, optional `strap[]`, `operating_envelope`,
  `power_pins_use_kicad_power_symbols` flag. Forward-kompat
  via `additionalProperties:true` an strategischen Stellen.

- **Three goldstandard examples** in `examples/circuit_block/`:
  TPS54202 buck, AMS1117-3.3 LDO, LM358 dual op-amp. Validated via
  `tests/test_circuit_block_tools.py::test_schema_validates_examples`.

- **Templates extended.** Three of the existing 17 schematic
  templates (`smps_buck_converter`, `linear_voltage_regulator`,
  `h_bridge`) now carry a `block_definition` section consumable by
  `apply_template_block`. The other 13 carry a stub that points
  at the format reference. Recognition (`identify_circuit_patterns`)
  and Generation now share one template file.

- **Test fixtures: `tests/test_circuit_block_tools.py`** — 18 cases
  covering all five tools (Happy/Edge/Error each), schema-loader
  smoke, examples validation, and an end-to-end skipped-without-CLI
  apply test.

- **Optional dep: `[project.optional-dependencies] pdf`** =
  `pdfplumber>=0.10`. Required dep: `jsonschema>=4.0`.

- **Tests: `tests/test_tool_audit.py`** — 13 audit-suites, parametrised
  per tool (one failed test → one tool name in the failure id):
  description-quality (length floor + usage cue or allowlist entry),
  path-normalisation, exact tool-count lock, snake-case naming,
  Args-vs-Docstring sync, ``success: bool`` key guarantee on dict
  returns, ``json.loads(<param>)`` guarded by try/except with
  structured failure, missing-path → ``success=False`` with
  ``not found`` error, ``dry_run=True`` is byte-stable on disk,
  additive tools are idempotent (hash-equal or collision-error),
  heavy deps (pcbnew/kipy/pdfplumber/cairosvg/PIL/wx) stay lazy at
  module-load time.

### Notes

- Layer T does not duplicate Layer-S logic. Every effect on the
  schematic still flows through `add_schematic_symbols` /
  `add_power_symbols` / `connect_pins`. The composition lives
  entirely in `kicad_mcp/generators/circuit_block/_block_to_patch.py`
  (pure function, no I/O) and the in-process MCP-call adapter in
  `circuit_block_tools_helpers.py`.
- The dynamic all-tools test (`test_all_tools_dynamic.py`) was
  extended to register the new tools and to recognise `pdf_path` /
  `out_path` as path parameters.

## 2026-05-01 — sch-patch grid + property-hide hardening

### Added

- **`snap_to_grid()` helper** in `kicad_mcp/utils/sch_geometry.py` —
  rounds an `(x, y)` pair to the nearest multiple of 1.27 mm (KiCad's
  default schematic placement grid). New constant `SCH_PLACE_GRID_MM`.
  Wired into every Phase-S code path that lands a coordinate on disk:
  `add_schematic_symbols`, `add_schematic_wire`, `add_schematic_label`,
  `add_power_symbols`, `convert_global_labels_to_power`,
  `move_schematic_group`, `rotate_schematic_group`, plus defensive
  snap inside `render_wire`, `render_label`, `render_symbol_instance`
  and `_build_power_symbol_snippet` so that any caller (even tests
  hitting the renderer directly) cannot drift symbols off-grid. Stops
  the `endpoint_off_grid` ERC warning storm observed on real-world
  schematics after a free-form move pass.

### Fixed

- **Power-symbol Description/Datasheet rendered visible.** New power-
  symbol instances inserted via `add_power_symbols` /
  `convert_global_labels_to_power` carried no explicit
  `(property "Description" …)` / `(property "Datasheet" …)` block;
  KiCad's GUI then fell back to the lib_symbol defaults
  ("Power symbol creates a global label with name \"+5V\"", etc.)
  and rendered them as cluttering text on the sheet.
  `render_symbol_instance` now always emits both properties as
  hidden (`hide=True`) instance overrides, breaking the fallback path.
  Same fix benefits non-power symbol instances created via
  `add_schematic_symbols`. Affected schematics need a one-time
  cleanup pass (the cached lib_symbol may still hold the visible
  default until "Update Symbols from Library" runs in KiCad).

### Tests

- `tests/test_sch_patch_tools.py` — three-resistor and wire-region
  fixtures now use grid-aligned coordinates (50.8 / 60.96 / 71.12 mm
  instead of 50 / 60 / 70) so the tool's defensive snap-to-grid does
  not shift the anchors. 337 of 338 tests pass; the remaining failure
  (`test_route_when_kipy_missing`) is a pre-existing assertion against
  a brittle error-message substring, unrelated to this change.

## 2026-04-29 — production-readiness sweep

### Added

- **`convert_global_labels_to_power`** tool (Phase-S) — scan a
  `.kicad_sch` for `(global_label "GND")` / `(global_label "+3V3")`
  blocks and replace each with a canonical `power:`-symbol instance at
  the same anchor. Uses `power_lib_id_for()` for net recognition and
  `default_power_rotation()` for the family-conventional orientation
  (0 for GND-family, 180 for positive rails). Supports `only_nets`
  whitelist + `dry_run` preview. Brings legacy schematics into line
  with the KiCad convention required by ERC's `power_pin_not_driven`
  rule. Tests: 4 new in `tests/test_sch_patch_tools.py` (happy path,
  dry-run idempotence, only_nets filter, no-power no-op).
- **Power-net guard in `add_schematic_label`** — emits
  `success=False` + `suggested_lib_id="power:<NET>"` when a global
  label with a recognised power-net name is requested, steering the
  caller to `add_power_symbols`.
- **`annotate_schematic`** tool (Phase-S) — pure-Python annotator.
  Assigns sequential numbers to `R?` / `C?` / non-conforming `#PWR_*`
  references, updates both `(property "Reference" …)` and nested
  `(reference "X")` instance entries. Modes: gap-fill (default) and
  `force_renumber`. Removes the previous dependency on Eeschema's GUI
  *Tools → Annotate* before `kicad-cli sch export netlist`.
- **`install_autorouter`** tool (Phase 0) — bundled-JRE bootstrap.
  Downloads Adoptium Temurin JRE 21 + the latest freerouting jar from
  GitHub into `~/.kicad-mcp/autoroute/`, idempotent, with SHA-256
  marker file. The previously-skipped `tests/test_autoroute_install.py`
  suite (6 tests) now runs green.
- **Region/type-based delete** for `delete_schematic_items` — accepts
  `types=["wire","label","junction",…]` plus `region={x,y,w,h}` so
  labels/wires/junctions can be group-deleted even though they carry
  no `kicad-mcp.group` tag.
- **`justify` parameter** for `add_schematic_label` (`"left"` /
  `"right"` / auto via `justify_for_angle()`).
- **Half-pitch pin-grid auto-snap** in `add_schematic_symbols` —
  `Device:C/R/L/CP` plus all `_Small` variants get their centre
  snapped to `(N + 0.5) × 2.54 mm` so both pins land on the
  schematic grid. Tool response carries a new `snapped: [...]` list
  for the moves.
- **CLI-based netlist extraction** — `extract_netlist()` now tries
  `kicad-cli sch export netlist --format kicadsexpr` first and falls
  back to the legacy label-only parser only when the CLI is
  unavailable. Returns `partial: False, source: "kicad-cli"` with
  full pin-level connectivity on the CLI path.
- **README.de.md** — German translation of the README.
- **`.github/workflows/ci.yml`** — pylint (errors + warnings strict)
  plus pytest on every push / PR.
- **`tests/test_all_tools_dynamic.py`** — dynamic per-tool walk:
  asserts no duplicates, ≥ 280-char descriptions, `to_local_path`
  normalisation on every `*_path` / `*_dir` parameter (with explicit
  delegation whitelist), ≥ 70 % usage-hint phrasing across all 91
  tools, plus an empty-call sanity probe per tool.
- **`tests/test_netlist_parser.py`** — mocked-CLI tests for the new
  netlist path so CI without KiCad still covers the parser.

### Changed

- Path abstraction is now repo-wide. Every `@mcp.tool` whose
  signature accepts a filesystem path normalises it through
  `to_local_path()` at the function entry. Previously-missing tools
  (`generate_project`, `generate_schematic`, `generate_pcb`,
  `generate_from_netlist`, `benchmark_loop`, `esphome_to_kicad`,
  `convert_ltspice_to_kicad`, `ipc_install_kipy`) were patched.
- LLM-facing docstrings rewritten for ten tools that fell below the
  280-char floor or lacked usage hints (`generate_project_thumbnail`,
  `ipc_save_all`, `connect_pins`, `add_schematic_label`, `run_erc`,
  `validate_design`, `generate_pcb`, `generate_schematic`, `ipc_save`,
  `ipc_install_kipy`).
- `connect_pins` / `delete_schematic_items` now report richer
  `Returns` blocks.
- README + CLAUDE.md updated to reflect 91 tools, 324 passing tests,
  bug-sweep status, and the new Phase-0 / Phase-2 / Phase-6 state.

### Fixed

- **Bug 1** — `run_erc` parsed top-level `violations`; KiCad-10
  splits them under `sheets[N].violations`. Now aggregates across
  sheets (already shipped 2026-04-27, documented here).
- **Bug 2** — `extract_schematic_netlist` was label-only with
  `partial: True`. CLI-based path delivers full pin-level data.
- **Bug 3 / 6** — annotation gap exposed via the new
  `annotate_schematic` tool.
- **Bug 4** — `add_schematic_label` lacked `justify` parameter.
- **Bug 5** — confirmed not reproducible; `ensure_lib_symbol`
  already deduplicates.
- **Bug 7** — `delete_schematic_items` couldn't address
  labels/wires/junctions; `types` + `region` selectors added.
- **Bug 8** — `_Small` passive symbols silently put pins
  off-grid; auto-snap added.
- **Bug 9** — freerouting v2.1.0 hung in WSL→Windows-Java
  subprocess (already shipped 2026-04-27).
- **Lint cleanup (ScanAllX)** — 9 errors (E1136 in
  `netlist_parser.py`) and 9 warnings (W1514, W0611, W1309, W0404,
  W0612 across `_tasks/mcp_supervisor/`, `autoroute_tools.py`,
  `sch_patch_tools.py`, `tests/test_autoroute_e2e.py`,
  `tests/test_netlist_parser.py`) cleared. Repo-wide pylint:
  0 errors, 0 warnings, 1374 INFO-only items (style/complexity).
- **`test_route_when_kipy_missing`** error-string assertion no
  longer brittle — accepts both "kicad-python" and "IPC bus is
  not reachable" (Phase-7 auto-open hook surfaces the second
  message first when the bus is down).

### Notes

- KiCad #2077 (Schematic Editor IPC API gaps) — upstream patch +
  reproducer + MR description ready in `_tasks/upstream_mr/`.
  Submission is on the maintainer; the patch is independent of the
  MCP-side mitigations and ships as `0001-eeschema-api-add-Save-
  Revert-RunAction-handlers.patch`.
- Phase 2 (live SCH `BeginCommit / CreateItems / EndCommit` smoke
  test) — script self-test passes with `--check-only`; full E2E
  needs a running Eeschema and is unblocked end-user side.
