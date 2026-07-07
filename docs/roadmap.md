# Nachhaltiger Plan — kicad-mcp

Ein dauerhafter Fahrplan, kein Ad-hoc-To-do. Ziel: das MCP von „viele
Einzel-Fixes" auf eine **stabile Qualitätsstruktur** heben, die Rückschritte
durch Gates verhindert. Neueste Prioritäten oben; Details unten.

## Nordstern

Ein Nutzer wählt eine Demo → **Schaltplan UND Platine** entstehen sichtbar
(Tool-für-Tool nachvollziehbar), **datenblatt-echt** und **DRC-sauber**, und
die Fach-Skills führen ihn weiter. Alles, was wir bauen, ist **semantisch**
(was KiCad NICHT kann) und **selektions-bewusst**.

## Kit-Lebenszyklus (die zentrale Nachhaltigkeits-Idee)

Jeder Demo-Bausatz durchläuft feste Stufen; höhere Stufe = mehr Gate. Ein Kit
darf nur „⭐ Prime-Time" heißen, wenn **beide** Achsen grün sind.

| Stufe | Schaltplan (Elektrik) | Platine (Fertigung) | Gate |
|---|---|---|---|
| 🔬 Draft | baut, Netzliste roundtrip 10/10 | baut, Footprints echt | `test_demo_kits` |
| ✅ Verified | **Pin-für-Pin gegen Herstellerdatenblatt** (Quelle in Spec) | Platzierung kollisionsfrei | Review-Log + `test_pcb_placement` |
| ⭐ Prime-Time | Verified **+** als Circuit-Block/Rezept modelliert | **0 DRC-Fehler / 0 offene Netze** (KiCad-CLI) | `test_finished_kits_route_drc_clean` |

**Regel:** Die „Prime-Time"-Liste ist ein **Test-Gate** (`_DONE_KITS`), keine
Meinung. Ein Kit fällt automatisch raus, sobald sein Board DRC-Fehler bekommt.

## Aktueller Stand (Stand 0.28.0)

| Kit | Platine | Schaltplan | Block+Rezept | Stufe |
|---|---|---|---|---|
| buck_converter | 0/0 ✅ | MP1584 ✅ | ✅ | ⭐ |
| motor_driver | 0/0 ✅ | DRV8871 ✅ | ✅ | ⭐ |
| audio_amp | 0/**2** | LM386 ✅ | ✅ | ✅ (2 offen) |
| led_ring | 0/0 ✅ | plausibel | — | ✅→⭐ (Schaltplan-Review offen) |
| kit_seeding | 0/0 ✅ | Demo | — | ✅ |
| production_ready | 0/0 ✅ | generisch | — | ✅ |
| ac_dc_supply | **10**/1 | Flyback plausibel | — | 🔬 |
| sketch_to_copper | 0/1 | (Show-Case) | — | 🔬 (bewusst Skizze) |
| usb_sensor_hub | **31**/**22** | ungeprüft | — | 🔬 |
| ethernet_device | **14**/**25** | ungeprüft | — | 🔬 |

Bilanz: **2 ⭐, 4 ✅, 4 🔬**. Ziel dieses Plans: **alle 10 auf mindestens ✅,
die „echten" Schaltungen auf ⭐.**

---

## Phasen (priorisiert)

### Phase 1 — Kuratieren & ehrlich labeln (klein, sofort)
Der Nutzer muss auf einen Blick sehen, was Referenz-Qualität hat.
- Demo-Menü: Stufe je Kit anzeigen (⭐/✅/🔬) aus einer **einzigen** Quelle
  (`demo_kits.py` bekommt ein `stage`-Feld, aus dem Test-Gate abgeleitet).
- „🔬"-Kits mit klarer Kennzeichnung „in Arbeit" statt stiller Gleichstellung.
- *Warum zuerst:* verhindert falsche Erwartung, kostet einen Tag, kein Risiko.

### Phase 2 — Die 4 🔬-Boards auf ✅/⭐ (Router-Härtung, Task #36)
Der schwerste Brocken, größter Hebel für „alle vorzeigbar".
1. **ac_dc_supply** (10/1) — Netzspannungs-Abstände + Platzierung; naheliegend.
2. **audio_amp** (0/2) — versiegelte Pin-Tasche an U1:3; Pin-Escape mit
   Stub-Konfliktprüfung (der verworfene Versuch ist dokumentiert).
3. **usb_sensor_hub** (31/22) & **ethernet_device** (14/25) — hohe Dichte:
   Router braucht Rip-up/Reroute oder bessere Netz-Reihenfolge; ggf. größere
   Boards. Hier liegt die eigentliche Router-Forschung.
- *Nachhaltig:* jede Verbesserung als generische Regel im Router/Platzierer,
  nicht als Spec-Handpflege. `pcb_gallery.py` bleibt die Messlatte.

### Phase 3 — Schaltpläne auf „Verified" (Datenblatt-Review der Rest-7)
Dein Kernauftrag „wirklich funktionierende aus der Industrie".
- Je Kit: echtes IC wählen, Pinout aus 2 Quellen verifizieren, Pflicht-
  Beschaltung ergänzen, Quelle in die Spec (Muster: der 0.26.1-Review).
- Ergebnis wandert als **Circuit-Block** in `resources/data/circuit_blocks/`
  → Kit als **Rezept** (Muster 0.27.0). Damit auch Phase 4 erledigt.

### Phase 4 — Verschmelzung vollenden (7 Kits auf Block+Rezept)
- Fällt großteils mit Phase 3 zusammen. Danach sind ALLE Kits Build-Artefakte
  aus einer Quelle; `test_kit_compose` wacht über alle 10.

### Phase 5 — KiCad-11-Bereitschaft (docs/kicad11_vorbereitung.md)
Vorlaufarbeit, damit v11 (~Feb 2027) kein Bruch wird.
1. Nightly-CI-Job + Capability-Probe (was kann die 10.99-Nightly schon?).
2. `board_backend`-Adapter (IPC vs. SWIG entkoppeln).
3. `render_backend`-Signatur vereinheitlichen.

### Phase 6 — Laufende Qualität (dauerhaft, kein Enddatum)
- ToolSearch-Discovery-Kosten senken (2–3 Calls je Kaltstart-Turn).
- Duplicate-code-Gate, pylint 0/0, Determinismus-Gates: bleiben scharf.
- Jede neue Fähigkeit: Selektions-Vertrag + Synergie (geteilte Helfer).

---

## Was „nachhaltig" konkret absichert

1. **Gates statt Meinung:** Prime-Time = grüner Test, nicht Zuruf. Regression
   bricht CI, nicht erst das Feld.
2. **Eine Quelle je Sache:** Kits aus Blöcken+Rezepten, Bundle gespiegelt,
   Metadaten in `demo_kits.py`/`plugin/superfeatures.py` — keine Zweitpflege.
3. **Ehrliche Reife-Labels:** der Nutzer sieht Draft/Verified/Prime-Time, nie
   ein 🔬-Kit als fertig verkauft.
4. **Generische Regeln statt Spec-Handpflege:** Board-Probleme werden im
   Router/Platzierer gelöst, damit der nächste Kit profitiert.

## Empfohlene Reihenfolge

Phase 1 (kuratieren) → Phase 3+4 verschränkt (die „echten" Schaltungen
verifizieren & modellieren, das ist dein Kernwunsch) → Phase 2 (Router-Härtung
für die dichten Boards) → Phase 5 (KiCad 11). Phase 6 läuft durchgehend mit.
