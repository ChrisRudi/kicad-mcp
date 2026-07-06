# TODO — Demo-Bausätze (Schaltpläne + Platinen) überarbeiten

**Status: überarbeitungsbedürftig** (Nutzer-Feedback 2026-07-04, nach Sichtung
der Galerie aller 10). Die 10 Bausätze unter
`kicad_mcp/resources/data/demo_kits/*.json` validieren und bauen zwar gegen
echtes KiCad (reale Footprints), sind als *Schaustück* aber noch nicht
vorzeigbar. **Alle 10** müssen überarbeitet werden — Schaltplan UND Platine.

## Was konkret nicht stimmt

### Schaltpläne — GRÖSSTENTEILS ERLEDIGT (0.12.1)
- ✅ **Platzierung:** `hint_sch` raus → der defrag-Platzierer clustert eng ums
  IC, gedreht, mit kurzen echten Leitungen (keine Label-Wüste). Der schwache
  „Simple solver" verdrängt ihn nicht mehr.
- ✅ **GND unten / VCC oben:** hart erzwungen in `route._place_power_symbol`.
- ✅ **Stecker außen, Leitung nach innen** (Signalfluss links→rechts) — das ist
  gewollt (Konvention), kein Bug.
- Rest-Politur (offen, klein): der dünne gestrichelte „Passives"-Gruppenrahmen
  wirkt etwas eigen; Ref/Value bei gedrehten Passiven sitzen noch knapp.

### Platinen
- **Nur geclusterte Startplatzierung, kein Routing:** die Bauteile liegen als
  kompakter Block in der Boardmitte, der Rest ist leer; es gibt keine
  sinnvolle Platzierung und **keine Leiterbahnen** (nur Ratsnest/kurze Stummel).
- Braucht: echte Platzierung (Funktionsblöcke, kurze Wege) + Routing — entweder
  von Hand vor-designt in der Spec, oder indem die Layout-Skills
  (Entwirren → Ausrichten → Routen) tatsächlich auf jedem Bausatz laufen.

### PCB-Messlatte — Baseline 2026-07-06 (Phase 1, Vorgabe: Nutzer-Go)

Harness: alle 10 Kits → `build_pcb` → `kicad-cli pcb drc --format json`
(severity error gezählt) + `unconnected_items` + Render F.Cu/B.Cu/Silk/Edge.
Der Router (`generators/pcb/route.py`, MST + L-Shape) läuft zwar, produziert
aber Shorts statt Umwege. Baseline (err / offen / Segmente):

| Kit | DRC-err | offen | segs |
|---|---|---|---|
| ac_dc_supply | 174 | 9 | 45 |
| audio_amp | 158 | 13 | 18 |
| buck_converter | 111 | 12 | 22 |
| ethernet_device | 507 | 20 | 39 |
| kit_seeding | 137 | 16 | 22 |
| led_ring | 164 | 21 | 33 |
| motor_driver | 118 | 9 | 16 |
| production_ready | 158 | 22 | 30 |
| sketch_to_copper | 39 | 6 | 10 |
| usb_sensor_hub | 585 | 25 | 38 |

Fehlertypen aggregiert (nur severity=error, alle Kits): solder_mask_bridge
882, clearance 626, **shorting_items 486**, hole_clearance 105,
copper_edge_clearance 38, tracks_crossing 14. Sichtbefund (buck): Bauteile
überlappen (C3 auf U1, R1/D1 kollidieren), Tracks laufen quer durch fremde
Pads. Reihenfolge Phase 2 (universelle Regeln, keine Per-Kit-Handarbeit):
(1) kollisionsfreie Platzierung mit Courtyard-Abstand, (2) Router darf nie
ein fremdes Pad/Segment schneiden (Konflikt-Registry wie im
Schaltplan-Router), (3) Mask/Silk-Politur. Ziel je Kit: 0 err / 0 offen.

## Mögliche Richtungen (noch zu entscheiden)

1. **Skills laufen lassen statt vor-platzieren:** genau der „magic"-Kern —
   den sequenziellen Skill-Ablauf bauen (Entwirren → … → Routen) und die
   Demo-Boards damit erzeugen. Dann ist die rohe Startplatzierung sogar
   erwünscht (die Skills räumen auf), und die Galerie zeigt Vorher/Nachher.
2. **Schaltplan-Generator verbessern:** Feld-Positionen (Ref/Value)
   auseinanderziehen, konventionelles Layout (Rails oben/unten).
3. **Kuratierte Vor-Layouts:** je Bausatz eine handgesetzte, saubere
   Platzierung + Basis-Routing in der Spec hinterlegen.

## Umfang
Alle **10**: audio_amp, usb_sensor_hub, ac_dc_supply, led_ring, motor_driver,
buck_converter, ethernet_device, sketch_to_copper, production_ready,
kit_seeding.
