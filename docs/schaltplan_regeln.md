# Regelwerk: So entsteht ein professioneller Schaltplan

Die Destillation aus dem Weg zu „Roundtrip 10/10 + badness 0". Jede Regel ist
im Code verankert (Modul in Klammern) und von Tests bewacht. **Reihenfolge der
Abschnitte = Reihenfolge der Pipeline.**

## 0. Die zwei unverhandelbaren Gates

1. **Elektrisch: gezeichnet = gewollt.** Aus dem fertigen `.kicad_sch` wird mit
   KiCads eigener Engine (`kicad-cli sch export netlist`) die Ist-Netzliste
   extrahiert und pin-genau mit der Soll-Netzliste verglichen. Kein Kurzschluss,
   kein zerfallenes Netz, kein offener Pin. *(netlist_check.py,
   test_netlist_roundtrip.py — läuft bei jeder Suite über alle 10 Kits.)*
2. **Optisch: am Profi geeicht.** Die Qualitäts-Metrik (`badness`) muss auf
   echten Profi-Referenzschaltplänen exakt 0 ergeben — sonst ist die Metrik
   falsch, nicht die Referenz. Erst eichen, dann fordern. *(layout_measure.py,
   test_layout_measure.py.)*

Dazu: **Determinismus** — gleiche Eingabe erzeugt ein byte-identisches Blatt.
Niemals über eine unsortierte String-Menge iterieren, wenn das Ergebnis davon
abhängt (PYTHONHASHSEED!). `sorted()`-Stellen im Code sind Absicht.

## 1. Eingabe verstehen: Pin-NAME schlägt Pin-Nummer

Kits/Nutzer beschreiben Pins semantisch („IN1", „DRAIN", „NRST"); Nummern sind
paket-abhängig und oft falsch. Beim Zuordnen zum Bibliotheks-Symbol gilt:
Name zuerst (mit `~{}`-Dekoration, `TXD0/MODE0`-Slash-Aliassen, NRST↔RST,
DRAIN→D-Synonymen, gestapelten GND-Gruppen), dann Nummer — aber nie auf einen
schon vergebenen Pin. Unzuordenbares bleibt ehrlich offen statt falsch
verbunden. Symbol-Wahl mit Pin-Zahl-Sanity: ein 11-Pin-Teil bekommt kein
176-Pin-BGA-Symbol, sondern die Platzhalter-Box; 4-Pin-Quarz → Crystal_GND24.
*(route._map_user_to_real_pins, symbol_lib._pin_count_sane — EINE Quelle für
Geometrie, Emission und Netzlisten-Vergleich.)*

## 2. Platzieren: Luft ist eine Anforderung

- Signalfluss links → rechts: Eingangs-Stecker links, ICs Mitte, Ausgang rechts;
  Versorgungsregler als eigener Block.
- **Mehr Luft lassen:** Körper-Spalt unter 2.54 mm ist Gedränge (Metrik
  `crowding`); kein Bauteil „ohne Anlass mitten ins Gewühl". Abstands-Faktor
  1.7. Beschriftungen brauchen Platz — wer den Platz nicht lässt, baut die
  Überdeckung selbst ein.
- **Pins dürfen nie aufeinander liegen** — Pin-auf-Pin ist in KiCad eine harte
  Verbindung; Kollisionen werden nach der Platzierung deterministisch entzerrt.
- **Wiederholung sieht gleich aus:** wiederholte Teilschaltungen
  (Multivibrator-Hälften, Ketten-Glieder) werden strukturell erkannt und
  identisch gestempelt, Instanzen in Leseordnung; der Optimierer bewegt die
  Formation nur starr. Das Gesamt-Layout wird zuletzt auf die Blattmitte
  zentriert. *(common/repetition.py, place._uniform_repeated_units,
  place._center_on_sheet.)*
- **Unverbunden = mehr Luft:** Bauteile ohne gemeinsames Signal-Netz halten
  mindestens einen Pin-Rasterpunkt (2.54 mm) ZUSÄTZLICHEN Abstand — Nähe ohne
  elektrischen Grund ist Gedränge; verbundene dürfen näher (sie haben einen
  Grund). Pin-reiche ICs (≥10 Pins) bekommen +5.08 mm Hof für Pin-Namen und
  Label-Korridor. *(geometry._pair_margin/_ic_air.)*
- **R/C/L an GND/VCC stehen senkrecht** (Rotation 0/180, Power-Symbol direkt
  drüber/drunter) — Pull-up und Abblock-C wie im Profi-Schaltbild; der
  Optimierer darf die Konvention nicht wegdrehen (`_rot_locked`).
  *(place.py inkl. _orient_power_passives, defrag_place.py,
  builder._resolve_pin_collisions.)*

## 3. Verdrahten: erst schützen, dann zeichnen

- **Jeder verdrahtete Pin bekommt einen Stub** (kurze axiale Leitung aus dem
  Bauteil heraus); Routen laufen Stub-Spitze → Stub-Spitze, nie durch den
  eigenen Körper. Hindernisse rotations-bewusst.
- **Konflikt-Registry:** Vor JEDER Leitung/jedem Stub wird geprüft, ob sie ein
  fremdes Netz elektrisch berührt (gemeinsamer Endpunkt, Endpunkt-auf-Segment,
  kollineare Überlappung, fremder PIN auf dem Weg). Bei Konflikt: andere
  Richtung/Länge — niemals blind zeichnen.
- **Selbstheilung:** Zerfällt ein Netz (Kante nicht routbar), bekommt JEDE
  Komponente ein gleichnamiges Label (KiCad vereint per Namen). Ein Label ohne
  Draht verbindet nichts — immer mit Stub, notfalls 0.635 mm.
- Kreuzungen (X) sind erlaubt; Leitungen übereinander nicht. Kollineare
  Segmente nur bei geteiltem Endpunkt vereinigen (gleicher Knoten = gleiches
  Netz — sonst wäre der Merge selbst ein Kurzschluss).
- **T-Abzweig ⇒ Junction-Punkt** (netz-bewusst; nie zwischen zwei Netzen).
  *(route.py: Registry, _emit_pin_stub, Union-Find-Heilung, Junctions.)*

## 4. Versorgung: Symbol an jeden Pin

Power-Netze werden nicht quer übers Blatt verdrahtet: **jeder** GND-Pin
bekommt sein GND-Symbol (nach unten), jeder Versorgungs-Pin seinen Pfeil (nach
oben) — wie im Profi-Schaltbild; KiCad vereint global über den Namen. Rails
ohne KiCad-Symbol (VIN …) bekommen Global-Labels, mit derselben
Konflikt-Verhandlung. Rail-Schreibweisen normalisieren (P3V3 → +3V3).

## 5. Beschriften: lesbar und draußen

- Labels zeigen vom Bauteil weg, mit ≥ 5 mm eigener Leitung; Richtung/Länge
  werden verhandelt (Registry + Körper + **Pin-Zone**: Körperkante + 2.84 mm —
  nie längs durch die Pin-Nummern-Spalte, nie ins Bauteil ragen; effektive
  Körpergröße = min(Grafik-Bbox, Pin-Käfig), Deko zählt nicht).
- Referenz/Wert: bei ICs über/unter dem Körper; bei gedrehten Bauteilen
  Property-Winkel gegenrotieren (KiCad rendert relativ zur Symbol-Rotation —
  sonst Buchstabensalat „10uC1").
- Heilungs-Labels sitzen an der Stub-Spitze (auf dem Draht, außerhalb der
  Zone), nicht am Pin. Der Nach-Emit-Aufräumer dreht kollidierende Labels in
  freie Richtungen — aber nur reine Stichleitungen (Anker-Grad 1, nie auf
  einer Pin-Position), elektrisch geprüft.
  *(route._stub_dir_free, builder: Ref/Value + _declutter_labels.)*

## 6. Nichts überdeckt irgendetwas (die Mess-Checkliste)

Bauteil↔Bauteil, Bauteil↔Enge, Label↔Bauteil/Pin-Zone, Label↔Label,
Label↔fremder Draht, Referenz/Wert↔Referenz/Wert, Draht↔Bauteil-Kern
(Endpunkt auf einem Pin = Anschluss, keine Querung), Draht↔Draht (kollinear),
Diagonalen, Off-Grid. Alles einzeln gewichtet in `badness`; der
Layout-Optimierer (Hill-Climb über Position/Rotation, re-emittiert echt)
minimiert genau diese Zahl und darf das Layout nie verschlechtern.

## 7. Arbeitsweise (Meta-Regeln, die den Erfolg gebracht haben)

1. **Messen → am Goldstandard eichen → fixen → visuell verifizieren.** Nie
   „sieht gut aus" behaupten ohne Render; nie eine Metrik fordern, die die
   Profi-Referenz nicht besteht.
2. **Wurzelursache statt Symptom:** jeden Befund bis zur Koordinate verfolgen
   (Kontaktpunkt-Diagnose), erst dann ändern.
3. **Ein Fix, ein Gate-Lauf:** nach jedem Schritt Roundtrip + Hashes; was die
   Gates bricht, fliegt raus — egal wie hübsch es ist.
