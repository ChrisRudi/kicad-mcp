# Nachhaltiger Plan — kicad-mcp (Detailfassung)

Dauerhafter Fahrplan mit Arbeitspaketen, Akzeptanzkriterien und Gates.
Baseline-Messwerte: 2026-07-07 (Harness `pcb_gallery`, KiCad 10.0.4-DRC).
Kurzfassung der Idee: Qualität wird über **Stufen mit Test-Gates** gehalten,
nicht über Zuruf — ein Rückschritt bricht CI, nicht erst das Feld.

## Nordstern

Ein Nutzer wählt eine Demo → **Schaltplan UND Platine** entstehen sichtbar
(Tool-für-Tool, seit 0.28.0), **datenblatt-echt** und **DRC-sauber**, und die
Fach-Skills laufen automatisch mit. Alles Neue ist **semantisch** (was KiCad
NICHT kann) und **selektions-bewusst**.

## Kit-Lebenszyklus (die Nachhaltigkeits-Mechanik)

| Stufe | Schaltplan (Elektrik) | Platine (Fertigung) | Gate |
|---|---|---|---|
| 🔬 Draft | baut, Netzlisten-Roundtrip 10/10 | baut, Footprints echt | `test_demo_kits` |
| ✅ Verified | **genau eine** Achse grün: entweder Schaltplan datenblatt-geprüft **oder** Platine 0 DRC | (die jeweils andere offen) | `test_pcb_placement` / Review-Log |
| ⭐ Prime-Time | **beide** Achsen: Schaltplan Pin-für-Pin datenblatt-geprüft (`verified`) **und** Platine 0 DRC / 0 offen (`board_clean`) | | `test_finished_kits_route_drc_clean` + Review-Log |

**Zwei unabhängige Achsen**, ein Menü-Symbol (implementiert in
`demo_kits.stage`): `verified` (Schaltplan) und `board_clean` (Platine). ⭐ =
beide, ✅ = eine, 🔬 = keine. Beide Default `False` → neue/geänderte Kits sind
automatisch 🔬. Das Modellieren als Circuit-Block+Rezept (Verschmelzung
0.27.0) ist das **Wartungs-Mittel**, um `verified` dauerhaft zu halten — kein
eigener Gate. **Regel:** „⭐"/`board_clean` ist ein grüner Test
(`board_clean_keys()` → `_DONE_KITS`), keine Meinung.

## Baseline 2026-07-07 (frisch gemessen, aktueller Code)

| Kit | err | offen | Haupt-Fehlertypen | Stufe |
|---|---|---|---|---|
| buck_converter | 0 | 0 | MP1584 ✓ | ⭐ |
| motor_driver | 0 | 0 | DRV8871 ✓ | ⭐ |
| led_ring | 0 | 0 | WS2812B ✓ (0.30.0) | ⭐ |
| kit_seeding | 0 | 0 | NE555 ✓ (0.30.0) | ⭐ |
| audio_amp | 0 | 0 | LM386 ✓; Platine 0/0 (0.31.0 Rip-up-lite) | ⭐ |
| production_ready | 0 | 0 | 74HC595 16-Pin ✓ (0.32.0 Rework) | ⭐ |
| ac_dc_supply | 0 | 0 | Platine 0/0 (0.31.0 Courtyard-Offset); TNY268-Review offen | ✅ |
| sketch_to_copper | 0 | **1** | AMS1117 ✓ (0.30.0) | ✅ (Platine: bewusst Skizze) |
| ethernet_device | **14** | **25** | clearance ×7, mask ×5, shorting ×2 (LQFP-48-Umfeld) | 🔬 |
| usb_sensor_hub | **31** | **22** | mask ×14, clearance ×9, shorting ×7, crossing ×1 (LQFP-48) | 🔬 |

**Stand nach 0.32.0: 6 ⭐ / 2 ✅ / 2 🔬** (board_clean: 7 Kits). Datenblatt-
Belege je Kit in `docs/kit_datasheet_reviews.md`.

---

# Phase 1 — Kuratieren: Reife-Labels aus einer Quelle

**Ziel:** Der Nutzer sieht im Demo-Menü sofort, was Referenz-Qualität hat.

**Arbeitspakete**
1. `plugin/demo_kits.py`: `DemoKit` bekommt `stage: str` (`"prime" | "verified" | "draft"`)
   + je Kit gesetzt (Stand: Tabelle oben). Menü-Titel bekommt das Emoji
   (⭐/✅/🔬), 🔬 zusätzlich Suffix „(in Arbeit)".
2. **Single Source herstellen:** `tests/test_pcb_placement._DONE_KITS` wird
   aus `demo_kits` abgeleitet (`[k.key for k in all_kits() if k.stage=="prime"]`)
   — das Label IST damit das DRC-Gate. Stufe hoch = Test muss grün sein;
   Stufe zu hoch = Test rot. Kein zweiter Pflegeort.
3. Konsistenz-Test in `test_demo_kits.py`: `stage=="prime"` ⇒ Kit hat
   Rezept unter `demo_kits/recipes/` (Verschmelzungs-Vertrag).
4. VERSIONS/CHANGELOG, i18n der neuen Menü-Texte (`tr()`), GUI-Smoke.

**Akzeptanz:** Menü zeigt 2 ⭐ / 4 ✅ / 4 🔬; `test_tool_audit` unverändert;
alle Gates grün. **Aufwand:** ~½ Tag. **Risiko:** keins (nur Anzeige+Gate).

# Phase 2 — Router-Härtung: die 4 Rest-Boards auf 0/0

**Ziel:** `_DONE_KITS` = alle 10. Nur generische Regeln, keine Spec-Handpflege.

**2a. ac_dc_supply (10 err / 1 offen) — Platzierungs-Regeln, kein Router-Thema**
- `pth_inside_courtyard ×4` + `courtyards_overlap ×2`: der Platzierer stellt
  THT-Löcher in fremde Courtyards (Trafo EE16/TO-220-Umfeld). Regel:
  Courtyard-Check auch gegen PTH-Bohrungen (Loch+Ring als Hindernis in
  `_resolve_pcb_overlaps`/`all_pads` aufnehmen — Ansatz existiert für MH).
- `shorting ×2`/`mask ×2` fallen erfahrungsgemäß mit der Platzierung (vgl.
  buck 111→0). Das 1 offene Netz danach neu bewerten.
- **Akzeptanz:** ac_dc in `_DONE_KITS`, 3-Seeds-byte-gleich, Galerie 0/0.
- **Aufwand:** 1–2 Tage.

**2b. audio_amp (0 err / 2 offen) — Pin-Escape mit Konfliktprüfung**
- Wurzel dokumentiert (test_pcb_placement.py:113): Pin-Tasche an U1:3 von
  Nachbar-Pad-Aufblasungen versiegelt. Naiver Escape-Stub shortete 2-Pad-
  Passives → verworfen.
- Plan: Escape-Stub als *gerouteter Pfad* behandeln (durch `_route_edge` mit
  Start=Pad-Zelle, Ziel=nächste freie Rasterzelle, volle Konfliktprüfung)
  statt als blinder Vektor. Fällt die Erreichbarkeit, ehrlich offen lassen.
- **Akzeptanz:** audio in `_DONE_KITS` (0/0) ODER dokumentierte Grenze bleibt
  explizit (dann bleibt ✅). **Aufwand:** 1–2 Tage, Forschungsanteil.

**2c. usb_sensor_hub (31/22) + ethernet_device (14/25) — Fine-Pitch-Fähigkeit**
- Befund: Fehler clustern am **LQFP-48 (0,5-mm-Pitch)** — das 0,635-mm-Raster
  kann Pads weder konfliktfrei anfahren noch zwischen ihnen hindurch
  (clearance+mask+shorting), und 22–25 Netze bleiben unerreichbar.
- Arbeitspakete (Reihenfolge):
  1. **Feinraster um Fine-Pitch-Zonen**: lokal 0,3175 mm (½ Raster) in einem
     Fenster um Footprints mit Pad-Pitch < 0,65 mm; global bleibt 0,635
     (Laufzeit!). Alternativ: Escape-Routing je LQFP-Pad auf den Grobraster-
     Ring (Fanout-Stubs), danach normal routen — industrieüblich, vermutlich
     der sauberere Schnitt.
  2. **Rip-up & Reroute light:** scheitert ein Netz, die 1–2 blockierenden
     Segmente entfernen, beide neu routen (max N Runden, deterministisch).
  3. **Mask-Bridges:** bei 0,5-mm-Pitch sind KiCads mask-bridge-Fehler z. T.
     footprint-inhärent → prüfen, ob `solder_mask_min_width`-Boardsetup die
     ehrliche Antwort ist statt Router-Verrenkung.
  4. USB-D±: als Paar nacheinander mit gemeinsamem Korridor routen (kein
     echtes Diff-Pair-Tuning — nur Nachbarschaft, semantisch ehrlich).
- **Akzeptanz:** beide Kits ≤ 2 err / 0 offen als Zwischenziel, dann 0/0 in
  `_DONE_KITS`; Laufzeit Galerie < 5 min gesamt; Determinismus 3 Seeds.
- **Aufwand:** 3–5 Tage (der Forschungsbrocken). **Risiko:** mittel —
  Fanout-Ansatz zuerst, Feinraster nur falls nötig.

**2d. sketch_to_copper (0/1):** nach 2c neu messen; vermutlich fällt das eine
offene Netz mit dem Escape-Routing. Bleibt sonst bewusst „Skizze" (🔬).

# Phase 3 — Datenblatt-Review der Rest-7 (Kernauftrag „Industrie-Schaltungen")

Muster je Kit wie 0.26.1 (MP1584/DRV8871/LM386): echtes IC, Pinout aus
**2 unabhängigen Quellen**, Pflicht-Beschaltung aus dem Datenblatt, Quelle in
den Block. Reihenfolge nach Demo-Wert:

| Kit | IC(s) | Referenz-Datenblatt | Kern-Prüfpunkte |
|---|---|---|---|
| ~~led_ring~~ ✓0.30.0 | WS2812B | Worldsemi V5 | DIN/DOUT-Kette, 5-V-Pegel, 100n-Abblockung (1 je 2 LEDs, dok.) |
| ~~kit_seeding~~ ✓0.30.0 | NE555 | TI SLFS022 | Astabil-Formeln R1/R2/C, CV-Abblock 10n, Reset an VCC |
| production_ready | 74HC595 | Nexperia | **Rework auf echtes 16-Pin** (/OE→GND Pin13, /SRCLR→VCC Pin10, RCLK Pin12 verdrahten) |
| ac_dc_supply | TNY268 + PC817 + TL431 | Power Integrations TNY263-268 | EN/BP-Pin-Beschaltung, Bias-Wicklung, TL431-Feedback-Teiler, **Kriechstrecken-Zonen prim/sek** |
| usb_sensor_hub | STM32F103C8 + BME280 + AMS1117 | ST DS5319 + Bosch BST-BME280 | VDDA/VBAT, BOOT0-Strap, NRST-C, USB-DP-Pullup 1k5, BME280 CSB/SDO-Straps |
| ethernet_device | STM32F407 + LAN8720 | ST + Microchip DS8720 | RMII-Pin-Zuordnung, 49R9-Terminierungen, XTAL-Beschaltung, PHY-Straps |
| sketch_to_copper | AMS1117-5.0 | AMS | trivial (Block existiert quasi: ams1117_ldo_3v3 klonen) |

**Je Kit:** Block-JSON (v1.1 voll: `kicad_symbol`, `between`, Quelle) +
Rezept; `scripts/compose_demo_kits.py`; Roundtrip 10/10; Determinismus.
**Akzeptanz:** Kit „Verified"; `test_kit_compose` deckt es; Review-Notiz im
CHANGELOG. **Aufwand:** ½–1 Tag je Kit; ac_dc + ethernet je 1–2 Tage
(mehr ICs / Straps). **Gesamt ~5–8 Tage.**

# Phase 4 — Verschmelzung vollenden

Fällt mit Phase 3 zusammen (jedes Review endet als Block+Rezept). Abschluss-
Kriterium: **alle 10** Kits sind Build-Artefakte; `test_kit_compose`
parametrisiert über 10 Rezepte; `examples/`-README bleibt reiner Wegweiser.
Restaufwand nach Phase 3: ~0 (Definition of Done von Phase 3).

# Phase 5 — KiCad-11-Bereitschaft (docs/kicad11_vorbereitung.md)

1. **Nightly-CI-Job + Capability-Probe** (jetzt): optionaler Job
   `kicad-nightly` (PPA kicad-dev-nightly, `continue-on-error`), Skript
   `scripts/kicad_capability_probe.py` druckt kicad-cli-Subkommandos,
   kipy-Handshake, Proto-Messages als Job-Summary. Wert = Wochen-Diff.
   Aufwand: klein, kein Produktcode.
2. **`utils/board_backend.py`-Adapter** (`load/fill_zones/connectivity/eval`):
   SWIG-Implementierung heute, IPC später; die 3 Warm-Worker ziehen um.
   Gate: bestehende pcbnew-Suite unverändert grün unter dem Adapter.
3. **`render_backend`-Signatur** vereinheitlichen (Datei-Render heute,
   Live-Render v11) — nur Signatur/Callsites, kein neues Feature.

**Trigger für mehr:** die Probe meldet headless-IPC → Rückbau-Liste aus dem
Doc (Warm-Worker auf IPC, live_ipc_harness ohne Xvfb). **Aufwand:** 1–2 Tage
jetzt; Rest ereignisgetrieben.

# Phase 6 — Laufende Qualität (ohne Enddatum)

- **Gates scharf halten:** pylint 0/0, duplicate-code (min 10 Zeilen),
  Netzlisten-Roundtrip 10/10, Byte-Determinismus (PYTHONHASHSEED-Seeds),
  `test_version_release`, Bundle-Sync, GUI-Smoke, Live-IPC.
- **ToolSearch-Discovery:** 2–3 Calls je Kaltstart-Turn senken (Messung
  zuerst: welche Tools werden wie oft nachgeladen; dann Gruppierung).
- **Feld-Reports:** weiter Reproduzieren → Wurzel → generische Regel →
  Regressionstest → Release (bewährtes Muster 0.25.7–0.28.0).
- **Jedes neue Feature:** Selektions-Vertrag (a) + Synergie (b) aus CLAUDE.md.

---

## Reihenfolge & Meilensteine

| # | Meilenstein | Inhalt | Ergebnis sichtbar |
|---|---|---|---|
| M1 | Kuratiert | Phase 1 | Menü zeigt ⭐/✅/🔬 ehrlich |
| M2 | „Alle echten ICs" | Phase 3 für led_ring, kit_seeding, production_ready, sketch | 6 Kits Verified+, 4 davon ⭐ |
| M3 | Netzteil-Demo ⭐ | Phase 2a + Phase 3(ac_dc) | das 230-V-Vorzeigeprojekt sauber |
| M4 | Fine-Pitch ⭐ | Phase 2c + Phase 3(usb, ethernet) | LQFP-Boards 0/0 — Router „kann MCU-Boards" |
| M5 | Audio 0/0 | Phase 2b | letztes offenes Netz weg (oder Grenze dokumentiert) |
| M6 | v11-bereit | Phase 5.1–5.3 | Nightly-Report läuft, Adapter steht |

Begründung der Reihenfolge: M1 ist billig und macht Erwartungen ehrlich.
M2 vor M3/M4, weil Datenblatt-Reviews unabhängig vom Router sind und sofort
Demo-Wert liefern (dein Kernauftrag). M3 vor M4 (ac_dc ist fast fertig,
Netzspannung ist der Show-Case). M4 ist der Forschungsbrocken mit eigenem
Zwischenziel. Phase 6 läuft immer mit.
