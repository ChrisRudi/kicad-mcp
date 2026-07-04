# TODO — Demo-Bausätze (Schaltpläne + Platinen) überarbeiten

**Status: überarbeitungsbedürftig** (Nutzer-Feedback 2026-07-04, nach Sichtung
der Galerie aller 10). Die 10 Bausätze unter
`kicad_mcp/resources/data/demo_kits/*.json` validieren und bauen zwar gegen
echtes KiCad (reale Footprints), sind als *Schaustück* aber noch nicht
vorzeigbar. **Alle 10** müssen überarbeitet werden — Schaltplan UND Platine.

## Was konkret nicht stimmt

### Schaltpläne
- **Label-Überlappung:** Referenz (`U1`) und Wert (`LM386`) landen an fast
  derselben Position → übereinander, unlesbar. Der Schaltplan-Generator setzt
  die Feld-Positionen nicht auseinander.
- **Unaufgeräumte Verdrahtung:** die Auto-Platzierung per `hint_sch_*` ergibt
  zwar korrekte Netze, aber kein lesbares, konventionelles Schaltplan-Layout
  (Versorgung oben, GND unten, Signalfluss links→rechts). Wirkt „hingewürfelt".
- **Streu-Labels:** einzelne Power-/Netz-Labels sitzen weit weg vom Bauteil
  (im Export links oben im Nichts).

### Platinen
- **Nur geclusterte Startplatzierung, kein Routing:** die Bauteile liegen als
  kompakter Block in der Boardmitte, der Rest ist leer; es gibt keine
  sinnvolle Platzierung und **keine Leiterbahnen** (nur Ratsnest/kurze Stummel).
- Braucht: echte Platzierung (Funktionsblöcke, kurze Wege) + Routing — entweder
  von Hand vor-designt in der Spec, oder indem die Layout-Skills
  (Entwirren → Ausrichten → Routen) tatsächlich auf jedem Bausatz laufen.

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
