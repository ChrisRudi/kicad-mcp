# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once
the first tag ships.

## [Unreleased]

### Changed (Demo-Ablauf: zeigt WAS gebaut wird, in erster Person — 0.37.0)
- **Steckbrief vor dem Bauen.** `kicad_mcp/demo.py` emittiert als Schritt ①
  jetzt „▶ Das baue ich jetzt": den vollen Beschreibungstext der Spec plus die
  Leitbauteile (`_lead_parts`: Ref-Präfixe U/Q/D/L/Y/J). Vorher nur der erste
  Satz + „N Bauteile" — man sah nicht, was entsteht.
- **Erste-Person-Narration.** Die ①–⑥-Zeilen und alle `demo_kits`-Rationale
  sind auf „Ich …" umgestellt (der Nutzer schaut zu, wird nicht instruiert).
  Panel-Copy in `chat_dialog.py` (`_write_demo_plan`, `_offer_next_demo_step`)
  ebenso; EN-Katalog (`i18n.py`) nachgezogen.
- **Datenblatt aus dem Netz.** Die Datenblatt-abhängigen Super-Feature-Prompts
  (`datasheet_diff`, `datasheet_circuit`, `xtal_caps`, `slew_rate`) suchen bei
  fehlendem lokalem PDF jetzt per WebSearch das offizielle Datenblatt, nennen
  Quelle + URL + Ablageort (`docs/<Value>.pdf`) und arbeiten damit weiter —
  statt anzuhalten (konsistent mit `bom_sourcing`).
- **Ergebnis sichtbar.** Neuer Schritt ⑥ rendert Schaltplan UND Platine via
  `export_png`; das Panel öffnet beide PNGs im Bild-Betrachter (neue
  `SCH_PNG`/`PCB_PNG`-Zeilen, geteilte `_os_open`-Hilfe) plus das Projekt.
  Best-effort: ohne kicad-cli bleibt der Schritt grün, die Dateien liegen im
  Projekt. `tests/test_demo.py` auf 6 Schritte + Ich-Narration angepasst.

### Fixed (Schaltplan: Netz-bewusste Draht-Vereinigung — 0.36.1)
- **0.36.0 wurde auf main im gemockten pytest-Job rot** (`test_wire_merge`:
  `usb_sensor_hub: 1 Leitungen übereinander`) — die volle USB-C-Beschaltung
  gab einem Signalnetz (D+ auf zwei Pads) eine dritte MST-Kante, deren A*-Route
  ein Stück derselben Spur nachfuhr wie eine bestehende → **eine** kollinear
  übereinander liegende Leitung. Nur im Platzhalter-Pfad (ohne Symbol-Lib);
  mit echter KiCad-Lib war der Plan sauber. (Meine lokale Verifikation war
  untreu: der Container hatte echte Symbole, `route.get_real_symbol` wurde nicht
  mitgenullt → realer statt Platzhalter-Pin-Geometrie.)
- **Fix an der Quelle:** neue `route._union_same_net_overlaps` vereinigt vor der
  Junction-Berechnung kollinear ECHT-überlappende Segmente **desselben** Netzes
  zu ihrer maximalen Spanne. Netz-bewusst (der globale, netz-blinde
  `_merge_overlapping_wires` darf das nicht — er würde Nachbar-Stubs zweier
  FREMDER Netze kurzschließen, Roundtrip-Befund). Nur echte Innen-Überlappung,
  Endpunkt-Berührung bleibt unangetastet. 3 Unit-Tests
  (`tests/test_wire_merge.py`).
- **Verifiziert:** Platzhalter-Pfad aller vier Prüf-Kits 0 Überlappungen,
  Netzlisten-Roundtrip 12/12, byte-deterministisch über Seeds 1/7/9, pylint 0/0.
  Nebeneffekt: 6 Bausatz-Schaltpläne bekommen etwas kürzere Leitungen (gleiche
  Konnektivität, aufgeräumter).

### Fixed (CI-Infra: Microsoft-apt-Repo-Flake härten — 0.36.0)
- **Live-IPC- und GUI-Smoke-Job fielen auf main um** (0.35.0-Push, 9b6386d) —
  aber NICHT an Code: der auf GitHub-Runnern vorinstallierte
  Microsoft/azure-cli-apt-Repo lieferte ein kaputtes `InRelease`
  (`got 'NOSPLIT'` / „no longer signed"), worauf `apt-get update` in
  `scripts/setup_container_kicad.sh` mit Exit 100 starb, bevor KiCad dran war.
  Der real-KiCad-pytest-Job auf demselben Commit lief grün → reiner
  Infra-Zufall. Das Skript entfernt jetzt vor `apt-get update` die
  nie benötigten Microsoft/azure-cli-Quellen (best-effort, idempotent).

### Fixed (USB-C-Buchse voll beschaltet — Footprint-Fehler an der Quelle — 0.36.0)
- **Der vom Nutzer gemeldete „Footprint-Fehler" ist behoben** (keine
  Geometrie-, sondern eine Beschaltungs-Sache): die Buchse
  `USB_C_Receptacle_HRO_TYPE-C-31-M-12` hat 16 logische Pads auf 12 physischen
  Positionen — vier Paare liegen **exakt übereinander** (A1+B12, A12+B1 = GND;
  A4+B9, A9+B4 = VBUS), D+/D− je auf zwei Pads (A6+B6 / A7+B7). Das Kit
  verdrahtete nur die A-Reihe → gestapelte Zwillinge ohne Netz, ein GND-
  Terminal ganz offen (Ratsnest-Geister beim Routen). `usb_sensor_hub.json`
  benetzt jetzt **alle** Pads: A1/A12/B1/B12/SH→GND, A4/A9/B4/B9→VBUS,
  A6/B6→USB_DP, A7/B7→USB_DM.
- **CC-Pulldowns ergänzt:** R4/R5 = 5,1 kΩ (CC1/CC2 → GND, Rd-Sink nach
  USB-Type-C-Spec R2.0). Explizites Symbol `Connector:USB_C_Receptacle_USB2.0_16P`
  an J1 (deterministische Auflösung, Buchse statt Stecker). Der generierte
  Schaltplan bildet damit exakt die Referenz-Platine ab (Kohärenz Plan↔Board).
- **usb_sensor_hub: verified=True → ⭐** (board_clean war seit 0.35.0). Beleg
  in `docs/kit_datasheet_reviews.md` (STM32-USB-Pins PA11/PA12, Quarz-Load-Caps,
  I²C-4k7-Pull-ups, AMS1117, CC-Rd). Stand: 8 ⭐ / 2 ✅ / 0 🔬.
- **Kein 0.34.0-Rückfall:** Schaltplan legt auch im Platzhalter-Pfad (ohne
  Symbol-Lib, wie im gemockten CI-Job) **0 Leitungen übereinander**
  (`test_wire_merge`), Netzlisten-Roundtrip 10/10, byte-deterministisch über
  Seeds. Die dichtere Verdrahtung diesmal überlappungsfrei — Grund war die
  saubere Symbol-Anbindung + vollständige, konsistente Netzliste.

### Added (USB-C- & Ethernet-Platine als fertige Referenz — 0.35.0)
- **`reference_pcb`-Feld an `DemoKit`:** Ein Bausatz kann eine mitgelieferte,
  fertig geroutete Platine hinterlegen (`.kicad_pcb` + gleichnamige
  `.kicad_pro` unter `kicad_mcp/resources/data/demo_kits/`). Gedacht für
  dichte, zweilagige Fein-Pitch-Boards, die der Auto-Router (noch) nicht
  restlos schließt: die *gelieferte saubere Platine* IST diese Referenz
  (Hand-Route mit GND-Fläche), nicht die frisch generierte.
- **`usb_sensor_hub` und `ethernet_device` sind jetzt `board_clean=True`**
  (🔬 → ✅), belegt durch die mitgelieferten Referenz-Platinen: `kicad-cli pcb
  drc` meldet **0 Fehler / 0 offene Netze** für beide. Die `.kicad_pro`
  deklarieren die (fertigbare) Prozess-Regelung, zu der geroutet wurde
  (Clearance 0,1 mm, Via 0,45 mm/Bohrung 0,2 mm, Thermal-Spokes ≥1) —
  keine Regel-Trickserei, sondern die reale JLCPCB-taugliche Vorgabe.
- **Gate erweitert:** `tests/test_pcb_placement.test_finished_kits_route_drc_clean`
  prüft für Kits mit `reference_pcb` genau diese Datei (+ `.kicad_pro`) per
  KiCad-DRC statt neu zu generieren; Kits ohne Referenz bleiben beim
  Generieren-und-prüfen. Eine Quelle (`demo_kits.board_clean_keys`) speist
  weiterhin Label UND Gate.
- **Neuer Wächter** `tests/test_demo_kits.test_reference_pcb_files_ship_with_project`:
  jede `reference_pcb` liefert `.kicad_pcb` UND `.kicad_pro` und impliziert
  `board_clean` — fängt eine fehlende Datei auch im gemockten CI-Job (ohne
  `kicad-cli`).
- **Hand-Route-Herkunft:** Die zwei Platinen wurden vom Nutzer geroutet
  (der Router scheiterte an der 2-lagigen Kapazität); der entscheidende Hebel
  war eine **GND-Kupferfläche** statt GND als Einzelbahnen.
- **Noch offen für ⭐:** `verified` beider Bausätze bleibt `False` — der
  Schaltplan muss pin-für-pin datenblatt-geprüft werden (USB-C voll
  beschalten wie die Platine — die B-Seite/Schirm/CC2 hängen im Schaltplan
  noch in der Luft, das ist der vom Nutzer gemeldete „Footprint-Fehler" —,
  Ethernet-PHY-Review). Erst dann board_clean UND verified → ⭐.

### Fixed (Hotfix: usb-Schaltplan-Spec zurückgerollt — 0.34.1)
- **CI-Rot auf main (074254a):** Der gemockte pytest-Job (ohne Symbol-Libs,
  Platzhalter-Symbole) meldete `test_wire_merge`
  `usb_sensor_hub: 1 Leitung übereinander`. Die voll beschaltete 16-Pin-
  USB-C-Buchse (U1 aus 0.34.0) verdichtet die GND/VBUS-Verdrahtung am
  Platzhalter-Symbol so, dass genau eine Leitungs-Überlappung entsteht —
  isoliert bestätigt: ohne CC-Rs identisch, mit 0.33.0-Spec = 0. Mit ECHTER
  KiCad-Lib ist der Plan sauber (der Echt-KiCad-Job war grün); die Schranke
  prüft aber beide Pfade. Lehre: `test_wire_merge` gehört in die lokale
  Gate-Runde (war übersehen).
- **usb_sensor_hub.json auf 0.33.0-Stand zurückgesetzt** → Schaltplan wieder
  0 Überlappungen (Mock verifiziert), Roundtrip 12/12, main grün. **Behalten:
  U2 (Fein-Pitch-Fanout, byte-safe — ethernet weiter 14→10) und der
  symbol_lib-Fix.** usb-DRC damit 31→26 (Fanout wirkt weiter aufs LQFP).
- **U1 (USB-C-Voll-Beschaltung) vertagt** — Re-Land braucht Schaltplan-
  Layout-Arbeit für die dichte Buchse UND `test_wire_merge` in den Gates
  (Roadmap §2c, Stufe U1').

### Added (Fein-Pitch U2: Fanout Stufe 1 + symbol_lib-Fix — 0.34.0, usb-Teil in 0.34.1 zurückgerollt)
- **U1 — USB-C-Kit vollständig beschaltet** (usb_sensor_hub): das
  16-Pad-Receptacle (TYPE-C-31-M-12) trägt gespiegelte Duplikat-Pads —
  B-Seite + SH-Schirm waren netzlos (→ shorting/clearance/mask zwischen
  Nachbar-Pads), CC1 war ein 1-Pad-Netz OHNE Rd. Jetzt: B1/B12/A12/SH→GND,
  B4/B9/A9→VBUS, B6→USB_DP, B7→USB_DM, CC1/CC2 je 5k1→GND (R4/R5; ohne Rd
  liefert ein USB-C-Netzteil kein VBUS). J1-Pin-Namen eindeutig
  (GND_A1, DP_B, …).
- **Symbol-Resolver: explizites lib_id gewinnt** (`symbol_lib`): die
  Fuzzy-Suche matchte für name "USB_C" den **Plug** (hat B6/B7 physisch
  nicht) — der Roundtrip meldete die Buchsen-Pins ehrlich „nicht
  angeschlossen". Existierendes explizites `lib_id` (usb: 
  `Connector:USB_C_Receptacle_USB2.0_16P`) wird jetzt VOR der Index-Suche
  respektiert; Tippfehler fallen weiter in Suche/Passthrough. Roundtrip
  wieder 12/12.
- **U2 — Fein-Pitch-Fanout Stufe 1** (`pcb/route.py`): Pads mit
  Schmalseite < 0,55 mm und Aspekt ≥ 1,5 (LQFP-48 0,3×1,5; LQFP-32
  0,5×1,5; USB-C 0,3×1,15) bekommen Startzellen NUR längs der Pad-Achse
  und NUR jenseits der Pad-Spitze; Anschluss-Stummel zweibeinig achsentreu
  (längs, dann kurz quer jenseits der Reihe); Escape-Korridor wird auf
  Passierbarkeit geprüft (frühere Netze) und danach als eigenes Netz
  markiert (spätere Netze); Baum-Treffer docken an der wirklich erreichten
  Zelle an. **Messung:** ethernet 14 err/25 offen → 10/13; usb 31/22 →
  12/23 (inkl. U1-Mehrlast: +9 Pflicht-Anschlüsse). Diagonal-Stummel-
  Fehler (Quer über Nachbar-Pad) vollständig eliminiert.
- **Nachhaltigkeits-Gate gehalten:** das Geometrie-Gating garantiert —
  per Hash-A/B verifiziert — **byte-identische Ausgabe für alle 8 Boards
  ohne Fein-Pitch-Pads**; Determinismus PCB+Schaltplan über 2 Seeds;
  Roundtrip 12/12; 139 Tests grün. Stufe 2 (Konvergenz zu 0/0) folgt
  nach Plan `docs/roadmap.md` §2c.

### Changed (ac_dc TNY268-Flyback datenblatt-korrigiert — ⭐ — Phase 3 — 0.33.0)
- **Der TNY268-Flyback hatte vier echte Fehler** (Review gegen PI-Datenblatt
  TNY263-268 + KiCad `Regulator_Switching:TNY268P`):
  (1) **Pinout komplett falsch** (DRAIN=1/S=4/BP=5/EN=8) → korrekt
  **BP=1, S=2/3/7/8, EN/UV=4, D=5** (Pin 6 weg — Kriechstrecke);
  (2) **BP-Bypass-C fehlte** → C3 100 n (BP→SOURCE, Datenblatt-Pflicht);
  (3) **Feedback-Teiler ergab ~12,5 V statt 5 V** → R1 10k→2k49
  (Vout=2,495·(1+R1/R2)≈4,99 V);
  (4) **Primär-RCD-Klemme fehlte** → R4 100k ‖ C4 2n2 + D2 UF4007 (ohne sie
  zerstört die Drain-Spitze den MOSFET). AUX-Wicklung (T1:3) freigelegt
  (TinySwitch selbstversorgt aus DRAIN). TL431/Opto-Schleife war korrekt.
- Board bleibt **0 DRC / 0 offen** (3 neue Primär-Teile trägt die
  0.31.0-Platzierung+Router), Roundtrip 10/10, PCB byte-deterministisch.
  **verified=True → ac_dc ⭐. Stand 7 ⭐ / 1 ✅ / 2 🔬.**
- **Ehrliche Grenze dokumentiert:** kompakte Lehr-Schaltung, Kern-Topologie
  datenblatt-korrekt, aber EMI-Filter/X-Y-Sicherheits-Cs/Kriechstrecken-
  Gehäuse bewusst simplifiziert — KEINE Serien-230-V-Vorlage
  (`docs/kit_datasheet_reviews.md`).

### Changed (74HC595 aufs echte 16-Pin — production_ready ⭐ — Phase 3 — 0.32.0)
- **production_ready trug ein auf SOIC-8 verkürztes 74HC595 mit
  unbeschaltetem RCLK** (Pin-Reduktion → das Schieberegister hätte real nie
  gelatcht = nicht funktionsfähig). Umgebaut aufs echte 16-Pin (SOIC-16,
  Symbol `74xx:74HC595`): QA(15)/QB(1)/QC(2)→LEDs über R4/R5/R6; SER(14),
  SRCLK(11), **RCLK(12)** an J1 (jetzt 1×05: VCC/SER/SRCLK/RCLK/GND);
  **/OE(13)→GND**, **/SRCLR(10)→VCC**; VCC(16)/GND(8). Board bleibt 0 DRC /
  0 offen (verbesserte Platzierung + Rip-up-Router aus 0.31.0 tragen das
  größere IC), Roundtrip 10/10, PCB byte-deterministisch (2 Seeds).
- **verified=True → production_ready ist ⭐.** Stand **6 ⭐ / 2 ✅ / 2 🔬**
  (buck, motor, kit_seeding, led_ring, audio, production_ready). Offen nur
  noch usb_sensor_hub + ethernet_device (Fein-Pitch LQFP-48, Phase 2c).
  Beleg: `docs/kit_datasheet_reviews.md`.

### Fixed (Platzierung + Router: ac_dc & audio auf 0/0 — Roadmap Phase 2 — 0.31.0)
- **Courtyard-Zentrum-Offset im Hart-Entzerrer (Wurzel-Fix).** Bei THT-
  Footprints (DIP, Dioden-Brücke, Pin-Header) sitzt der Platzierungs-Origin
  auf Pin 1, NICHT im Courtyard-Zentrum (DIP-4: +3,8/+1,3 mm). Der Entzerrer
  (`fd_refine._resolve_pcb_overlaps`) verglich origin-zentrierte Boxen → er
  ließ Überlappungen stehen, die KiCads DRC als `courtyards_overlap`/
  `pth_inside_courtyard` meldet (ac_dc: 10 Fehler, U1↔U2 & T1↔BR1). Neuer
  `bbox._read_courtyard_bbox` + `_fp_center_offset`; der Entzerrer rechnet
  jetzt in Courtyard-Zentren (Offset rotations-korrekt über
  `pcb_local_to_world`), Board-Klemmung ebenso. Derselbe Fix im Test-Helfer
  `_overlap_pairs` (er hatte den gleichen Origin-Bug → Phantom-Overlaps).
- **Rip-up-lite im Router.** Scheitert ein Netz (von früher gerouteten Netzen
  zugebaut), fährt `route_pcb` EINEN Rettungslauf auf frischem Grid mit den
  gescheiterten Netzen ZUERST und übernimmt nur bei strikt weniger offenen
  Netzen (`_route_pass` ausgelagert). Boards, die schon voll routen, lösen
  keinen Retry aus → byte-identische Ausgabe. Löste die 2 offenen IN_NODE-
  Kanten des Audio-Boards (versiegelte Pin-Tasche U1:3) mit.
- **Ergebnis: ac_dc_supply 10/1 → 0/0, audio_amp 0/2 → 0/0.** Stand jetzt
  **5 ⭐ / 3 ✅ / 2 🔬** (board_clean: 7 Kits). Byte-Determinismus über 2
  PYTHONHASHSEED-Seeds bestätigt; `_DONE_KITS` (aus `board_clean_keys()`)
  deckt jetzt 7 Kits, alle DRC 0/0. Offen: usb_sensor_hub, ethernet_device
  (Fein-Pitch LQFP-48, Phase 2c).

### Changed (Datenblatt-Review: 3 weitere Kits verified — Roadmap Phase 3 — 0.30.0)
- **kit_seeding (NE555), led_ring (WS2812B), sketch_to_copper (AMS1117-5.0)**
  Pin-für-Pin gegen Datenblatt geprüft und auf `verified=True` gesetzt. Belege
  in `docs/kit_datasheet_reviews.md` (NE555: Astabil Ra=R1/Rb=R2/C1, CV-Cap
  C2 10n, RESET→VCC, f≈1,38 Hz; AMS1117: In/Out-Caps 10µ/22µ; WS2812B: Pinout
  + Daisy-Chain, Abblockung dokumentiert 1×100n je 2 LEDs — 6 Caps sprengen
  die Kupfer-Kantenabstände des runden Boards, 3 halten es 0/0).
- **Stand jetzt 4 ⭐ / 3 ✅ / 3 🔬** (vorher 2/4/4): buck, motor, kit_seeding,
  led_ring sind ⭐ (Platine 0 DRC + Schaltplan datenblatt-echt). Nur
  `verified`-Metadaten + Docs geändert — `_DONE_KITS`/Board unberührt (null
  DRC-Risiko).
- **Roadmap-Lebenszyklus geschärft:** ⭐ = beide Achsen (`board_clean` +
  `verified`), ✅ = eine, 🔬 = keine; das Circuit-Block+Rezept-Modellieren ist
  Wartungs-Mittel für `verified`, kein eigener Gate (`docs/roadmap.md`).
- **Offen:** production_ready trägt ein 8-Pin-reduziertes 74HC595 (real 16-Pin,
  RCLK unbeschaltet) → Rework nötig vor `verified`; bleibt board_clean (✅).

### Added (Kit-Reife-Labels aus einer Quelle — Roadmap Phase 1 — 0.29.0)
- **Zwei-Achsen-Reife am Bausatz:** `DemoKit` bekommt `board_clean` (Platine
  0 DRC / 0 offen) und `verified` (Schaltplan Pin-für-Pin datenblatt-geprüft),
  beide Default `False` → ein neuer/geänderter Kit ist automatisch „🔬 Draft"
  (nie fälschlich fertig). `stage()`/`stage_badge()` verdichten zu ⭐ (beide) /
  ✅ (eine) / 🔬 (keine). Aktuell 2 ⭐ (buck, motor), 4 ✅ (audio, led_ring,
  kit_seeding, production_ready), 4 🔬 (ac_dc, usb, ethernet, sketch).
- **Demo-Menü zeigt die Stufe:** Untermenü-Titel mit Badge, erste Zeile im
  Untermenü als Klartext-Reifezeile (i18n de/en). Der Nutzer sieht sofort,
  was Referenz-Qualität hat und was in Arbeit ist.
- **Eine Quelle, kein zweiter Pflegeort:** `tests/test_pcb_placement._DONE_KITS`
  wird jetzt aus `demo_kits.board_clean_keys()` abgeleitet — das Label IST der
  DRC-Gate-Eintrag. Hebt jemand ein Kit auf `board_clean`, muss der DRC-Test
  bestehen; fällt ein Board zurück, wird der Test rot. Label und Realität
  können nicht auseinanderlaufen. Konsistenz-Tests: Stufe je Kit ableitbar +
  gebadgt, Default = Draft, Rezept-Kits sind `verified`
  (`test_demo_kits`).

### Added (Demo als sichtbare Tool-Kette + Auto-Lauf der Skills — 0.28.0)
- **Bau als sichtbare Tool-Kette** (Feld-Wunsch „die Entstehung
  verfolgen, damit der User weiß: so kann ich das auch machen"):
  `demo.run_demo` ruft statt des gebündelten `generate_project` jetzt die
  drei echten, einzeln aufrufbaren MCP-Tools in Folge — `validate_design`
  → `generate_schematic` → `generate_pcb` — und streamt je Aufruf eine
  `⚙`-Zeile (Tool-Name + Kurz-Argumente + Ergebnis, inkl. ERC-Zähler).
  Das Panel färbt `⚙`-Zeilen gedämpft wie Claudes eigene Tool-Zeilen.
  Byte-Identität zum bisherigen `generate_project`-Pfad für alle 10 Kits
  empirisch verifiziert (alle Einzelblatt < Multisheet-Schwelle 40) und
  als Regressions-Test verankert
  (`test_build_is_byte_identical_to_generate_project`) — DRC-/
  Determinismus-Gates unberührt. Schritte jetzt 5 (idee, pruefen,
  schaltplan, berechnung, platine).
- **Auto-Lauf der geführten Skills:** Nach dem Bau fährt der Demo-Ablauf
  die Pipeline-Skills automatisch nacheinander ab (`_auto_run_demo_step`
  reiht sich per `wx.CallLater` an den Turn-Abschluss, wartet `_busy` ab)
  statt einen „✨ Weiter"-Klick je Schritt zu verlangen. „✋ Stoppen"
  bricht jederzeit ab; jeder Skill bleibt einzeln per ✨-Button nutzbar.
  Ein fehlgeschlagener Turn hält die Kette natürlich an (kein Runaway).

### Fixed (Warnungs-Flut im Demo-Transkript: 73× „Flyback_Trafo" — 0.27.2)
- **Platzhalter-Warnung je Symbol dedupliziert** (Feld-Report AC-DC-Demo):
  der Layout-Optimierer ruft `build_schematic` dutzendfach auf; jede Runde
  lief durch `_emit_lib_symbols` und warnte erneut über dasselbe fehlende
  Symbol → 73 identische WARNING-Zeilen im Chat. Modul-globales
  `_WARNED_PLACEHOLDERS`-Set: eine Warnung je lib_id und Prozess
  (Regression: `test_placeholder_warning_logged_once_across_rebuilds`).
  Inhaltlich bleibt die Warnung richtig — für den 6-Pin-Flyback-Trafo
  (P1/P2/AUX/S1/S2/SGND) gibt es kein Stock-Symbol; der Platzhalter mit
  benannten Pins ist dort korrekt.

### Fixed (Demo baute im Feld nichts — Spec-Pfad zeigte auf Dev-Checkout — 0.27.1)
- **`demo_runner._SPEC_DIR` war der Dev-Checkout-Pfad**
  (`parents[1]/kicad_mcp/…`), den es im INSTALLIERTEN Plugin nicht gibt —
  `spec_exists()` war im Feld immer False, der ▶-Eintrag hieß „Ablauf
  zeigen (Spec folgt)" und `_run_demo_kit` schrieb nur die Vorschau
  („Spec noch nicht gebaut (folgt)") statt Schaltplan+Platine zu bauen.
  Jetzt löst `_spec_dir()` über den kanonischen mcp-root-Resolver auf
  (`server_manager.default_mcp_root()`: env-Override → Bundle
  `<plugin>/mcp`); im Repo greift derselbe Weg auf den Bundle-Spiegel.
  Feld-Layout per Simulation verifiziert (Plugin-Kopie außerhalb des
  Repos findet die Spec). Veralteten Modul-Docstring („Specs gibt es
  noch nicht") mitkorrigiert.

### Changed (Eine Quelle: Demo-Kits aus Circuit-Blocks komponiert — 0.27.0)
- **Verschmelzung der zwei Orte/Aufgaben (Nutzer-Auftrag):** Die
  datenblatt-geprüfte IC-Applikationsschaltung lebt genau EINMAL als
  Circuit-Block unter `kicad_mcp/resources/data/circuit_blocks/`
  (v1.1-Schema + `connect`/`part_name`/`pin_names`/`pcb_group`-Felder;
  Pin-`net`-Override, `""` = bewusst unbeschaltet). Ein Demo-Kit ist nur
  noch ein **Rezept** (`…/demo_kits/recipes/<key>.json`): welcher Block,
  welche Referenzen, welche Stecker, welche Netz-Reihenfolge
  (`"@<block-id>"`-Platzhalter expandiert deterministisch zu den
  Block-Anschlüssen). Die eingecheckten `demo_kits/<key>.json` sind
  BUILD-ARTEFAKTE: `scripts/compose_demo_kits.py` regeneriert sie,
  `tests/test_kit_compose.py` ist der Drift-Wächter (Muster
  Bundle-Spiegel). Umgestellt: buck_converter, motor_driver, audio_amp
  (Blocks `mp1584_buck_5v`, `drv8871_hbridge`, `lm386_amp20` mit
  Hersteller/Datenblatt/Quelle; Rekomposition gegen den 0.26.1-Stand
  elektrisch verifiziert). Composer:
  `kicad_mcp/generators/circuit_block/kit_compose.py`.
- **Blocks umgezogen:** `examples/circuit_block/*.json`
  (tps54202_buck_3v3, ams1117_ldo_3v3, lm358_opamp) →
  `kicad_mcp/resources/data/circuit_blocks/` (ausgeliefert statt nur im
  Repo); `examples/circuit_block/README.md` ist reiner Wegweiser. Alle
  sechs Blocks sind vollwertige v1.1-Specs (die drei Kit-Blocks bekamen
  `kicad_symbol` + `between` je Peripherie; MP1584 hat kein
  Stock-Symbol — `kicad_symbol_note` dokumentiert das, validate warnt
  nur). Schema-Gate: `test_schema_validates_shipped_blocks`.
- **Nackte Block-Namen:** `validate_circuit_block` / `apply_circuit_block`
  akzeptieren jetzt `spec="mp1584_buck_5v"` — `_load_spec_from_arg` löst
  Namen ohne Pfadtrenner gegen die ausgelieferte Block-Bibliothek auf
  (Pfad und Inline-JSON unverändert).

### Added (Demo-UX: Speicherort, Auto-Öffnen, geführter Ablauf — 0.26.2)
- **Nutzer-Feedback umgesetzt:** (1) Demo-Projekte landen sichtbar unter
  `Dokumente/KiCad/claude-demos/<kit>/` statt im versteckten
  `.kicad-mcp/demo` („geht gar nicht"); (2) nach dem Bau öffnet sich die
  `.kicad_pro` automatisch über den OS-Handler (wie Doppelklick auf das
  Projekt — kein Geister-Editor-Spawn wie beim direkten pcbnew-Start,
  vgl. 0.7.8); (3) **geführter Ablauf**: nach der Demo bietet ein
  „✨ Weiter (i/N)"-Chip den jeweils nächsten Pipeline-Skill an
  (kanonischer Prompt + Kit-Begründung + Selektions-Zeile), nach jedem
  fertigen Turn erscheint der Folge-Chip; „✋ Ablauf beenden" jederzeit.
  Damit hat der Demo-Nutzer einen klaren Klick-für-Klick-Pfad — kein
  Prompt-Kopieren, kein Suchen in der Feature-Leiste
  (`chat_dialog._offer_next_demo_step/_run_demo_step`, `_demo_flow`).

### Fixed (Industrie-Review der Demo-Kits + Pad-Winkel-Emission — 0.26.1)
- **buck_converter trug das MP2307-Pinout unter MP1584-Namen** (1 BST,
  2 IN, 3 SW … 8 SS = Mini-360-Chip). Korrigiert aufs verifizierte
  MP1584-Pinout (1 SW, 2 EN, 3 COMP, 4 FB, 5 GND, 6 FREQ, 7 VIN, 8 BST;
  zwei unabhängige Quellen) und die Datenblatt-Pflicht-Beschaltung
  ergänzt: FREQ-R 100k, COMP-Serien-RC 40k2+3n3 (MPS-Designverfahren,
  fc ≈ fsw/20); EN an VIN (automatischer Start). FB-Teiler 100k/19k1
  → 4,99 V bestätigt.
- **motor_driver: DRV8871-Pins VM/IN1/OUT2/ILIM falsch** — nach
  TI-Datenblatt SLVSCY9B korrigiert (1 GND, 2 IN2, 3 IN1, 4 ILIM, 5 VM,
  6 OUT1, 7 PGND, 8 OUT2); PGND jetzt angeschlossen.
- **audio_amp hatte keinen Versorgungsstecker** (VS hing nur am
  Abblock-C) — J3 (VS/GND) ergänzt; LM386-Beschaltung gegen TI-Datenblatt
  bestätigt (gain=20, Zobel 10Ω+47n, Bypass 10µ).
- **Pad-Winkel-Emission (CLAUDE.md-Footgun #3 im Generator):** KiCad
  interpretiert Pad-Winkel absolut — unsere Emission drehte bei
  rotierten Footprints die Pad-POSITIONEN, aber nicht die Pad-FORMEN;
  Router-Modell (korrekt mitgedreht) und reales Board divergierten
  (led_ring: GND-Bahn streift den un-gedrehten D5-Pad).
  `build_footprint_with_nets` schreibt jetzt `rotation` in Footprint-`at`
  UND jedes Pad-`at` (wie KiCads eigenes Speichern).
- **Stecker-Kantenplätze kollisionsgeprüft** (`place._edge_slot`): das
  blinde `y += 15` landete auf dem Montageloch-Keepout, `_place` schob
  den Stecker quer ins Feld und gab AUF einem anderen fixen Stecker auf
  (audio: J1 auf J3). Audio-Board 34→40 mm tief (zwei THT-Stecker +
  Loch-Keepouts passen in 34 mm nicht übereinander).
- **Router: Multi-Start über die Pad-Fläche** — die Mittelzelle allein
  kann von der Aufblasung eines dicht benachbarten Fremd-Pads
  überstempelt sein („nicht routbar", obwohl der Pad-Rand frei ist);
  Clearance 0.2 → 0.25 (Masken-Aufweitung + solder_mask_min_width).
- Stand: **5 Boards 0 DRC-Fehler / 0 offen** (buck, motor, led_ring,
  kit_seeding, production_ready — Gate aktualisiert); audio ehrlich
  0 Fehler / 2 offene IN_NODE-Kanten (versiegelte Pin-Tasche; Pin-Escape
  braucht Stub-Konfliktprüfung — dokumentierter Rest). Bilanz aller 10:
  2141 → 55. PCB-/Schaltplan-Determinismus über Seeds byte-gleich.

### Added (Demo-Platinen Phase 2b: Grid-Router + Entwirren — 0.26.0)
- **Neuer Zwei-Lagen-Router** (`pcb/route.py`, komplett ersetzt): Dijkstra
  auf 0.635-mm-Raster mit Knick-/Via-Kosten und HARTEM Konfliktmodell —
  fremde/netzlose Pads (echte Geometrie + Clearance), bereits geroutete
  Spuren, Vias (Zylinder > Zellbreite: 5-Zellen-Check + 0.85-Keepout),
  Montagelöcher und Board-Rand sind unpassierbar; SMD-Pads starten nur
  auf F.Cu (sonst B.Cu-Spur ohne Via-Anbindung), Anschluss-Stummel auf
  der Andock-Lage. Mehr-Pad-Netze wachsen als Baum (Multi-Target-Suche →
  T-Abzweige). Kein Weg → Netz bleibt ehrlich offen statt Kurzschluss.
  GND wird mitgeroutet (kicad-cli füllt Zonen beim DRC nicht).
- **Entwirren vor dem Routen** (Nutzer-Tipp): `place._untangle_pcb`
  minimiert Luftlinien-Kreuzungen mit derselben Bewertung wie das
  ✨-Entwirren-Feature (`utils.placement_eval.evaluate_layout`) —
  deterministisch, kollisions-geprüft, Rotationen nur für Passives.
- **Montagelöcher als fixe Hindernisse** in Platzierung UND Router
  (`pcb/board_geom.py`, eine Regel für Builder+Platzierung) — vorher saß
  J1 auf MH3 (hole_clearance).
- Ergebnis: **6 von 10 Demo-Boards bestehen KiCads DRC mit 0 Fehlern und
  0 offenen Verbindungen** (audio_amp, buck, kit_seeding, led_ring,
  motor_driver, production_ready) — Gate
  `test_finished_kits_route_drc_clean`. Bilanz aller 10: 2141 → 76.

### Fixed (Demo-Platinen Phase 2b — 0.26.0)
- **Footprint-Grafik fiel auf F.Cu zurück:** `build_footprint_with_nets`
  strich JEDE alleinstehende `(layer …)`-Zeile statt nur der Footprint-
  Kopfzeile — sämtliche Silk-/Fab-Elemente verloren ihre Lage, KiCad
  defaultete sie auf Kupfer: „Rectangle [<no net>] of C1 on F.Cu"
  shortete jede kreuzende Bahn (größter Einzelposten der Messlatte).
- **Kit-Specs vom starren `hint_pcb`-Gitter befreit** (das „geclusterte
  Startplatzierung"-Layout aus docs/demo_kits_todo.md): die Hints
  überstimmten das Auto-Layout exakt und kollidierend; der
  Hint-Mechanismus selbst bleibt für bewusst designte Vorlagen.
  buck 44×32→50×40, sketch 32×24→40×30 (ehrlicher Platzbedarf mit echten
  Courtyards — rechnerisch belegt).
- **PCB seed-abhängig** (`production_ready`): `connectivity_raw` wurde
  über Set-Iteration gefüllt; die Nachbar-Reihenfolge steuert Float-
  Summationen der Kräfte-Physik → `sorted()`. PCB-Determinismus jetzt
  über 3 Seeds byte-gleich geprüft.
- `read_footprint_pad_positions`/`read_footprint_pads` nach `common/bbox`
  (geteilt Builder/Platzierung), `_fd_pcb_refine` mit `extra_fixed`.

### Fixed (Demo-Platinen Phase 2a: Platzierung kollisionsfrei — 0.25.10)
- **Courtyard-Parser elementgenau** (`bbox._read_courtyard_size`): die
  Regex-Fassung spannte lazy über Element-Grenzen — beim SOIC-8 wurde das
  Pin-1-Silk-Dreieck (0.48×0.33 mm) als Courtyard gelesen statt ~7.4×5.4;
  ICs waren für jede Abstandsrechnung Staubkörner (C3 landete AUF U1).
  Jetzt via `parse_sexpr`: nur Grafik-Elemente, deren eigener Layer auf
  `*.CrtYd` liegt, zählen zur Bbox.
- **`_fd_pcb_refine` rotations-blind für das eigene Teil:** w/h wurden nur
  beim Gegenüber getauscht — Kollisionen an gedrehten Teilen unsichtbar.
- **Neuer Hart-Entzerrer `_resolve_pcb_overlaps`** nach der Kräfte-Physik
  (deren Schrittweite → 0.2 mm eine späte Kollision nie mehr löst):
  deterministisch (sortierte Refs), Achse der geringsten Durchdringung,
  Stecker bleiben an ihrer Kante, Board-Kanten-Klemmung führt zu
  Richtungs-/Achswechsel statt Endlos-Schieben. Kollisionsfreiheit ist
  jetzt ein GATE: `tests/test_pcb_placement.py` erzwingt 10/10 Demo-Kits
  ohne Bauteil-Überlappung (mit und ohne Footprint-Lib).
- Messlatte (kicad-cli-DRC, alle Kits): Fehler gesamt 2141 → 1432; Rest
  ist Router-Arbeit (shorting/clearance/mask — Phase 2b).

### Changed (E2E-Report: Foto/Datenblatt-Features suchen selbst — 0.25.9)
- **Feld-E2E-Lauf (Windows, 34 Features: 32 PASS, 2 WARN) zurückgelesen:**
  `photo_reverse` und `datasheet_circuit` stoppten bei fehlender Eingabe
  sofort (0 Tools → WARN „keine-tools-benutzt"), weil ihre Prompts das so
  befahlen. Jetzt suchen beide ZUERST selbst im Projekt (Foto: Glob
  jpg/jpeg/png/webp auch unter docs//fotos/; Datenblatt: docs/*.pdf +
  *.pdf, per `list_schematic_components` mit den IC-Values abgeglichen):
  eindeutiger Treffer → direkt loslegen, mehrere → Auswahl-Rückfrage,
  keiner → Ein-Satz-Anleitung wie bisher. Im Feld heißt das: Datei ins
  Projekt legen reicht, der Knopf findet sie — und der E2E-Bewerter sieht
  echte Tool-Arbeit (PASS „bericht") bzw. eine Rückfrage (PASS
  „fragt-nach"). Go-Gates unverändert.

### Fixed (Windows-Diagnose-Crash WinError 6 — 0.25.8)
- **Feld-Crash:** Diagnose-Dialog stürzte unter Windows ab —
  `server_manager.pid_alive` prüfte Lebendigkeit mit `os.kill(pid, 0)`
  („works on Windows too" stimmte nicht): WinError 6 „Das Handle ist
  ungültig", unter KiCads eingebettetem Python sogar als `SystemError`
  (kein `OSError`-Subtyp), der jedem `except OSError` entkam und über
  `status() → is_healthy()` den Dialog tötete. Fix: unter Windows
  `OpenProcess`/`GetExitCodeProcess` via ctypes (kein Konsolenfenster,
  kein Subprozess pro Health-Ping; ERROR_ACCESS_DENIED = lebt), dazu ein
  Catch-all, damit ein Liveness-Fehler NIE mehr einen Dialog crasht.
  `spawned_registry._alive` war bereits Windows-korrekt (tasklist).
  2 Wächter-Tests (SystemError-Netz, nt-Dispatch geht nie über os.kill).

### Fixed (Projektwechsel-Detektor im Chat — 0.25.7)
- **Feld-Bug: „↺ Unterhaltung aus letzter Sitzung fortgesetzt" im FALSCHEN
  Projekt.** Der als AUI-Pane gedockte Chat überlebt „Datei → Öffnen" eines
  anderen Projekts; `set_plan` frischte nur den RunPlan auf — Session-ID
  (bei Panel-Erzeugung aus dem alten Projektordner geladen), Board-Pfad und
  Link-Ziele blieben am alten Projekt hängen, und ohne erneuten Toolbar-
  Klick wurde GAR nichts aufgefrischt. Jetzt: Projektwechsel-Detektor
  (`runtime_env.project_switch_dir`, normcase/normpath-fest) läuft in
  `set_plan` UND vor jedem Turn (`_sync_project` in `_dispatch_prompt`);
  bei Wechsel wird über eine vom Action-Plugin gereichte `plan_factory`
  ein frischer Plan fürs neue Projekt gebaut (inkl. `.kicad-mcp`-Config),
  die Session-ID des NEUEN Projekts geladen, Board-Pfad/Links/Chips
  umgezogen und der Wechsel im Transkript angesagt
  („Projekt gewechselt: … — fortgesetzt/neu"). 5 Wächter-Tests; GUI-Smoke
  4/4, Plugin-Suite 570 grün.

### Docs
- `docs/kicad11_vorbereitung.md`: Vorbereitungsdokument KiCad 11 — vorab
  ausgearbeitete Features (headless IPC = SWIG-Ablösung, API-Plot = Render
  des lebenden Boards, Schematic-API-Schiene), Versions-Koexistenz-Strategie
  (Capability-Gate statt Versionsnummern), Nightly-CI-Frühwarnjob und
  Trigger-Tabelle mit Rückbau-Liste.

### Added (Eichrunde Annot-Boxen: rotations-bewusst + Fremdkörper — 0.25.6)
- **Neue Metrik-Dimension `annot_body_overlaps` (Gewicht 25):** Referenz/
  Wert-Text eines Bauteils liegt auf einem FREMDEN Körper — der buck-Salat
  („19k/U1/MP1584": U1s Seiten-Annotation stand mitten auf R2). Eigene
  Felder über dem eigenen Körper (bewusst: Wert IM IC) zählen nicht; Power-
  Symbol-Texte (KiCad-Standard) bleiben außen vor. An den Profi-Referenzen
  geeicht (weiterhin exakt 0, 26/26 Metrik-Tests).
- **Annot-Boxen rotations-bewusst + ehrlicher Anker:** effektiver Text-
  Winkel = Symbol-Rotation + Property-Winkel (senkrechte Texte belegen eine
  Hochkant-Box), Anker ZENTRIERT wie KiCads Text-Default ohne `justify` —
  die alte „Text läuft vom Anker nach rechts"-Annahme verfehlte den fremden
  Körper unter dem Text um genau die halbe Breite. `(justify left/right)`
  wird respektiert.
- **Platzhalter messen mit wahrer Höhe:** `_embedded_pin_counts` liest die
  Pin-Zahl je Definition aus dem eingebetteten `lib_symbols`-Block des
  Dokuments (die einzige Quelle für Doppelpunkt-lose Platzhalter) — vorher
  maß die Metrik jeden Platzhalter mit der 2-Pin-Fallback-Höhe (5.08 statt
  20.32 mm beim 8-Pin-MP1584) und war blind für Gedränge/Überdeckung an ihm
  (U1↔R2-Spalt 0.25 mm ohne crowding-Treffer).
- Ergebnis: der Optimierer (validiert gegen dieselbe Metrik) räumt den
  buck-Salat selbst auf — U1 wird entdreht, Referenz oben/Wert unten.
  Galerie: 8×0 / eth 30 / motor 25 / usb 25 unverändert; buck ehrlich 5
  (offgrid-Konflikt-Stub 0.635 an U1s dichter Pin-Spalte — vorher blind 0
  MIT Salat). Roundtrip 10/10, Determinismus über Seeds byte-gleich.

### Changed (Metrik sieht Platzhalter + Grundluft 1.8 — 0.25.5)
- **Blinde Metrik-Stelle geschlossen:** `_bbox_for_lib` nutzt für UNSERE
  Platzhalter (lib_id ohne Doppelpunkt) die exakte Emitter-Formel statt des
  Mini-Fallbacks — der Optimierer konnte Bauteile ungestraft IN Platzhalter-
  Körper schieben (badness sah nichts). Nicht ladbare echte `Lib:Name`-
  Symbole (Profi-Referenzen!) behalten den konservativen Fallback — die
  Eichung bleibt exakt 0 (23/23). Der 99-Hz-Zähler misst dadurch ehrlicher
  (60 → 380: Label-auf-Platzhalter wird jetzt gezählt).
- **„Man kann weiter auflockern":** `SCHEMATIC_LAYOUT_FACTOR` 1.7 → 1.8.
- Galerie: unverändert 8×0 / eth 30 / motor+usb 25 — keine Regression.
- Bekannt & nächste Eichrunde: Seiten-Annotation GEDREHTER ICs kollidiert
  optisch mit Nachbarn (buck „19k U1"-Salat) — Annot-Boxen der Metrik sind
  rotations-blind und Annot-über-Fremdkörper wird noch nicht gemessen.

### Fixed (Global-Label-Format + Stecker-Anziehung — 0.25.4)
- **Global-Label-Text stand NEBEN dem Pfeilkasten** (Nutzer-Frage): unserer
  Emission fehlten `(shape input)` und das winkelabhängige `(justify …)` —
  ohne sie zentriert KiCad den Text auf den Anker statt ihn in den Kasten zu
  setzen. `SExpr.global_label` schreibt jetzt beides (justify left für 0/90,
  right für 180/270, wie Eeschema selbst). Per Format-Experiment verifiziert.
- **Riesige Leerstrecken zu den Steckern** (Nutzer-Frage): die Kanten-
  Platzierung nagelte J/P/CN/X an feste Blattkoordinaten, die Schaltung lag
  kompakt dazwischen. `place._pull_connectors_to_circuit` zieht Randstecker
  auf die Schaltungs-Bbox + 15.24 mm heran (Seite bleibt erhalten), danach
  laufen Luft-/Überlappungs-Regeln wie gehabt.

### Added (Unverbunden = mehr Luft — 0.25.3)
- **Nutzer-Regel („kost wenig, bringt viel"):** Bauteile OHNE gemeinsames
  Signal-Netz halten beim Entzerren mindestens einen Pin-Rasterpunkt
  (2.54 mm) ZUSÄTZLICHEN Abstand — Nähe ohne elektrischen Grund ist
  Gedränge; verbundene dürfen näher. Neue eine Quelle
  `geometry._pair_margin` (Basis + IC-Hof + Unverbunden-Luft) für
  `force_no_overlap` UND `_resolve_overlaps`; `place.py` annotiert
  `_sig_neighbors` aus der Signal-Netz-Nachbarschaft. PCB-Pfad/Aufrufer
  ohne Annotation unverändert. Zähler-Testfall: Roundtrip MATCH, badness
  60 stabil, Kreuzungen 18 → 8. Regelwerk §2 ergänzt; Wächter in
  `test_repetition.py`.

### Changed (Mehr Luft um pin-reiche ICs — 0.25.2)
- **Nutzer-Befund am 99-Hz-Zähler („U2 bekommt seine Netze überlappend
  ohne Grund — machs luftiger"):** Passive klebten an der IC-Kante, ihre
  Referenzen/Werte kollidierten mit Pin-Namen und Netz-Labels des ICs.
  Neu: ``geometry._ic_air`` — Bauteile mit ≥ 10 Pins bekommen +5.08 mm
  Hof in ``_boxes_overlap`` (wirkt in ``force_no_overlap`` und
  ``_resolve_overlaps``), denn ihre Pin-Namen ragen über den Körper hinaus
  und die Netz-Labels brauchen den Korridor davor. Zähler-Testfall:
  badness 360 → 60 (Label-Überlappungen 3 → 0). Kits: Roundtrip 10/10,
  deterministisch; nur ethernet/usb ändern sich (badness-Vergleich in der
  Galerie, keine Regression).

### Fixed (Universaltest-Fund: Fuzzy-Symbol kippte Bauteilklasse — 0.25.1)
- **„100n" wurde ein MOSFET:** die Fuzzy-Symbolsuche matchte den Wert
  eines Kondensators auf `Transistor_FET:BSC100N10NSFG` (enthält „100N") —
  die fremde Pin-Geometrie erzeugte im 99-Hz-Zähler-Test einen ECHTEN
  Kurzschluss (GND↔CONT), den das Roundtrip-Gate fing. Seit 0.24.1 füllt
  `normalize_parts` name←value, damit landen rohe Werte in der Suche.
  Neu: `symbol_lib._class_consistent` — 2-Pin-R/C/L/D dürfen per
  Fuzzy-Treffer nie die Bauteilklasse wechseln (R→Device:R, C→Device:C,
  L→Device:L, D→Device:D/LED/Diode); ICs bleiben frei. Kit-Emission
  byte-identisch; Wächter in `test_generation_minimal_spec.py`.

### Added (Wiederholung → Symmetrie + Blatt-Zentrierung — 0.25.0)
- **Nutzer-Regel „Wiederholung sieht gleich aus":** wiederholte
  Teilschaltungen (Multivibrator-Hälften, LED-Ketten-Glieder) werden
  strukturell erkannt (`common/repetition.py`: Anker-Signatur ≥3 Pins ×k,
  BFS-Zuordnung der k-fachen Begleiter, Abbruch bei ungleichen Instanzen)
  und identisch gestempelt — Layout der ersten Instanz als Schablone, alle
  Instanzen in Leseordnung nebeneinander. Der Layout-Optimierer bewegt die
  Formation nur noch STARR (`_rep_unit`) und dreht kein Mitglied einzeln.
- **„Auf dem Blatt zentrieren":** `place._center_on_sheet` schiebt das
  Gesamt-Layout als allerletzten Schritt raster-gesnappt in die Blattmitte
  (reine Translation, badness-invariant).
- Gates: Roundtrip 10/10, deterministisch, Galerie ohne Regression (nur
  led_ring ändert sich — Kette jetzt D1→D6 in Reihe, weiter badness 0);
  Multivibrator-Demo: zwei identische Hälften, badness 0. Wächter
  `tests/test_repetition.py` (Erkennung, Uniformität, starre Formation,
  Zentrierung).

### Fixed (Feld-Bug: Generator lehnte LLM-Specs ab — 0.24.1)
- **„zeichne einen astabilen Multivibrator" scheiterte im Feld:** der
  Validator erzwang `name`, `footprint` und je Pin `name`+`type` — ein
  Sprachmodell liefert Specs aber minimal (ref + value + Pin-Nummern) und
  lief in eine Fehlerwand (5 Retries, dann PowerShell-Flucht des Agenten).
  Neu: `validator.normalize_parts` ergänzt Ableitbares (name←value,
  footprint←Default nach Ref-Präfix via `resolve_footprint`, Pin-name←num,
  Pin-type←passive); abgelehnt wird nur echt Unbrauchbares. Fehler-Results
  tragen jetzt einen `hint` mit dem Minimal-Spec-Format, die Tool-Docstrings
  beschreiben es. E2E verifiziert: Multivibrator-Minimal-Spec →
  `generate_project` → Netzlisten-Roundtrip MATCH; Demo-Kit-Emission
  byte-identisch. Neuer Wächter `tests/test_generation_minimal_spec.py`.

### Changed (Codequalität: Duplikat-Gate, geteilte Helfer, except-Audit — 0.24.0)
- **Duplikat-Abbau auf 0 + CI-Gate:** pylints `duplicate-code` (Schwelle 10
  Zeilen) läuft jetzt als eigener CI-Schritt und der Bestand ist abgeräumt:
  Klammer-Walker → `utils/sexpr_parser.block_end`; Segment-Emitter →
  `utils/pcb_net_format.segment_block`; File-Edit-Abschluss →
  `tools/_text_edit.apply_text_edit` (7 Tool-Bodies); Warm-Worker-Server-
  Seite → `tools/_worker_common.py` (pcbnew-Import stumm + Request-Loop,
  von allen 3 Workern genutzt; Start per Dateipfad UND Paket-Import
  verifiziert); Inline-S-Expr-Parser in `audit_tools`/`netlist_tools` →
  `parse_sexpr`; `_build_ref_to_nets`-Kopie in `defrag_place` → Import aus
  `placement_cost`; BOM-Sammel-Kern → `bom_consolidate.
  iter_classified_footprints`; Netzlisten-Tool-Präambel →
  `netlist_parser.load_netlist_with_progress`.
- **except-pass-Audit (102 Stellen):** 82 stille Schlucker tragen jetzt
  einen Begründungs-Kommentar, 6 diagnose-kritische Fallbacks loggen per
  `logger.debug` (Symbol-Index-Cache, Pad-Cache, Constraint-Solver-,
  Template-Matcher-Fallback, `drop_commit`), 6 Exception-Typen verengt
  (`OSError`/`SubprocessError` statt breitem `Exception`). Kein
  Erfolgspfad geändert.
- Emission byte-identisch über alle 10 Kits (Hash-Vergleich vor/nach),
  Roundtrip 10/10, volle Suite grün.

### Changed (Doppelungen raus, Runde 2 — 0.23.0)
- **Trainings-Pipeline entfernt (−626 Zeilen):** die drei PDF-Extraktions-
  Skripte in `kicad_mcp/training/` (`extract_pages`, `detect_content`,
  `crop_regions`) waren einmalige Werkzeuge zur Template-Gewinnung — vom
  Produkt ungenutzt (es braucht nur `training/templates/schematic/*.json`).
  Bei Bedarf: Git-Historie.
- **EINE Bus-Inferenz statt zwei:** `common/bus_detect.py` pflegte eine
  eigene Protokoll-Tabelle parallel zu `utils/bus_infer` (Bus-Radar).
  `find_bus_groups` (Ref-Paar-/Pin-Paar-Gruppierung für die bus-bewusste
  Platzierung in `defrag_place`) bezieht die Netz→Bus-Zuordnung jetzt aus
  `bus_infer.group_buses` — ein neues Protokoll wird nur noch an einer
  Stelle eingetragen. Emission byte-identisch über alle 10 Kits
  (Hash-Vergleich), Roundtrip 10/10. (Dritte Vokabel-Insel in
  `review_tools._BUS_PATTERNS` bleibt bewusst: Review-Tagging inkl.
  RESET/BOOT, andere Semantik.)

### Changed (Ein Qualitäts-Richter statt zwei — 0.22.0)
- **`schematic_scorer.py` entfernt (−953 Zeilen):** der Vor-Emissions-Scorer
  (0–100-Score auf `parts/nets`) war seit der geeichten Metrik ein zweites,
  divergenzfähiges Urteil. Seine einzigen Nutzer (`benchmark_schematic`,
  `benchmark_loop`) messen jetzt das EMITTIERTE Blatt mit
  `layout_measure` — Result-Felder `score` → `badness` (0 = Profi-Referenz)
  + `badness_breakdown` (Verstoß-Zähler je Dimension); `violations` sind
  jetzt die konkreten Befunde der Metrik („Label 'THR' auf/an Device:R").
  Neu: `Metrics.breakdown()`. Emission byte-identisch (10-Kit-Hash-Vergleich
  vor/nach), Roundtrip 10/10, Tool-Zahl unverändert 189.

### Added (Power-Passives senkrecht — 0.21.0)
- **Nutzer-Regel:** Geht ein 2-Pin-R/C/L an ein Power-Netz (GND/VCC/3V3 …),
  steht er SENKRECHT (Rotation 0/180, Pins oben/unten, Power-Symbol direkt
  drüber/drunter) — wie in jedem Profi-Schaltbild (Pull-up, Abblock-C).
  `place._orient_power_passives` setzt die Konvention vor der finalen
  Abstands-/Überlappungs-Runde; `_rot_locked` verhindert, dass der
  Layout-Optimierer sie wieder wegdreht (90/270-Kandidaten werden für
  gesperrte Teile verworfen, 0↔180 bleibt erlaubt). Regelwerk
  `docs/schaltplan_regeln.md` §2 ergänzt. Gates unverändert grün: Roundtrip
  10/10, byte-deterministisch über PYTHONHASHSEED 1/2/7. Neuer Wächter
  `tests/test_power_passives.py` (Einheit + Optimierer-Sperre + Ende-zu-Ende).

### Changed (Code-Diät & Tempo, Runde 1 — 0.20.1)
- **`resolve_lib_id` memoisiert** (`symbol_lib._RESOLVE_CACHE`, Schlüssel =
  Name/Wert/lib_id/Ref-Präfix/Pin-Zahl): Profil zeigte 528 Aufrufe je Emission
  (108 Fuzzy-Index-Suchen) = 44 % der Laufzeit. Emission 106 → 68 ms;
  Ausgabe byte-identisch (10-Kit-Hash-Vergleich).
- **Toter Code entfernt (−143 Zeilen):** `_carve_pin_corridors`,
  `_cells_owned_by`, `_should_wire_power_net` (route.py) und
  `_rasterize_path_cells` (common/routing.py) — seit dem Netzlisten-Umbau
  (Power-Symbole je Pin, Registry-Routing) unbenutzt.
- **`docs/optimierungsplan_schematic.md`:** priorisierter Plan (Tempo /
  Kürzung / Suche) mit Messrezept; eiserne Regel: Roundtrip 10/10 +
  Byte-Determinismus nach jedem Schritt.

### Added/Fixed (Kosmetik-Runde: Luft, Pin-Zonen, Determinismus — 0.20.0)
- **Mehr Luft (Nutzer: „einfach mehr Luft lassen"):** `SCHEMATIC_LAYOUT_FACTOR`
  1.4→1.7; neue Metrik ``crowding`` (Körper-Spalt < 2.54 mm, Gewicht 10, an den
  Referenzen auf 0 geeicht) treibt den Optimizer zum Auseinanderziehen.
- **Pin-Zonen-Schutz für Labels:** ``label_overlaps`` misst jetzt gegen Körper
  + Pin-Zone (+2.84 mm, effektive Größe = min(Grafik-Bbox, Pin-Käfig) — WS2812B/
  MB6S-Deko zählt nicht); ``_stub_dir_free`` dreistufig (Zone frei → Spitze
  frei → elektrisch sauber) mit Sonde über die Label-Länge; Declutter nutzt
  dieselbe Zone; Heilungs-Label sitzt an der Pin-Stub-Spitze mit Auswärts-
  Winkel statt am Pin (Pin liegt IN der Zone).
- **wire_through_body:** Pin-Anschluss-Ausnahme generalisiert (Segment-Endpunkt
  auf einem PIN des Symbols = Anschluss, auch wenn der Pin innerhalb der
  Grafik-Bbox liegt); L-Bend prüft gegen das VOLLE Hindernis-Set; Routen
  zusätzlich geometrisch gegen Körper-Innenzonen (`_seg_through_body_core`).
- **Determinismus (Wurzelursache der „flaky" Tests + springender badness):**
  drei PYTHONHASHSEED-abhängige Stellen fixiert — String-Set-Iteration in
  ``connectivity._build_connection_graph`` (Kanten-/Zählreihenfolge → alle
  Tie-Breaks), Cap-Zuteilungs-Sortierung ohne Ref-Tie-Break in
  ``defrag_place`` (+ sortierte Set-Summen), Pin-Emissions-Sort mit
  kollabierendem Key für nicht-numerische Pin-Nummern in ``builder``.
  Gleiche Eingabe → byte-identisches ``.kicad_sch`` über Prozesse.
- ``build_schematic(optimize_seconds=…)``: Optimizer-Zeitbudget durchreichbar.
- **Stand:** 7/10 Kits badness 0 unter der VERSCHÄRFTEN Metrik (ethernet 50,
  motor 25, usb 25 — je Referenz/Wert-Text-Berührungen); Roundtrip 10/10.

### Added/Fixed (Netzlisten-Roundtrip: gezeichnet = gewollt, 10/10 — 0.19.0)
- **Nutzer-Vorschlag als hartes Gate:** „Nimm die Original-Schaltung, mach eine
  Netzliste; nimm deine gezeichnete Schaltung, erstelle daraus eine Netzliste;
  wenn beide matchen, melde Erfolg." → Neues Modul
  **`generators/schematic/netlist_check.py`**: Ist-Netzliste via ``kicad-cli
  sch export netlist`` (KiCads eigene Konnektivitäts-Engine) aus der fertigen
  ``.kicad_sch``, pin-genauer Gruppen-Vergleich (namensunabhängig) mit
  ``merged``/``split``/``missing``-Befunden + ``build_pin_aliases``.
  **Erstbefund: ALLE 10 Kits elektrisch falsch** (kit_seeding: EIN Netz mit
  allen 24 Pins) — bei badness 0 und grünem ERC. Jetzt **10/10 MATCH**, auch
  mit Optimizer. Dauerhafter Wächter ``tests/test_netlist_roundtrip.py``.
- **Kurzschluss-Prävention (route.py, Segment-Registry):** jede Route/jeder
  Stub wird VOR der Emission gegen alle fremden Segmente (Endpunkt-teilt-Punkt,
  Endpunkt-auf-Segment, kollineare Überlappung) UND alle Pin-Positionen
  geprüft; Ausweichen über Richtung/Länge (5.08/2.54/1.27/7.62/10.16 …), sonst
  Label-Heilung. Läufe des A* starten netz-geordnet (Power zuerst).
- **Selbstheilung per Union-Find:** zerfällt ein Netz in Komponenten (Kante
  nicht routbar), bekommt JEDE Komponente ein gleichnamiges Label (verdrahtete
  am Pin, einzelne mit Stub) — vorher blieben Rest-Inseln namenlos.
- **Power-Symbole an JEDEM Power-Pin** statt MST-verdrahteter Power-Netze
  (wie die Profi-Referenzen; KiCad vereint global über den Symbol-Namen).
  Stub-Länge/Richtung konfliktverhandelt; Rails ohne Symbol (VIN) über
  Global-Labels mit derselben Verhandlung.
- **Junction-Punkte (`SExpr.junction`, netz-bewusst):** an jedem Punkt mit ≥3
  Draht-Enden und jedem T-Abzweig (Endpunkt auf Segment-Innerem) desselben
  Netzes — Nutzer-Regel „Abzweig braucht einen Punkt".
- **Pin-Name schlägt Pin-Nummer (`_map_user_to_real_pins`, eine Quelle für
  Geometrie, Emission und Vergleich):** Kits adressieren semantisch; reale
  Symbole nummerieren anders (DRV8871: Kit-GND(8) = reales OUT2(8)!).
  Namens-Match zuerst, mit ~{}-Dekoration, Slash-Aliassen (TXD0/MODE0),
  Aktiv-Low-Toleranz (NRST↔RST), Synonymen (DRAIN→D), gestapelten Namens-
  Gruppen (GND auf 1/7/9) und Belegungs-Tracking; nicht Zuordenbares bleibt
  ehrlich offen statt falsch verbunden.
- **Platzhalter-Pins korrekt:** Y-Flip (Lib ist Y-up) + Mirror/Rotation im
  Placeholder-Pfad — vorher dockten alle Drähte eines gedrehten/jeden
  Platzhalter-ICs an gespiegelten Pins an.
- **Pin-Kollisions-Auflösung (`_resolve_pin_collisions`):** Bauteile, deren
  Pins durch Placement-Pech exakt auf fremden Pins landen (R2:1 auf U1:7),
  werden deterministisch in 2.54er-Schritten verschoben.
- **Declutter elektrisch sicher:** bewegt nur noch reine Stichleitungen
  (Anker-Grad 1, nicht auf Pin-Position), prüft die neue Lage elektrisch.
- **Merge-Guard:** kollineare Segmente werden nur noch mit GETEILTEM Endpunkt
  vereinigt (gleicher Knoten = gleiches Netz) — das blinde Vereinigen hatte
  zwei übereinanderliegende Fremd-Stubs kurzgeschlossen.
- **4-Pin-Quarz** („Crystal" mit 4 Pins) → ``Device:Crystal_GND24`` (GND-Pins
  2/4 waren vorher offen).

### Fixed (Finalisierung: 10/10 Kits badness 0 — 0.18.0)
- **Gegenrotierte Referenz/Wert-Texte (`builder._emit_symbol_instances`):**
  KiCad rendert Property-Text RELATIV zur Symbol-Rotation — bei rot=90/270
  wurde `angle=0` vertikal gezeichnet und Referenz+Wert (gleiches x) lagen als
  Buchstabensalat übereinander („10uC1", „22uG2" an JEDEM liegenden C/R/D, in
  praktisch jedem Kit sichtbar). Fix: Property-Winkel 270 (rot=90) bzw. 90
  (rot=270) → effektiv horizontal, Referenz oben, Wert darunter — wie KiCads
  eigene Feld-Autoplatzierung. Empirisch am Render verifiziert (Probe-Patch,
  dann alle 10 Kits).
- **Pin-Zahl-Sanity-Check im Symbol-Match (`symbol_lib._pin_count_sane`):**
  der Fuzzy-Namens-Match traf für „STM32F407" (11 deklarierte Pins) das
  erstbeste `STM32F407IEHx` (UFBGA-176, ~220-mm-Symbol) — Pin-Nummern passten
  nicht aufs BGA-Raster, das Riesen-Symbol verschluckte das ganze Blatt
  (ethernet_device badness 1296). Regel: Symbol darf höchstens
  ``max(5·n, n+24)`` Pins haben, sonst Platzhalter-Box in Kit-Größe. Faktor 5
  an den Kits geeicht: usb_sensor_hub (10 Pins) behält sein echtes 48-Pin-
  STM32F103, ethernet bekommt die kompakte Box.
- **Wirkung: alle 10 Demo-Kits badness 0** (ethernet 1296→0; erstmals 10/10).
- Tests: gegenrotierter Property-Winkel bei rot=90; Sanity-Check lehnt
  176-Pin-Symbol für 11-Pin-Teil ab / erlaubt es für 60-Pin-Teil.

### Added/Fixed (Pin-Stubs an den ICs + keine Busse mehr quer durch die Bauteile — 0.17.0)
- **Nutzer:** „noch immer viele vor allem Lokale Busse über die Bauteile
  drübergezeichnet und keine Stubs an den ICs???" → zwei Wurzelursachen behoben,
  am Goldstandard geeicht (Profi-Referenzen bleiben badness 0).
- **Pin-Stubs (`route._pin_stub_point` + `_emit_pin_stub`, neu):** Jeder
  A*-verdrahtete Pin bekommt eine kurze axiale Leitung (`PIN_STUB_LEN = 2.54`)
  aus dem Körper heraus; der A*-Draht startet erst an DEREN Spitze. Zwei Effekte:
  (1) sichtbarer Anschluss-Stub an jedem IC-Pin; (2) der Router beginnt
  AUSSERHALB des Körpers → nie ein Bus quer durchs eigene (große) Bauteil.
  Angewandt auf alle vier verdrahteten Pfade (Signal-`use_wires`, Signal-
  Kurzkanten, Power-verdrahtet, Power-gemischt). ERC-Gate bleibt grün.
- **Rotations-bewusste Hindernisse (`_build_obstacle_set` / `_cells_owned_by`):**
  Ein um 90/270° gedrehtes Bauteil hatte im Router vertauschte Breite/Höhe — ein
  waagrechter Widerstand wurde als schmal-hohes Hindernis modelliert, sodass A*
  einen waagrechten Bus MITTEN durch die Körpermitte zog. Der Swap (wie in Metrik
  und Label-Richtung längst) blockiert jetzt den echten Körper.
- **A* nutzt keine globalen Pin-Zellen mehr:** Da Routen jetzt Stub-Spitze→
  Stub-Spitze laufen (beide außerhalb der Körper), wird kein Pin mehr global als
  passierbar markiert — vorher konnte A* über die eigenen Pins eines waagrechten
  Widerstands durch dessen Körper „springen".
- **Metrik `wire_through_body` (geschärft, `layout_measure`):** die alte
  aggressive Pin-Ring-Ausnahme („Endpunkt < 2.84 mm vom Rand → ganzes Segment
  ignorieren") ist RAUS — sie versteckte reale Busse quer über große ICs (STM32)
  und Widerstands-Körper. Voller Körper-Rahmen (Shrink 0.4). Neue, enge Ausnahme
  nur für Ein-Pin-Bauteile (TestPoint/Flag), deren Anschluss GENAU im Zentrum
  liegt (≤0.6 mm) — ein Stub startet dort zwangsläufig „im" winzigen Körper.
- **Power-Symbol-Erkennung (`layout_measure._parse`):** Power-/Flag-Symbole
  werden jetzt auch an der Referenz `#PWR…`/`#FLG…` erkannt (universeller
  KiCad-Marker), nicht nur an `power:`-lib_id/`in_bom no`. Profi-Referenzen mit
  eigener Symbol-Lib (`sallen_key_schlib:GND`, `in_bom yes`) zählten sonst als
  „Bauteil" → ein Draht in ihren Stub als „quer durchs Bauteil" (Falsch-Positiv,
  das die 0-Eichung sprengte). Jetzt Referenzen wieder sauber 0.
- **Wirkung:** 9/10 Demo-Kits **badness 0** (ac_dc, audio, buck, kit_seeding,
  led, motor, production, sketch, usb — usb von 50 → 0). Nur ethernet_device
  bleibt (überdimensioniertes 176-Pin-STM32-Symbol aus der Lib — Symbol-Wahl,
  kein Routing-Problem). Visuell: Drähte routen um U1 herum statt hindurch,
  jeder Pin trägt einen Stub.
- Tests: `test_pin_stubs.py` (Stub zeigt auswärts; Hindernis rotations-bewusst;
  verdrahtete Pins bekommen einen Stub); `test_layout_measure.py` erweitert (Bus
  quer über IC zählt trotz Pin-Endpunkt; Ein-Pin-Zentrums-Anschluss zählt nicht;
  Custom-`#PWR`-Symbol ist kein Bauteil).

### Added (draht-bewusste Label-Umdrehung — Labels überdecken keine Drähte mehr — 0.16.1)
- **Nutzer:** „die Beschriftungen sind das Letzte, was sich gegenseitig überdeckt"
  → **`builder._declutter_labels` (neu):** ein Nach-Emit-Schritt dreht/spiegelt
  jedes Netz-Label, dessen Text-Box einen FREMDEN Draht, einen Bauteilkörper oder
  ein anderes Label trifft, auf eine freie Auswärts-Richtung — und legt seinen
  Stub um seinen Pin herum entsprechend um. Verbindung bleibt intakt (Pin↔Label),
  **ERC-Gate grün**. Findet sich keine freie Richtung, bleibt es.
- **Wirkung:** usb_sensor_hub ``label_wire_overlaps`` 2→**0**, ac_dc_supply
  vollständig **badness 0**. Damit überdecken Labels keine Drähte mehr, wo Platz
  ist. usb behält noch 2 Referenz/Wert-Text-Überlappungen (kein Label-Problem),
  ethernet die Label-auf-Körper vom Riesen-Symbol.
- Tests: kollidierendes Label wird gedreht + Stub folgt (Verbindung intakt);
  freies Label bleibt unangetastet.

### Added/Changed (vollständige gegenseitige Überdeckung + 5-mm-Label-Leitung — 0.16.0)
- **Nutzer-Vorgaben:** „Label und wire und Bauteile dürfen sich alle gegenseitig
  nicht überdecken"; „beim motor_driver liegen Labels auf den Kondensatoren";
  „auch Labels benötigen 5 mm Leitung gleich wie alle Bauteile".
- **`label_overlaps` box-basiert:** prüft jetzt die TEXT-BOX des Labels gegen den
  Bauteil-Rahmen (nicht nur den Ankerpunkt) — fängt den Fall „Label-Text ragt
  über einen Nachbar-Kondensator" (motor_driver), den die Anker-Prüfung übersah.
- **Neu `label_label_overlaps` + `label_wire_overlaps`:** zwei Netz-Labels
  übereinander bzw. ein Label über einem FREMDEN Draht (der eigene Stub zählt
  nicht). Damit sind ALLE Paarungen gemessen: Bauteil↔Bauteil, Label↔Bauteil,
  Label↔Label, Label↔Draht, Referenz/Wert↔Referenz/Wert, Draht↔Bauteil,
  Draht↔Draht. Zeichenbreite 0.6 mm → Profi-Referenzen bleiben 0 (geeicht).
- **`LABEL_STUB_LEN` 2.54 → 5.08:** Labels bekommen wie alle Bauteile eine
  sichtbare 5-mm-Leitung; das schiebt sie zugleich weiter von Bauteilen und
  Nachbar-Drähten weg.
- **Wirkung:** motor_driver ist jetzt sauber (das Label sitzt 5 mm frei neben den
  Cs); 7/10 Kits badness 0. ac_dc/usb behalten am 30-s-Budget 1–2 Label-über-
  Draht-Reste, ethernet bleibt der Symbol-Sonderfall.

### Fixed (Draht-Merge: „keine Leitungen übereinander" jetzt hart bei 0 — 0.15.3)
- **`builder._merge_overlapping_wires` (neu):** ein Nach-Emit-Schritt führt
  kollinear ÜBEREINANDER liegende Draht-Segmente (Router-Artefakt desselben
  Netzes) zu ihrer Vereinigung zusammen. Nur ECHT überlappende Intervalle —
  fortlaufende Leitungen (geteilter Endpunkt), Lücken (verschiedene Netze) und
  Diagonalen bleiben unangetastet. Elektrisch neutral (überlappende kollineare
  Segmente decken sich mit ihrer Vereinigung), **ERC-Gate bleibt grün**.
- **Wirkung:** ``wire_overlaps`` ist jetzt schon beim Bauen **0 für ALLE Kits**
  (vorher buck 24, production 14 …). Damit erreichen **8/9 Kits badness 0** —
  auch die vorher hängenden usb_sensor_hub (85→0) und buck (50→0); der
  Optimierer musste den Rest gar nicht mehr suchen. Nur ethernet_device bleibt
  (überdimensioniertes MCU-Symbol). Kreuzungen sind dabei erlaubt und kommen vor.
- Tests: überlappende Segmente mergen, fortlaufende/getrennte/diagonale nicht;
  alle Kits ``wire_overlaps=0`` nach dem Build.

### Added/Changed (Regel „keine Leitungen übereinander"; Kreuzungen sind OK — 0.15.2)
- **Nutzer-Vorgabe:** „keine Leitungen übereinander" UND „Kreuzungen sind ok".
- **`layout_measure.wire_overlaps` (neu):** zählt Paare von Draht-Segmenten, die
  ÜBEREINANDER liegen — kollinear (beide waagrecht auf gleichem y bzw. beide
  senkrecht auf gleichem x) mit gemeinsamer STRECKE (nicht nur ein Punkt →
  fortlaufende Leitung zählt nicht; ein X → Kreuzung zählt nicht). Gewicht 18.
  Referenzen kalibrieren auf 0; die Kits hatten real welche (buck 24,
  production 14). Der Optimierer treibt sie stark runter (production 14→0,
  buck 24→3); den harten Rest an dichten Stellen räumt Phase B (mehr Abstand →
  der Router bekommt getrennte Spuren).
- **Kreuzungen (X) nicht mehr bestraft:** ``wire_crossings``-Gewicht 8 → **0**.
  Kreuzungen sind laut Nutzer OK; sie werden weiter GEMESSEN und berichtet, aber
  zählen nicht mehr gegen die badness. Damit blockiert eine unvermeidliche
  Kreuzung nicht mehr den „sauber"-Status.
- Tests: kollinear-überlappende Leitungen werden erkannt; fortlaufende (nur
  Endpunkt geteilt) und Kreuzungen NICHT; Referenzen bleiben ``wire_overlaps=0``.

### Changed (Struktur-Angleichung Phase A.2: Referenz/Wert bei ICs über/unter den Körper — 0.15.1)
- **Referenz/Wert-Platzierung (`builder._emit_symbol_instances`):** bei
  mehrpinnigen ICs (> 4 Pins, Pins links/rechts) kommen ``U1`` / ``74HC595`` jetzt
  ÜBER bzw. UNTER den Körper, x-zentriert — statt rechts auf die seitliche
  Pinreihe. Damit sind die Pin-Namen (SER, SRCLK, QA–QH …) wieder lesbar, nicht
  mehr unter „U1 74HC595" begraben. 2-Pin-Passives behalten die bewährte
  Seiten-Platzierung (an der Referenz auf 0 Annotations-Überlappung geeicht).
- **Wirkung:** am 74HC595 (production_ready) sind die Pin-Namen jetzt frei; alle
  8/9 Kits weiter badness 0 (production 218→0). Referenzen unverändert 0.

### Changed (Struktur-Angleichung an die Original-Schaltbilder, Phase A.1: Power-Rails als Symbole — 0.15.0)
- **Nutzer-Vorgabe:** „Warum orientieren wir uns nicht an den Original-Schaltungen
  und versuchen diesen nahe zu kommen." — Kern-Erkenntnis (gemessen): die Profis
  sind nicht besser *platziert*, sondern strukturell anders verdrahtet. Label-
  Dichte Referenz ~0.06–0.33/Symbol, wir bis 1.38 → Text-Stau an ICs/Steckern.
- **Power-Rail-Namen normalisiert (`route._normalize_power_name`):** die Kits
  nennen Rails ``P5V`` / ``P3V3`` (auch ``5V`` / ``3V3`` / ``3.3V``). Ohne
  Normalisierung wurden die als wiederholtes TEXT-Label emittiert; jetzt mappen
  sie auf die kompakten KiCad-Power-Symbole (``power:+5V`` / ``power:+3V3``) — so
  zeichnen es die Referenzen. Die Verbindung bleibt intakt: das Symbol trägt den
  ORIGINAL-Netznamen als Value (``P5V``), nur der Graph ist das Rail-Symbol.
- **Wirkung:** led_ring 16→**8** Labels (Dichte 0.73→**0.29**, Referenz-Niveau),
  usb 22→14, ethernet leicht besser. 8/9 Kits weiter badness 0; ethernet 985→840.
  Am Render sichtbar: die P5V-Textflut an den LEDs ist weg, Versorgung sind jetzt
  saubere Symbole.
- Erster Schritt der vollen Struktur-Angleichung; es folgen: Signal-Label-
  Sparsamkeit (lokale Netze verdrahten), die 3 Struktur-Regeln (Rails/Reihe-
  Shunt/getrennte Blöcke) und eine Nähe-zur-Referenz-Metrik.

### Added (Regel „keine Leitung durch ein Bauteil" als harte Wächter-Metrik — 0.14.3)
- **Nutzer-Vorgabe:** „Es dürfen auch keine Leitungen durch Bauteile gehen."
- **`layout_measure.wire_through_body` (neu):** zählt Draht-Segmente, die das
  Innere eines FREMDEN Bauteil-Rahmens queren (Liang-Barsky Segment-vs-Rechteck).
  Das verbundene Bauteil wird sauber ausgeschlossen — über den Abstand zum
  Körper-RAND (≤ Pin-Länge), NICHT zentrums-basiert, damit ein Riesensymbol wie
  der STM32 keine überdimensionale Ausnahme-Zone bekommt. Gewicht 30 in
  ``badness``.
- **Messung ehrlich:** alle 9 Kits UND beide Profi-Referenzen haben schon 0
  Durchquerungen — der A*-Router legt die Leitungen bereits um die Bauteil-Boxen.
  Die Metrik ist damit ein **Wächter**: sie hält den Optimierer (der Bauteile
  verschiebt und neu routet) davon ab, je eine Durchquerung einzuführen, und
  schützt gegen Regressionen. (Ein früher Wegwerf-Prüfer meldete fälschlich
  Treffer — sein Ausschluss war zentrums-basiert und fehlerhaft.)
- Tests: gecrafteter Quer-durch-IC-Draht wird erkannt; ein Pin-Stub am Rand
  nicht; Referenzen bleiben ``wire_through_body=0``.

### Added (Regel 6 vollständig: auch Referenz/Wert-Text darf nicht überlappen — 0.14.2)
- **Nutzer-Fund am gerenderten Bild:** bei den Widerständen war Regel 6 NICHT
  eingehalten — die Bauteil-Körper überlappten zwar nicht (badness 0), aber die
  Referenz/Wert-**Beschriftung** eng gepackter Passives lag übereinander. Die
  Metrik hatte diesen Fall nicht gemessen.
- **`layout_measure.annot_overlaps` (neu):** zählt Paare von Bauteilen, deren
  SICHTBARE Referenz/Wert-Texte sich überlappen (block-genau je Symbol; verborgene
  Felder via balanciertem ``(hide yes)`` ausgeschlossen — ein simpler Tail-Regex
  verpasste das mehrzeilige Format). Konservative Zeichenbreite (0.6 mm), sodass
  die Profi-Referenzen **0** bleiben (am Goldstandard geeicht), die echten
  Kollisionen aber sichtbar werden. Gewicht 25 in ``badness``.
- **Ergebnis:** der Optimierer treibt jetzt auch die Text-Überlappung auf 0 —
  **8 von 9 Kits erreichen badness 0** (buck 66→0, production_ready 81→0,
  usb_sensor_hub 93→0), alle mit ``annot_overlaps=0``. Am gerenderten Bild
  bestätigt: die Widerstands-Beschriftung liegt nicht mehr übereinander.
  ethernet_device unverändert der bekannte Sonderfall (überdimensioniertes
  MCU-Symbol).
- Tests: gecraftete Annotations-Überlappung wird erkannt; verborgene Felder
  zählen nicht; Referenzen bleiben ``annot_overlaps=0``.

### Fixed (KRITISCH: Überlappungs-Metrik war blind — Closure-``+=``-Falle — 0.14.1)
- **`layout_measure._bbox_for_lib` fiel bei JEDEM Symbol mit Rechteck-Körper
  auf die 2.54×2.54-Fallback-Bbox zurück:** in der verschachtelten ``_walk``-
  Closure rebindet ``rxs += [...]`` die freie Variable → Python macht sie lokal →
  ``UnboundLocalError`` → vom bloßen ``except`` verschluckt. Folge: die
  Bauteil-Überlappungs-Metrik sah JEDES Bauteil als winzigen 2.54-mm-Kasten und
  war damit blind für echte Überlappungen (außer exakten Stapeln). ``.extend``
  statt ``+=`` behebt es. **Damit war die 0.14.0-Aussage „alle 10 Kits erreichen
  badness 0" mit der defekten Metrik gemessen und FALSCH** — siehe unten die
  ehrlichen Zahlen. Regressions-Test: ein IC misst jetzt real groß, nicht
  Fallback.

### Changed (ehrliche Zahlen + Label-Richtung an der Quelle + Zeitbudget — 0.14.1)
- **Freie Label-Richtung (`route._free_stub_direction`):** ein Netz-Label wählt
  jetzt die Auswärts-Richtung, deren Anker in FREIEM Raum landet (statt blind der
  Pin-Normalen zu folgen und in einen Nachbarn zu ragen) — direkt die
  Nutzer-Regel „alle Netlabels müssen vom Bauteil weg zeigen". Power-Symbole
  bleiben GND↓/VCC↑ (Richtung dort erzwungen).
- **`spread`-Operator** (alles vom Schwerpunkt weg skalieren) für dichte Cluster,
  wo ein Label in jeder Richtung einen Nachbarn trifft; **Wanduhr-Budget**
  (`max_seconds=30`), damit Extremfälle die Generierung nie minutenlang blockieren
  (der Optimierer gibt das bis dahin beste, nie schlechtere Layout zurück).
- **Ehrliche Bilanz mit der korrigierten Metrik:** 8 von 9 Demo-Schaltungen
  erreichen badness 0 (ac_dc, audio, buck, led, motor, production_ready, sketch,
  usb). **`ethernet_device` erreicht NICHT 0:** es nutzt für 11 verdrahtete Pins
  ein 176-Pin-STM32F407-Symbol (71×221 mm); die Labels sitzen im riesigen Körper
  und können mit 2.54-mm-Stub nicht heraus. Root-Cause = überdimensioniertes
  Symbol (Symbol-Wahl), kein Layout-Problem — es bleibt als „oversized MCU
  symbol slimmen" offen.

### Added (Layout-Optimierer: echte Such-Schleife gegen den Profi-Goldstandard — 0.14.0)
- **Nutzer-Vorgabe:** „der größte Hebel: nichts übereinander legen … alle
  Netlabels müssen vom Bauteil weg zeigen … versuche deine vorgeschlagene
  Selbstoptimierung mit 20 Regeln und echten Loops."
- **`generators/schematic/layout_optimizer.py` (neu):** eine echte Hill-Climb-
  Schleife über der Platzierung. Sie verschiebt/dreht Bauteile in Raster-
  Schritten, emittiert den FERTIGEN Schaltplan neu und misst ihn mit der am
  Profi-Goldstandard geeichten `layout_measure.badness` — ein Schritt wird nur
  behalten, wenn die badness SINKT (sonst zurückgerollt), plus Zufalls-Neustart
  gegen lokale Minima. Die Nachbarschaft steht als **wartbare 20er-Operator-
  Liste** (`OPERATORS`: Nudges 1–2 Raster, Diagonalen, 90°-Drehung, Passiv-
  Pin-Tausch, Achsen-Ausrichtung, Nachbar-Kompaktierung, Bauteil-Tausch).
- **Ergebnis (empirisch):** die Demo-Schaltungen erreichen nach der Optimierung
  **badness 0** — dieselbe 0 wie die Profi-Referenz-Schaltbilder: 0 Bauteil-
  Überlappungen, 0 Label-auf-Bauteil, 0 Labels-die-in-Nachbarn-ragen, 0 Draht-
  Kreuzungen, 0 Diagonalen, alles auf dem Raster. (**Korrektur in 0.14.1:** diese
  Messung lief noch mit der bbox-defekten Metrik; ehrlich sind es 8 von 9 Kits →
  0, ethernet_device ausgenommen — siehe 0.14.1.)
- **In der Pipeline:** `build_schematic(..., optimize=True)`; aktiviert in
  `generation_tools` (generate_project / generate_schematic / generate_from_netlist).
  Der Optimierer macht ein Layout NIE schlechter als die Pipeline (Eingang =
  Untergrenze). `place=False`/`keep_placement=True` erlauben das wiederholte
  Emittieren derselben Platzierung ohne Neu-Platzieren.

### Fixed (Bauteil-Bbox: polyline-gezeichnete Symbole waren 0 breit — 0.14.0)
- **Root-Cause `production_ready`-Überlappung:** `common.bbox._get_symbol_bbox`
  sammelte nur `pin`+`rectangle`. Kondensatoren/Spulen/Dioden zeichnen ihren
  Körper aber als **polyline** → Breite kollabierte auf 0 → `force_no_overlap`
  ließ zwei gestapelte Cs 1.27 mm ineinander stehen. Jetzt werden
  `polyline`/`circle`/`arc` mitgezählt und jede Achse auf ≥ `GRID` gedeckelt.
  Damit deckt sich die Enforcement-Bbox mit der Fitness-Bbox.

### Performance (Symbol-Auflösung 35× schneller — 0.14.0)
- **`symbol_cache.get_real_symbol` memoisiert:** vorher re-extrahierte JEDER der
  ~170 Aufrufe pro Emit das Symbol neu, und `_paren_depth_before` scannte dabei
  bei Stock-Libs zig MB → **~18 s pro Schaltplan-Emit**. Mit dem Symbol-Memo
  (kleine Strings, RAM-unkritisch) + memoisiertem `_pins_from_real_symbol` /
  `_detect_units` sinkt Place+Emit von **18,6 s → 0,5 s**, Re-Emit **8,7 s →
  0,12 s**. Das beschleunigt JEDE Schaltplan-Generierung, nicht nur den
  Optimierer (der davon überhaupt erst praktikabel wird).

### Changed (Layout-Regel-Set aus echten Referenz-Schaltbildern neu abgeleitet — 0.13.0)
- **Nutzer-Vorgabe:** „rendere die Original-Schaltung aus dem Internet und leite
  daraus selbst 10 Regeln ab … nur diese 10 und die alten rausnehmen."
- **Referenz:** die offiziellen KiCad-Demos `sallen_key` + `rectifier`
  (gitlab.com/kicad) heruntergeladen, mit `kicad-cli`+cairosvg gerendert,
  angesehen. `layout_rules.RULES` komplett ersetzt durch **10 daraus abgeleitete
  Regeln** (je mit `derived_from`-Beleg): signal_flow_ltr, power_rails,
  series_horizontal_shunt_vertical, ic_in_signal_direction, orthogonal_on_grid,
  generous_spacing, power_symbols_and_io_labels, ref_value_stacked,
  junctions_at_tees, separate_supply_blocks. Die alten (erfundenen) Keys sind
  raus.
- **`LayoutRule`** bekommt `derived_from` (Beleg) und `enforcer`
  (`spacing`|`grid_snap`|""); `validate()` erzwingt genau 10 Regeln + Beleg.
- **`place._enforce_layout_rules`** ist jetzt über das `enforcer`-Feld getrieben
  (nicht mehr Key-Mapping): `generous_spacing`→`spacing` (force_no_overlap +
  min_wire, GEOMETRY-Fixpunkt), `orthogonal_on_grid`→`grid_snap` (FINISH). Reihen-
  folge im `_spacing`: erst Überlappung, dann Draht (Konvergenz).
- **Verhalten unverändert:** alle 10 Demo-Schaltpläne weiter 0 Überlappungen UND
  0 Verbindungen <5 mm. Die 3 Struktur-Regeln (power_rails,
  series_horizontal_shunt_vertical, separate_supply_blocks) sind PLANNED — noch
  nicht umgesetzt. `tests/test_layout_rules.py` neu (exakt-10-Check, Beleg-Pflicht,
  Enforcer-Feld). Selftest 10/10, pylint 10/10. Version 0.12.5 → 0.13.0.

### Changed (Regeln listen-getrieben in den Generator eingebaut — Plugin 0.12.5)
- **Nutzer-Vorgabe:** „die Regeln in die Generatoren einbauen, aber möglichst
  weiterhin mit einer Liste wartbar."
- **`layout_rules`** bekommt eine `phase` je Regel (PLACEMENT | GEOMETRY |
  FINISH) + `by_phase()`. GEOMETRY = no_overlap, min_wire, wire_along_pin_exit;
  FINISH = grid_snap; der Rest ist intrinsisch im Platzierer/Router (PLACEMENT).
- **`place._enforce_layout_rules`** (neu): der Motor, der das Regel-Set abfährt
  statt fest verdrahteter Schritte — GEOMETRY-Regeln in einer Fixpunkt-Schleife
  (Überlappung ↔ Mindest-Draht wechselwirken, bis stabil), FINISH einmal. Eine
  neue geometrische Regel = Eintrag im Set (phase=GEOMETRY) + Enforcer
  registrieren, keine Pipeline-Chirurgie in `place_schematic`.
- **`common.geometry.force_no_overlap`** gibt jetzt `bool` (moved) zurück, damit
  es in die Fixpunkt-Schleife passt.
- **`tests/test_layout_rules.py`:** +Tests (Phasen-Zuordnung, by_phase-Partition,
  Engine deckt GEOMETRY-Regeln ab). Verhalten unverändert: alle 10
  Demo-Schaltpläne 0 Überlappungen UND 0 Verbindungen <5 mm; Selftest 10/10,
  pylint 10/10. Version 0.12.4 → 0.12.5.

### Changed (Layout-Regel-Set kuratiert — Nutzer-Vorgabe)
- **Regel 1 „tight_cluster" gelöscht**, dafür neu **`pin_swap_passives`**: bei
  Kondensatoren, Spulen und Widerständen (unpolare 2-Pin-Bauteile) dürfen Pin 1
  und Pin 2 vertauscht werden, um kürzere/kreuzungsärmere Leitungen zu bekommen
  (realisiert über die 180°-Drehung in `defrag._best_rotation`; polarisierte
  Teile — Elkos, Dioden/LEDs — ausgenommen).
- **Regel 10 `astar_route` ersetzt durch `no_wire_through_parts`**: „Drähte gehen
  niemals durch Bauteile" — der A*-Router führt jeden Draht um die Rahmen herum;
  ohne freien Weg wird ein Label gesetzt statt eines Durchstich-Drahts.
- `tests/test_layout_rules.py` mitgezogen (Kern-Regel-Liste, Negativ-Check für
  die entfernten Keys). Reine Regel-Set-Pflege (Text/Registry), keine
  Enforcement-Änderung → kein Version-Bump.

### Added (Layout-Regel-Set + Leitung folgt Pin-Austrittsrichtung, Plugin 0.12.4)
- **Nutzer-Vorgabe:** „Liste alle Regeln — diese müssen als eigenes wartbares
  Set in die Generatoren" + „die 5 mm schließen sich an die Richtung an, in der
  die Anschlüsse aus dem Bauteilkörper kommen".
- **`generators/schematic/layout_rules.py`** (neu, pure/stdlib): das zentrale,
  wartbare Regel-Set als Single Source — 11 `LayoutRule`-Einträge (Aussage +
  Begründung + `enforced_in`-Verweis + Ausnahmen + Status): tight_cluster,
  smart_rotation, no_labels, connectors_outermost, gnd_down_vcc_up, no_overlap,
  min_wire, wire_along_pin_exit, ref_value_right, astar_route, grid_snap. Löst
  die verstreuten Regel-Kommentare (route „Rules applied", builder „Rule R12")
  in ein review-bares Set ab (Durchsetzung bleibt vorerst in den genannten
  Funktionen; späterer Refactor kann gegen die Keys zentralisieren).
- **`place._enforce_min_wire`:** schiebt das Blatt jetzt ENTGEGEN seiner
  Pin-Austrittsrichtung (`_RETREAT` via `route._stub_direction`), damit die
  5-mm-Leitung geradlinig aus dem Pin läuft; Fallback trennt zusätzlich direkt,
  falls der Partner nicht auf der Achse liegt (Garantie ≥5 mm hat Vorrang).
- **`tests/test_layout_rules.py`** (neu, 16 Tests): Set wohlgeformt, Kern-Regeln
  vorhanden, `enforced_in`-Funktionen existieren wirklich. Weiter alle 10
  Demo-Schaltpläne 0 Überlappungen UND 0 Verbindungen <5 mm. pylint 10/10.
  Version 0.12.3 → 0.12.4.

### Added (Schaltplan: Mindest-Leitungslänge — nie Pin-an-Pin, Plugin 0.12.3)
- **Nutzer-Anforderung:** „Für alle Bauteile gilt min 5 mm Leitung, niemals
  direkt verbinden."
- **`place._enforce_min_wire()`** (neu): schiebt direkt verdrahtete Signal-Pins
  VERSCHIEDENER Bauteile auf ≥ `MIN_WIRE_MM` (5.08 mm = 2 Grid) auseinander —
  das „Blatt" (weniger Verbindungen) weicht entlang der Pin-zu-Pin-Achse. Nur
  Signal-Netze (Power-Pins gehen über GND/VCC-Symbole, kein direkter Draht),
  nur verschiedene Bauteile (zwei Pins desselben ICs sind durch die
  Symbol-Geometrie fixiert). In `place.py` nach der Überlappungs-Garantie in
  ein paar Runden mit `force_no_overlap` verzahnt, bis BEIDES zugleich hält.
- **Verifiziert:** alle 10 Demo-Schaltpläne gleichzeitig 0 Überlappungen UND
  0 Signal-Verbindungen < 5 mm (vorher 3 zu kurze). Wirkt auf ALLE erzeugten
  Schaltpläne. Selftest 10/10, 18 Generator-Tests grün, pylint 10/10.
  Version 0.12.2 → 0.12.3.

### Fixed (Schaltplan: Bauteile überlappen nie mehr — Plugin 0.12.2)
- **Nutzer-Anforderung:** „Bauteile dürfen niemals übereinander liegen."
- **Root-Cause 1 — Rotation:** `_resolve_overlaps` (common/geometry.py) nahm für
  gedrehte Bauteile die UNgedrehten Maße → übersah gedrehte Kollisionen (ließ
  kleine Rs im Rahmen eines gedrehten Nachbarn). Jetzt rotations-bewusst
  (`_half_extents` tauscht w/h bei 90/270°), Trennung entlang geringster
  Durchdringung mit `_OVERLAP_MARGIN`=2 mm (> HALF_GRID, snap-stabil).
- **Root-Cause 2 — Oszillation bei Riesensymbolen:** ein volles LQFP-48-Symbol
  ist ~81 mm hoch; der sanfte Schieber drückte kleine Teile raus→in Nachbarn→
  zurück, konvergierte nie (auch 60 Durchgänge nicht). Neu:
  `geometry.force_no_overlap()` als harte GARANTIE — größte Symbole zuerst als
  Anker, jedes weitere Teil sucht ringweise nach außen die nächste freie Zelle
  (Grid-Schritte, terminiert immer). In `place.py` nach dem sanften Schritt
  aufgerufen.
- **Verifiziert:** alle 10 Demo-Schaltpläne mit 0 Überlappungen (vorher 3, davon
  2 hartnäckig am MCU). Wirkt auf ALLE erzeugten Schaltpläne. Version 0.12.1 →
  0.12.2, pylint 10/10.

### Fixed (Schaltplan-Lesbarkeit — Platzierung, GND/VCC-Konvention, Plugin 0.12.1)
- **Besserer Platzierer greift jetzt.** `constraint_solver.solve_placement`
  fiel ohne OR-Tools auf einen schwachen Greedy-„Simple solver" zurück, der die
  Überlappungen nicht auflöste und den purpose-built `defrag`-Platzierer
  (`incremental_place_and_score`: Bauteile eng ums IC, gedreht für kürzeste
  Drähte) VERDRÄNGTE. Fix: `solve_placement(..., allow_greedy=False)` ist Default
  — ohne echten Solver liefert es `None`, `place.py` nutzt defrag. Ergebnis:
  eng geclusterte, gedrehte Bauteile mit kurzen echten Leitungen (keine
  Netz-Label-Wüste für kleine Schaltungen).
- **GND immer unten, Versorgung immer oben.** `route._place_power_symbol` drehte
  das Power-Symbol nach der Pin-/Stub-Richtung mit — GND zeigte mal seitwärts.
  Jetzt hart erzwungen: `sym_type=="ground"` → Stub nach unten (Symbol darunter,
  zeigt nach unten), `"supply"` → nach oben. KiCad-Standard-Konvention.
- **Demo-Specs ohne `hint_sch`.** Die 10 Bausätze setzten ein naives
  Schaltplan-Raster, das den Platzierer übersprang (place.py nimmt Hints und
  überspringt die Optimierung). `hint_sch_*` entfernt (die Board-`hint_pcb_*`
  bleiben) → der defrag-Platzierer läuft. Ein-/Ausgangs-Stecker sitzen außen,
  Leitung nach innen (Signalfluss links→rechts) — bewusst so, das ist die
  Konvention. Wirkt auf ALLE Schaltpläne, nicht nur Demos. Selftest 10/10,
  30 Generator-Tests grün, keine Regression; Version 0.12.0 → 0.12.1.

### TODO / Bekannte Grenze (Demo-Bausätze überarbeiten — Nutzer-Feedback 2026-07-04)
- **Alle 10 Schaltpläne UND Platinen müssen überarbeitet werden.** Sie
  validieren und bauen gegen echtes KiCad, sind als Schaustück aber noch nicht
  vorzeigbar: Schaltplan-Label-Überlappung (Ref+Value übereinander),
  unaufgeräumte Verdrahtung, Streu-Labels; Platinen nur geclusterte
  Startplatzierung ohne Routing. Vermerk + mögliche Richtungen (Skills laufen
  lassen / Generator verbessern / kuratierte Vor-Layouts) in
  `docs/demo_kits_todo.md`, Pointer im `plugin/demo_kits.py`-Docstring.

### Added (10 Demo-Schaltungen aus freien Referenz-Topologien — live baubar, Plugin 0.12.0)
- **Nutzer-Wunsch:** „freie KiCad-Schaltpläne im Internet suchen und diese
  möglichst minimal integrieren." Umsetzung: die 10 Bausätze sind an
  **publizierten, frei verfügbaren Referenz-Designs** orientiert (Datenblatt-
  Typical-Applications + Open-Hardware) und als minimale Generator-Specs
  (parts+pins+nets, keine fremden Dateien vendored → lizenzsauber + passt zur
  Pipeline) neu geschrieben.
- **`kicad_mcp/resources/data/demo_kits/*.json`** (10 neu): audio_amp (TI
  LM386), usb_sensor_hub (STM32 'Blue Pill'), ac_dc_supply (Flyback, TI/PI-
  App-Notes), led_ring (WS2812, rundes Board), motor_driver (TI/Adafruit
  DRV8871), buck_converter (MPS MP1584), ethernet_device (Microchip EVB8720 /
  LAN8720), sketch_to_copper (AMS1117), production_ready (74HC595 + fast-gleiche
  R/C), kit_seeding (TI NE555 Astabil). Jede Quelle ist in der Spec-
  `description` genannt. Alle Footprints in KiCad-10-Standard-Libs verifiziert;
  je Spec 6–14 Bauteile; board-gefüllte Startplatzierung (Ring bei rundem
  Board). Ende-zu-Ende gegen echtes KiCad geprüft (validiert + baut Schaltplan
  + Board mit ECHTEN Footprints, gerendert kontrolliert).
- **`kicad_mcp/demo.py`:** `run_demo`/`main` nehmen jetzt einen Bausatz
  (`--kit <key>`, `kit_spec_path`); der Berechnungs-Schritt entfällt neutral
  bei Bausätzen ohne LED-Zweig (`_spec_has_led`), Schritt-2-Text ist
  kit-agnostisch.
- **`plugin/chat_dialog.py`:** „▶ Diese Demo starten" baut jetzt sichtbar
  Schaltplan + Platine (Subprozess mit `--kit`, per-Kit-Ausgabeordner) und zeigt
  danach die Skill-Folge (`_write_demo_plan`); fehlt eine Spec, nur die
  Vorschau. `demo_runner.spec_exists` steuert Label/Verhalten.
- **`tests/test_demo_kits.py`:** +Tests — jede Spec existiert, validiert, ist
  minimal, und baut (skipif ohne KiCad-Libs) Schaltplan + Board mit echten
  Footprints. Version 0.11.0 → 0.12.0, Bundle-Sync, pylint 10/10. Keine neuen
  MCP-Tools (Tool-Count 189 unverändert).

### Added (Demo-Auswahlmenü mit Hover-Vorschau der Super-Skills — Abschnitte)
- **Nutzer-Wunsch:** die Demo soll über ein Menü wählbar sein, und schon beim
  Aufklappen/Hover sieht man, welche Super-Skills beteiligt sind und was
  passiert; den Demo-Button in Abschnitte unterteilen.
- **`plugin/chat_dialog.py`:** aus dem einen „▶ Demo"-Knopf wird „▶ Demo ▾" mit
  Aufklapp-Menü (`_build_demo_menu`): nach `demo_kits.SECTIONS` gruppiert
  (Analog & Simulation, Digital & Schnittstellen, Leistung & Norm,
  Spezial-Layout, Fertigung & Methode), je Bausatz ein **Untermenü**, dessen
  Einträge die Skill-Folge zeigen (`1. 🧬 SPICE-Modelle — <warum hier>`, …) —
  so sieht man beim Hover eines Bausatzes seine beteiligten Super-Skills und die
  Begründung. Ein „▶ starten"-Eintrag löst die Demo aus; ganz unten die bisherige
  Schnell-Demo (Testboard, `_run_quick_demo`).
- **`_run_demo_kit`:** solange die Schaltplan-Spec eines Bausatzes noch nicht
  gebaut ist, zeigt der Klick den Ablauf transparent in Abschnitten (Schaltplan
  anlegen → Skill für Skill mit Begründung) als Vorschau; mit vorhandener Spec
  läuft daraus die echte Demo (Ausführung folgt mit den Specs).
- **`plugin/demo_kits.py`:** `section`-Feld je Bausatz + `SECTIONS`-Tabelle,
  `by_section()`, sowie pure Anzeige-Helfer `pipeline_items()` (Skill-Label +
  Begründung, Label aus `superfeatures`) und `hover_preview()` (Zweck + Skill-
  Kette mit Zählwert). `validate()` prüft jetzt auch die Abschnitts-Zuordnung.
- **`tests/test_demo_kits.py`:** +22 Tests (jetzt 53) — Abschnitte partitionieren
  alle Bausätze, `pipeline_items`/`hover_preview` zeigen jede beteiligte Skill.
  pylint 10/10. Weiterhin keine neuen MCP-Tools (reine Plugin-Ebene).

### Added (Demo-Bausatz-Registry + Runner-Gerüst — 10 Schaustück-Schaltungen, alle 34 Super-Skills)
- **Nutzer-Wunsch:** die Demo soll zeigen, was die ~34 Super-Skills können —
  ~10 Demo-Schaltungen, je Bausatz wird ein hinterlegter Schaltplan angelegt und
  dann mit ~5 Skills der Reihe nach transparent das Board entworfen; dieselben
  Schaltpläne dienen als Bausatzsystem für neue Projekte.
- **Scope dieses Schritts (bewusst, ohne Schaltpläne):** erst die Registry + das
  Runner-Gerüst als Code zum Review; die `.kicad_sch`-Specs folgen separat.
- **`plugin/demo_kits.py`** (neu, pure/stdlib): Single Source der Zuordnung
  Projekt→Skills. 10 `DemoKit`-Einträge (Audioverstärker, USB-C Sensor-Hub,
  AC-DC-Netzteil, LED-Ring, Motor-Treiber, Buck-Wandler, Ethernet-Gerät,
  Skizze→Kupfer, Serienreife&Kosten, Datenblatt&Foto→Schaltung), je mit
  geordneter `pipeline` (Super-Skill-Keys als echter Design-Ablauf) und
  `rationale` (warum jeder Skill genau hier hilft). `validate()`/
  `covered_skills()`/`uncovered_skills()` als Integritäts- und Vollständigkeits-
  Prüfung.
- **`plugin/demo_runner.py`** (neu, rein/planend): `plan(kit_key)` löst einen
  Bausatz zu geordneten `DemoStep`s auf — Schritt 0 „Schaltplan anlegen", dann
  je Skill der kanonische Prompt aus `superfeatures.py` + die Hier-Begründung.
  `spec_path`/`spec_exists` zeigen den erwarteten Ort der noch fehlenden Specs;
  der Build-Schritt markiert ehrlich „noch nicht gebaut". Ausführung/GUI-Dropdown
  hängen sich später an `plan()`.
- **`tests/test_demo_kits.py`** (neu, 31 Tests): Registry wohlgeformt, je Bausatz
  4–6 Skills, rationale deckt die Pipeline exakt, und DER Wächter —
  `test_all_34_super_skills_are_covered` — stellt sicher, dass jeder Super-Skill
  in mindestens einem Bausatz vorkommt. pylint 10/10. Keine neuen MCP-Tools
  (reine Plugin-Ebene), daher keine Version-/Tool-Count-Änderung.

### Added (Schaltung als Vorlage — „du zeichnest, der MCP merkt sich und baut", Plugin 0.11.0)
- **Nutzer-Wunsch:** „nein also ich zeichne eins vor die speicherst ins mcp und
  dann magic". KiCad 10 hat keine Schaltplan-Schreib-API (empirisch verifiziert:
  leerer IPC-Befehlssatz), also zeichnet der Nutzer den Block selbst, der MCP
  liest ihn ein, merkt ihn sich und baut daraus auf Wunsch ein Board.
- **3 neue Tools** (Tool-Count 186 → 189): `save_circuit_template`,
  `list_circuit_templates`, `build_circuit_template` in
  `kicad_mcp/tools/circuit_template_tools.py`.
  - `save_circuit_template(schematic_path, name, description)`: liest den
    gezeichneten `.kicad_sch` via `kicad-cli sch export netlist` +
    `_parse_netlist_to_spec` (korrekt für Busse/Power/hierarchisch) und legt
    Bauteile+Netze als benannte Vorlage ab.
  - `list_circuit_templates()`: alle gemerkten Vorlagen (Name, Slug,
    Beschreibung, Bauteil-/Netz-Zahl).
  - `build_circuit_template(name, output_dir, project_name)`: baut Schaltplan
    **und** Platine über die bestehende Pipeline (`expand_netlist` →
    `build_schematic`/`build_pcb`), Pins/Footprints auto-aufgelöst.
- **`kicad_mcp/utils/circuit_templates.py`** (neu, rein/stdlib): persistenter
  Vorlagen-Speicher (`~/.local/state/kicad-claude/circuit_templates` bzw.
  `%LOCALAPPDATA%`, override `KICAD_MCP_TEMPLATE_DIR`); pfadsicherer Slug,
  Save/Load-Round-Trip, `to_compact` (Vorlagen-Netze `R1.1` →
  Generator-Form `R1:1`).
- **Ende-zu-Ende gegen echtes KiCad geprüft:** gezeichnet → Vorlage (7 Bauteile
  / 4 Netze) → gebautes Board (11 Footprints / 27 Symbole). Tests in
  `tests/test_circuit_templates.py` (Store rein + Tool-Weg self-skip ohne
  kicad-cli). `EXPECTED_TOOL_COUNT` 189, `__tool_count__` 189, Bundle-Sync,
  CLAUDE.md „189 Tools"; Version 0.10.3 → 0.11.0.

### Fixed (Demo-Board wirkte „leer" — überlappendes Auto-Layout, Plugin 0.10.3)
- **Feld-Report:** „Demo startet, aber nichts im Schaltplan, Platine leer." Die
  Dateien waren NICHT leer (7 Footprints, 31 Symbole, geroutete Tracks —
  per kicad-cli-Render verifiziert), aber das Auto-Layout stapelte mehrere
  Footprints auf denselben Punkt (U1/C1/D1 bei 153.5,105) → winziger Klumpen
  in großem Blatt, las sich als leer.
- **`generators/pcb/place.py`:** neue „Phase 0" — Teile mit `hint_pcb_x`/
  `hint_pcb_y`/`hint_pcb_rot` werden EXAKT platziert (kein Auto-Layout, keine
  Kollisions-Verschiebung), analog zum Schaltplan-`hint_sch_*`. Eine bewusst
  gestaltete Vorlage bleibt so unangetastet.
- **`resources/data/selftest_board.json`:** Demo-Board sauber vorplatziert
  (J1→C1→U1→C2→R1→D1 Links-nach-rechts, TP1 oben), `hint_pcb_*` + `hint_sch_*`
  je Bauteil. Schaltplan & Platine gerendert geprüft. Spec bleibt
  validator-konform. Bundle-Sync; Version 0.10.2 → 0.10.3.

### Fixed + Changed (Feld-Feedback zu Design A, Plugin 0.10.2)
- **Demo-Knopf: `ModuleNotFoundError: kicad_mcp`** — das Plugin-GUI-Python
  hat das Server-Paket nicht auf `sys.path`, der In-Process-Import
  (`from kicad_mcp import demo`) flog. Fix: `kicad_mcp/demo.py` bekommt ein
  `main()`, `mcp_config.demo_bootstrap_code`, und `_demo_worker` startet den
  Ablauf als Subprozess mit sys.path-Bootstrap (wie der Systemtest),
  streamt stdout ins Transkript, liest die `BOARD\t<pfad>`-Zeile.
- **Ein Button pro Feature** statt Kategorie-Dropdown (`_build_superfeature_bar`
  + `_add_feature_button`): 34 Einzelknöpfe, nach Kategorie gruppiert
  (farbiger Gruppen-Titel), Hover (`EVT_ENTER_WINDOW`) → Beschreibung in der
  Statuszeile; `_popup_feature_menu` entfernt.
- **Kontrast erhöht:** `chat_theme` FOREGROUND `#15181D`, DIM `#49505A`,
  SURFACE `#E3E8ED` (Knöpfe klarer abgesetzt), Kategoriefarben kräftig
  abgedunkelt, LINK `#1F5FA8`.
- **ngspice-Ampel entfernt:** Fußzeile nur noch MCP + IPC; `_probe_ngspice_light`
  weg, `_set_light` vereinfacht (SPICE-Status bleibt in der Diagnose).
  Version 0.10.1 → 0.10.2.

### Changed (Design A „Werkbank" — helles natives KiCad-Theme, Plugin 0.10.1)
- **`chat_theme.py` Palette auf hell-nativ** (Design A gewählt): BACKGROUND
  `#FAFBFC`, FOREGROUND `#20242A`, SURFACE `#EDF0F3`; neuer Token `LINK`
  (`#2F6FB0`, KiCad-Blau) für klickbare Board-Links; `CLAUDE_ORANGE` bleibt
  als warmer Marken-Akzent (Chevron/Spinner/Überschriften), auf Hell
  abgedunkelt; Kategoriefarben für hellen Grund kontrastiert. Token-Namen
  stabil → die 66 Panel-Referenzen folgen automatisch.
- `chat_dialog._write_link` nutzt `theme.LINK` (Board-Links jetzt blau, nicht
  orange). Theme-Tests auf Hell umgestellt (`test_light_native_background`,
  `test_link_is_a_distinct_blue`). Auf Linux via gui-smoke verifiziert.
  Version 0.10.0 → 0.10.1.

### Added (Wählbares Agenten-Backend — MCP-fähige CLIs, Plugin 0.10.0)
- **`plugin/backends.py` (neu):** Backend-Abstraktion für MCP-sprechende
  Agenten-CLIs. `Backend`-Vertrag kapselt die Unterschiede: `find()`,
  `build_command()`, `write_mcp_config()`, `config_path()`, `normalize(line)`
  (Stream → normalisiertes Ereignis-Dict). Registry `get()/available()`,
  `DEFAULT_KEY="claude_code"`.
  - `ClaudeCodeBackend`: delegiert an die bestehenden, getesteten
    claude_bridge/mcp_config-Funktionen — der Claude-Pfad ist bit-identisch
    (`config_path` == base).
  - `CodexBackend` (**experimentell**): `codex exec --json`, kicad-mcp als
    `[mcp_servers.kicad-mcp]` in einer `.codex.toml` (Codex hat kein
    `--mcp-config`), JSONL-Normalisierung nach dokumentiertem Stand;
    defensiv (fremdes Schema killt den Zug nicht). Im Feld ungetestet.
- **`claude_bridge`:** `_run_turn` ist backend-parametrisch (über
  `normalize`), Claude-Verhalten unverändert (66 Bridge-Tests grün);
  `ask(backend=…)` löst das Backend aus den Einstellungen auf, nutzt
  `find`/`build_command`/`config_path`/`normalize`; Nicht-Claude-Backends
  schreiben ihre Config über `_prepare_backend_config` (stdio).
- **`settings.py`:** neuer Key `backend` (Default `claude_code`).
  **Einstellungs-Dialog:** „KI-Backend"-Auswahl als erstes Feld.
- Tests `tests/test_plugin_backends.py` (13: Registry, Claude-Delegation,
  Codex-Command/-TOML/-Normalisierung, Bridge nutzt gewähltes Backend).
  Version 0.9.2 → 0.10.0.

### Added (Demo-Knopf: Idee→Schaltplan→Berechnung→Platine, Plugin 0.9.2)
- **`kicad_mcp/demo.py` (neu):** deterministischer, LLM-freier Showcase aus
  `selftest_board.json` — `run_demo(out_dir, on_step)` fährt vier narrierte
  Schritte: Idee (Spec), Schaltplan+PCB (`generate_project` in-process),
  Berechnung (LED-Vorwiderstand aus echten Spec-Werten:
  I = (V_rail − V_f)/R, gegen 1–20 mA bewertet), Platine (Pfad). Wirft nie
  (Schritt-Fehler → `ok:False`, Ablauf lebt weiter). `_parse_ohms`/
  `_led_resistor_check` rein & getestet.
- **Plugin:** „▶ Demo"-Knopf in der Feature-Leiste (`_on_demo`/
  `_demo_worker`): streamt die Schritte ins Transkript, nennt am Ende den
  Board-Pfad (KiCad-10-IPC kann kein Dokument öffnen). Kein Modell-Kontingent.
  Tests `tests/test_demo.py` (7: Ohm-Parser, LED-Rechnung, voller Flow gegen
  echtes KiCad, Fehler-Isolation). Bundle-Sync; Version 0.9.1 → 0.9.2.

### Fixed + Added (Erster Linux-GUI-Test: Dialoge unter Xvfb, 2 reale Bugs)
- **`scripts/gui_smoke.py` (neu):** rendert jeden wxPython-Dialog
  (Chat-Panel, Markdown+Links+Chips-Antwort, Einrichtung, Settings) unter
  Xvfb mit KiCads python3.12+wx, macht je einen Screenshot, fängt Fehler
  pro Dialog. Die GUI lief nie außerhalb von Windows-KiCad — der erste
  Linux-Lauf fand prompt zwei echte Bugs:
  1. **Einrichtungs-Knopfleiste lief rechts aus dem Fenster** (Systemtest/
     Chat-starten abgeschnitten): 7 Knöpfe passten bei den breiteren
     GTK-Button-Metriken nicht in die fixe 560-px-Breite. Fix: `BoxSizer`
     → `WrapSizer` (bricht sauber um) + `Fit()`/`SetMinSize`.
  2. **Deutsche i18n-Inseln im EN-Modus:** Banner-Summary (Platine/
     Footprints/Netze/Größe), Interaktions-Guide, „Bereit.", „↶ Rückgängig",
     Empfehl-Zeile, Kopfzeile („verbunden mit") und vier Setup-Knöpfe waren
     hartkodiert Deutsch. Alle über `tr()` + EN-Katalog geführt; der
     Interaktions-Guide bekam eine EN-Fassung (Sprachwahl via `i18n.get_lang`).
  Tests: `test_plugin_banner.py` sprach-explizit (autouse-Fixture DE) +
  neuer EN-Übersetzungs-Test. **CI-Job `gui-smoke`** rendert die Dialoge
  bei jedem Push, Screenshots als Artefakt.

### Added (Dev/CI: Live-IPC gegen echten laufenden Editor — die „Mitarbeiter"-Schicht)
- **`tests/live_ipc_harness.py` (neu):** `LiveEditor`-Kontext startet einen
  ECHTEN pcbnew unter Xvfb, klickt den bei jedem pcbnew-Standalone-Start neu
  erscheinenden „Welcome to KiCad"-Erststart-Dialog per xdotool weg (Escape →
  „Yes") und wartet, bis die kipy-IPC (`/tmp/kicad/api.sock`) antwortet;
  sauberes Teardown. `tools_present()` gate (Xvfb/pcbnew/xdotool).
- **`tests/test_live_ipc.py` (neu):** fährt den REALEN Produktpfad
  (`server.call_tool("ipc_*")`) gegen den laufenden Editor —
  `ipc_check_status` (board_open/ready), `ipc_get_open_documents`, und der
  Kern: **Selektions-Cross-Probe-Roundtrip** (U1 via kipy im Editor
  markieren → `ipc_get_selection` liest Referenz/Typ zurück → Deselektion →
  count 0). Das ist „was ist das?"/„Auswahl einbeziehen" end-to-end gegen
  echtes KiCad — bisher nur auf der Nutzer-Maschine prüfbar. Opt-in +
  selbst-skippend (`KICAD_MCP_LIVE_IPC=1` + Tools da).
- **`setup_container_kicad.sh`:** installiert zusätzlich xvfb/xdotool/x11-utils
  und seedt `kicad_common.json` (IPC-API-Server an). **CI-Job `live-ipc`**
  (ubuntu-latest) fährt die Live-Tests bei jedem Push.

### Added (Dev/CI: echtes KiCad 10 im Container und in Actions)
- **`scripts/setup_container_kicad.sh` (neu):** installiert KiCad 10 aus dem
  offiziellen PPA in einen Ubuntu-24.04-Container/CI-Runner (Key über die
  Launchpad-API, ohne die ~5-GB-3D-Modelle) und brückt pcbnew ins
  Projekt-venv (dediziertes `/opt/pcbnew-bridge` + `.pth` — nur
  `pcbnew.py`/`_pcbnew.so`, damit dist-packages keine venv-Pakete
  beschattet). Ergebnis im Dev-Container: Selftest **10/10 PASS, 0 SKIP**
  (Connectivity via pcbnew 10.0.4, DRC via kicad-cli, Symbol-Auflösung
  gegen echte Bibliotheken), Peak-RAM 275 MB.
- **CI-Job `tests-kicad`:** ubuntu-latest + dasselbe Skript → Selftest und
  die volle pytest-Suite laufen gegen echtes KiCad; die ~194 bisher
  geskippten pcbnew-Tests und die kicad-cli-Pfade (ERC/DRC) werden damit
  dauerhaft ECHT getestet. Selftest-Report als Build-Artefakt.

### Fixed (RAM-Verhalten + Mess-Transparenz, Plugin 0.9.1)
- **Fünfter Feld-Report („Systemtest braucht auf einmal viel RAM?"):**
  - `symbol_cache._read_lib_file`: `lru_cache` 64 → 8. Der Cache hält
    KOMPLETTE `.kicad_sym`-Texte (Stock-Libs bis ~40 MB/Datei) — 64
    Einträge akkumulierten in einem langlebigen Warm-Server potenziell
    Gigabytes. 8 deckt die Handvoll Libs eines Generats (Device,
    Connector, …) weiterhin ab.
  - `selftest`: `peak_ram_mb()` (Windows: PeakWorkingSet via psapi;
    POSIX: `ru_maxrss`) in Report-Meta + MD-Kopf („Peak-RAM: … MB
    (transient — endet mit dem Prozess)"); der Connectivity-Worker
    stirbt am stdin-EOF mit dem Testlauf, nichts bleibt resident.
  - `selftest.main`: filtert die „coroutine … was never awaited"-
    RuntimeWarnings (bekanntes sync-ctx.info-Rauschen der Tools) aus dem
    Fenster-Output — Fehler laufen über Schritt-Verdikte, nicht Warnings.
  - `diagnose`: hartkodiertes „tools/list (167)" → `__tool_count__` (186).
  Version 0.9.0 → 0.9.1.

### Added (Standalone-Systemtest ohne Claude, Plugin 0.9.0)
- **`kicad_mcp/selftest.py` (neu):** `python -m kicad_mcp.selftest` —
  orchestrierbarer Feldtest der Produkt-Maschinerie OHNE Claude. Erzeugt
  ein Demo-Projekt aus der gebündelten Spec
  (`resources/data/selftest_board.json`, LDO+LED, validator-geprüft) und
  fährt 10 Schritte durch die ECHTEN MCP-Tools in-process
  (`create_server` + `call_tool`): Registry-Zählung, `generate_project`
  (spec→sch→pcb), `list_schematic_components`,
  `extract_schematic_netlist`, `list_pcb_footprints`,
  `compute_pad_world_positions`, `add_via_to_pcb`, `check_connectivity`
  (SKIP ohne pcbnew), `run_drc_check` (SKIP ohne kicad-cli), stdio-
  Handshake (Transport gepinnt). Interaktion nur bei Fehlern: grün = eine
  Zeile + Exit 0, rot = FAIL-Zeilen + Exit 1; Report
  `selftest_report.{md,json}` im `--out`-Ordner. Schritte injectable
  (`run_all(steps=…)`), ein roter Schritt killt nie den Lauf.
- **Plugin:** „🔬 Systemtest"-Knopf im Einrichtungs-Fenster (Stream-Fenster,
  läuft unter KiCads Python via `mcp_config.selftest_bootstrap_code` —
  exakt derselbe sys.path-Bootstrap wie der Server, Guard-Test).
- Tests: `tests/test_selftest.py` (9: Spec-Validierung, grüne Pipeline,
  Fehler-Isolation, Exit-Codes, echter Handshake) + Bootstrap-Spiegel-Test.
  Version 0.8.7 → 0.9.0.

### Added (GUI: Markdown-Rendering, Gruppenfarben, klickbare Ampeln, Plugin 0.8.7)
- **`plugin/chat_markdown.py` (neu, pure logic):** zerlegt eine Antwort in
  ``(text, stil)``-Segmente (bold/heading/bullet/code/codeblock/rule;
  bewusst KEIN kursiv — ``_``/``*`` stecken in Netznamen wie ``GND_3V3``).
  Link-Invariante: nur Marker werden entfernt, nie Wortlaut —
  ``_append_claude`` schickt jedes Segment weiterhin durch
  ``board_links.tokenize``, Links funktionieren auch in fetten Passagen
  und Überschriften; nur Codeblöcke bleiben roh (copy-treu, 📋-Chips).
  Styles zentral in ``chat_theme.MARKDOWN_STYLES`` (+ ``CODE_FG``,
  ``OK_GREEN``); ``_write`` kann jetzt Hintergrundfarbe (Code auf SURFACE).
- **Super-Feature-Leiste mit Gruppenfarben:** ``chat_theme.CATEGORY_COLORS``
  (Key-gekoppelt an ``superfeatures.CATEGORIES``, Guard-Test) färbt die
  sechs Gruppen-Buttons (blau/violett/gelb/grün/rosa/türkis).
- **Ampeln einzeln, größer, klickbar:** drei StaticTexts (MCP/IPC/ngspice)
  mit je eigenem Tooltip + Handcursor, Farbe pro Zustand
  (grün/rot/grau), Klick öffnet die Einrichtung (`_on_light_click`).
  Tests: ``tests/test_plugin_chat_markdown.py`` (13, inkl. Link-Invariante
  und Theme-Kopplungs-Guards). Version 0.8.6 → 0.8.7.

### Fixed (MCP-Status-Fehldiagnose — Init-Event lügt beim Kaltstart, Plugin 0.8.6)
- **Vierter Feld-Report (zweiter E2E-Lauf):** wieder 34/34 FAIL
  `mcp-nicht-verbunden` — aber das Log beweist: die Features riefen
  kicad-mcp-Tools auf (`check_ampacity`, `audit_design`,
  `get_safety_spacing`, …). Der Server WAR verbunden; Claudes frühes
  system/init-Event meldete nur einen nicht-connected-Status (Kaltstart-
  Race), und der wurde nie korrigiert → ⚠-Warnung in jedem Turn, sinnloser
  MCP-Retry (jedes Feature lief doppelt), E2E-Verdikt pauschal FAIL. Fix:
  - `has_kicad_mcp_tool_use()` + Ground-Truth-Override in `_run_turn`:
    ein `mcp__kicad-mcp__*`-Tool-Use beweist die Verbindung → Status
    `connected`, Statuszeile „MCP verbunden — Board-Tools laufen",
    kein Retry, Judge wertet nicht mehr FAIL.
  - `mcp_status_from_init`: bewertet nur noch den `kicad-mcp`-Eintrag
    (fremde Einträge stempeln den Turn nicht) und kennt
    `pending/connecting/starting` als eigenen Zustand (`pending: …`,
    Statuszeile „MCP verbindet noch (Kaltstart) …" statt ⚠, kein Retry).
  Version 0.8.5 → 0.8.6.

### Fixed (http-Modus: 34/34 E2E-FAIL „mcp-nicht-verbunden", Plugin 0.8.5)
- **Dritter Feld-Report (erster echter E2E-Lauf):** jedes Feature FAIL
  `mcp-nicht-verbunden`, stdio-Fallback nie ausgelöst. Zwei blinde Flecken:
  `is_healthy` = nur pid+port (ein Server, der den Port hält, aber MCP nicht
  beantwortet — wedged, fremder Port-Nachnutzer, altes claude ohne
  http-MCP-Support — galt ewig gesund); der Fallback griff nur, wenn
  `ensure_running` selbst scheiterte. Fix:
  - `server_manager.ensure_running`: echter MCP-Ping (`probe_http`,
    `PING_TIMEOUT_S=5`) vor jeder Wiederverwendung UND nach jedem Spawn
    (Port offen ≠ MCP antwortet); stummer Server → kill + replace bzw.
    Start-Fehler `beantwortet kein MCP-initialize`. `_probe` injectable.
  - `claude_bridge.ask`: Rettungsleiter — meldet claude auf einem
    http-Versuch „failed: kicad-mcp", schreibt der nächste Versuch die
    Config via `_prepare_transport(force_stdio=True)` hart auf stdio
    (Status: „Warm-Server läuft, aber Claude kommt nicht rein — dieser Zug
    läuft über stdio"). Kein toter Chat mehr, egal woran http scheitert.
  Tests: mute-server-replace, reuse-Ping (URL+Token), Spawn-Ping-Pflicht,
  http-Reject→stdio-Retry, force_stdio ruft nie ensure_running.
  Version 0.8.4 → 0.8.5.

### Fixed (E2E-Button im Feld tot — absoluter Selbst-Import, Plugin 0.8.4)
- **Zweiter Feld-Report:** Klick auf „🧪 E2E-Test" tat nichts — nicht mal
  der Bestätigungsdialog kam. Wurzel: `setup_dialog._run_e2e` zählte die
  Features per `__import__("plugin.superfeatures")`; installiert heißt das
  Paket aber `claude_kicad` (install_plugin.*: PKGNAME) →
  `ModuleNotFoundError` VOR der MessageBox. wx schluckt
  Handler-Exceptions (stderr, in KiCad unsichtbar) → „toter Button". In
  Repo/CI heißt das Paket zufällig `plugin`, daher grün. Fix:
  - `_run_e2e` nutzt den relativen Import (`from . import superfeatures`)
    und `superfeatures.SHIPPED` statt des String-Literals.
  - `SetupDialog._guarded()`: alle Leisten-Buttons (Erneut prüfen, Update,
    Diagnose, Einstellungen, E2E) zeigen Handler-Exceptions jetzt als
    kopierbaren Fehlerdialog statt still zu sterben.
  - Ratchet `tests/test_plugin_imports.py`: verbietet absolute
    Selbst-Importe (`import plugin…`/`__import__("plugin…`/
    `import_module("plugin…`) im gesamten Plugin-Paket.
  Version 0.8.3 → 0.8.4.

### Fixed (http-Env steckte stdio-Pfade an — Errno 10048, Plugin 0.8.3)
- **Erster Feld-Report des 0.8.x-Warm-Modus:** Diagnose-Probe FAIL mit
  `[Errno 10048] bind 127.0.0.1:8331`. Wurzel: `parse_args` liest
  `KICAD_MCP_TRANSPORT` als Fallback — im http-Modus erbten auch Prozesse,
  die stdio sprechen MÜSSEN (Diagnose-Probe, stdio-Fallback-Server der
  Bridge), das http-Env, banden den Default-Port 8331 (belegt/kollidierend)
  und beantworteten nie den stdio-Handshake → „MCP nicht verbunden" trotz
  intaktem Server. Fix — Transport überall explizit gepinnt:
  - `build_mcp_config` env-Block: `KICAD_MCP_TRANSPORT=stdio` (Config-Env
    schlägt vererbtes Env bei Claude-Spawns).
  - `build_probe_cmd`: `--transport stdio` (argv schlägt Env) + Doppel-Pin
    im Probe-Env; Diagnose-Überschrift benennt den stdio-Startpfad.
  - Warm-Server unverändert explizit `--transport streamable-http` auf
    `pick_free_port` — nichts bindet mehr implizit 8331.
  Tests: Probe-Pin (auch unter http-Env), Config-Pin. Version 0.8.2 → 0.8.3.

### Added (E2E-Loop durchs Produkt, Plugin 0.8.2)
- **`plugin/e2e_runner.py` (neu):** iteriert die SHIPPED-Features der
  Registry und schickt jeden Button-Prompt + `[E2E-TESTMODUS]`-Zusatz
  (keine Mutation; Plan + `[[CHOICES]]` am Go-Gate; „Voraussetzung fehlt"
  = korrekt) als echten Turn durch `claude_bridge.ask` — frische Session
  je Feature, `--max-turns 15`, Timeout-Budget. Verdikt-Heuristik
  (PASS: plan+go-gate / fragt-nach / bericht; WARN: keine-tools-benutzt;
  FAIL: bridge/mcp/leer) + `FeatureResult`-Messwerte (Dauer, Tools,
  Choices, Antwort-Auszug). Report als Markdown (FAIL/WARN zuerst — zum
  Zurücklesen durch den Entwicklungs-Agenten) und JSON nach
  `<Projekt>/.kicad-mcp/e2e_report.{md,json}`.
- **🧪-Button im Einrichtungs-Fenster:** Bestätigungs-Dialog (Dauer/
  Kontingent), Stream-Fenster mit Live-Fortschritt je Feature, Report-Pfade
  am Ende. Kern pur + getestet (`test_plugin_e2e_runner.py`: Suffix,
  Judge-Matrix, Exception-Fest, run_all, Report-Sortierung).
  Version 0.8.1 → 0.8.2.

### Added (libngspice-Backend + Menü-Hover, Plugin 0.8.1)
- **`run_spice_sim` Backend 2 — KiCads mitgeliefertes libngspice:** Eeschemas
  Simulator ist GUI-only (keine Eeschema-IPC, kein kicad-cli-sim), aber seine
  Engine liegt als `libngspice-0.dll`/`libngspice.so` jedem KiCad bei.
  `find_libngspice()` (Env `KICAD_MCP_LIBNGSPICE` → KiCad-bin/../lib →
  ctypes.util.find_library) + `run_libngspice()`: ctypes-Runner in einem
  ISOLIERTEN Kindprozess (ControlledExit-Callback gegen ngspices exit();
  Absturz/Timeout kann den Warm-Server nicht reißen), skalare Ergebnis-
  Vektoren via ngSpice_CurPlot/AllVecs/Get_Vec_Info direkt als `values`.
  Backend-Reihenfolge: ngspice-CLI → libngspice → ehrlicher Hinweis;
  `backend`-Feld im Result. Simulation braucht damit auf normalen
  KiCad-Installationen keine Extra-Software.
- **Gruppen-Menü Hover:** EVT_MENU_HIGHLIGHT zeigt den Tooltip des
  gehighlighteten Features live in der Statuszeile — kompakte Gruppen
  bleiben, die Erklärung pro Feature ist ohne Klick sichtbar.
  Version 0.8.0 → 0.8.1.

### Added (GUI 0.8.0 — Mehrsprachigkeit, Gruppen, Chips, Session, Einstellungen)
- **`plugin/i18n.py` (neu):** Auto-Sprachwahl (Einstellung → KiCads
  `kicad_common.json` system.language → Locale → en); Deutsch bleibt
  Quellsprache, `tr()` übersetzt über einen Katalog (fehlende Einträge
  fallen sichtbar auf Deutsch zurück). Antwortsprache des Agenten folgt via
  System-Prompt-Zusatz (`build_command(language=…)`).
- **`plugin/settings.py` (neu):** persistente GUI-Einstellungen
  (`settings.json` im State-Dir) → `apply_env()` setzt
  KICAD_MCP_TRANSPORT/NGSPICE/MAX_TURNS beim Panel-Start; hand-gesetzte Env
  behält Vorrang. Einstellungs-Dialog im Einrichtungs-Fenster
  (Sprache/Transport/ngspice/Max-Schritte).
- **Super-Feature-Leiste gruppiert:** `SuperFeature.category` +
  `CATEGORIES`/`by_category()`; sechs Gruppen-Buttons mit Popup-Menü statt
  34 Einzelbuttons.
- **Chat-Panel:** mehrzeilige Eingabe (Enter=senden, Shift+Enter=Umbruch,
  Paste bleibt vollständig, wächst bis 5 Zeilen); Antwort-Chips aus dem
  neuen `[[CHOICES: …]]`-Marker (Verhaltensregel 9 + `parse_choices`) und
  📋-Copy-Chips je Codeblock (`extract_code_blocks`); Session pro Projekt
  (`.kicad-mcp/chat_session.json`, ↺-Hinweis im Banner, 🆕-Button);
  Ampel-Zeile MCP/IPC/ngspice im Fuß; Freitext-Optionsfeld entfernt
  (⚙-Dropdown pflegt den internen Schalter-Zustand, Reset-Eintrag,
  aktive Schalter sichtbar); Hover-Erklärung für „Auswahl einbeziehen".
- Tests: `test_plugin_i18n_settings.py` (Sprachwahl, Katalog-Vollständigkeit
  für Labels/Gruppen, Settings-Roundtrip/Env-Vorrang, Choices-/Codeblock-
  Parser, Sprachzusatz im Kommando). Version 0.7.8 → 0.8.0.

### Fixed (Links tot + „Kein eindeutiges Board" nach Folge-Abfragen, Plugin 0.7.8)
- **Geister-Editor-Bug:** `_require_editor` las den TRANSIENTEN
  "no handler for GetOpenDocuments" (KiCad-GUI kurz busy bei
  Folge-Abfragen) als „Editor fehlt" und spawnte einen zweiten, detachten
  Editor — zwei Instanzen auf dem IPC-Bus machen jeden `GetOpenDocuments`
  mehrdeutig, alle Cross-Probe-Links sterben mit „Kein eindeutiges Board",
  bis das Schattenfenster gefunden und geschlossen wird. Dreifach-Fix:
  - `_docs_with_transient_retry`: Handler-Fehler werden mit Backoff (3×)
    wiederholt, bevor ihnen geglaubt wird; Bus-down wird weiter sauber
    gemeldet statt in einen Launch zu laufen.
  - `KICAD_MCP_NO_AUTO_OPEN=1`: das Plugin setzt den Schalter für seinen
    Server (stdio-Config-Env UND Warm-Server-Spawn) — im GUI-Betrieb ist
    Auto-Open grundsätzlich falsch; headless/standalone bleibt es erhalten.
  - Selbstheilung: `board_links.connect()` reapt bei der
    Multi-Instanz-Signatur zuerst die im Spawned-Registry verzeichneten
    MCP-Editoren (`claude_bridge.reap_spawned_editors`) und verbindet
    einmal neu — die Links heilen mitten in der Sitzung, statt bis
    KiCad-Neustart tot zu bleiben. Tests: Transient-Retry/Env-Gate
    (`test_ipc_auto_open.py`), Connect-Self-Heal
    (`test_plugin_board_links.py`), Env-Durchreichung (bridge/manager).
    Version 0.7.7 → 0.7.8.
  - **Die eigentliche Lücke in der bestehenden Geister-Abwehr:** die
    Spawned-Registry + Reaper (0.2.20) existierten längst — aber NUR
    `ipc_open_kicad` registrierte seine Spawns; der Auto-Open-Pfad in
    `_require_editor` hielt nicht einmal die PID fest. Diese Geister waren
    für ALLE Reaper unsichtbar. Jetzt registriert auch Auto-Open
    (`spawned_registry.record`), und ein Quell-Ratchet-Test erzwingt, dass
    jede DETACHED-Spawn-Stelle registriert
    (`test_every_detached_editor_spawn_is_registered`).

### Added (Super-Feature „Schutzklassen" — IEC-60664-Normwerte, Plugin 0.7.7)
- **`get_safety_spacing` — geforderte Kriech-/Luftstrecke je Spannungsgrenze**
  (Tool #186, neue Familie `tools/safety_tools.py`). Die IEC-60664-1-Tabellen
  leben als kuratierter, DATIERTER Snapshot in
  `resources/data/safety_spacing_iec60664.json` (F.1 Stoßspannung je
  Nennspannung/OVC, F.2 Luftstrecke Fall A inkl. PD-Minima 0,2/0,8 mm, F.4
  Kriechstrecke je PD/Materialgruppe; Werte gegen publizierte Normauszüge
  quergeprüft, u. a. BS EN 60335-1 Tab. 15/16/18). Regeln: verstärkte
  Isolierung = Kriechweg ×2 + Stoßspannung eine Vorzugsstufe höher; F.7
  (Kriechweg ≥ Luftstrecke); konservativ nächste Tabellenzeile, keine
  Interpolation. Lookup-Kern pur in `utils/safety_spacing.py`.
- **🔌 Schutzklassen-Button (34. Feature):** Schutzklasse I/II/III nach
  IEC 61140 klären → Spannungs-Domänen aus `analyze_pcb_nets` → geforderte
  Abstände je Grenze aus `get_safety_spacing` (Klasse II ⇒ reinforced) →
  Ist-Abstände messen (`center_item_clearance`) → Urteil mit Koordinaten.
  Der ⚡ Sicherheitsabstände-Prompt nutzt jetzt ebenfalls die Snapshot-Werte
  statt Modell-Gedächtnis. Disclaimer in jedem Ergebnis: Richtwerte /
  Vorprüfung, keine Zertifizierung. Tests: `test_safety_spacing.py`
  (Norm-Fixpunkte 230 V→2,5 kV→1,5/2,5 mm, verstärkt 2,0/5,0 mm, PD-Minima,
  SELV-Zeilen, Fehlerpfade). Tool-Count 185→186, Version 0.7.6 → 0.7.7.

### Added (echte SPICE-Ausführung + Entwirren-Geister-Vorschau, Plugin 0.7.6)
- **`run_spice_sim` — ngspice-Batch-Ausführung** (Tool #185, neue Familie
  `tools/sim_tools.py`). Bewusst dumm: führt ein SELBSTSTÄNDIGES SPICE-Deck
  verbatim aus (`ngspice -b`) und liefert Werte/Fehler/Warnungen strukturiert
  zurück — Deck-Bau und Interpretation sind LLM-Arbeit. Discovery:
  `KICAD_MCP_NGSPICE`-Env → PATH → neben `kicad-cli` im KiCad-bin; ohne
  ngspice ein klarer Installationshinweis statt Traceback. Timeout-Schutz,
  Temp-Deck wird aufgeräumt. Tests: `test_sim_tools.py` (Discovery,
  Output-Parser, Fake-ngspice-Läufe headless; echter RC-Teiler skipif ohne
  Binary). Simulations-Prompt nutzt jetzt zuerst `run_spice_sim`,
  analytischer Fallback bleibt. Tool-Count 184→185.
- **🧶 Entwirren: Geister-Vorschau auf dem Skizzen-Layer** — der Plan wird vor
  dem Go als EIN `ipc_draw_markers`-Batch (Kreuz + Ref-Label je Ziel) auf
  MCP.Skizze gezeichnet und nach Umsetzung ODER Ablehnung per
  `ipc_clear_markers` weggeräumt. Plugin-Version 0.7.5 → 0.7.6.

### Added (ALLE Super-Features aktiv — 33/33 Buttons live, Plugin 0.7.5)
- **Die letzten 20 Features auf SHIPPED mit geführten v1-Prompts** — kein neues
  Tool nötig (Grundregel: keine Calculator-Formeln als Selbstzweck — Physik-
  Rechnungen sind LLM-Stärke, die Tools liefern die Fakten): `pin_swap`
  (Swap-Vorschläge, Umsetzung Go-gated via `connect_pins`, Eeschema zu),
  `nl_navigation` (`analyze_pcb_nets`), `select_place` (Plan→Go→EIN Zug via
  `ipc_move_items`), `watch_mode` (Ein-Klick-Review via
  `live_summarize_user_changes`; ehrlich: kein Event-Dauerauge),
  `xtal_caps` (`audit_design` + C=2·(CL−Cstray) offen gerechnet),
  `thermal` (`audit_power_tree`), `operating_temp` (Tj=Ta+P·θJA),
  `slew_rate`, `impedance` (Stackup via `pcb_eval`, IPC-2141-Näherung —
  kein Feldlöser), `dfm_check` (`run_drc_check`+`get_board_stats` gegen
  Fab-Regeln als datierter Wissensstand), `cost_estimate`, `simulate`
  (analytisch + SPICE-Deck zum Kopieren; ehrlich: keine numerische
  Ausführung), `sim_models` + `bom_sourcing` (WebSearch),
  `photo_reverse` (Bild via Read, Netz-Hypothesen mit Konfidenz),
  `safety_spacing` (`center_item_clearance` gegen IEC-62368-Richtwerte —
  Vorprüfung, keine Zertifizierung), `firmware_map` (Pinmap als
  C-Header/DeviceTree/ESPHome-Codeblock), `mlcc_derating`, `silk_cleanup`
  (Plan→Go→gebündelter Live-Zug), `datasheet_circuit`
  (`extract_circuit_from_pdf`→Vorschau→Go→`apply_circuit_block`).
- Gemeinsame Verträge in jedem Prompt: Selektion respektieren, Mutation nur
  nach Go, Annahmen offenlegen, bei Unsicherheit fragen statt raten, kein
  Render zwischendrin, Grenze benannt. `SHIPPED_TOOL`-Guard-Test deckt jetzt
  alle 33; docs/superfeatures.md: alle Marker ✅ + „Stand 0.7.5"-Tabelle der
  v1-Grenzen. Plugin-Version 0.7.4 → 0.7.5.

### Added (Super-Feature „Stromtragfähigkeit" — IPC-2221-Ampacity, Plugin 0.7.4)
- **`check_ampacity` — Leiterbahn-Breite vs. Design-Strom** (Tool #184, in der
  `pcb_tools`-Familie, nutzt den gecachten `_extract_all`-Reader). KiCad kennt
  Geometrie, keine Ströme — der Strom je Netz ist Design-Absicht und kommt als
  `currents`-JSON vom Agenten/Nutzer. Ohne `currents`: Breiten-Inventar je
  Netz (min/max-Breite, Länge, Layer) als Entscheidungsgrundlage. Mit
  `currents`: jedes Track-Segment gegen IPC-2221 geprüft (I = k·ΔT^0.44·A^0.725;
  k außen 0.048, innen 0.024; Parameter `temp_rise_c`, `copper_oz`), Verstöße
  mit Ist-/Soll-Breite und Koordinaten, schlimmste zuerst; `nets`-Filter für
  die Selektion. Mathe pur in `utils/ampacity.py` (beide Richtungen:
  nötige Breite ↔ tragbarer Strom). Grenze: bewertet Segmente, keine Zonen.
  Tests: `test_ampacity.py` (Mathe-Fixpunkte, Audit, Tool-Pfade). Tool-Count
  183→184; GUI-Button 🔥 aktiviert (Inventar → offengelegte Strom-Annahmen →
  EIN Prüf-Call). Plugin-Version 0.7.3 → 0.7.4.

### Added (vier weitere Super-Features aktiv — 12 Buttons live, Plugin 0.7.3)
- **📄 Datenblatt-Abgleich SHIPPED (v1):** mit IC-Auswahl reviewt der Button
  über `review_ic_against_datasheet` (Pin-Tabelle + Schaltplan-Crop +
  Datenblatt-Seite, Abgleich durch den Agenten); ohne Auswahl inventarisiert
  `list_missing_datasheets`, welche PDFs unter `docs/<Value>.pdf`
  liegen/fehlen (inkl. Datasheet-URL). Grenze: PDF muss lokal vorliegen.
- **💡 Board erklären SHIPPED:** EINMAL `list_pcb_footprints` +
  `analyze_pcb_nets`, daraus Funktionsblöcke/Schnittstellen/Stromfluss mit
  exakten (klickbaren) Ref-/Netznamen; mit Selektion gezielt der markierte
  Teilschaltkreis.
- **⊙ Polar-Board SHIPPED (v1, geführt):** EIN `polar_grid`-Call
  (`op=check_grid_config`) zeigt die Grid-Parameter; Workflow-Erklärung
  (place_on_ring/spoke, polare Bögen/Segmente); Mutation nur nach Go.
- **🖊️ Skizzen-Layer SHIPPED (v1):** `ipc_list_markers` zeigt den Inhalt von
  User.9; Legende (`ipc_draw_sketch_legend`) und Leeren
  (`ipc_clear_markers`) werden angeboten, laufen aber erst nach Go. Grenze:
  Layer-Sichtbarkeit ist über IPC nicht schaltbar.
- `SHIPPED_TOOL`-Testkopplung um die vier erweitert; docs-Marker auf ✅ mit
  „Gebaut/Grenze"-Absätzen. Plugin-Version 0.7.2 → 0.7.3.

### Added (Entwirren v1 + globale Auswahl-Regel + Optionen-Dropdown, Plugin 0.7.2)
- **🧶 Entwirren SHIPPED (v1):** der Button orchestriert den im Roadmap-Doc
  beschriebenen Ablauf — EINMAL lesen (`list_pcb_footprints` +
  `analyze_pcb_nets`), im Kopf entwirren, Kandidat gegen den nicht-mutierenden
  `evaluate_layout`-Scorer prüfen (max. 3 Durchgänge), Plan mit Score
  vorher → nachher zeigen, erst nach ausdrücklichem Go EIN gebündelter
  Live-Move + EIN `check_connectivity`. Ehrliche Grenze dokumentiert
  (Text-Vorschau statt Geister-Vorschau; Trigger-Erkennung offen).
- **Selektions-Scoping ist jetzt der globale Vertrag jedes Buttons:** ohne
  Auswahl boardweit, mit Auswahl nur die markierten Bauteile — das Panel
  zeigt beim Klick sichtbar an, worauf der Zug wirkt
  (`_selection_scope_line`, „🎯 Wirkt auf deine Auswahl: …"). Der separate
  Roadmap-Eintrag `scoped_untangle` entfällt (Regel statt Extra-Feature);
  Guard-Test `test_scoped_untangle_is_gone`.
- **Aktive Buttons orange:** SHIPPED-Features tragen `CLAUDE_ORANGE`,
  SOON bleibt gedimmt — der Roadmap-Streifen zeigt den echten Stand.
- **`plugin/claude_options.py` (neu) + Optionen-Dropdown:** kuratierte
  sinnvolle CLI-Schalter (Modell Sonnet/Opus/Haiku, `--fast`,
  `--fallback-model`), dynamisch gegen `claude --help` der installierten
  CLI gefiltert (nie ein unbekanntes Flag anbieten; von
  `build_command` reservierte Flags ausgeschlossen). `apply_switch` ersetzt
  ein vorhandenes Flag gleichen Namens statt zu duplizieren; Hilfe-Text
  wird einmal pro Sitzung im Hintergrund gelesen, bei Fehlschlag bleibt
  das Dropdown einfach verborgen (Freitextfeld unverändert nutzbar).
  Tests: `test_plugin_claude_options.py`. Plugin-Version 0.7.1 → 0.7.2.

### Added (Super-Features aktiviert — 7 Buttons live, Plugin 0.7.1)
- **Die ersten 7 Super-Feature-Buttons sind verdrahtet** — bisher stand die
  ganze Leiste auf `SOON`, obwohl die Backend-Tools seit 0.6.0 ausgeliefert
  sind. `SuperFeature` trägt jetzt ein `prompt`-Feld (der kanonische
  Chat-Auftrag des Buttons); `chat_dialog._on_superfeature` dispatcht ihn bei
  `SHIPPED` als echten Chat-Zug über den neuen gemeinsamen Pfad
  `_dispatch_prompt` (auch von `_on_send` genutzt), mit der aktuellen
  KiCad-Auswahl als Kontext (Selektions-Vertrag). `SOON`-Buttons zeigen
  weiter den Pitch.
  - Aktiv: `semantic_erc`→`audit_design`, `bus_radar`→`list_bus_members`,
    `test_points`→`audit_test_points`, `bom_consolidate`→`consolidate_bom`,
    `preferred_parts`→`suggest_preferred_parts`, `via_cost`→`via_promote`
    (dry_run-Report, Umsetzung nur auf Zuruf), `sketch_conductor`→
    `ipc_markup_to_tracks` (erst dry_run-Check, dann EIN Umsetzungs-Call =
    ein Undo-Schritt).
  - Die Prompts folgen den Anti-Toolcall-Explosion-Regeln (Tool benennen,
    kein `pcb_render`, Auswahl respektieren, Result lesen statt Rücklesen);
    `test_plugin_superfeatures.py` erzwingt das (SHIPPED ⇔ geliefertes Tool
    im Prompt benannt + No-Render-Klausel; SOON ⇔ kein Prompt).
  - `docs/superfeatures.md`: Status-Marker der sieben auf ✅. Plugin-Version
    0.7.0 → 0.7.1.

### Added (Warm-Server — persistenter lokaler HTTP-MCP-Server, Plugin 0.7.0)
- **`KICAD_MCP_TRANSPORT=http` — den Tool-Server einmal pro KiCad-Sitzung warm
  halten** statt ihn bei jeder Chat-Nachricht per stdio neu zu spawnen (Plan:
  `docs/warm-server-plan.md`; Kanal B, die kipy-IPC zu KiCad, bleibt
  unangetastet). Default bleibt `stdio`, bis der http-Pfad auf echten
  Windows-Setups validiert ist — Rollback ist ein Env-Wort.
  - `kicad_mcp/server.py`: `--transport {stdio,http,streamable-http}` /
    `--host` / `--port` (Env-Fallbacks `KICAD_MCP_TRANSPORT`,
    `KICAD_MCP_HTTP_HOST`, `KICAD_MCP_HTTP_PORT`); im http-Modus
    `mcp.run(transport="streamable-http", host, port)` strikt auf
    `127.0.0.1`, optional gated durch ein Bearer-Token
    (`KICAD_MCP_HTTP_TOKEN`, framework-freie ASGI-Middleware → 401).
  - `plugin/server_manager.py` (neu): Lebenszyklus des warmen Servers —
    `ensure_running()` (Health-Check pro Turn → Auto-Restart bei Crash/Hänger,
    Reuse sonst), Pidfile in `%LOCALAPPDATA%\kicad-claude\` bzw.
    `~/.local/state/kicad-claude/` (Plugin-Reloads finden denselben Server,
    Waisen nach KiCad-Crash werden beim nächsten Start weggeräumt),
    Zufalls-Token pro Start, `shutdown()` (Prozessbaum, auch via `atexit` —
    der Server überlebt KiCad nie), `status()` für die Diagnose.
  - `plugin/mcp_config.py`: `build_http_mcp_config`/`write_http_mcp_config` —
    `{"type":"http","url":…,"headers":{Authorization}}`; claude spawnt nichts
    mehr, es verbindet nur.
  - `plugin/claude_bridge.py::ask`: im http-Modus vor jedem Spawn
    `ensure_running()` + Config-Rewrite auf die aktuelle URL/Token; schlägt
    der Warm-Start fehl, wird die Config auf stdio zurückgeschrieben und der
    Zug läuft wie bisher (kein toter Chat).
  - `plugin/server_probe.py::probe_http`: MCP-`initialize`-Ping gegen den
    LAUFENDEN Server (nichts wird gespawnt); `plugin/diagnose.py` zeigt
    Transport-Modus + Warm-Server-Status (läuft? PID, Port, Uptime, Ping).
  - `plugin/runtime_env.py::transport_mode()`: das Flag, typo-sicher
    (unbekannter Wert → stdio).
  - Vorab-Checks aus dem Plan: uvicorn/starlette sind **harte** Dependencies
    von fastmcp (`fastmcp-slim[server]`) — jede `_deps`-Installation hat sie
    bereits; kein `deps.py`-Change nötig. FastMCP-Endpoint ist `/mcp`
    (kanonisch, ohne Slash — `/mcp/` antwortet 307).
  - Tests: `test_server_http.py` (echter Server über HTTP: initialize +
    tools/list = 183 Tools, 401 ohne Token, Manager-End-to-End
    start→ping→reuse→shutdown), `test_plugin_server_manager.py` (pure:
    ensure-once, Restart-Entscheid, Pidfile, Token), Bridge-/Probe-/
    Diagnose-/runtime_env-Tests erweitert. Plugin-Version 0.6.1 → 0.7.0.

### Added (Super-Feature „Test-Punkt-Wächter" — Probe-Zugang der kritischen Netze)
- **`audit_test_points` — Testbarkeit prüfen, die ERC/DRC nicht sehen** (Tool
  #183). Rankt Netze nach Bring-up-/Serientest-Wichtigkeit (Versorgung, Reset,
  Clock, Bus) und meldet, welche kritischen Netze **keinen** Prüfpunkt-/Stecker-
  Zugang haben — die blinden Flecken für Flying-Probe/Nadeladapter. Liefert
  Abdeckung in %, die blinden Netze und je Netz, worüber es zugänglich ist
  (`TP*`/`TestPoint`-Footprint oder Stecker). `include_signals` nimmt auch reine
  Signalnetze mit auf (zählen nie in die kritische Abdeckung); `refs`-Filter für
  die Selektion.
  - Synergie: liest den *einmal* geparsten `design_rules.BoardContext` und rankt
    über dieselben Signale wie der Design-Wächter (`is_power_net`, Reset-Regex,
    `bus_infer`) — kein Re-Parse, keine Doppellogik. Ground wird nicht auditiert
    (ist ohnehin überall erreichbar). Tests: `test_test_points.py`. Tool-Count
    182→183.

### Added (Super-Feature „Fab-Standardteile" — No-Load-Fee-Teile, fab-agnostisch)
- **`suggest_preferred_parts` — R/C aufs Vorzugsteil des Fertigers mappen**
  (Tool #182). Bestücker verlangen eine Feeder-Ladegebühr pro Bauteiltyp
  außerhalb ihrer Hausbibliothek (JLCPCB Basic vs Extended ~3 $/Typ, Seeed OPL,
  Aisler Push-Parts …). Das Tool mappt jeden R/C-Wert+Bauform auf das Vorzugsteil
  und schätzt die gesparte Gebühr (`load_fee × Typen-mit-Vorzugsteil`, als obere
  Schranke ausgewiesen). Reine Analyse; `refs`-Filter für die Selektion.
  - **Fab-agnostisch** über eine Provider-Registry (`utils/fab_parts.PROVIDERS`,
    gleiches Single-Source-Muster wie `design_rules.RULES`): je Fertiger ein
    datierter Snapshot `resources/data/fab_parts_<provider>.json`. Neuer Fertiger
    = JSON + eine Registry-Zeile, kein Tool-Umbau. V1: `jlcpcb`.
  - Snapshot = kuratierte Seed-Abdeckung mit `snapshot_date` + `disclaimer`
    (beide im Result), **nicht** der Live-Katalog — Tool sagt „vor Bestellung
    Lager prüfen".
  - Synergie: liest Werte+Bauform über den geteilten `pcb_board_parse` (neu:
    Footprint-Lib-ID als `fpid` → Bauform via `extract_package`), Value-Parsing
    über `bom_consolidate` (so matchen `4k7` und `4.7k` dieselbe Snapshot-Zeile).
    Läuft nach `consolidate_bom`. Tests: `test_fab_parts.py`. Tool-Count 181→182.

### Added (Super-Feature „BOM-Konsolidierung" — E-Reihe standardisieren)
- **`consolidate_bom` — fast-gleiche R/C-Werte auf E-Reihe zusammenlegen**
  (Tool #181). Jeder eigene R/C-Wert = eine BOM-Zeile, Rolle und Bestückungs-
  Feeder. Das Tool snappt jeden Wert auf den nächsten E-Reihen-Wert
  (`E6`…`E96`, Default `E24`) und meldet, welche Zeilen sich zusammenlegen lassen
  — weniger Feeder (Rüstkosten), größere Stückzahlen — **ohne** ein Bauteil über
  `max_shift_pct` (Default 5 %) zu verschieben. Werte, deren nächster Standardwert
  weiter weg liegt, kommen als `unmergeable` zurück statt still verbogen zu
  werden. Reine Analyse (schlägt vor, ändert nicht), `refs`-Filter für die
  Selektion.
  - Neuer kanonischer SI-Value-Parser in `utils/bom_consolidate.py`
    (Ohm/Farad), der die Infix-Notation `4k7`=4,7 kΩ / `4n7`=4,7 nF korrekt liest
    — die tuple-Parser in `component_utils` lesen `4k7` als 4 kΩ falsch.
  - Synergie: liest die Werte über den geteilten `pcb_board_parse` (kein
    Re-Parse). Tests: `test_bom_consolidate.py`. Registry + Tool-Count 180→181.

### Added (Super-Feature „Design-Wächter" — persistente Regel-Registry)
- **`audit_design` — semantische Design-Checks jenseits des ERC, registry-getrieben**
  (Tool #180). Die Regeln leben als **persistente Registry** in
  `utils/design_rules.RULES` (key/Titel/Severity/Check-Fn) — dieselbe
  Single-Source-Ebene wie `TOOL_REGISTRARS`/`superfeatures.FEATURES`. Das Board
  wird **einmal** in einen geteilten `BoardContext` geparst, jede Regel liest
  ihn; neue Regel = ein Registry-Eintrag, taucht automatisch im Tool auf.
  Optionaler `rules`-Filter (Subset).
  - **Regel 1 — I²C-Bus ohne Pull-ups** (open-drain → braucht sie).
  - **Regel 2 — Quarz ohne Load-Caps** (jeder XIN/XOUT-Terminal braucht ein C
    gegen GND; Quarz per `Y*`-Ref oder Value erkannt).
  - **Regel 3 — IC-Versorgungspin ohne/mit entfernter Entkopplung.** Board-weit:
    je IC-Supply-Pin den nächsten Bypass-Cap (`C*` von Rail gegen GND) über die
    Pad-Welt-Koordinaten suchen — kein Cap → `warning`, Cap > 3 mm entfernt →
    `info` mit Distanz. Liest dieselbe Intent wie `audit_power_tree`, aber ohne
    Re-Parse (nutzt die neuen `pad_xy`-Welt-Koords im `BoardContext`).
  - **Regel 4 — Active-Low-Reset ohne Pull-up** (`NRST`/`RESET`/`MR`/`POR` …):
    Reset-Netz ohne erkennbaren Pull-up gegen eine Supply → `info` (ein
    Supervisor/Debug-Probe darf es treiben, daher nicht `warning`).
  - Komponiert `bus_infer` + `pcb_board_parse` + `placement_eval.is_power_net`
    (Synergie statt Neubau). Tests: `test_design_rules.py`,
    `test_design_rules_tools.py`.

### Changed (Projekt-Regel)
- **CLAUDE.md: „nur bauen, was KiCad NICHT kann".** Grundregel dokumentiert —
  keine Funktion nachbauen, die KiCad bereits enthält (ERC/DRC-Basics, Router,
  PCB-Calculator-Formeln); stattdessen KiCads Vorhandenes nutzen und semantisch
  darüber hinausgehen. Plus die zwei Querschnitts-Verträge (selektions-fähig +
  maximale Code-Synergie).

### Added (Super-Feature „Bus-Radar")
- **`list_bus_members` — semantische Bus-Erkennung** (Tool #179). KiCad kennt
  Einzelnetze, nicht *Busse*; dieses Tool gruppiert die Netze eines Boards zu
  Bussen und listet je Bus die Netze + Pins (`REF.PAD`): Protokoll-Vokabular
  (I²C = SDA+SCL, SPI = MOSI/MISO/SCK, UART, USB, CAN, SWD/JTAG), nummerierte
  Busse (`D0..D7`) und Differential-Paare (`X_P`/`X_N`). Filter per Bus-Label
  oder Member-Netz („was ist auf SDA?"). Fundament für Gruppen-Platzierung/
  -Routing. Reine Inferenz in `utils/bus_infer.py`; Pins über den geteilten
  `utils/pcb_board_parse`. Tests: `test_bus_infer.py`, `test_bus_tools.py`.

### Added (Super-Feature „Entwirren" — Fundament)
- **`get_board_layout` — Board → Scorer-Eingabe** (Tool #178, Read-Seite). Liest
  ein `.kicad_pcb` einmal in die `evaluate_layout`-Form (Footprint-Pose +
  Pad-Local-Offsets + Courtyard-Bbox + Netz→Pad-Karte) und gibt gleich den
  Ist-Score als Baseline mit. Der Agent editiert dann `x/y/rot` im Kopf und
  scort Kandidaten über `evaluate_layout` — **kein Board-Zugriff in der Schleife**.
  - **Synergie statt Neubau:** der Board-Parser lag schon in `audit_tools`
    (`_parse_pcb_for_audit`). Er ist jetzt der geteilte
    `utils/pcb_board_parse.parse_pcb_footprints` (um Pad-Local-Offsets + Bbox
    erweitert); `audit_tools` importiert ihn zurück. Ein Parser für Audit **und**
    Platzierung.
- **`evaluate_layout` — non-mutating Platzierungs-Scorer** (Tool #177). Der
  „Notizzettel" hinter der Ratsnest-Entkreuzung: bewertet eine *hypothetische*
  Footprint-Anordnung, **ohne das Board zu berühren** — Signalnetz-Kreuzungen
  (Ratsnest via MST pro Netz, echter CCW-Segment-Schnitt), Footprint-Überlappung
  und Wirelength. Power-/GND-Netze werden auto-ausgeschlossen (werden Kupfer­fläche,
  keine Luftlinie). So kann der Agent „durch Nachdenken lösen" (vorschlagen →
  bewerten → verfeinern) und erst die *finale* Lösung in einem Zug anordnen.
  Reiner Kern in `utils/placement_eval.py` (footgun-sichere Rotation über
  `pcb_local_to_world`), Tests in `tests/test_placement_eval.py`.

### Added (Plugin — Interaktion)
- **Glaskasten-Zug: der Agent-Zug spricht Board-Sprache statt Tool-Namen.** Der
  Transkript- und Statuszeilen-Stream zeigt jetzt „6× Via gesetzt" /
  „prüft die Konnektivität" statt roher Slugs wie `add_vias_to_pcb`
  (`claude_bridge.describe_tool`, gespeist aus dem Tool-Input via
  `tool_calls`). Nach einem Zug, der das Board geändert hat, erscheint eine
  **Änderungs-Quittung** „✎ geändert: R12, GND, (120.5, 84.0)" mit einem
  klickbaren **📍 zeigen** (markiert alle geänderten Elemente im Editor;
  `changed_targets` + der bestehende `markall`-Pfad).
- **Undo sichtbar: „↶ zurück" pro Quittung + Footer-Button „↶ Rückgängig".**
  Löst KiCads natives Undo im laufenden Editor aus (`board_links.undo` →
  `run_action("common.Interactive.undo")`, Action-Name gegen KiCads
  `actions.cpp` verifiziert). Ein Undo nimmt den letzten Agent-Commit zurück
  (KiCad bündelt eine Tranche zu einem Schritt) — „nimm zurück, was Claude
  gerade tat" ist ein Klick entfernt, ohne das PCB-Fenster zu suchen.
- Tests: `tests/test_plugin_bridge.py` (describe_tool/changed_targets/
  tool_calls), `tests/test_plugin_board_links.py` (undo).

### Fixed (Plugin — Chat-Links)
- **Tote Chat-Links, die nicht ins Board sprangen, behoben.** Manche
  unterstrichenen Stellen taten beim Klick nichts, weil der Tokenizer nach
  Oberflächen-Muster verlinkte, der Resolver aber KiCads echte Adressierung
  braucht:
  - **Pins verifiziert:** `board_targets`/`board_targets_from_file` liefern jetzt
    die echten Pad-Nummern pro Ref; `tokenize(..., known_pins=…)` verlinkt
    `<ref>.<pin>` nur, wenn das Pad wirklich existiert — tote Links wie `U3.3V3`
    (ein Schienenname, kein Pad) verschwinden. Ohne Pin-Vokabular bleibt das
    Verhalten permissiv (Rückwärtskompatibilität).
  - **Koordinaten robuster:** `select_coord`-Default-Radius 8 → 25 mm (eine
    Koordinate in dünn besetztem Kupfer fiel vorher aus dem Fenster und der Link
    verpuffte), plus Tracks als zusätzliche Anker. Enge explizite Radien bleiben
    respektiert.
  - Tests in `tests/test_plugin_board_links.py`.

### Changed (Struktur, cont.)
- **Reine kipy-Helfer aus `ipc_tools` nach `utils/ipc_board.py`.**
  `layer_to_enum` / `find_net` / `board_default_via_nm` (vorher
  `ipc_tools._*`, quer importiert von `ipc_interact_tools` und
  `ipc_markup_tools`) leben jetzt in `utils/` — sie sind rein (nur kipy-Protos +
  Board-Objekt), also risikoloser Move. Verbleibende Kopplung
  (`_connect_kicad`/`_require_editor`, 29/184 Z. Editor-Auto-Launch + Presence-
  Beacon) bleibt bewusst im Tool-Modul: untestbar ohne KiCad-GUI, zu riskant für
  diesen Durchgang.
- **Ratchet-Test gegen cross-tool Private-Imports** (`test_no_cross_tool_
  private_import.py`). Blockt jeden **neuen** Import eines `_`-privaten Namens
  aus einem anderen `tools/`-Modul (die Wurzel der „God-Module"/„heimliche
  Shared-Library"-Befunde); die verbleibenden bekannten Fälle sind mit
  Begründung + Auflösungsort in einer Allowlist eingefroren, sodass die
  Kopplung nur noch schrumpfen kann. Relative Imports werden aufgelöst.

### Changed (Struktur)
- **Geteilte Parser aus Tool-Modulen nach `utils/` gezogen** (beseitigt
  quer-über-Layer importierte private Namen, u.a. eine generators→tools-
  Inversion):
  - `utils/schematic_parse.py` — `parse_schematic` / `extract_components`
    (vorher `schematic_tools._parse_*`, quer importiert von `review_tools`).
  - `utils/svg_render.py` — `ensure_cairosvg` / `svg_to_png` /
    `ensure_cairo_dll_searchable` (vorher `cli_export_tools._*`, quer importiert
    von `export_tools` **und** `generators/review/_svg_crop` — Letzteres eine
    Aufwärts-Inversion). Tool-Module importieren die Funktionen jetzt aus
    `utils/`; kein Verhaltens- oder Signatur-Wechsel.

### Performance (cont.)
- **`check_connectivity` (overview) von O(nets×pads) auf O(pads+conn).**
  `_compute_overview` scannte pro Netz **alle** Board-Pads und rief `_pad_id`
  (3 SWIG-Calls) je Pad — auf einem Mainboard (z.B. 2000 Pads × 500 Netze)
  ~10⁶ Pad-Iterationen mit SWIG-Übergängen, obwohl das Tool als „billiger"
  Verify beworben wird. Jetzt: Pads einmal nach netcode gruppiert, `_pad_id` je
  Board-Pad einmal memoisiert, Netze mit <2 Pads (nicht fragmentierbar)
  übersprungen. Output identisch. Headless-Test `test_connectivity_overview.py`
  (Fakes statt pcbnew), plus `whatif` auf den geteilten `_clusters_for_net`-
  Helper umgestellt.

### Performance (cont.)
- **`file_cache` hält den Lock nicht mehr über den Disk-Read.** `get_text`
  serialisierte bei einem Cache-Miss den (auf Cloud-Disks zig Sekunden langen)
  `open().read()` gegen jeden anderen Cache-Zugriff. Jetzt Double-Checked-
  Locking: `stat` + Miss-Read laufen außerhalb des Locks, nur die Dict-Ops sind
  geschützt. Zusätzlich ist der `_KEY_MEMO` (path→realpath) jetzt auf 512
  Einträge gedeckelt (Drop-wholesale + Lazy-Rebuild) statt unbegrenzt zu wachsen.
- **Via-Batch: redundante Multi-MB-Vollkopie pro Via entfernt.**
  `_insert_before_root_close` rief `pcb_text.rstrip()` — eine Kopie des ganzen
  Board-Texts — nur um das letzte `)` zu finden; `rfind(")")` überspringt
  Trailing-Whitespace ohnehin. Verhalten identisch, halbiert die Kopierlast pro
  Insert (spürbar bei N-Via-Tranchen). Tests in `test_pcb_geometry_tools.py`,
  `test_file_cache.py`.

### Fixed (Quality)
- **Board-Open-Guard-Bypass in 8 Mutations-Stellen geschlossen.**
  `add_vias_to_pcb`, `add_zone_pour_to_pcb`, weitere Geometrie-Writes und die
  drei `via_promote`-Writes schrieben mit rohem `open(...,"w")` + `put_text` und
  umgingen so den zentralen `write_text`-Chokepoint — sie hätten das GUI-Save
  eines offen im KiCad-Editor liegenden Boards überschreiben können. Jetzt alle
  über `write_text` (→ `BoardOpenError`, wie der PCB-Text-Patcher).
- **`generate_project`: Multi-Sheet-Fehler wird nicht mehr verschluckt.**
  `except Exception: pass` fiel still auf Single-Sheet zurück — ein echter Bug
  im Multi-Sheet-Build lieferte dem Nutzer unbemerkt ein anderes Ergebnis als
  angefordert. Fehler wird jetzt geloggt und als `multisheet_fallback` im
  Result gemeldet; partieller Output wird verworfen.
- **File-Deskriptor-Leaks im Renderer.** `pcb_render_tools` las PCB- und
  SVG-Dateien via `open(...).read()` ohne `with` → FDs akkumulierten im
  langlebigen Warm-Server. Jetzt `with`-gekapselt.
- **`validate_project` folgt der `success`-Konvention.** Result trägt jetzt
  `success` (lief der Check) zusätzlich zum domänenspezifischen `valid` — ohne
  bestehende `valid`-Consumer zu brechen.
- **Toter/irreführender Code entfernt.** Unerreichbarer Block nach `return` in
  `server.create_server`, auskommentierte Preload-Kruft, und das nachweislich
  ungenutzte generische `KiCadAppContext.cache`-Feld (echtes Caching lebt in
  `cache/file_cache.py` + Warm-Daemons) — klärt, wo State wirklich liegt.

### Performance
- **PCB-Read-Tools laden das Board nicht mehr pro Call neu.**
  `list_pcb_footprints`, `analyze_pcb_nets` und `find_tracks_by_net` liefen alle
  über `_extract_all` → `pcbnew.LoadBoard` bei **jedem** Aufruf; der typische
  „Board angucken"-Flow (3–5 Reads hintereinander) zahlte jeden Load neu
  (~1 s lokal, ~80 s kalt auf Cloud-Disk). Jetzt cached `_extract_all` das
  geparste Ergebnis per `(mtime_ns, size)`-Fingerprint (LRU, 4 Boards):
  unverändertes Board = Dict-Lookup, GUI-gespeichertes/mutiertes Board
  (mtime ändert sich) lädt frisch nach — selbstkorrigierend, keine explizite
  Invalidierung nötig. Große Boards werden so **einmal** geladen statt N-mal.
- **Board-Load blockiert nicht mehr den async-Event-Loop.** Der sekundenlange
  (kalt minutenlange) `_extract_all` lief direkt im Loop-Thread der `async`
  Read-Tools und fror bei einem langsamen Load den ganzen Server ein. Jetzt via
  `asyncio.to_thread` ausgelagert. Tests: `tests/test_pcb_extract_cache.py`.

### Fixed
- **DRC-Subprozess konnte den Server unbegrenzt aufhängen.**
  `tools/drc_impl/cli_drc.py` rief `subprocess.run` **ohne** Timeout auf; bei
  gesperrter/korrupter `.kicad_pcb` oder einem kalten Cloud-Read (~80 s) hing
  der Call endlos und blockierte den MCP-Server. Jetzt: ein **größen-adaptiver,
  konfigurierbarer** Timeout statt eines fixen Werts — DRC läuft auf großen
  Boards legitim minutenlang (KiCad #17434, kein `--refill-zones` im Default),
  also darf kein kurzer Fixwert echte Arbeit killen. Budget =
  `drc_base (300 s) + Boardgröße_MB × drc_per_mb (45 s)`, gedeckelt auf
  `drc_max (1800 s)`; Override via `KICAD_MCP_DRC_TIMEOUT_S` (Sekunden, oder
  `none`/`0`/`off` = kein Timeout). `subprocess.TimeoutExpired` wird als
  sauberes `{"success": False, "error": …}` zurückgegeben.
- **DRC blockierte den async-Event-Loop.** Der blockierende `subprocess.run`
  lief direkt im Loop-Thread (`run_drc_via_cli` ist `async`) → ein
  minutenlanger DRC fror den ganzen Server ein. Jetzt via `asyncio.to_thread`
  ausgelagert (Timeout wird an `subprocess.run` durchgereicht, Kind wird bei
  Ablauf sauber gekillt). Neuer Helper `config.drc_timeout_seconds`; Tests in
  `tests/test_drc_timeout.py`.

## [0.5.1] — 2026-06-30

### Security / Privacy
- **Persönliche Pfade aus dem Code entfernt.** Ein hardcodierter privater
  Windows-Dev-Pfad (persönlicher Benutzername + privater OneDrive-
  Projektordner) steckte im ausgelieferten Plugin-Code
  (`plugin/claude_action.py`) sowie in `scripts/check_kipy.py` und
  `tests/test_plugin_runtime_env.py`. Ersetzt durch:
  `_DEV_MCP_ROOT` ist jetzt env-getrieben (`KICAD_MCP_DEV_ROOT`, leer als
  Default — kein maschinenspezifischer Pfad wird mehr hardcodiert oder
  mitgeliefert); Doc-Beispiel auf `<path-to>\kicad-mcp\…` neutralisiert;
  Test-Fixtures auf neutralen Benutzernamen `user` umgestellt. **Hinweis:**
  betrifft nur den aktuellen Stand — der Pfad existiert weiterhin in der
  Git-Historie (separate Bereinigung nötig, falls gewünscht).

## [0.5.0] — 2026-06-30

### Added
- **Behavior-Audit der Schaltplan-Pfade** (`docs/kicad_mcp_behavior_delta.md`,
  `docs/kicad_mcp_behavior_audit.md`). Vollständige Inventur der automatischen
  Verhaltensabweichungen MCP vs. Stock-KiCad über 12 Bereiche, je gegen 10
  Audit-Fragen geprüft und K0–K3-klassifiziert.
- **`center_item_clearance` — räumliches Via-Zentrieren in einem Call** (Tool
  #174). Statt „Clearance zu Wand A messen → Clearance zu Wand B messen → von
  Hand um die Differenz nudgen" (die ~9 Calls aus dem Live-Mitschnitt) sammelt
  das Tool selbst das *fremde* Kupfer (Netz ≠ Via-Netz) im Radius — Tracks,
  Vias, Pads — löst den Zielpunkt und draggt das Via dorthin, **Stubs ziehen
  automatisch nach** (die fernen Enden bleiben verankert, nichts reißt). Zwei
  Modi: `equalize` (Default) trifft exakt die Mittelsenkrechte zwischen den zwei
  nächsten, gegenüberliegenden Wänden (der `(C₁−C₂)/2`-Schritt); `maximize`
  steigt bis die engste Clearance nicht weiter wächst (lokaler Inkreis-Vertex).
  `dry_run=True` rechnet nur (Zielposition + Clearance vorher/nachher) ohne zu
  bewegen → perfekt als Vorschau. Result echot alte/neue Position, Clearance je
  Nachbar vorher/nachher, `min_clearance`, `meets_rule` (gegen die Board-Default-
  Netzklasse), `stubs_followed`, `connectivity_ok`. Rendert **nicht** (Mutations-
  Tool-Regel) — `pcb_render` separat nach Abschluss. Arbeitet nur an Vias; für
  einen freien Nudge bleibt `ipc_move_items`. Liegt in der `ipc_interact`-Familie
  (`register_ipc_interact_tools`), reicht die Live-IPC-Bausteine
  (`_find_items_by_uuids`, Commit-Pattern) nach.
- **`kicad_mcp/utils/pcb_clearance.py` (neu)** — die reine, KiCad-freie Geometrie
  dahinter: Obstacle-Modell (`SegmentObstacle`/`CircleObstacle`/`RectObstacle`
  mit `probe(px,py) -> (gap, ux, uy)`) plus die zwei Solver (`solve_equalize`
  Closed-Form + iterativ, `solve_maximize` Soft-Min-Subgradient-Ascent mit
  monotoner Schrittkontrolle). Headless unit-testbar.
- `tests/test_pcb_clearance.py` (16 Geometrie-Tests: Probes, Korridor-Zentrieren,
  Ecke-Fallback, Monotonie) + `tests/test_center_clearance.py` (11 Tool-Tests:
  equalize+Stub-Drag, dry_run, maximize, Layer-Scoping, Selektion/Validierung).
  Tool-Count-Lock 173 → 174.
- **`drc_triage` + `drc_select_group` — DRC-Ergebnisse gruppieren & gezielt
  zeigen** (Tools #175/#176). Hintergrund: KiCad-10-IPC gibt die DRC-Marker des
  GUI **nicht** raus (die `Board`-API hat keine drc/marker-Methode), also läuft
  DRC headless über `kicad-cli` mit denselben `.kicad_pro`-Regeln — die
  `items[].uuid` sind echte Board-KIIDs und damit live selektierbar.
  - **`drc_triage`** speichert das Live-Board, runnt DRC und gibt die Verstöße
    **nach Typ gruppiert** zurück: pro Gruppe `{type, severity, count,
    item_uuids, nets, layers, item_kinds, centroid_mm, bbox_mm, suggested_tool}`,
    Errors zuerst. `suggested_tool` ist eine **Fix-Strategie-Map** (via-aware):
    Clearance-mit-Via → `center_item_clearance`, Annular/Drill → `via_resize`,
    blind/buried → `via_promote`, Unconnected → `ipc_route_pin_to_pin`, Silk/
    Courtyard → `ipc_move_items`. So laufen die Folge-Tools **im Batch** (eine
    Tranche je Typ) statt ein Call pro Verstoß — die „Batch vor Einzeln"-Linie.
  - **`drc_select_group`** selektiert eine Gruppe (per `group_type` oder
    `index`) live im Editor (clear + add_to_selection) → „einzelne Verstöße
    gezielt zeigen", inkl. echo des `suggested_tool`.
  - Pad-aware Auflösung (`_resolve_drc_items`) steigt für pad-level-Verstöße
    (unconnected/Pad-Clearance) zusätzlich in `get_pads()` ab — sonst wären die
    nicht selektierbar. Grouped-DRC ist per (path→mtime) gecacht, sodass
    `triage` + folgendes `select` **einen** DRC-Lauf teilen.
  - `tests/test_drc_triage.py` (18 Tests: Gruppierung, Fix-Map-Unit, Severity-/
    Unconnected-Filter, Select per Typ/Index, Pad-level-Select, Cache-Reuse).
    Tool-Count-Lock 174 → 176.

### Changed
- **Plugin-Chat-System-Prompt (`BEHAVIOR_SYSTEM_PROMPT` in
  `plugin/claude_bridge.py`) geschärft.** Drei Ergänzungen am pro-Turn via
  `--append-system-prompt` injizierten Verhaltenstext: (1) **Rollen-Rahmung** —
  „Du bist ein erfahrener Senior-PCB-/Platinen-Entwickler" primt
  Domänenkompetenz und Tonfall. (2) **Offen-Board-Lenkung auf IPC** — das
  Plugin läuft per Definition gegen ein in KiCad geöffnetes Board, daher
  explizit „mutiere über `ipc_*`/`live_*`; die Text-Patcher (`*_text`,
  `pcb_batch`) sind bei offenem Board geblockt (`BoardOpenError`)" — spart den
  sonst garantierten Fehlversuch-Zyklus. (3) **Positiv-Wegweiser zur
  Tool-Wahl** (Aufgabe → Tool, aus `CLAUDE.md`), statt des bisher fast reinen
  Verbots-Katalogs. `tests/test_plugin_bridge.py` deckt Rolle + IPC-Lenkung ab.
- **Pre-commit-Hook für automatischen Bundle-Sync** (`.githooks/pre-commit`,
  `scripts/setup-hooks.sh`). Spiegelt `plugin/mcp/kicad_mcp/` bei jedem Commit
  aus dem kanonischen `kicad_mcp/` (`scripts/sync_bundle.py`) und staged das
  Ergebnis mit — so muss nur ein Pfad gepflegt werden. Aktivierung einmalig
  via `core.hooksPath`. Doc-Status der vier v0.4.0-Pläne (`docs/*_plan.md`,
  `pinout_pipeline_spec.md`) auf „implementiert" korrigiert.

### Fixed
- **Power-Symbol-Rotation im Circuit-Block-Generator vereinheitlicht
  (AUD-203).** `generators/circuit_block/_block_to_patch.py` drehte positive
  Rails (+3V3/+5V/VBUS/VCC …) hart auf 180° (`0 if net.startswith("GND") else
  180`), während alle anderen Pfade (`add_power_symbols`,
  `convert_global_labels_to_power`) der kanonischen `default_power_rotation`
  (immer 0) folgen — dieselbe Rail erschien je nach Erzeugungsweg unterschiedlich
  orientiert. Die drei Anker-Stellen rufen jetzt `default_power_rotation(net)`
  als Single Source of Truth auf; der Generator kann nicht mehr driften.
  Regressionstest `test_power_anchors_rotation_zero_for_all_rails` (positive
  Rail VCC → Rotation 0). Dokumentiert in `docs/kicad_mcp_behavior_audit.md`.

## [0.4.5] — 2026-06-19

### Fixed
- **Geist-`pcbnew` beseitigt — die *eigentliche* Wurzel von „kein eindeutiges
  Board".** `ipc_open_kicad` startete den Editor DETACHED
  (`creationflags=DETACHED_PROCESS`) und hielt die PID **nirgends** fest. Beim
  KiCad-Schließen killt das Plugin den claude+MCP-Baum (`taskkill /F /T`) — ein
  detached Kind liegt aber **außerhalb** dieses Baums und überlebt. Resultat: ein
  board-loser `pcbnew` (live beobachtet: 7-MB-Prozess, der KiCads Schließen
  überlebte), der den IPC-Socket besetzt → `GetOpenDocuments` liefert „no
  handler" / 0 Boards → **jeder** Chat-Link scheitert mit „kein eindeutiges
  Board". Behoben durch eine **Spawned-Editor-Registry**:
  - `kicad_mcp/utils/spawned_registry.py` (neu): `ipc_open_kicad` schreibt jede
    gespawnte PID in eine feste Temp-Datei.
  - **Zwei unabhängige Reaper** lesen sie: `ipc_close_kicad` (serverseitig) und
    `claude_bridge.terminate_all` (plugin-seitig, auf Panel-Close/KiCad-Exit via
    atexit). Der Plugin-Reaper liest die Datei direkt (kann das Paket nicht
    importieren — `kicad_mcp/__init__` zieht den ganzen Server); ein Test sichert,
    dass beide denselben Dateinamen verwenden.
  Damit ist die Lebensdauer eines MCP-gestarteten Editors an die Session
  gebunden — kein Geist mehr, und das echte Board wird nie mit-gekillt (gezieltes
  `taskkill /PID`, nie `/IM pcbnew.exe`).

### Added
- `tests/test_spawned_registry.py` — 13 Headless-Tests (record/forget/reap mit
  injiziertem Killer, Korrupt-/Fehlt-Datei, Plugin-Reaper liest dieselbe Datei +
  Dateinamen-Kontrakt server↔plugin).

## [0.4.4] — 2026-06-19

### Changed
- **„Kein eindeutiges Board"-Meldung unterscheidet jetzt die zwei Ursachen.**
  Derselbe rohe kipy-Fehler („no handler for GetOpenDocuments") feuert sowohl
  bei MEHREREN KiCad-Instanzen auf einem Socket als auch bei einem
  kipy↔KiCad-Versions-Mismatch. `board_links.connect()` löst das jetzt über die
  `env_resolve`-Kopplung auf: ist die geladene kipy NICHT die für das laufende
  KiCad gekoppelte Version → Meldung führt mit dem Versions-Fix („Installieren"
  in der Einrichtung); sonst → mit „schließe zusätzliche KiCad-Fenster". Plus
  erkannte KiCad-/kipy-Version in der Meldung. (Kein Auto-Pick: `KiCad.get_board()`
  nimmt bereits `docs[0]` — der scheiternde Aufruf IST die Enumeration, ein
  Auto-Pick ist im Mehrfachinstanz-Fall technisch unmöglich.)
- **WSL↔Windows-Diagnose im IPC-Connect-Fehler.** Läuft der MCP-Server unter WSL
  (`ipc_session` nutzt `path_env.is_wsl()` — die bereits vorhandene, einzige
  Umgebungs-Erkennung), erklärt der Connect-Fehler jetzt, dass KiCads
  Live-API-Socket Windows-nativ und aus WSL nicht erreichbar ist (Live-IPC nur
  unter Windows-KiCad; aus WSL nur datei-basiert) — statt eines nackten Timeouts,
  der wie „KiCad ist tot" aussieht.

### Fixed
- **Gebündelter Server `plugin/mcp/kicad_mcp/` war veraltet — jetzt synchron +
  dauerhaft abgesichert.** Das Live-Plugin lädt den Server *bundled-first*
  (`claude_action._mcp_root`), führte also alten Code aus: dem Bundle fehlten
  9 Dateien (das komplette `generators/pinout/`-Feature aus v0.4.0 +
  `tools/pinout_tools.py`) und 14 Dateien waren inhaltlich veraltet (u. a. die
  Phantom-Disconnect-Härtung aus `df91f33`, `ipc_session`, `ipc_tools`,
  `board_open_guard`, `tool_registry`). Es gab nie einen Sync-Schritt —
  `make_pcm_zip.py` packte nur, was zufällig in `plugin/mcp/` lag. Behoben: das
  Bundle ist jetzt ein exakter Spiegel des kanonischen `kicad_mcp/`.

### Added
- **`scripts/sync_bundle.py`** — spiegelt `kicad_mcp/` → `plugin/mcp/kicad_mcp/`
  (ohne Caches/Bytecode); `--check` meldet Drift. **`tests/test_bundle_sync.py`**
  lässt die Suite fehlschlagen, sobald Bundle und kanonisch divergieren — die
  Drift kann damit nicht mehr unbemerkt wiederkehren.

## [0.4.3] — 2026-06-19

### Fixed
- **`_deps` koppelt `kicad-python` (kipy) jetzt an die laufende KiCad-Version,
  statt blind „latest" zu ziehen — die Wurzel von „nichts orange" /
  `failed: kicad-mcp`.** `deps.PIP_SPECS` installierte `kicad-python` ungepinnt,
  also zog pip die *neueste* kipy nach `_deps`. kipy spricht aber KiCads
  IPC-Protokoll, das pro Major-Release bricht: eine kipy neuer als das laufende
  KiCad reicht der GUI ein Protobuf-Schema, das sie nicht versteht →
  `KiCad().get_version()` scheitert *im GUI-Prozess*, und jedes board-bewusste
  Feature (Chat-Links, Live-Selektion) geht still aus. Neu: `plugin/env_resolve.py`
  erkennt die KiCad-Version (`pcbnew.GetBuildVersion()`, Fallback: Install-Pfad)
  und pinnt kipy auf die **gekoppelte** Version (KiCad 10 → `kicad-python==0.7.1`).
  Unbekannte/zukünftige KiCad-Version → unverändert ungepinnt (defensiv, bricht
  den Install nie).

### Added
- **Downgrade-Ausführung mit atomarem `_deps`-Swap (kein Brick).** Der Install
  landet zuerst in `_deps.new`, wird dort unter `-S` import-verifiziert und erst
  dann **atomar** über das Live-`_deps` geschoben (`env_resolve.atomic_swap_dir`:
  altes `_deps` zur Seite → neues hinein → altes löschen, mit Rollback bei
  Fehlschlag). Ein fehlgeschlagener Install lässt das alte `_deps` damit intakt.
  Weil `_deps.new` vor dem Install geleert wird, nimmt der Install eine zu neue
  kipy auch wirklich auf die gekoppelte Version **zurück** (pip `--target`
  downgradet eine vorhandene Kopie sonst nicht).
- **Environment-Fingerprint** (`_deps/.env_fingerprint`): hält fest, für welche
  KiCad-Version + gekoppelte kipy der Baum gebaut wurde
  (`env_resolve.build_fingerprint` / `fingerprint_stale`), sodass ein späterer
  Lauf „bereits gekoppelt" von „braucht Rebuild" unterscheiden kann.
- **Handshake-Selbstcheck nach dem Install** (im Einrichtungs-Dialog): bestätigt
  die Kopplung (`downgrade_decision` gegen das frische `_deps`) und startet
  testweise den MCP-Server (`server_probe.probe_server`). Bei Mismatch oder
  totem Handshake erscheint ein **lauter, handlungsweisender Hinweis** im
  Install-Log statt eines stillen „nichts orange".
- **Read-it-out-Fallback für unbekannte KiCad-Majors + Pollution-Wächter.** Die
  Coupling-Tabelle deckt nur bekannte Majors (heute KiCad 10). Damit ein
  künftiges KiCad 11 nicht auf blindes „latest" zurückfällt, liest
  `kicad_bundled_kipy_version` die kipy-Version, die KiCad **selbst** in seinen
  site-packages mitbringt (install-Pfad bevorzugt, sonst user-`3rdparty`; das
  Plugin-eigene `_deps` wird ignoriert), und pinnt `_deps` auf **diese** Version
  — strikt besser als „latest", mit lautem „abgeleitet, bitte verifizieren".
  `plan_kipy_pin` entscheidet `table → bundled → unpinned` und meldet zusätzlich
  **Verschmutzung**: hat die Tabelle den Major, weicht aber das (veränderbare)
  3rdparty-kipy ab, gewinnt die Tabelle und ein lauter Hinweis erscheint im
  Install-Log. (Live-Befund 2026-06-19: KiCad 10 liefert kipy nur im mutablen
  user-`3rdparty`, **keine** pristine Install-Kopie → Tabelle bleibt primär,
  3rdparty ist Fallback/Cross-Check, nicht Autorität.)
- `tests/test_plugin_env_resolve.py` — 61 Headless-Tests (Coupling,
  `resolve_pip_specs` inkl. 3rdparty-Fallback, Versions-Parse/-Vergleich,
  Downgrade-Entscheidung, Pfad-Klassifizierung, `plan_kipy_pin`/Pollution,
  Fingerprint, atomarer Swap inkl. Rollback).

## [0.4.2] — 2026-06-19

### Fixed
- **`_deps`-Installer macht den Ordner jetzt self-contained — kein stilles
  Auslassen von `kicad-python` mehr.** `pip install --target _deps … kicad-python`
  lief ohne `--ignore-installed`: weil `kicad-python` (kipy) samt seiner nativen
  Transitiv-Deps `protobuf` + `pynng` + `sniffio` in der Regel bereits in KiCads
  User-`3rdparty/Python311/site-packages` liegt, wertete pip sie als „already
  satisfied" und kopierte sie **nie** nach `_deps`. Der Server lief dann nur, weil
  KiCads Python jenes `3rdparty`-Verzeichnis als Backstop auf `sys.path` legt — ein
  `_deps`, das den Stack gar nicht enthält, fiel nicht auf. Der Install erzwingt den
  vollständigen Baum jetzt via `--ignore-installed` in `_deps` (Terminal-Variante
  `pip_install_commands` **und** der direkt genutzte `pip_install_argv`).
- **Deps-Verify deckt einen unvollständigen `_deps` jetzt auf, statt ihn zu
  maskieren.** Die Import-Verifikation lief ohne `-S`; KiCads Python legte dabei die
  `3rdparty`-site-packages auf den Pfad, sodass `import kipy` aus `3rdparty` gelang
  und „OK" meldete, obwohl `_deps` selbst leer war (genau die Lücke, die einen
  veralteten `_deps` durchrutschen ließ). `verify_import_argv` und die
  Terminal-Verify-Zeile laufen jetzt unter `-S` (site deaktiviert) → einzige Quelle
  ist `_deps`, und `import kipy` scheitert laut, wenn ein nativer Transitiv-Dep dort
  fehlt.

### Added
- **SessionStart-Hook für Claude Code on the web (`.claude/`).** Ein dauerhaft im
  Repo eingebauter Startup-Hook (`.claude/hooks/session-start.sh`, registriert in
  `.claude/settings.json`) installiert in jeder Web-Session die Dev-Umgebung
  (`pip install -e ".[dev]"` + `pylint`), damit Lint (`pylint kicad_mcp tests`)
  und Tests (`pytest tests/`) ohne manuelles Setup sofort laufen — dieselbe Matrix
  wie `.github/workflows/ci.yml`. Idempotent, nicht-interaktiv, nur im Remote-Env
  aktiv (`$CLAUDE_CODE_REMOTE`).

### Changed
- **Versionszählung auf eine Quelle der Wahrheit vereinheitlicht.** `pyproject.toml`
  trug statisch `1.0.0`, während `plugin/version.py` — das sich selbst als „single
  source of truth" deklariert — auf `0.4.0` stand: zwei divergierende Nummern für
  dasselbe Release. `pyproject.toml` bezieht die Version jetzt **dynamisch** aus
  `plugin/version.py` (`[tool.hatch.version] path = "plugin/version.py"`,
  `project.dynamic = ["version"]`) und legt die Wheel-Discovery explizit auf
  `kicad_mcp` fest. Ein einziger Bump in `version.py` bewegt fortan GUI-Plugin und
  gepacktes Wheel gemeinsam; die alte `pyproject`-`1.0.0` wird auf die aktive
  0.4.x-Linie zusammengeführt.
- **`kicad-python==0.7.1` als feste Test-Dependency (dev-Extra + CI).** kipy
  importiert headless vollständig (inkl. `kipy.proto`); als gepinnte
  dev-Abhängigkeit löst die Suite kipy/protobuf **deterministisch** auf, statt dass
  jeder Test `sys.modules['kipy']` ad-hoc faked. Damit laufen die
  `importorskip("kipy")`-IPC-Tests verlässlich (statt davon abzuhängen, ob etwas
  kipy mitten im Lauf installiert). Auf die KiCad-10.0-Version 0.7.1 gepinnt
  (vermeidet die v0.3.5-Versionsdiskrepanz-Klasse).

### Fixed
- **CI wieder grün — `google.protobuf` in `ignored-modules`.** Der `pylint`-Job
  scheiterte auf `main` durchgehend (E0401 `Unable to import
  'google.protobuf.empty_pb2'` in `ipc_tools.py`), und weil der `pytest`-Job per
  `needs: lint` davon abhängt, lief er gar nicht erst (Status: skipped) — die
  komplette Pipeline war rot. `google.protobuf` ist — wie `kipy` selbst — eine
  reine Laufzeit-/KiCad-seitige Abhängigkeit (kipys Wire-Format-Transport, via
  `kicad-python` gezogen) und im plain-CPython-CI-Runner abwesend; sie fehlte nur
  in der `ignored-modules`-Ausnahmeliste. Damit ist die Lint-Stufe wieder 0/0 und
  der per `needs: lint` zuvor übersprungene `pytest`-Job läuft überhaupt erst
  wieder (lokal 2145 grün).
- **Test-Suite hermetisch: kein echtes `pip install` mehr im Lauf + `fake_kipy`-
  Fixture entschärft.** Der dynamische „rufe jedes Tool mit `{}` auf"-Smoke-Test
  (`test_all_tools_dynamic`) rief `ipc_install_kipy` **echt** auf →
  `pip install --upgrade kicad-python` mitten in der Suite. Auf einem sauberen
  Runner (= CI) installierte das kipy *während* des Laufs, wodurch
  `test_ipc_markup_tools` — dessen Layer-Enums zur Collection-Zeit (kipy noch
  abwesend) als `None` eingefroren waren — von der Laufzeit (kipy jetzt da)
  divergierte: 5 **deterministische** Failures, sobald der `pytest`-Job (nach dem
  Lint-Fix) überhaupt lief. Drei Korrekturen: (1) der Installer wird im Smoke-Test
  gestubbt **und** ein autouse-conftest-Guard blockt *jeden* echten `pip install`
  aus Tests (per yield/finally statt `monkeypatch`, um die Fixture-Teardown-
  Reihenfolge nicht zu stören); (2) die `fake_kipy`-Fixture importiert jetzt das
  **echte** kipy, statt via `sys.modules.get("kipy") or ModuleType(...)` ein leeres
  Modul ohne `.proto` zu fabrizieren (das ließ `_layer_to_enum` still auf `None`
  fallen — maskiert nur dadurch, dass eine Modul-Konstante kipy zur Collection-Zeit
  als Seiteneffekt importierte); (3) die Layer-Enums werden zur Test-Zeit statt zur
  Collection-Zeit aufgelöst.
- **IPC-Verbindung: seltenere Phantom-Abrisse ("MCP nicht verbunden …").** Drei
  zusammenwirkende Härtungen am Live-IPC-Layer, gegen das häufige intermittierende
  Abreißen der kipy↔KiCad-Verbindung (Ursache: KiCad serialisiert API + UI auf
  einem Thread, und unsere Pre-Flight-/Cache-Pfade fingen einen bloß-busy oder
  veralteten Socket nicht ab):
  - `ipc_session.get_client()` health-checkt den gecachten Client jetzt mit einem
    billigen `ping()` vor der Wiederverwendung. Ein toter/desynchroner Socket
    (KiCad neu gestartet, oder ein vorheriger Call ist mitten im recv getimeoutet
    und hat den pynng-REQ/REP-Socket aus dem Tritt gebracht) wird transparent neu
    aufgebaut, statt eine tote Verbindung zurückzugeben. Ein `ping()`, das nur
    wegen "busy" fehlschlägt, behält den Client (Busy-Backoff bleibt Sache von
    `call_with_retry`).
  - `_require_editor` (der Pre-Flight-Gate vor fast jedem IPC-Tool) nutzt jetzt den
    zentralen, wiederverwendeten + auto-reconnectenden Client und wickelt die
    `get_open_documents`-Probe in `call_with_retry`, statt pro Call frisch und
    ohne Retry zu verbinden — das war der Haupt-Funnel für falsche
    "Cannot reach KiCad"-Abbrüche bei kurz beschäftigter GUI.
  - **Koordinierte Cache-Invalidierung:** `ipc_session.reset_client()` feuert jetzt
    registrierte Reset-Hooks, sodass Geschwister-Caches (der `board_open_guard`-
    Fast-Probe-Client) im Gleichschritt fallen. Ein einziges Reconnect-Ereignis
    invalidiert damit alle Verbindungs-Caches kohärent über einen KiCad-Neustart;
    die flakigen 1-s-Timeouts des Guards bleiben dagegen lokal (sie over-firen bei
    bloß-busy KiCad und dürfen den autoritativen 15-s-Client nicht abschießen).

## [0.4.0] — 2026-06-18

### Added
- **Pinout-Pipeline — Datenblatt-Validator + rangierte Symbol-Suche** (3 neue
  Tools, Tool-Count 170→173). Neues, eigenständiges deterministisches Modul
  `kicad_mcp/generators/pinout/`: prüft ein KiCad-`.kicad_sym`-Pinout strikt
  gegen das Datenblatt (Pin-Nummer, Pin-Name, electrical_type) und fängt damit
  „Pin vertauscht / falsche Package-Variante / EP-Nummer falsch". PDF-Extraktion
  hybrid (pdfplumber zuerst, austauschbarer LLM-Hook nur bei Versagen), Typ-
  Mapping gegen `symbol_author.VALID_PIN_TYPES`, aktive-Low-Namensnormalisierung,
  EP/PowerPAD-Abgleich. Tools: `search_symbol` (read-only, rangierte Kandidaten
  über Stock- + User-sym-lib-table), `validate_pinout`, `match_symbol_to_datasheet`
  (disambiguiert Varianten per Diff-Treffer). Abgegrenzt von
  `review_ic_against_datasheet` (Bild+LLM); CLI unter
  `python -m kicad_mcp.generators.pinout`.
- **Panel-Start: Platinen-Zusammenfassung, Interaktionsanleitung, Version,
  Empfehlungs-Mailto** (Plugin). Beim Öffnen des Chat-Panels sofort (ohne
  Claude-Turn): Versionszeile + verbundenes Board, ein klickbarer
  Empfehlungs-`mailto:`, die Interaktionsanleitung und eine asynchrone
  Platinen-Zusammenfassung (Footprints/Netze/Lagen, Bestückung nach Ref-Prefix,
  Board-Größe best-effort aus Edge.Cuts). Nebeneffekt: refs/nets/layers werden
  schon beim Start geladen → die ERSTE Antwort ist verlinkbar (vorher erst die
  zweite). Reine Builder in `plugin/banner.py` (`recommend_mailto`,
  `summary_lines`, `interaction_guide`) + `board_links.board_summary` /
  `board_extent_mm_from_file`.
- **Reverse-Brücke Board → Chat** (Plugin, Interaktions-Lücken). Die bisher
  einseitige Chat→Board-Brücke spricht jetzt zurück: „🔗 Auswahl einbeziehen"
  stellt die Editor-Selektion (`board_links.get_selection` →
  `selection_context`) dem Prompt voran (P1); ein Klick auf einen Bauteil-/Pin-
  Link zeigt zusätzlich die Pad→Netz-Verbindungen in der Statuszeile (P3,
  `inspect_ref`/`inspect_summary`); Rechtsklick bietet pro Link „nur markieren /
  hinzoomen / Eigenschaften" (P2); eine Antwort, die mehrere Elemente nennt,
  bekommt eine „📍 alle markieren"-Zeile (P4); Strg-Klick sammelt die Auswahl
  (P5).

### Changed
- **Chat-Board-Links erkennen die kanonischen KiCad-Benennungen toleranter**
  (Vokabular-Vertrag). Der Producer-System-Prompt (`claude_bridge`) verlangt
  jetzt kanonische Tokens (bare Reference, exakter Netzname, `F.Cu`,
  `<ref>.<pin>`, `(x, y)` mm), und `board_links.tokenize` normalisiert drei
  SICHERE Alias-Klassen ohne die Zero-False-Positive-Garantie aufzugeben:
  führender `/` an Netzen (`/GND`↔`GND`) + Groß/Kleinschreibung, Pin-Prosa
  (`pin 33 of U1` / `U1 pin 33` → `U1.33`) und Layer-Aliase nur mit Qualifier
  (`top copper`→`F.Cu`, bare „top" bleibt Text). Kein semantisches Raten
  (`ground`→`GND`).

### Fixed
- **CI wieder grün (Lint + Tests).** Der `pylint`-Job scheiterte (Exit 6) seit
  Längerem an `import-error` für Module, die nur unter KiCads gebündeltem Python
  bzw. als optionale Extras existieren (`pcbnew`, `wx`, `kipy`, `cairosvg`,
  `reportlab`) — sie stehen jetzt in `ignored-modules`. `pip install -e ".[dev]"`
  in der CI lief ins Leere (`dev` war nur eine `dependency-group`, kein
  PEP-621-Extra) und fiel auf `pip install -e .` zurück → `pytest` fehlte; ein
  spiegelndes `[project.optional-dependencies] dev`-Extra behebt das.
- **`mirror_layout`-Constraint in `place_with_constraints` rief
  `clone_layout_around_pivot_text` mit nicht existierender Signatur auf**
  (`source_pivot_ref=…`, `rotation_offset_deg=…`) → garantierter `TypeError` zur
  Laufzeit. Jetzt korrekt auf `source_ref` / `source_peripherals` /
  `target_pivots` gemappt, mit optionalem `target_refs`; `rotation_offset_deg`
  wird (mangels Hook in der Klon-Funktion) ehrlich als strukturierter Fehler
  abgewiesen statt still ignoriert.
- **`check_connectivity` / `via_promote` prüfen Datei-Existenz vor dem
  `pcbnew`-Import** (CLAUDE.md-Konvention #1): eine fehlende Datei liefert jetzt
  „PCB not found …" statt der irreführenden „pcbnew not importable"-Meldung.
- Diverse tote/ungenutzte Imports und Variablen entfernt (`defaultdict`,
  redundante lokale Reimports, ungenutzte `block`/`make_sub`/`ref_pads` u.a.);
  veralteter `server_bootstrap_code`-Test um die pywin32-`.pth`-Pfade ergänzt.

## [0.3.5] — 2026-06-16

### Fixed
- **`BoardUnavailable` zeigt jetzt den rohen Fehler.** Klick auf einen Link →
  „Kein eindeutiges Board über die KiCad-API erreichbar" (kipy lädt also, der
  Deps-Fix v0.3.3 griff). Derselbe Roh-Fehler („no handler for
  GetOpenDocuments") entsteht bei ZWEI verschiedenen Ursachen — mehrere
  KiCad-Instanzen auf einem IPC-Socket ODER eine kipy↔KiCad-Versionsdiskrepanz
  (Install zog ungepinntes `kicad-python` = neueste; CLAUDE.md nennt für
  KiCad 10.0 kipy 0.7.1). Die Meldung hängt jetzt den technischen Roh-Fehler an
  (`[Technisch: …]`) und nennt beide Ursachen, damit unterscheidbar ist, ob ein
  zweites Fenster zu schließen ist oder die kipy-Version anzupassen.

## [0.3.4] — 2026-06-16

### Fixed
- **Disk-Fallback verschluckte den Live-IPC-Fehlergrund.** Wenn der Disk-Parser
  die Links rettete (v0.3.2), löschte `_worker` das `_link_error` — die
  `ⓘ`-Zeile sagte nur „aus Datei", aber nicht WARUM Live-IPC (und damit der
  Klick) ausfiel. Jetzt bleibt der Grund erhalten (`_link_live_error`) und steht
  in der Statuszeile: `… aus Datei — Klick inaktiv (Live-IPC: <Grund>)`. So ist
  ablesbar, ob kipy noch fehlt (`No module named 'kipy'` → Deps-Installation
  nachholen) oder ob kipy lädt, aber die KiCad-API/das Board nicht erreichbar
  ist (anderer Fix).

## [0.3.3] — 2026-06-16

### Fixed
- **`ModuleNotFoundError: No module named 'kipy'` — die eigentliche Wurzel von
  „nichts ist orange" + fehlgeschlagener Live-Auswahl.** Die `ⓘ`-Diagnose
  (v0.3.1) hat es zutage gefördert: Es waren NICHT mehrere KiCad-Instanzen,
  sondern `kipy` war schlicht nicht installiert. `kipy` (PyPI: `kicad-python`,
  zieht `protobuf` + `pynng`) ist NICHT in KiCad gebündelt und wurde fälschlich
  als „von KiCad bereitgestellt" angenommen — fehlt es, scheitern der gesamte
  Live-IPC-Pfad (`board_links.connect()`, `ipc_select_items`, alle `ipc_*`) und
  die Chat-Links. Fix in drei Teilen:
  - `deps.IMPORT_NAMES` += `kipy`, `deps.PIP_SPECS` += `kicad-python` → der
    bewährte `pip install --target _deps`-Installer (umlaut-/Program-Files-fest
    aus v0.2.28–37) zieht kipy jetzt mit; die Deps-Prüfung erkennt es als
    Pflicht-Dependency.
  - `plugin/__init__._inject_local_deps()` legt `_deps` auch im **GUI-Plugin**
    auf `sys.path` (bisher injizierte nur der MCP-Server) — sonst fände
    `board_links` das frisch installierte kipy nicht (KiCads Python ignoriert
    `PYTHONPATH`).
  - `_discover_board_path()` fällt auf den ersten `*.kicad_pcb` im run-cwd
    zurück, wenn `GetFileName()` leer ist, damit der Disk-Link-Fallback (v0.3.2)
    immer eine Datei hat.
  Nach Update + Deps-Installation funktionieren Links (orange) UND die
  nicht-destruktive Editor-Auswahl wieder.

## [0.3.2] — 2026-06-16

### Fixed
- **Chat-Links erscheinen jetzt auch, wenn Live-IPC das Board nicht auflösen
  kann — Disk-Fallback.** Symptom: Antworten nannten Bauteile (`R_GATE_PD1`,
  `R_FAULT1` …), aber NICHTS war orange/klickbar. Ursache: Der MCP-Server liest
  das Board (er erzeugt die Tabelle), aber der separate kipy-Client des
  Chat-Panels (`board_links.connect()`) ging leer aus — klassisch bei mehreren
  KiCad-Instanzen auf einem IPC-Socket (`BoardUnavailable`), wodurch
  `self._refs/_nets/_layers` leer blieben und `tokenize` (headless als korrekt
  verifiziert) nichts zu matchen hatte. Fix: Neue
  `board_links.board_targets_from_file()` parst Footprint-Refs, Netznamen und
  Layer direkt aus der `.kicad_pcb` (derselben Datei, die auch der MCP-Server
  liest). Das Chat-Panel erfasst den offenen Board-Pfad beim Start
  (`_discover_board_path()` via `pcbnew.GetBoard().GetFileName()`) und fällt im
  `_worker` auf den Disk-Parser zurück, wenn Live-IPC fehlschlägt ODER 0
  Elemente liefert. Links RENDERN damit immer; die `ⓘ`-Statuszeile weist
  „aus Datei — Live-IPC nicht verfügbar" aus (Klick braucht weiter Live-IPC).
  4 neue Headless-Tests.

## [0.3.1] — 2026-06-16

### Fixed
- **Chat-Link-Status ist jetzt IMMER sichtbar — „nichts ist orange" ist nicht
  mehr undiagnostizierbar.** Bisher war die `ⓘ`-Diagnose nur im Fehler- und im
  0-gelesen-Fall sichtbar; bei „Board liefert Daten, aber im Reply matcht
  nichts" UND auf dem Erfolgspfad blieb sie stumm — man konnte nicht
  unterscheiden, ob die Links fehlen, weil (a) die Board-Verbindung scheiterte,
  (b) das Board 0 Refs/Netze/Layer lieferte, (c) Daten da waren, aber kein Token
  im Antworttext matchte, oder (d) alles ok ist. Neuer Helfer
  `_write_link_status()` druckt pro Antwort GENAU eine dimm-graue Zeile, die den
  Fall benennt (inkl. `N im Reply klickbar · r Refs / n Netze / ly Layer`).
  `tokenize` selbst ist headless verifiziert korrekt (Refs/Netze/Layer/Pins/
  Koordinaten) — die Ursache von „nichts orange" liegt also im Board-Refresh,
  und diese Zeile macht sie auf einen Blick lesbar.

## [0.3.0] — 2026-06-16 — Stabilitäts-Meilenstein

Sammelt die v0.2.28–v0.2.37-Arbeit zu einem getaggten Release: umlaut-feste
Installer, robuste Deps-Injektion unter KiCads gebündeltem Python, LF-sichere
`.bat`-Skripte, Live-Kollaboration (CAS auf `ipc_set_footprint_pose` /
`live_move_footprint`) und die Chat-Link-Diagnose. Alles unten Gelistete ist
Teil dieses Releases.

### Fixed
- **Chat-Link-Diagnose schließt die stille Lücke „Board-Daten da, aber
  nichts klickbar".** Der Chat-Panel meldete bisher nur den Fehlerfall
  (`ⓘ Links aus: …`) und 0 gelesene Board-Elemente (`ⓘ Links: 0 …`) — der
  Fall „Refs/Netze/Layer erfolgreich gelesen (Counts > 0), aber `tokenize`
  erkennt im konkreten Reply 0 Tokens" blieb **unbeobachtbar** (keine
  `ⓘ`-Zeile, Text wirkte normal). `_append_claude` gibt jetzt die Zahl der
  tatsächlich gerenderten Link-Spans zurück; `_on_reply` zeigt
  `ⓘ Links: <r> Refs / <n> Netze / <ly> Layer vom Board gelesen, aber 0 im
  Antworttext erkannt`, wenn Board-Daten vorlagen, aber nichts linkifiziert
  wurde. Macht die Ursache (Token-Format-Mismatch vs. leere Refs) auf einen
  Blick sichtbar.
- **v0.2.37: `ModuleNotFoundError: No module named 'pywintypes'` beim
  Deps-Verify/Serverstart behoben.** `mcp` 1.27 importiert beim Laden hart
  `pywintypes` (aus pywin32). `pip install --target _deps mcp` zieht pywin32
  zwar mit, aber dessen `.pth` (das `win32`/`win32\lib` auf den Pfad legt und
  die `pywin32_system32`-DLL-Dir via `os.add_dll_directory` registriert) wird
  unter `--target` **nie ausgeführt** → `import pywintypes` scheitert, obwohl
  pywin32 da ist. Fix: an JEDER Deps-Injektion (`deps.verify_import_argv`,
  `_check_code`, `pip_install_commands`, `mcp_config.server_bootstrap_code`,
  Standalone-`main.py`) den `.pth` nachbilden — neue Helfer
  `deps.pywin32_path_entries` + `deps.pywin32_dll_setup_code` (alles
  isdir/hasattr-guarded → no-op auf Nicht-Windows). End-to-end gegen ein
  isoliertes `--target`-Dir verifiziert (`import mcp` inkl. pywintypes → OK).
- **v0.2.36: `install_plugin.bat` / `start_mcp.bat` brachen mit `"." kann
  syntaktisch … nicht verarbeitet werden` — Ursache waren LF-Zeilenenden, NICHT
  der Umlaut.** GitHubs Source-ZIP wendet `.gitattributes eol=crlf` nicht an und
  liefert die `.bat` mit dem Repo-Blob = **LF**; cmd.exe ver-parst LF-`.bat` bei
  mehrzeiligen `(…)`-Blöcken und `for`-Schleifen (empirisch verifiziert:
  Einzelzeilen-`if`/`goto`/`set` laufen unter LF, `for /d` und Block-`(…)`
  brechen). Beide Skripte auf **reine Einzelzeilen-Konstrukte** umgeschrieben
  (Flow über `goto`/Labels; ZIP-Ordner deterministisch `kicad-mcp-<branch>`
  statt `for /d`; `kicad-cli`-Pfad via Substring-Ersetzung statt `for`). Unter
  echtem cmd.exe mit LF + Umlaut-Pfad end-to-end getestet (Plugin-Copy läuft
  durch, kein Syntaxfehler). Der umlaut-sichere `$env:WORK`-Download bleibt.
- **v0.2.35: Standalone-Installer scheitern nicht mehr bei Umlaut-Usernamen
  (`C:\Users\üser\…`).** Dieselbe Wurzel wie die Plugin-Deps-Fixes v0.2.28–31:
  `install.ps1` nutzte `pip install --user -e <repo>` — `--user` ist unter
  KiCads gebündeltem Python fragil, und der Pfad kippt. Fix: Installation in ein
  lokales `<repo>\_deps` via `pip install --upgrade --target` als **argv** (PS
  `&` → CreateProcessW, Unicode-sicher) mit `-X utf8`; `main.py` injiziert dieses
  `_deps` in `sys.path` (KiCad-Python ignoriert `PYTHONPATH`), additiv/no-op wenn
  abwesend. `install.ps1` setzt zudem die Konsole auf UTF-8. `install_plugin.bat`:
  der ü-behaftete Temp-Pfad wird im PowerShell-Download nicht mehr inline
  (`%WORK%`) sondern über `$env:WORK` gelesen (UTF-16 statt cmd-OEM-Codepage).

### Added
- **v0.2.34: CAS-Rollout auf `ipc_set_footprint_pose` (ipc-Layer).** Derselbe
  optimistic-concurrency-Schutz wie bei `live_move_footprint`, jetzt auch im
  `ipc_*`-Layer für den Footprint-Pose-Mutator: `dry_run=True` liefert die
  aktuelle `sig`, der reale Write wird mit `expect_sig` gegen diese Baseline
  geprüft und bei einer zwischenzeitlichen User-Bewegung verweigert
  (`{success: False, conflict: True, who: "user", baseline_sig, current_sig}`)
  statt zu überschreiben; jeder Erfolg gibt die neue `sig` zurück. Nutzt die
  geteilte reine Engine `cas_conflict`/`fp_signature`. **Noch offen (bewusst
  nicht überhastet):** die Multi-Item-UUID-Mutatoren `ipc_move_items` /
  `ipc_set_track_width` / `ipc_remove_items` brauchen eine `expect_sigs`-Map
  (uuid→sig) + generische Per-Typ-Signatur (fp/track/via/shape/text) — nächster
  Schritt, da gemischt-typig und nicht rein unit-testbar.
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
